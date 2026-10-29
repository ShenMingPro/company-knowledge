"""Lazy adapter around the existing RAG implementation.

Importing ``rag.rag_service`` creates model clients in the legacy code. Keeping that
import inside ``answer`` lets the web process expose health/auth/CRUD endpoints even
when an external model is not configured.
"""

from __future__ import annotations

from collections.abc import Iterator
from queue import Full, Queue
from threading import Event, Lock, Thread
from typing import Any

from utils.logger_handler import logger


class RagUnavailableError(RuntimeError):
    """Raised when the configured RAG implementation cannot be initialized."""


class _StreamCancelled(RuntimeError):
    """Stops the model iterator after the HTTP consumer disconnects."""


class LegacyRagAdapter:
    def __init__(self) -> None:
        self._instances: dict[str, Any] = {}
        self._instance_lock = Lock()
        self._role_locks: dict[str, Lock] = {}

    def _get_instance(self, role: str) -> Any:
        instance = self._instances.get(role)
        if instance is not None:
            return instance
        with self._instance_lock:
            instance = self._instances.get(role)
            if instance is None:
                try:
                    from rag.rag_service import RagSummarizeService

                    instance = RagSummarizeService(user_role=role)
                except Exception as exc:
                    logger.exception("RAG 初始化失败，role=%s", role)
                    raise RagUnavailableError("RAG 服务当前不可用，请检查模型与向量库配置") from exc
                self._role_locks[role] = Lock()
                self._instances[role] = instance
        return instance

    def answer(
        self,
        *,
        query: str,
        role: str,
        history: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        instance = self._get_instance(role)
        try:
            with self._role_locks[role]:
                return instance.rag_summarize(query, history=history)
        except Exception as exc:
            logger.exception("RAG 问答执行失败，role=%s", role)
            raise RagUnavailableError("RAG 问答执行失败，请稍后重试") from exc

    def answer_stream(
        self,
        *,
        query: str,
        role: str,
        history: list[dict[str, Any]] | None = None,
    ) -> Iterator[dict[str, Any]]:
        instance = self._get_instance(role)
        output: Queue[tuple[str, Any]] = Queue(maxsize=64)
        cancelled = Event()

        def emit(event_type: str, payload: Any) -> bool:
            while not cancelled.is_set():
                try:
                    output.put((event_type, payload), timeout=0.1)
                    return True
                except Full:
                    continue
            return False

        def emit_token(chunk: str) -> None:
            if not emit("delta", chunk):
                raise _StreamCancelled()

        def generate() -> None:
            try:
                with self._role_locks[role]:
                    result = instance.rag_summarize(
                        query,
                        history=history,
                        on_token=emit_token,
                    )
                if cancelled.is_set():
                    return
                if result.get("refusal_reason") == "generation_error":
                    emit("error", RagUnavailableError("RAG 问答生成失败，请稍后重试"))
                    return
                emit("done", result)
            except Exception as exc:
                if cancelled.is_set() or isinstance(exc, _StreamCancelled):
                    return
                logger.exception("RAG 流式问答执行失败，role=%s", role)
                emit("error", exc)

        Thread(target=generate, name=f"rag-stream-{role}", daemon=True).start()
        emitted_delta = False
        try:
            while True:
                event_type, payload = output.get()
                if event_type == "delta":
                    emitted_delta = True
                    yield {"type": "delta", "delta": payload}
                    continue
                if event_type == "error":
                    if isinstance(payload, RagUnavailableError):
                        raise payload
                    raise RagUnavailableError("RAG 问答执行失败，请稍后重试") from payload

                if not emitted_delta and payload.get("answer"):
                    yield {"type": "delta", "delta": payload["answer"]}
                yield {"type": "done", "result": payload}
                return
        finally:
            cancelled.set()


rag_adapter = LegacyRagAdapter()
