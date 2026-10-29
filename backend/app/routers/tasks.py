"""Background task status API."""

from fastapi import APIRouter, Depends, HTTPException, status

from backend.app.dependencies import get_current_user
from backend.app.redis_client import redis_store
from models.database import User


router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("/{task_id}")
def task_status(task_id: str, user: User = Depends(get_current_user)) -> dict:
    task = redis_store.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在或状态已过期")
    if user.role != "admin" and task.get("owner_user_id") != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在或状态已过期")
    return task
