from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from backend.app.adapters.indexing import index_document
from backend.app.config import get_settings
from backend.app.dependencies import get_current_user, get_db, require_roles
from backend.app.rate_limit import enforce_rate_limit
from backend.app.redis_client import redis_store
from backend.app.schemas import DocumentUpdate, RestoreRequest
from models.database import Document, DocumentVersion, KnowledgeSpace, User
from services.audit_service import AuditService
from services.document_service import DocumentService


router = APIRouter(prefix="/documents", tags=["documents"])


def _documents_cache_key(
    user: User,
    space_id: int | None,
    search: str | None,
    status_filter: str | None,
    include_deleted: bool,
) -> str:
    return ":".join(
        [
            "documents",
            "list",
            str(user.id),
            user.role,
            str(space_id or "all"),
            search or "",
            status_filter or "",
            str(include_deleted).lower(),
        ]
    )


async def _invalidate_document_cache() -> None:
    await run_in_threadpool(redis_store.delete_cache_prefix, "documents:")


def _space_roles(space: KnowledgeSpace) -> set[str]:
    return {role.strip() for role in space.allowed_roles.split(",") if role.strip()}


def _require_space_access(session: Session, user: User, space_id: int) -> KnowledgeSpace:
    space = session.query(KnowledgeSpace).filter(
        KnowledgeSpace.id == space_id,
        KnowledgeSpace.is_enabled.is_(True),
    ).first()
    if space is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="知识空间不存在")
    if user.role not in _space_roles(space):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权访问该知识空间")
    return space


async def _write_upload(upload: UploadFile, extension: str) -> tuple[str, int]:
    settings = get_settings()
    handle = tempfile.NamedTemporaryFile(prefix="knowbase_", suffix=extension, delete=False)
    path = handle.name
    size = 0
    try:
        with handle:
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > settings.upload_max_bytes:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"文件不能超过 {settings.upload_max_bytes // (1024 * 1024)} MB",
                    )
                handle.write(chunk)
        if size == 0:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="不能上传空文件")
        return path, size
    except Exception:
        try:
            os.unlink(path)
        except OSError:
            pass
        raise
    finally:
        await upload.close()


@router.get("")
async def list_documents(
    space_id: int | None = None,
    search: str | None = Query(default=None, max_length=255),
    status_filter: str | None = Query(default=None, alias="status"),
    include_deleted: bool = False,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> list[dict]:
    if include_deleted and user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="权限不足")
    cache_key = _documents_cache_key(user, space_id, search, status_filter, include_deleted)
    if space_id is not None:
        _require_space_access(session, user, space_id)
        cached = await run_in_threadpool(redis_store.get_json, cache_key)
        if cached is not None:
            return cached
        result = await run_in_threadpool(
            DocumentService.get_documents,
            space_id,
            include_deleted,
            search,
            status_filter,
        )
        await run_in_threadpool(redis_store.set_json, cache_key, result)
        return result

    accessible_ids = [
        item.id
        for item in session.query(KnowledgeSpace).filter(KnowledgeSpace.is_enabled.is_(True)).all()
        if user.role in _space_roles(item)
    ]
    cached = await run_in_threadpool(redis_store.get_json, cache_key)
    if cached is not None:
        return cached
    documents: list[dict] = []
    for allowed_space_id in accessible_ids:
        documents.extend(
            await run_in_threadpool(
                DocumentService.get_documents,
                allowed_space_id,
                include_deleted,
                search,
                status_filter,
            )
        )
    result = sorted(documents, key=lambda item: item.get("created_at"), reverse=True)
    await run_in_threadpool(redis_store.set_json, cache_key, result)
    return result


@router.get("/{document_id}")
async def document_detail(
    document_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> dict:
    document = session.query(Document).filter(Document.id == document_id).first()
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文档不存在")
    _require_space_access(session, user, document.space_id)
    cache_key = f"documents:detail:{user.id}:{user.role}:{document_id}"
    cached = await run_in_threadpool(redis_store.get_json, cache_key)
    if cached is not None:
        return cached
    result = await run_in_threadpool(DocumentService.get_document_detail, document_id)
    if not result.get("success"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=result.get("message", "文档不存在"))
    await run_in_threadpool(redis_store.set_json, cache_key, result)
    return result


@router.patch("/{document_id}")
async def update_document(
    document_id: int,
    payload: DocumentUpdate,
    user: User = Depends(require_roles("admin")),
    session: Session = Depends(get_db),
) -> dict:
    document = session.query(Document).filter(Document.id == document_id).first()
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文档不存在")
    safe_name = DocumentService.sanitize_filename(payload.name).strip()
    if safe_name in {"", ".", ".."}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="文档名称无效")
    duplicate = session.query(Document.id).filter(
        Document.space_id == document.space_id,
        Document.name == safe_name,
        Document.id != document.id,
        Document.is_deleted.is_(False),
    ).first()
    if duplicate:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="同一知识空间内已存在同名文档")
    document.name = safe_name
    session.commit()
    await run_in_threadpool(
        AuditService.record_document_operation,
        user.id,
        "document_update",
        document.id,
        document.current_version_id,
        document.space_id,
        True,
        None,
    )
    await _invalidate_document_cache()
    return {"message": "文档已更新", "document_id": document.id, "name": document.name}


@router.post("", status_code=status.HTTP_202_ACCEPTED)
@router.post("/upload", status_code=status.HTTP_202_ACCEPTED)
async def upload_document(
    background_tasks: BackgroundTasks,
    space_id: int = Form(...),
    file: UploadFile = File(...),
    user: User = Depends(require_roles("admin")),
    session: Session = Depends(get_db),
) -> dict:
    settings = get_settings()
    await run_in_threadpool(
        enforce_rate_limit,
        scope="document-upload",
        identity=str(user.id),
        limit=settings.rate_limit_upload_requests,
        window_seconds=settings.rate_limit_window_seconds,
    )
    _require_space_access(session, user, space_id)
    original_name = file.filename or "document"
    safe_name = DocumentService.sanitize_filename(original_name)
    extension = Path(safe_name).suffix.lower()
    if extension not in get_settings().allowed_document_extensions:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="仅支持 PDF、DOCX、TXT 和 XLSX 文件",
        )

    temp_path, file_size = await _write_upload(file, extension)
    try:
        result = await run_in_threadpool(
            DocumentService.create_document,
            safe_name,
            space_id,
            user.id,
            temp_path,
            file_size,
        )
    finally:
        try:
            os.unlink(temp_path)
        except OSError:
            pass

    if not result.get("success"):
        await run_in_threadpool(
            AuditService.record_document_operation,
            user.id,
            "document_upload",
            result.get("document_id", 0),
            None,
            space_id,
            False,
            result.get("message"),
        )
        code = status.HTTP_409_CONFLICT if result.get("status") == "unchanged" else status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=code, detail=result.get("message", "上传失败"))

    await run_in_threadpool(DocumentService.update_version_status, result["version_id"], "processing")
    await _invalidate_document_cache()
    action_type = "document_new_version" if result.get("is_new_version") else "document_upload"
    task_id = str(uuid.uuid4())
    await run_in_threadpool(
        redis_store.create_task,
        task_id,
        owner_user_id=user.id,
        task_type="document_index",
        document_id=result["document_id"],
        version_id=result["version_id"],
        space_id=space_id,
    )
    background_tasks.add_task(
        index_document,
        file_path=result["file_path"],
        document_id=result["document_id"],
        version_id=result["version_id"],
        space_id=space_id,
        document_name=safe_name,
        user_id=user.id,
        action_type=action_type,
        task_id=task_id,
    )
    return {
        "message": "文档已接收，正在后台索引",
        "document_id": result["document_id"],
        "version_id": result["version_id"],
        "task_id": task_id,
        "task_status": "queued",
        "status": "processing",
        "is_new_version": bool(result.get("is_new_version")),
    }


@router.post("/{document_id}/reindex", status_code=status.HTTP_202_ACCEPTED)
async def reindex_document(
    document_id: int,
    background_tasks: BackgroundTasks,
    version_id: int | None = Query(default=None, ge=1),
    user: User = Depends(require_roles("admin")),
    session: Session = Depends(get_db),
) -> dict:
    document = session.query(Document).filter(Document.id == document_id).first()
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文档不存在")
    target_id = version_id or document.current_version_id
    version = session.query(DocumentVersion).filter(
        DocumentVersion.id == target_id,
        DocumentVersion.document_id == document_id,
    ).first()
    if version is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文档版本不存在")

    result = await run_in_threadpool(DocumentService.reindex_version, version.id)
    if not result.get("success"):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=result.get("message", "无法重新索引"))
    await _invalidate_document_cache()
    task_id = str(uuid.uuid4())
    await run_in_threadpool(
        redis_store.create_task,
        task_id,
        owner_user_id=user.id,
        task_type="document_reindex",
        document_id=document.id,
        version_id=version.id,
        space_id=document.space_id,
    )
    background_tasks.add_task(
        index_document,
        file_path=version.file_path,
        document_id=document.id,
        version_id=version.id,
        space_id=document.space_id,
        document_name=document.name,
        user_id=user.id,
        action_type="document_reindex",
        task_id=task_id,
    )
    return {
        "message": "已开始重新索引",
        "document_id": document.id,
        "version_id": version.id,
        "task_id": task_id,
        "task_status": "queued",
        "status": "processing",
    }


async def _document_action(
    *,
    action: str,
    document_id: int,
    user: User,
    version_id: int | None = None,
) -> dict:
    operations = {
        "deactivate": (DocumentService.deactivate_document, "document_deactivate"),
        "restore": (DocumentService.restore_document, "document_restore"),
        "delete": (DocumentService.soft_delete_document, "document_delete"),
    }
    operation, audit_action = operations[action]
    args = (document_id, version_id) if action == "restore" else (document_id,)
    result = await run_in_threadpool(operation, *args)
    await run_in_threadpool(
        AuditService.record_document_operation,
        user.id,
        audit_action,
        document_id,
        result.get("version_id", version_id),
        None,
        bool(result.get("success")),
        None if result.get("success") else result.get("message"),
    )
    if not result.get("success"):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=result.get("message", "操作失败"))
    await _invalidate_document_cache()
    return result


@router.post("/{document_id}/deactivate")
async def deactivate_document(
    document_id: int,
    user: User = Depends(require_roles("admin")),
) -> dict:
    return await _document_action(action="deactivate", document_id=document_id, user=user)


@router.post("/{document_id}/restore")
async def restore_document(
    document_id: int,
    payload: RestoreRequest,
    user: User = Depends(require_roles("admin")),
) -> dict:
    return await _document_action(
        action="restore",
        document_id=document_id,
        user=user,
        version_id=payload.version_id,
    )


@router.delete("/{document_id}")
async def delete_document(
    document_id: int,
    user: User = Depends(require_roles("admin")),
) -> dict:
    return await _document_action(action="delete", document_id=document_id, user=user)


@router.get("/{document_id}/versions/{version_id}/download", response_class=FileResponse)
def download_document(
    document_id: int,
    version_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> FileResponse:
    document = session.query(Document).filter(Document.id == document_id).first()
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文档不存在")
    _require_space_access(session, user, document.space_id)
    version = session.query(DocumentVersion).filter(
        DocumentVersion.id == version_id,
        DocumentVersion.document_id == document_id,
    ).first()
    if version is None or not os.path.isfile(version.file_path):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文档文件不存在")
    return FileResponse(version.file_path, filename=document.name)
