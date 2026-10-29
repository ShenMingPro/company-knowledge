"""Password and JWT primitives used by the HTTP authentication seam."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import jwt
from jwt import InvalidTokenError

from backend.app.config import get_settings


class TokenError(ValueError):
    """Raised when an access token is invalid or expired."""


_ALLOWED_JWT_ALGORITHMS = frozenset({"HS256", "HS384", "HS512"})
_INSECURE_JWT_SECRETS = frozenset(
    {
        "development-only-change-me-32-bytes-minimum",
        "replace_with_a_long_random_secret",
    }
)


def _jwt_settings():
    settings = get_settings()
    if settings.jwt_algorithm not in _ALLOWED_JWT_ALGORITHMS:
        raise TokenError("JWT 签名算法配置无效")
    if (
        len(settings.jwt_secret_key.encode("utf-8")) < 32
        or settings.jwt_secret_key in _INSECURE_JWT_SECRETS
    ):
        raise TokenError("JWT 签名密钥未配置或不安全")
    return settings


def create_access_token(*, user_id: int) -> tuple[str, int]:
    """Create a short-lived access token containing only identity metadata.

    Username, role and enabled state are intentionally not authorization claims.
    They are loaded from the database for every authenticated request.
    """
    if user_id <= 0:
        raise ValueError("用户标识必须为正整数")

    settings = _jwt_settings()
    now = datetime.now(timezone.utc)
    expires = now + timedelta(minutes=settings.access_token_expire_minutes)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "type": "access",
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "jti": str(uuid4()),
        "iat": now,
        "nbf": now,
        "exp": expires,
    }
    token = jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    return token, int((expires - now).total_seconds())


def decode_access_token(token: str) -> dict[str, Any]:
    settings = _jwt_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            options={
                "require": ["sub", "type", "iss", "aud", "jti", "iat", "nbf", "exp"],
            },
        )
    except InvalidTokenError as exc:
        raise TokenError("登录凭证无效或已过期") from exc

    if payload.get("type") != "access":
        raise TokenError("登录凭证类型无效")
    try:
        user_id = int(payload["sub"])
    except (KeyError, TypeError, ValueError) as exc:
        raise TokenError("登录凭证缺少有效用户标识") from exc
    if user_id <= 0 or not isinstance(payload.get("jti"), str) or not payload["jti"]:
        raise TokenError("登录凭证缺少有效用户标识")
    return payload
