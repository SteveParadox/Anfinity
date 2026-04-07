"""Audit Log API routes."""
from datetime import datetime
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditAction, EntityType, get_audit_logs, get_recent_activity
from app.core.auth import get_current_active_user, get_workspace_context
from app.database.models import User as DBUser
from app.database.session import get_db

router = APIRouter(prefix="/audit", tags=["Audit Logs"])


class AuditLogResponse(BaseModel):
    id: str
    action: str
    entity_type: Optional[str]
    entity_id: Optional[str]
    user_id: Optional[str]
    metadata: dict = Field(default_factory=dict)
    ip_address: Optional[str]
    created_at: str


class AuditLogListResponse(BaseModel):
    items: List[AuditLogResponse]
    total: int


def _parse_action(value: Optional[str]) -> Optional[AuditAction]:
    if not value:
        return None
    try:
        return AuditAction(value)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid action: {value}") from exc


def _parse_entity_type(value: Optional[str]) -> Optional[EntityType]:
    if not value:
        return None
    try:
        return EntityType(value)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid entity_type: {value}") from exc


@router.get("/workspace/{workspace_id}", response_model=AuditLogListResponse)
async def get_workspace_audit_logs(
    workspace_id: UUID,
    action: Optional[str] = None,
    entity_type: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    await get_workspace_context(workspace_id, current_user, db)
    logs = await get_audit_logs(
        db=db,
        workspace_id=workspace_id,
        action=_parse_action(action),
        entity_type=_parse_entity_type(entity_type),
        start_date=start_date,
        end_date=end_date,
        limit=limit,
        offset=offset,
    )
    total_logs = await get_audit_logs(
        db=db,
        workspace_id=workspace_id,
        action=_parse_action(action),
        entity_type=_parse_entity_type(entity_type),
        start_date=start_date,
        end_date=end_date,
        limit=10000,
        offset=0,
    )
    return AuditLogListResponse(
        items=[
            AuditLogResponse(
                id=str(log.id),
                action=log.action,
                entity_type=log.entity_type,
                entity_id=str(log.entity_id) if log.entity_id else None,
                user_id=str(log.user_id) if log.user_id else None,
                metadata=log.metadata or {},
                ip_address=log.ip_address,
                created_at=log.created_at.isoformat(),
            )
            for log in logs
        ],
        total=len(total_logs),
    )


@router.get("/workspace/{workspace_id}/recent", response_model=AuditLogListResponse)
async def get_recent_workspace_activity(
    workspace_id: UUID,
    limit: int = Query(20, ge=1, le=100),
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    await get_workspace_context(workspace_id, current_user, db)
    logs = await get_recent_activity(db, workspace_id, limit)
    return AuditLogListResponse(
        items=[
            AuditLogResponse(
                id=str(log.id),
                action=log.action,
                entity_type=log.entity_type,
                entity_id=str(log.entity_id) if log.entity_id else None,
                user_id=str(log.user_id) if log.user_id else None,
                metadata=log.metadata or {},
                ip_address=log.ip_address,
                created_at=log.created_at.isoformat(),
            )
            for log in logs
        ],
        total=len(logs),
    )


@router.get("/actions")
async def get_audit_actions(current_user: DBUser = Depends(get_current_active_user)):
    return {
        "actions": [action.value for action in AuditAction],
        "entity_types": [entity.value for entity in EntityType],
    }
