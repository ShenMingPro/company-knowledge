from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from starlette.concurrency import run_in_threadpool

from backend.app.dependencies import require_roles
from models.database import User
from services.audit_service import AuditService


router = APIRouter(prefix="/audit", tags=["audits"])


@router.get("/logs")
async def list_audits(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    user_id: int | None = Query(default=None, ge=1),
    action_type: str | None = None,
    is_success: bool | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    user: User = Depends(require_roles("admin")),
) -> dict:
    filters = {
        "user_id": user_id,
        "action_type": action_type,
        "is_success": is_success,
        "start_time": start_time,
        "end_time": end_time,
    }
    return await run_in_threadpool(AuditService.list_logs, user.role, filters, page, page_size)


@router.get("/logs/{log_id}")
async def audit_detail(
    log_id: int,
    user: User = Depends(require_roles("admin")),
) -> dict:
    result = await run_in_threadpool(AuditService.get_log_detail, user.role, log_id)
    if not result.get("success"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=result.get("error", "日志不存在"))
    return result
