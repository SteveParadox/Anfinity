"""User notification API routes."""

from __future__ import annotations

from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.database.models import User as DBUser
from app.database.session import get_db
from app.services.note_comments import (
    list_user_notifications,
    mark_notification_read,
    serialize_notification,
)


router = APIRouter(prefix="/notifications", tags=["Notifications"])


class NotificationActorResponse(BaseModel):
    id: str
    email: str
    name: str


class UserNotificationResponse(BaseModel):
    id: str
    user_id: str
    actor_user_id: Optional[str] = None
    workspace_id: Optional[str] = None
    note_id: Optional[str] = None
    comment_id: Optional[str] = None
    notification_type: str
    payload: dict = Field(default_factory=dict)
    is_read: bool
    read_at: Optional[str] = None
    created_at: Optional[str] = None
    actor: Optional[NotificationActorResponse] = None


@router.get("", response_model=List[UserNotificationResponse])
async def get_notifications(
    limit: int = 50,
    current_user: DBUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    notifications = await list_user_notifications(db, user_id=current_user.id, limit=max(1, min(limit, 200)))
    return [UserNotificationResponse(**serialize_notification(item)) for item in notifications]


@router.post("/{notification_id}/read", response_model=UserNotificationResponse, status_code=status.HTTP_200_OK)
async def mark_notification_read_endpoint(
    notification_id: UUID,
    current_user: DBUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    notification = await mark_notification_read(db, notification_id=notification_id, user_id=current_user.id)
    return UserNotificationResponse(**serialize_notification(notification))
