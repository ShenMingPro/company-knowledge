from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

import jwt
import pytest
from fastapi.testclient import TestClient

from backend.app.adapters.rag import RagUnavailableError, rag_adapter
from backend.app.config import get_settings
from backend.app.main import app
from backend.app.redis_client import RedisUnavailableError, redis_store
from backend.app.routers import auth as auth_routes
from backend.app.routers import documents as document_routes
from backend.app.routers import health as health_routes
from backend.app.security import TokenError, create_access_token
from models import database
from models.database import Base, KnowledgeSpace, User
from services.auth_service import AuthService
from services.document_service import DocumentService


@pytest.fixture()
def client(tmp_path, monkeypatch):
    database_url = f"sqlite:///{(tmp_path / 'api.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("REDIS_ENABLED", "false")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-only-jwt-secret-with-at-least-32-bytes")
    get_settings.cache_clear()
    redis_store.reset_for_tests()
    engine = database.get_engine()
    Base.metadata.create_all(engine)
    session = database.get_session()
    try:
        session.add_all(
            [
                User(
                    username="employee_api",
                    password_hash=AuthService.hash_password("employee-pass"),
                    role="employee",
                ),
                User(
                    username="admin_api",
                    password_hash=AuthService.hash_password("admin-pass"),
                    role="admin",
                ),
                KnowledgeSpace(
                    name="公共空间",
                    code="public-api",
                    description="test",
                    allowed_roles="employee,admin",
                ),
                KnowledgeSpace(
                    name="管理空间",
                    code="admin-api",
                    description="test",
                    allowed_roles="admin",
                ),
            ]
        )
        session.commit()
    finally:
        session.close()

    with TestClient(app) as test_client:
        yield test_client
    engine.dispose()
    get_settings.cache_clear()
    redis_store.reset_for_tests()


def _login(client: TestClient, username: str, password: str) -> dict:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200
    return response.json()


def test_health_does_not_require_model_configuration(client: TestClient):
    assert client.get("/api/health").json() == {"status": "ok"}
    assert client.get("/api/health/ready").json() == {
        "status": "ready",
        "database": "ok",
        "redis": "disabled",
    }


def test_readiness_reports_redis_security_dependency_failure(client: TestClient, monkeypatch):
    monkeypatch.setattr(health_routes.redis_store, "health_status", lambda: "degraded")

    response = client.get("/api/health/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "not_ready", "database": "ok", "redis": "degraded"}


def test_login_and_current_user(client: TestClient):
    login = _login(client, "employee_api", "employee-pass")
    response = client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {login['access_token']}"},
    )
    assert response.status_code == 200
    assert response.json()["username"] == "employee_api"
    assert response.json()["role"] == "employee"

    oauth_login = client.post(
        "/api/auth/token",
        data={"username": "employee_api", "password": "employee-pass"},
    )
    assert oauth_login.status_code == 200
    assert oauth_login.json()["token_type"] == "bearer"


def test_login_uses_independent_ip_and_account_rate_limits(client: TestClient, monkeypatch):
    calls: list[tuple[str, str]] = []

    def capture_limit(*, scope: str, identity: str, **_kwargs) -> None:
        calls.append((scope, identity))

    monkeypatch.setattr(auth_routes, "enforce_rate_limit", capture_limit)
    _login(client, "employee_api", "employee-pass")

    assert [scope for scope, _ in calls] == ["login-ip", "login-account"]
    assert calls[1][1] == "employee_api"


def test_access_token_contains_identity_not_mutable_authorization_claims(client: TestClient):
    login = _login(client, "employee_api", "employee-pass")
    payload = jwt.decode(login["access_token"], options={"verify_signature": False})

    assert payload["sub"].isdigit()
    assert payload["type"] == "access"
    assert payload["iss"] == get_settings().jwt_issuer
    assert payload["aud"] == get_settings().jwt_audience
    assert payload["jti"]
    assert "username" not in payload
    assert "role" not in payload


def test_protected_route_rejects_missing_tampered_and_expired_tokens(client: TestClient):
    missing = client.get("/api/auth/me")
    assert missing.status_code == 401
    assert missing.headers["www-authenticate"] == "Bearer"

    login = _login(client, "employee_api", "employee-pass")
    header, body, signature = login["access_token"].split(".")
    replacement = "A" if signature[0] != "A" else "B"
    tampered = f"{header}.{body}.{replacement}{signature[1:]}"
    invalid = client.get("/api/auth/me", headers={"Authorization": f"Bearer {tampered}"})
    assert invalid.status_code == 401
    assert invalid.headers["www-authenticate"] == "Bearer"

    settings = get_settings()
    now = datetime.now(timezone.utc)
    expired_token = jwt.encode(
        {
            "sub": "1",
            "type": "access",
            "iss": settings.jwt_issuer,
            "aud": settings.jwt_audience,
            "jti": "expired-test-token",
            "iat": now - timedelta(minutes=2),
            "nbf": now - timedelta(minutes=2),
            "exp": now - timedelta(minutes=1),
        },
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )
    expired = client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {expired_token}"},
    )
    assert expired.status_code == 401
    assert expired.headers["www-authenticate"] == "Bearer"


def test_insecure_default_jwt_secret_is_rejected(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "replace_with_a_long_random_secret")
    get_settings.cache_clear()
    try:
        with pytest.raises(TokenError, match="不安全"):
            create_access_token(user_id=1)
    finally:
        get_settings.cache_clear()


def test_rbac_uses_live_database_role_and_enabled_state(client: TestClient):
    login = _login(client, "employee_api", "employee-pass")
    headers = {"Authorization": f"Bearer {login['access_token']}"}
    create_payload = {
        "name": "实时角色测试",
        "code": "live-role-test",
        "allowed_roles": ["employee"],
    }

    assert client.post("/api/spaces", headers=headers, json=create_payload).status_code == 403

    session = database.get_session()
    try:
        user = session.query(User).filter(User.username == "employee_api").one()
        user.role = "admin"
        session.commit()
    finally:
        session.close()

    promoted = client.post("/api/spaces", headers=headers, json=create_payload)
    assert promoted.status_code == 201

    session = database.get_session()
    try:
        user = session.query(User).filter(User.username == "employee_api").one()
        user.role = "employee"
        session.commit()
    finally:
        session.close()
    assert client.patch("/api/spaces/1", headers=headers, json={"name": "拒绝"}).status_code == 403

    session = database.get_session()
    try:
        user = session.query(User).filter(User.username == "employee_api").one()
        user.is_enabled = False
        session.commit()
    finally:
        session.close()
    disabled = client.get("/api/auth/me", headers=headers)
    assert disabled.status_code == 401
    assert disabled.headers["www-authenticate"] == "Bearer"


def test_logout_revokes_access_token(client: TestClient, monkeypatch):
    revoked_tokens: set[str] = set()
    monkeypatch.setattr(
        redis_store,
        "revoke_token",
        lambda jti, ttl_seconds: bool(ttl_seconds > 0 and not revoked_tokens.add(jti)),
    )
    monkeypatch.setattr(redis_store, "is_token_revoked", lambda jti: jti in revoked_tokens)

    login = _login(client, "employee_api", "employee-pass")
    headers = {"Authorization": f"Bearer {login['access_token']}"}
    logout = client.post("/api/auth/logout", headers=headers)

    assert logout.status_code == 204
    rejected = client.get("/api/auth/me", headers=headers)
    assert rejected.status_code == 401
    assert rejected.headers["www-authenticate"] == "Bearer"


def test_token_revocation_check_fails_closed_when_redis_is_unavailable(client: TestClient, monkeypatch):
    login = _login(client, "employee_api", "employee-pass")
    headers = {"Authorization": f"Bearer {login['access_token']}"}
    monkeypatch.setattr(
        redis_store,
        "is_token_revoked",
        lambda _jti: (_ for _ in ()).throw(RedisUnavailableError("offline")),
    )

    response = client.get("/api/auth/me", headers=headers)

    assert response.status_code == 503
    assert response.json()["detail"] == "认证状态服务暂时不可用"


def test_spaces_are_permission_filtered_and_admin_mutations_are_protected(client: TestClient):
    employee = _login(client, "employee_api", "employee-pass")
    employee_headers = {"Authorization": f"Bearer {employee['access_token']}"}
    spaces = client.get("/api/spaces", headers=employee_headers)
    assert spaces.status_code == 200
    assert [item["code"] for item in spaces.json()] == ["public-api"]

    forbidden = client.post(
        "/api/spaces",
        headers=employee_headers,
        json={"name": "拒绝", "code": "denied", "allowed_roles": ["employee"]},
    )
    assert forbidden.status_code == 403

    admin = _login(client, "admin_api", "admin-pass")
    created = client.post(
        "/api/spaces",
        headers={"Authorization": f"Bearer {admin['access_token']}"},
        json={
            "name": "新空间",
            "code": "new-space",
            "description": "created by API",
            "allowed_roles": ["service", "admin"],
        },
    )
    assert created.status_code == 201
    assert created.json()["allowed_roles"] == ["service", "admin"]


def test_versioned_compatibility_health_path(client: TestClient):
    assert client.get("/api/v1/health").status_code == 200


def test_chat_query_persists_session_message_and_feedback(client: TestClient, monkeypatch):
    received_history = []

    def fake_answer(**kwargs):
        received_history.append(kwargs["history"])
        return {
            "answer": "测试答案",
            "has_answer": True,
            "references": [{"source_name": "manual.txt", "document_version_id": 1}],
            "retrieved_count": 1,
            "filtered_count": 1,
            "valid_count": 1,
            "retrieval_time_ms": 4,
            "generation_time_ms": 6,
            "total_time_ms": 10,
        }

    monkeypatch.setattr(
        rag_adapter,
        "answer",
        fake_answer,
    )
    login = _login(client, "employee_api", "employee-pass")
    headers = {"Authorization": f"Bearer {login['access_token']}"}

    query = client.post("/api/chat/query", headers=headers, json={"query": "测试问题"})
    assert query.status_code == 200
    body = query.json()
    assert body["answer"] == "测试答案"
    assert body["metrics"]["total_time_ms"] == 10
    assert received_history == [None]

    follow_up = client.post(
        "/api/chat/query",
        headers=headers,
        json={"query": "继续追问", "session_id": body["session_id"]},
    )
    assert follow_up.status_code == 200
    assert received_history[1] == [
        {"role": "user", "content": "测试问题"},
        {"role": "assistant", "content": "测试答案"},
    ]

    sessions = client.get("/api/chat/sessions", headers=headers)
    assert sessions.status_code == 200
    assert sessions.json()[0]["message_count"] == 2

    messages = client.get(
        f"/api/chat/sessions/{body['session_id']}/messages",
        headers=headers,
    )
    assert messages.status_code == 200
    assert messages.json()[0]["system_answer"] == "测试答案"

    feedback = client.put(
        f"/api/chat/messages/{body['message_id']}/feedback",
        headers=headers,
        json={"feedback_type": "thumbs_up"},
    )
    assert feedback.status_code == 200

    admin = _login(client, "admin_api", "admin-pass")
    not_owned = client.post(
        "/api/chat/query",
        headers={"Authorization": f"Bearer {admin['access_token']}"},
        json={"query": "越权查询", "session_id": body["session_id"]},
    )
    assert not_owned.status_code == 404
    assert len(received_history) == 2


def _parse_sse(response, lines=None) -> list[tuple[str, dict]]:
    events = []
    event_name = None
    data_lines = []
    for line in lines if lines is not None else response.text.splitlines():
        if line.startswith("event: "):
            event_name = line.removeprefix("event: ")
        elif line.startswith("data: "):
            data_lines.append(line.removeprefix("data: "))
        elif not line and event_name is not None:
            events.append((event_name, json.loads("\n".join(data_lines))))
            event_name = None
            data_lines = []
    return events


def test_chat_query_streams_deltas_then_persists_complete_answer(client: TestClient, monkeypatch):
    received_history = []

    def fake_answer_stream(**kwargs):
        received_history.append(kwargs["history"])
        yield {"type": "delta", "delta": "测试"}
        yield {"type": "delta", "delta": "答案"}
        yield {
            "type": "done",
            "result": {
                "answer": "测试答案",
                "has_answer": True,
                "references": [{"source_name": "manual.txt", "document_version_id": 1}],
                "retrieved_count": 1,
                "filtered_count": 1,
                "valid_count": 1,
                "retrieval_time_ms": 4,
                "generation_time_ms": 6,
                "total_time_ms": 10,
            },
        }

    monkeypatch.setattr(rag_adapter, "answer_stream", fake_answer_stream)
    login = _login(client, "employee_api", "employee-pass")
    headers = {"Authorization": f"Bearer {login['access_token']}"}

    with client.stream(
        "POST",
        "/api/chat/query/stream",
        headers=headers,
        json={"query": "流式测试"},
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["cache-control"] == "no-cache"
        events = _parse_sse(response, response.iter_lines())

    assert [event for event, _ in events] == ["delta", "delta", "done"]
    assert "".join(data["delta"] for event, data in events if event == "delta") == "测试答案"
    completed = events[-1][1]
    assert completed["answer"] == "测试答案"
    assert completed["references"][0]["source_name"] == "manual.txt"
    assert completed["metrics"]["total_time_ms"] == 10
    assert received_history == [None]

    messages = client.get(
        f"/api/chat/sessions/{completed['session_id']}/messages",
        headers=headers,
    )
    assert messages.status_code == 200
    assert messages.json()[0]["system_answer"] == "测试答案"
    assert messages.json()[0]["references"][0]["source_name"] == "manual.txt"


def test_chat_query_stream_expresses_rag_failure_without_persisting(client: TestClient, monkeypatch):
    def fake_answer_stream(**_kwargs):
        yield {"type": "delta", "delta": "半截"}
        raise RagUnavailableError("模型服务暂时不可用")

    monkeypatch.setattr(rag_adapter, "answer_stream", fake_answer_stream)
    login = _login(client, "employee_api", "employee-pass")
    headers = {"Authorization": f"Bearer {login['access_token']}"}

    response = client.post(
        "/api/chat/query/stream",
        headers=headers,
        json={"query": "失败测试"},
    )

    assert response.status_code == 200
    events = _parse_sse(response)
    assert events == [
        ("delta", {"delta": "半截"}),
        ("error", {"code": "rag_unavailable", "detail": "模型服务暂时不可用"}),
    ]
    assert client.get("/api/chat/sessions", headers=headers).json() == []


def test_admin_xlsx_upload_returns_before_model_indexing(client: TestClient, monkeypatch, tmp_path):
    monkeypatch.setattr(
        DocumentService,
        "get_version_file_path",
        staticmethod(
            lambda document_id, version_number, filename: str(
                tmp_path / f"{document_id}_{version_number}_{filename}"
            )
        ),
    )
    monkeypatch.setattr(document_routes, "index_document", lambda **_: None)
    login = _login(client, "admin_api", "admin-pass")
    headers = {"Authorization": f"Bearer {login['access_token']}"}
    spaces = client.get("/api/spaces", headers=headers).json()
    space_id = next(item["id"] for item in spaces if item["code"] == "public-api")

    response = client.post(
        "/api/documents/upload",
        headers=headers,
        data={"space_id": str(space_id)},
        files={
            "file": (
                "guide.xlsx",
                b"mock xlsx content; indexing is stubbed in this endpoint test",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert response.status_code == 202
    assert response.json()["status"] == "processing"

    listed = client.get(f"/api/documents?space_id={space_id}", headers=headers)
    assert listed.status_code == 200
    assert listed.json()[0]["name"] == "guide.xlsx"
