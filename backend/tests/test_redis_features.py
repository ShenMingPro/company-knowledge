from __future__ import annotations

from fnmatch import fnmatch
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app import rate_limit as rate_limit_module
from backend.app import redis_client as redis_module
from backend.app.redis_client import RedisStore, RedisUnavailableError
from backend.app.routers import spaces as spaces_routes
from backend.app.routers import tasks as task_routes
from backend.app.schemas import SpaceCreate
from models.database import Base, KnowledgeSpace, User


class FakePipeline:
    def __init__(self, redis: "FakeRedis") -> None:
        self.redis = redis
        self.operations: list[tuple[str, str, int | None]] = []

    def incr(self, key: str) -> "FakePipeline":
        self.operations.append(("incr", key, None))
        return self

    def expire(self, key: str, ttl: int) -> "FakePipeline":
        self.operations.append(("expire", key, ttl))
        return self

    def execute(self) -> list[object]:
        results: list[object] = []
        for operation, key, ttl in self.operations:
            if operation == "incr":
                value = int(self.redis.values.get(key, "0")) + 1
                self.redis.values[key] = str(value)
                results.append(value)
            else:
                results.append(bool(ttl))
        return results


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def setex(self, key: str, _ttl: int, value: str) -> bool:
        self.values[key] = value
        return True

    def exists(self, key: str) -> int:
        return int(key in self.values)

    def scan_iter(self, match: str, count: int = 100):
        del count
        return iter([key for key in self.values if fnmatch(key, match)])

    def delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            removed += int(self.values.pop(key, None) is not None)
        return removed

    def pipeline(self, transaction: bool = True) -> FakePipeline:
        del transaction
        return FakePipeline(self)

    def ping(self) -> bool:
        return True


@pytest.fixture()
def redis_store(monkeypatch) -> RedisStore:
    settings = SimpleNamespace(
        redis_enabled=True,
        redis_url="redis://fake/0",
        redis_timeout_seconds=0.1,
        cache_ttl_seconds=60,
        task_status_ttl_seconds=3600,
    )
    monkeypatch.setattr(redis_module, "get_settings", lambda: settings)
    store = RedisStore()
    store._client = FakeRedis()
    store._client_url = settings.redis_url
    return store


def test_redis_store_cache_rate_limit_task_and_revocation(redis_store: RedisStore):
    assert redis_store.set_json("documents:list:1", {"items": [1]})
    assert redis_store.get_json("documents:list:1") == {"items": [1]}
    assert redis_store.delete_cache_prefix("documents:") == 1
    assert redis_store.get_json("documents:list:1") is None

    assert redis_store.rate_limit("query", "user-1", 2, 60)[0]
    assert redis_store.rate_limit("query", "user-1", 2, 60)[0]
    allowed, retry_after = redis_store.rate_limit("query", "user-1", 2, 60)
    assert not allowed
    assert retry_after > 0

    task = redis_store.create_task("task-1", owner_user_id=7, document_id=3)
    assert task["status"] == "queued"
    redis_store.update_task("task-1", "processing", progress=25, message="正在处理")
    assert redis_store.get_task("task-1")["status"] == "processing"
    assert redis_store.get_task("task-1")["progress"] == 25
    redis_store.update_task("task-1", "succeeded")
    assert redis_store.get_task("task-1")["owner_user_id"] == 7

    assert redis_store.revoke_token("jti-1", 60)
    assert redis_store.is_token_revoked("jti-1")
    assert redis_store.health_status() == "ok"


def test_rate_limit_error_has_retry_after(monkeypatch):
    monkeypatch.setattr(
        rate_limit_module.redis_store,
        "rate_limit",
        lambda *_args, **_kwargs: (False, 17),
    )
    with pytest.raises(HTTPException) as exc_info:
        rate_limit_module.enforce_rate_limit(
            scope="login",
            identity="127.0.0.1:user",
            limit=1,
            window_seconds=60,
        )
    assert exc_info.value.status_code == 429
    assert exc_info.value.headers == {"Retry-After": "17"}


def test_client_identity_ignores_untrusted_forwarding_headers():
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/auth/login",
            "headers": [(b"x-forwarded-for", b"spoofed, 203.0.113.8")],
            "client": ("172.20.0.4", 12345),
        }
    )
    assert rate_limit_module.client_identity(request) == "172.20.0.4"


def test_rate_limit_returns_503_when_redis_security_control_is_unavailable(monkeypatch):
    monkeypatch.setattr(
        rate_limit_module.redis_store,
        "rate_limit",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RedisUnavailableError("offline")),
    )
    with pytest.raises(HTTPException) as exc_info:
        rate_limit_module.enforce_rate_limit(
            scope="login-ip",
            identity="127.0.0.1",
            limit=1,
            window_seconds=60,
        )
    assert exc_info.value.status_code == 503


def test_task_status_is_visible_only_to_owner_or_admin(monkeypatch):
    task = {"task_id": "task-private", "owner_user_id": 7, "status": "queued"}
    monkeypatch.setattr(task_routes.redis_store, "get_task", lambda _task_id: task)
    owner = User(id=7, username="owner", password_hash="x", role="employee", is_enabled=True)
    admin = User(id=8, username="admin", password_hash="x", role="admin", is_enabled=True)
    stranger = User(id=9, username="stranger", password_hash="x", role="employee", is_enabled=True)

    assert task_routes.task_status("task-private", owner) == task
    assert task_routes.task_status("task-private", admin) == task
    with pytest.raises(HTTPException) as exc_info:
        task_routes.task_status("task-private", stranger)
    assert exc_info.value.status_code == 404


def test_space_write_invalidates_space_and_document_caches(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    admin = User(id=1, username="admin", password_hash="unused", role="admin", is_enabled=True)
    session.add(
        KnowledgeSpace(
            name="公共空间",
            code="public-cache-test",
            allowed_roles="employee,admin",
        )
    )
    session.commit()

    cache: dict[str, object] = {}
    invalidated: list[str] = []
    monkeypatch.setattr(spaces_routes.redis_store, "get_json", cache.get)
    monkeypatch.setattr(
        spaces_routes.redis_store,
        "set_json",
        lambda key, value: cache.__setitem__(key, value) or True,
    )

    def delete_prefix(prefix: str) -> int:
        invalidated.append(prefix)
        keys = [key for key in cache if key.startswith(prefix)]
        for key in keys:
            cache.pop(key)
        return len(keys)

    monkeypatch.setattr(spaces_routes.redis_store, "delete_cache_prefix", delete_prefix)
    first = spaces_routes.list_spaces(False, admin, session)
    assert first[0]["code"] == "public-cache-test"
    assert cache

    spaces_routes.create_space(
        SpaceCreate(name="新增空间", code="new-cache-test", allowed_roles=["admin"]),
        admin,
        session,
    )
    assert invalidated == ["spaces:", "documents:"]
    assert cache == {}
    session.close()
    engine.dispose()
