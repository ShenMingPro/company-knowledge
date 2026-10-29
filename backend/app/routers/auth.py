from datetime import datetime, timezone
from math import ceil

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from backend.app.dependencies import (
    get_current_token_payload,
    get_current_user,
    get_db,
    require_roles,
)
from backend.app.config import get_settings
from backend.app.rate_limit import client_identity, enforce_rate_limit
from backend.app.redis_client import redis_store
from backend.app.schemas import LoginRequest, TokenResponse, UserCreate, UserResponse
from backend.app.security import create_access_token
from models.database import User
from services.auth_service import AuthService


router = APIRouter(prefix="/auth", tags=["auth"])
_DUMMY_PASSWORD_HASH = "$2b$12$C6UzMDM.H6dfI/f/IKcEe.2u22sYH7kLteCArr9H/SkOQWqYS"


async def _authenticate(username: str, password: str, session: Session) -> User:
    user = session.query(User).filter(User.username == username).first()
    password_matches = await run_in_threadpool(
        AuthService.verify_password,
        password,
        user.password_hash if user else _DUMMY_PASSWORD_HASH,
    )
    valid = bool(user) and password_matches
    if not valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_enabled:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="账号已被禁用")
    return user


def _issue_token(user: User) -> TokenResponse:
    token, expires_in = create_access_token(user_id=user.id)
    return TokenResponse(
        access_token=token,
        expires_in=expires_in,
        user=UserResponse.model_validate(user),
    )


async def _enforce_login_limits(request: Request, username: str) -> None:
    settings = get_settings()
    normalized_username = username.strip().lower()
    for scope, identity, limit in (
        ("login-ip", client_identity(request), settings.rate_limit_login_ip_requests),
        ("login-account", normalized_username, settings.rate_limit_login_requests),
    ):
        await run_in_threadpool(
            enforce_rate_limit,
            scope=scope,
            identity=identity,
            limit=limit,
            window_seconds=settings.rate_limit_window_seconds,
        )


@router.post("/login", response_model=TokenResponse)
async def login(
    request: Request,
    payload: LoginRequest,
    session: Session = Depends(get_db),
) -> TokenResponse:
    await _enforce_login_limits(request, payload.username)
    user = await _authenticate(payload.username, payload.password, session)
    return _issue_token(user)


@router.post("/token", response_model=TokenResponse, include_in_schema=False)
async def oauth_token(
    request: Request,
    form: OAuth2PasswordRequestForm = Depends(),
    session: Session = Depends(get_db),
) -> TokenResponse:
    """OAuth2 form variant used by the interactive OpenAPI authorization dialog."""
    await _enforce_login_limits(request, form.username)
    user = await _authenticate(form.username, form.password, session)
    return _issue_token(user)


@router.get("/me", response_model=UserResponse)
def current_user(user: User = Depends(get_current_user)) -> User:
    return user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    payload: dict = Depends(get_current_token_payload),
    _: User = Depends(get_current_user),
) -> None:
    remaining_seconds = ceil(float(payload["exp"]) - datetime.now(timezone.utc).timestamp())
    if remaining_seconds <= 0 or not redis_store.revoke_token(payload["jti"], remaining_seconds):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="注销服务暂时不可用",
        )


@router.post("/users", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: UserCreate,
    _: User = Depends(require_roles("admin")),
    session: Session = Depends(get_db),
) -> User:
    if session.query(User.id).filter(User.username == payload.username).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="用户名已存在")
    user = User(
        username=payload.username,
        password_hash=await run_in_threadpool(AuthService.hash_password, payload.password),
        role=payload.role,
    )
    session.add(user)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="用户名已存在") from exc
    session.refresh(user)
    return user
