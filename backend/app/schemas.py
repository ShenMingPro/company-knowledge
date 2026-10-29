"""Request and response schemas for the public HTTP interface."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


Role = Literal["employee", "service", "admin"]


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1, max_length=128)


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    role: Role
    is_enabled: bool
    created_at: datetime | None = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    user: UserResponse


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=8, max_length=128)
    role: Role


class SpaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    code: str = Field(pattern=r"^[a-zA-Z0-9_-]+$", min_length=1, max_length=50)
    description: str | None = Field(default=None, max_length=2000)
    allowed_roles: list[Role] = Field(min_length=1)

    @field_validator("allowed_roles")
    @classmethod
    def unique_roles(cls, roles: list[Role]) -> list[Role]:
        return list(dict.fromkeys(roles))


class SpaceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=2000)
    allowed_roles: list[Role] | None = Field(default=None, min_length=1)
    is_enabled: bool | None = None


class SessionCreate(BaseModel):
    title: str | None = Field(default=None, max_length=200)


class SessionTitleUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class AskRequest(BaseModel):
    query: str = Field(min_length=1, max_length=8000)
    session_id: int | None = Field(default=None, ge=1)
    history: list[dict[str, Any]] | None = None


class AskResponse(BaseModel):
    session_id: int
    message_id: int
    answer: str
    has_answer: bool
    references: list[dict[str, Any]]
    metrics: dict[str, Any]


class FeedbackUpsert(BaseModel):
    feedback_type: Literal["thumbs_up", "thumbs_down"]
    content: str | None = Field(default=None, max_length=500)


class RestoreRequest(BaseModel):
    version_id: int | None = Field(default=None, ge=1)


class DocumentUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class MessageResponse(BaseModel):
    message: str
