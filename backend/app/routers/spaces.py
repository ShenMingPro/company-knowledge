from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.dependencies import get_current_user, get_db, require_roles
from backend.app.redis_client import redis_store
from backend.app.schemas import SpaceCreate, SpaceUpdate
from models.database import KnowledgeSpace, User


router = APIRouter(prefix="/spaces", tags=["knowledge-spaces"])


def _serialize(space: KnowledgeSpace) -> dict:
    return {
        "id": space.id,
        "name": space.name,
        "code": space.code,
        "description": space.description,
        "allowed_roles": [role.strip() for role in space.allowed_roles.split(",") if role.strip()],
        "is_enabled": space.is_enabled,
        "created_at": space.created_at,
    }


@router.get("")
def list_spaces(
    include_disabled: bool = False,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> list[dict]:
    cache_key = f"spaces:list:{user.id}:{user.role}:{str(include_disabled).lower()}"
    cached = redis_store.get_json(cache_key)
    if cached is not None:
        return cached
    query = session.query(KnowledgeSpace)
    if not include_disabled or user.role != "admin":
        query = query.filter(KnowledgeSpace.is_enabled.is_(True))
    spaces = query.order_by(KnowledgeSpace.name).all()
    result = [
        _serialize(space)
        for space in spaces
        if user.role in {role.strip() for role in space.allowed_roles.split(",")}
    ]
    redis_store.set_json(cache_key, result)
    return result


@router.post("", status_code=status.HTTP_201_CREATED)
def create_space(
    payload: SpaceCreate,
    _: User = Depends(require_roles("admin")),
    session: Session = Depends(get_db),
) -> dict:
    if session.query(KnowledgeSpace).filter(KnowledgeSpace.code == payload.code).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="知识空间编码已存在")
    space = KnowledgeSpace(
        name=payload.name,
        code=payload.code,
        description=payload.description,
        allowed_roles=",".join(payload.allowed_roles),
    )
    session.add(space)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="知识空间编码已存在") from exc
    session.refresh(space)
    redis_store.delete_cache_prefix("spaces:")
    redis_store.delete_cache_prefix("documents:")
    return _serialize(space)


@router.patch("/{space_id}")
def update_space(
    space_id: int,
    payload: SpaceUpdate,
    _: User = Depends(require_roles("admin")),
    session: Session = Depends(get_db),
) -> dict:
    space = session.query(KnowledgeSpace).filter(KnowledgeSpace.id == space_id).first()
    if space is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="知识空间不存在")
    changes = payload.model_dump(exclude_unset=True)
    roles = changes.pop("allowed_roles", None)
    for key, value in changes.items():
        setattr(space, key, value)
    if roles is not None:
        space.allowed_roles = ",".join(dict.fromkeys(roles))
    session.commit()
    session.refresh(space)
    redis_store.delete_cache_prefix("spaces:")
    redis_store.delete_cache_prefix("documents:")
    return _serialize(space)
