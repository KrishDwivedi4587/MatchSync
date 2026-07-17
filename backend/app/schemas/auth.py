"""Auth API schemas (public request/response DTOs)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.domain.value_objects.enums import UserStatus


class UserOut(BaseModel):
    """The authenticated user, safe to expose to the client."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    display_name: str | None
    timezone: str
    status: UserStatus
    created_at: datetime


class AuthStatusResponse(BaseModel):
    authenticated: bool
    user: UserOut | None = None


class MessageResponse(BaseModel):
    status: str
