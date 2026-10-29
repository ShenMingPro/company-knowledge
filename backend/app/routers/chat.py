import json
from collections.abc import Iterator

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from starlette.responses import StreamingResponse

from backend.app.adapters.rag import RagUnavailableError, rag_adapter
from backend.app.config import get_settings
from backend.app.dependencies import get_current_user, get_db
from backend.app.rate_limit import enforce_rate_limit
from backend.app.schemas import (
    AskRequest,
    AskResponse,
    FeedbackUpsert,
    SessionCreate,
    SessionTitleUpdate,
)
from models.database import ChatMessage, ChatSession, Feedback, User
from services.audit_service import AuditService
from services.feedback_service import FeedbackService
from services.interaction_service import InteractionService
from utils.logger_handler import logger


router = APIRouter(prefix="/chat", tags=["chat"])

_METRIC_NAMES = (
    "retrieved_count",
    "filtered_count",
    "valid_count",
    "retrieval_time_ms",
    "generation_time_ms",
    "total_time_ms",
    "retrieval_mode",
    "rerank_applied",
    "query_rewrite_applied",
)


def _ask_response(rag_result: dict, persisted: dict) -> AskResponse:
    return AskResponse(
        session_id=persisted["session_id"],
        message_id=persisted["message_id"],
        answer=rag_result.get("answer", ""),
        has_answer=rag_result.get("has_answer", False),
        references=rag_result.get("references", []),
        metrics={name: rag_result.get(name) for name in _METRIC_NAMES if name in rag_result},
    )


def _sse_event(event: str, data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n"


@router.get("/sessions")
async def list_sessions(
    limit: int = 50,
    user: User = Depends(get_current_user),
) -> list[dict]:
    safe_limit = max(1, min(limit, 100))
    return await run_in_threadpool(InteractionService.get_user_sessions, user.id, safe_limit)


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
def create_session(
    payload: SessionCreate,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> dict:
    chat_session = ChatSession(user_id=user.id, title=payload.title)
    session.add(chat_session)
    session.commit()
    session.refresh(chat_session)
    return {
        "id": chat_session.id,
        "title": chat_session.title or "空会话",
        "message_count": 0,
        "created_at": chat_session.created_at,
        "updated_at": chat_session.updated_at,
    }


@router.get("/sessions/{session_id}/messages")
async def session_messages(
    session_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    owned = db.query(ChatSession.id).filter(
        ChatSession.id == session_id,
        ChatSession.user_id == user.id,
    ).first()
    if owned is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="会话不存在")
    return await run_in_threadpool(InteractionService.get_session_messages, session_id, user.id)


@router.patch("/sessions/{session_id}")
async def update_session_title(
    session_id: int,
    payload: SessionTitleUpdate,
    user: User = Depends(get_current_user),
) -> dict:
    result = await run_in_threadpool(
        InteractionService.update_session_title,
        session_id,
        user.id,
        payload.title,
    )
    if not result.get("success"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=result.get("message", "会话不存在"))
    return result


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_session(
    session_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> None:
    chat_session = session.query(ChatSession).filter(
        ChatSession.id == session_id,
        ChatSession.user_id == user.id,
    ).first()
    if chat_session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="会话不存在")
    message_ids = [
        row[0]
        for row in session.query(ChatMessage.id).filter(ChatMessage.session_id == chat_session.id).all()
    ]
    if message_ids:
        session.query(Feedback).filter(Feedback.message_id.in_(message_ids)).delete(
            synchronize_session=False
        )
        session.query(ChatMessage).filter(ChatMessage.id.in_(message_ids)).delete(
            synchronize_session=False
        )
    session.delete(chat_session)
    session.commit()


@router.post("/query", response_model=AskResponse)
@router.post("/ask", response_model=AskResponse, include_in_schema=False)
async def ask(
    payload: AskRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AskResponse:
    settings = get_settings()
    await run_in_threadpool(
        enforce_rate_limit,
        scope="rag-query",
        identity=str(user.id),
        limit=settings.rate_limit_query_requests,
        window_seconds=settings.rate_limit_window_seconds,
    )
    history = payload.history
    if payload.session_id is not None:
        owned_session = db.query(ChatSession.id).filter(
            ChatSession.id == payload.session_id,
            ChatSession.user_id == user.id,
        ).first()
        if owned_session is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="会话不存在")
        if history is None:
            exchanges = await run_in_threadpool(
                InteractionService.get_session_messages,
                payload.session_id,
                user.id,
            )
            history = []
            for exchange in exchanges:
                history.extend(
                    [
                        {"role": "user", "content": exchange["user_query"]},
                        {"role": "assistant", "content": exchange["system_answer"]},
                    ]
                )
    try:
        rag_result = await run_in_threadpool(
            rag_adapter.answer,
            query=payload.query,
            role=user.role,
            history=history,
        )
    except RagUnavailableError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc

    persisted = await run_in_threadpool(
        InteractionService.record_exchange,
        user.id,
        payload.session_id,
        payload.query,
        rag_result,
    )
    if not persisted.get("success"):
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="问答结果持久化失败")

    await run_in_threadpool(
        AuditService.record_rag_query,
        user.id,
        persisted["message_id"],
        payload.query,
        rag_result,
    )
    return _ask_response(rag_result, persisted)


@router.post("/query/stream")
@router.post("/ask/stream", include_in_schema=False)
async def ask_stream(
    payload: AskRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    settings = get_settings()
    await run_in_threadpool(
        enforce_rate_limit,
        scope="rag-query",
        identity=str(user.id),
        limit=settings.rate_limit_query_requests,
        window_seconds=settings.rate_limit_window_seconds,
    )
    history = payload.history
    if payload.session_id is not None:
        owned_session = db.query(ChatSession.id).filter(
            ChatSession.id == payload.session_id,
            ChatSession.user_id == user.id,
        ).first()
        if owned_session is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="会话不存在")
        if history is None:
            exchanges = await run_in_threadpool(
                InteractionService.get_session_messages,
                payload.session_id,
                user.id,
            )
            history = []
            for exchange in exchanges:
                history.extend(
                    [
                        {"role": "user", "content": exchange["user_query"]},
                        {"role": "assistant", "content": exchange["system_answer"]},
                    ]
                )

    user_id = user.id
    user_role = user.role
    session_id = payload.session_id
    query = payload.query

    def stream_events() -> Iterator[str]:
        try:
            for event in rag_adapter.answer_stream(
                query=query,
                role=user_role,
                history=history,
            ):
                if event.get("type") == "delta":
                    yield _sse_event("delta", {"delta": event.get("delta", "")})
                    continue
                if event.get("type") != "done":
                    continue

                rag_result = event["result"]
                persisted = InteractionService.record_exchange(
                    user_id,
                    session_id,
                    query,
                    rag_result,
                )
                if not persisted.get("success"):
                    yield _sse_event(
                        "error",
                        {"code": "persistence_failed", "detail": "问答结果持久化失败"},
                    )
                    return

                audited = AuditService.record_rag_query(
                    user_id,
                    persisted["message_id"],
                    query,
                    rag_result,
                )
                if not audited.get("success"):
                    logger.error(
                        "流式问答审计记录失败，user_id=%s, message_id=%s",
                        user_id,
                        persisted["message_id"],
                    )
                completed = _ask_response(rag_result, persisted)
                yield _sse_event("done", completed.model_dump(mode="json"))
                return
        except RagUnavailableError as exc:
            yield _sse_event("error", {"code": "rag_unavailable", "detail": str(exc)})
        except Exception:
            logger.exception("流式问答响应失败，user_id=%s", user_id)
            yield _sse_event("error", {"code": "internal_error", "detail": "问答处理失败，请稍后重试"})

    return StreamingResponse(
        stream_events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.put("/messages/{message_id}/feedback")
async def upsert_feedback(
    message_id: int,
    payload: FeedbackUpsert,
    user: User = Depends(get_current_user),
) -> dict:
    result = await run_in_threadpool(
        FeedbackService.upsert_feedback,
        user.id,
        message_id,
        payload.feedback_type,
        payload.content,
    )
    if not result.get("success"):
        detail = result.get("error", "反馈提交失败")
        code = status.HTTP_403_FORBIDDEN if "无权" in detail else status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=code, detail=detail)
    return result


@router.get("/messages/{message_id}/feedback")
async def get_feedback(message_id: int, user: User = Depends(get_current_user)) -> dict:
    return await run_in_threadpool(FeedbackService.get_message_feedback, user.id, message_id)
