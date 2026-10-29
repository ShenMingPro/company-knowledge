"""Endpoint rate-limit helpers."""

from fastapi import HTTPException, Request, status

from backend.app.redis_client import RedisUnavailableError, redis_store


def client_identity(request: Request) -> str:
    # The API is also reachable directly in development, so client-controlled
    # forwarding headers are never trusted for a security control.
    return request.client.host if request.client else "unknown"


def enforce_rate_limit(
    *,
    scope: str,
    identity: str,
    limit: int,
    window_seconds: int,
) -> None:
    try:
        allowed, retry_after = redis_store.rate_limit(scope, identity, limit, window_seconds)
    except RedisUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="限流服务暂时不可用",
        ) from exc
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="请求过于频繁，请稍后重试",
            headers={"Retry-After": str(retry_after)},
        )
