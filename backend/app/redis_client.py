"""Redis-backed cache, rate-limit counters, and background task state."""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any

from fastapi.encoders import jsonable_encoder

from backend.app.config import get_settings


logger = logging.getLogger(__name__)


class RedisUnavailableError(RuntimeError):
    """Raised when a Redis-backed security control cannot be evaluated."""


class RedisStore:
    """Small fail-soft Redis boundary used by the HTTP application.

    Cache failures fall back to the database and task state is mirrored in a
    bounded in-process store. Security controls fail closed while Redis is enabled.
    """

    def __init__(self) -> None:
        self._client: Any | None = None
        self._client_url: str | None = None
        self._task_fallback: dict[str, dict[str, Any]] = {}
        self._task_fallback_expiry: dict[str, float] = {}
        self._lock = threading.Lock()
        self._warned = False

    def _redis(self) -> Any | None:
        settings = get_settings()
        if not settings.redis_enabled:
            return None
        if self._client is not None and self._client_url == settings.redis_url:
            return self._client
        try:
            from redis import Redis

            self._client = Redis.from_url(
                settings.redis_url,
                decode_responses=True,
                socket_connect_timeout=settings.redis_timeout_seconds,
                socket_timeout=settings.redis_timeout_seconds,
            )
            self._client_url = settings.redis_url
            return self._client
        except Exception as exc:  # pragma: no cover - import/configuration edge
            self._report_unavailable(exc)
            return None

    def _report_unavailable(self, exc: Exception) -> None:
        if not self._warned:
            logger.warning("Redis unavailable; using degraded behavior: %s", exc)
            self._warned = True

    @staticmethod
    def _key(kind: str, key: str) -> str:
        return f"knowbase:{kind}:{key}"

    def get_json(self, key: str) -> Any | None:
        client = self._redis()
        if client is None:
            return None
        try:
            raw = client.get(self._key("cache", key))
            return json.loads(raw) if raw is not None else None
        except Exception as exc:
            self._report_unavailable(exc)
            return None

    def health_status(self) -> str:
        if not get_settings().redis_enabled:
            return "disabled"
        client = self._redis()
        if client is None:
            return "degraded"
        try:
            return "ok" if client.ping() else "degraded"
        except Exception as exc:
            self._report_unavailable(exc)
            return "degraded"

    def set_json(self, key: str, value: Any, ttl_seconds: int | None = None) -> bool:
        client = self._redis()
        if client is None:
            return False
        ttl = ttl_seconds or get_settings().cache_ttl_seconds
        try:
            client.setex(
                self._key("cache", key),
                ttl,
                json.dumps(jsonable_encoder(value), ensure_ascii=False),
            )
            return True
        except Exception as exc:
            self._report_unavailable(exc)
            return False

    def delete_cache_prefix(self, prefix: str) -> int:
        client = self._redis()
        if client is None:
            return 0
        try:
            keys = list(client.scan_iter(match=self._key("cache", f"{prefix}*"), count=100))
            return int(client.delete(*keys)) if keys else 0
        except Exception as exc:
            self._report_unavailable(exc)
            return 0

    def rate_limit(self, scope: str, identity: str, limit: int, window_seconds: int) -> tuple[bool, int]:
        """Return (allowed, retry_after), failing closed when Redis is enabled."""
        if limit <= 0 or window_seconds <= 0:
            return True, 0
        client = self._redis()
        if client is None:
            if get_settings().redis_enabled:
                raise RedisUnavailableError("Redis rate limiter is unavailable")
            return True, 0
        now = int(datetime.now(tz=timezone.utc).timestamp())
        bucket = now // window_seconds
        key = self._key("rate", f"{scope}:{identity}:{bucket}")
        try:
            pipe = client.pipeline(transaction=True)
            pipe.incr(key)
            pipe.expire(key, window_seconds + 1)
            count, _ = pipe.execute()
            if int(count) <= limit:
                return True, 0
            retry_after = window_seconds - (now % window_seconds)
            return False, max(1, retry_after)
        except Exception as exc:
            self._report_unavailable(exc)
            raise RedisUnavailableError("Redis rate limiter is unavailable") from exc

    def revoke_token(self, jti: str, ttl_seconds: int) -> bool:
        if not jti or ttl_seconds <= 0:
            return False
        client = self._redis()
        if client is None:
            return False
        try:
            return bool(client.setex(self._key("revoked-token", jti), ttl_seconds, "1"))
        except Exception as exc:
            self._report_unavailable(exc)
            return False

    def is_token_revoked(self, jti: str) -> bool:
        client = self._redis()
        if client is None:
            if get_settings().redis_enabled:
                raise RedisUnavailableError("Redis token denylist is unavailable")
            return False
        try:
            return bool(client.exists(self._key("revoked-token", jti)))
        except Exception as exc:
            self._report_unavailable(exc)
            raise RedisUnavailableError("Redis token denylist is unavailable") from exc

    def create_task(self, task_id: str, **metadata: Any) -> dict[str, Any]:
        task = {
            "task_id": task_id,
            "status": "queued",
            "progress": 0,
            "message": "任务已排队",
            "created_at": self._now(),
            "updated_at": self._now(),
            "error": None,
            **metadata,
        }
        self._save_task(task_id, task)
        return task

    def update_task(self, task_id: str, status: str, **changes: Any) -> dict[str, Any]:
        task = self.get_task(task_id) or {"task_id": task_id, "created_at": self._now()}
        task.update(changes)
        task["status"] = status
        task["updated_at"] = self._now()
        self._save_task(task_id, task)
        return task

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        client = self._redis()
        if client is not None:
            try:
                raw = client.get(self._key("task", task_id))
                if raw is not None:
                    task = json.loads(raw)
                    self._remember_task(task_id, task)
                    return task
            except Exception as exc:
                self._report_unavailable(exc)
        return self._fallback_task(task_id)

    def _save_task(self, task_id: str, task: dict[str, Any]) -> None:
        self._remember_task(task_id, task)
        client = self._redis()
        if client is None:
            return
        try:
            client.setex(
                self._key("task", task_id),
                get_settings().task_status_ttl_seconds,
                json.dumps(jsonable_encoder(task), ensure_ascii=False),
            )
        except Exception as exc:
            self._report_unavailable(exc)

    def _remember_task(self, task_id: str, task: dict[str, Any]) -> None:
        now = time.monotonic()
        ttl = get_settings().task_status_ttl_seconds
        with self._lock:
            self._prune_task_fallback(now)
            self._task_fallback.pop(task_id, None)
            self._task_fallback[task_id] = dict(task)
            self._task_fallback_expiry[task_id] = now + ttl
            while len(self._task_fallback) > 1000:
                oldest = next(iter(self._task_fallback))
                self._task_fallback.pop(oldest, None)
                self._task_fallback_expiry.pop(oldest, None)

    def _fallback_task(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            self._prune_task_fallback(time.monotonic())
            task = self._task_fallback.get(task_id)
            return dict(task) if task is not None else None

    def _prune_task_fallback(self, now: float) -> None:
        expired = [
            task_id
            for task_id, expires_at in self._task_fallback_expiry.items()
            if expires_at <= now
        ]
        for task_id in expired:
            self._task_fallback.pop(task_id, None)
            self._task_fallback_expiry.pop(task_id, None)

    @staticmethod
    def _now() -> str:
        return datetime.now(tz=timezone.utc).isoformat()

    def reset_for_tests(self) -> None:
        """Clear process-local state; production code should not call this."""
        with self._lock:
            self._task_fallback.clear()
            self._task_fallback_expiry.clear()
        self._client = None
        self._client_url = None
        self._warned = False


redis_store = RedisStore()
