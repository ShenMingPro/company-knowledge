"""Environment-backed configuration for the HTTP application."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

from dotenv import load_dotenv


load_dotenv()


def _csv(name: str, default: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in os.getenv(name, default).split(",") if item.strip())


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    app_name: str
    api_prefix: str
    jwt_secret_key: str
    jwt_algorithm: str
    jwt_issuer: str
    jwt_audience: str
    access_token_expire_minutes: int
    cors_origins: tuple[str, ...]
    upload_max_bytes: int
    allowed_document_extensions: tuple[str, ...]
    redis_enabled: bool
    redis_url: str
    redis_timeout_seconds: float
    cache_ttl_seconds: int
    task_status_ttl_seconds: int
    rate_limit_window_seconds: int
    rate_limit_login_ip_requests: int
    rate_limit_login_requests: int
    rate_limit_query_requests: int
    rate_limit_upload_requests: int


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        app_name=os.getenv("APP_NAME", "KnowBase API"),
        api_prefix=os.getenv("API_PREFIX", "/api"),
        jwt_secret_key=os.getenv("JWT_SECRET_KEY", ""),
        jwt_algorithm=os.getenv("JWT_ALGORITHM", "HS256"),
        jwt_issuer=os.getenv("JWT_ISSUER", "knowbase-api"),
        jwt_audience=os.getenv("JWT_AUDIENCE", "knowbase-web"),
        access_token_expire_minutes=int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "120")),
        cors_origins=_csv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"),
        upload_max_bytes=int(os.getenv("UPLOAD_MAX_BYTES", str(20 * 1024 * 1024))),
        allowed_document_extensions=_csv("ALLOWED_DOCUMENT_EXTENSIONS", ".pdf,.docx,.txt,.xlsx"),
        redis_enabled=_bool("REDIS_ENABLED", False),
        redis_url=os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"),
        redis_timeout_seconds=float(os.getenv("REDIS_TIMEOUT_SECONDS", "0.5")),
        cache_ttl_seconds=max(1, int(os.getenv("CACHE_TTL_SECONDS", "60"))),
        task_status_ttl_seconds=max(60, int(os.getenv("TASK_STATUS_TTL_SECONDS", "86400"))),
        rate_limit_window_seconds=max(1, int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60"))),
        rate_limit_login_ip_requests=max(0, int(os.getenv("RATE_LIMIT_LOGIN_IP_REQUESTS", "60"))),
        rate_limit_login_requests=max(0, int(os.getenv("RATE_LIMIT_LOGIN_REQUESTS", "10"))),
        rate_limit_query_requests=max(0, int(os.getenv("RATE_LIMIT_QUERY_REQUESTS", "30"))),
        rate_limit_upload_requests=max(0, int(os.getenv("RATE_LIMIT_UPLOAD_REQUESTS", "10"))),
    )
