"""FastAPI dependencies shared by route modules."""

from __future__ import annotations

from collections.abc import Callable, Generator
from typing import Any

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from backend.app.config import get_settings
from backend.app.redis_client import RedisUnavailableError, redis_store
from backend.app.security import TokenError, decode_access_token
from models.database import User, get_session


oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{get_settings().api_prefix}/auth/token")


def _credentials_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="登录凭证无效或已过期",
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_db() -> Generator[Session, None, None]:
    session = get_session()
    try:
        yield session
    finally:
        session.close()


def get_current_token_payload(
    token: str = Depends(oauth2_scheme),
) -> dict[str, Any]:
    try:
        payload = decode_access_token(token)
    except TokenError as exc:
        raise _credentials_error() from exc
    try:
        revoked = redis_store.is_token_revoked(payload["jti"])
    except RedisUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="认证状态服务暂时不可用",
        ) from exc
    if revoked:
        raise _credentials_error()
    return payload


def get_current_user(
    payload: dict[str, Any] = Depends(get_current_token_payload),
    session: Session = Depends(get_db),
) -> User:
    user_id = int(payload["sub"])

    user = session.query(User).filter(User.id == user_id).first()
    if user is None or not user.is_enabled:
        raise _credentials_error()
    return user


def require_roles(*roles: str) -> Callable[..., User]:
    allowed = frozenset(roles)

    def dependency(user: User = Depends(get_current_user)) -> User:
        if user.role not in allowed:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="权限不足")
        return user

    return dependency
