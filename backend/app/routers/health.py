from fastapi import APIRouter, HTTPException, status
from fastapi.responses import JSONResponse
from sqlalchemy import text

from backend.app.redis_client import redis_store
from models.database import get_session


router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
def liveness() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready", response_model=None)
def readiness():
    session = get_session()
    try:
        session.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="数据库未就绪",
        ) from exc
    finally:
        session.close()
    redis_status = redis_store.health_status()
    if redis_status == "degraded":
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "not_ready", "database": "ok", "redis": redis_status},
        )
    return {"status": "ready", "database": "ok", "redis": redis_status}
