"""Background document indexing adapter."""

from __future__ import annotations

from backend.app.redis_client import redis_store
from services.audit_service import AuditService
from services.document_service import DocumentService


def index_document(
    *,
    file_path: str,
    document_id: int,
    version_id: int,
    space_id: int,
    document_name: str,
    user_id: int,
    action_type: str,
    task_id: str | None = None,
) -> None:
    result: dict = {"success": False, "message": "索引服务不可用"}
    if task_id:
        redis_store.update_task(task_id, "processing", progress=10, message="正在解析并建立向量索引")
    try:
        # Deliberately lazy: the legacy module initializes the embedding client.
        from rag.vector_store import VectorStoreService

        result = VectorStoreService().index_document(
            file_path=file_path,
            document_id=document_id,
            version_id=version_id,
            space_id=space_id,
            document_name=document_name,
        )
    except Exception as exc:
        result = {"success": False, "message": str(exc)}
        DocumentService.update_version_status(version_id, "failed", error_message=str(exc))
    finally:
        succeeded = bool(result.get("success"))
        try:
            AuditService.record_document_operation(
                user_id=user_id,
                action_type=action_type,
                document_id=document_id,
                version_id=version_id,
                space_id=space_id,
                is_success=succeeded,
                error_message=None if succeeded else result.get("message"),
            )
        finally:
            redis_store.delete_cache_prefix("documents:")
            if task_id:
                redis_store.update_task(
                    task_id,
                    "succeeded" if succeeded else "failed",
                    progress=100,
                    message="索引完成" if succeeded else "索引失败",
                    error=None if succeeded else result.get("message", "索引失败"),
                )
