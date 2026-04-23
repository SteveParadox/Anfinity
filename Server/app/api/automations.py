"""Automation configuration and internal execution API."""

from __future__ import annotations

import secrets
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.auth import get_current_user
from app.core.permissions import ensure_workspace_permission
from app.database.models import Automation, User as DBUser, Workspace, WorkspaceSection
from app.database.session import get_db
from app.services.automation_registry import automation_registry_payload
from app.services.automations import (
    execute_backend_action,
    serialize_automation,
    validate_automation_config,
)


router = APIRouter(prefix="/automations", tags=["Automations"])


class AutomationActionPayload(BaseModel):
    id: Optional[str] = Field(default=None, max_length=120)
    type: str
    config: Dict[str, Any] = Field(default_factory=dict)


class AutomationCreateRequest(BaseModel):
    workspace_id: UUID
    name: str = Field(..., min_length=1, max_length=255)
    trigger_type: str
    conditions: List[Dict[str, Any]] = Field(default_factory=list)
    actions: List[AutomationActionPayload] = Field(..., min_length=1)
    enabled: bool = True


class AutomationUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    trigger_type: Optional[str] = None
    conditions: Optional[List[Dict[str, Any]]] = None
    actions: Optional[List[AutomationActionPayload]] = None
    enabled: Optional[bool] = None


class AutomationResponse(BaseModel):
    id: str
    workspace_id: str
    name: str
    trigger_type: str
    conditions: List[Dict[str, Any]]
    actions: List[Dict[str, Any]]
    enabled: bool
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class InternalEnabledAutomationsResponse(BaseModel):
    automations: List[AutomationResponse]


class InternalWorkspaceResolutionResponse(BaseModel):
    id: str
    slug: str
    name: Optional[str] = None


class InternalActionRequest(BaseModel):
    action_type: str
    config: Dict[str, Any] = Field(default_factory=dict)
    context: Dict[str, Any] = Field(default_factory=dict)
    automation_id: str
    action_id: Optional[str] = None


def _require_internal_token(x_automation_internal_token: Optional[str] = Header(default=None)) -> None:
    expected = settings.AUTOMATION_INTERNAL_TOKEN
    if not expected:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Automation internal token is not configured")
    if not x_automation_internal_token or not secrets.compare_digest(x_automation_internal_token, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid automation internal token")


def _actions_to_json(actions: List[AutomationActionPayload]) -> List[Dict[str, Any]]:
    return [action.model_dump(exclude_none=True) for action in actions]


@router.get("", response_model=List[AutomationResponse])
async def list_automations(
    workspace_id: UUID,
    current_user: DBUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await ensure_workspace_permission(workspace_id, current_user, db, WorkspaceSection.WORKFLOWS, "view")
    rows = await db.execute(
        select(Automation)
        .where(Automation.workspace_id == workspace_id)
        .order_by(Automation.updated_at.desc(), Automation.created_at.desc())
    )
    return [AutomationResponse(**serialize_automation(automation)) for automation in rows.scalars().all()]


@router.post("", response_model=AutomationResponse, status_code=status.HTTP_201_CREATED)
async def create_automation(
    payload: AutomationCreateRequest,
    current_user: DBUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await ensure_workspace_permission(payload.workspace_id, current_user, db, WorkspaceSection.WORKFLOWS, "create")
    actions = _actions_to_json(payload.actions)
    validate_automation_config(trigger_type=payload.trigger_type, conditions=payload.conditions, actions=actions)

    automation = Automation(
        workspace_id=payload.workspace_id,
        created_by_user_id=current_user.id,
        name=payload.name,
        trigger_type=payload.trigger_type,
        conditions=payload.conditions,
        actions=actions,
        enabled=payload.enabled,
    )
    db.add(automation)
    await db.flush()
    await db.refresh(automation)
    return AutomationResponse(**serialize_automation(automation))


@router.get("/registry")
async def get_automation_registry():
    return automation_registry_payload()


@router.patch("/{automation_id}", response_model=AutomationResponse)
async def update_automation(
    automation_id: UUID,
    payload: AutomationUpdateRequest,
    current_user: DBUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    automation = (await db.execute(select(Automation).where(Automation.id == automation_id))).scalar_one_or_none()
    if automation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Automation not found")

    await ensure_workspace_permission(automation.workspace_id, current_user, db, WorkspaceSection.WORKFLOWS, "update")

    next_trigger_type = payload.trigger_type if payload.trigger_type is not None else automation.trigger_type
    next_conditions = payload.conditions if payload.conditions is not None else automation.conditions
    next_actions = _actions_to_json(payload.actions) if payload.actions is not None else automation.actions
    validate_automation_config(trigger_type=next_trigger_type, conditions=next_conditions, actions=next_actions)

    if payload.name is not None:
        automation.name = payload.name
    if payload.trigger_type is not None:
        automation.trigger_type = payload.trigger_type
    if payload.conditions is not None:
        automation.conditions = payload.conditions
    if payload.actions is not None:
        automation.actions = next_actions
    if payload.enabled is not None:
        automation.enabled = payload.enabled

    await db.flush()
    await db.refresh(automation)
    return AutomationResponse(**serialize_automation(automation))


@router.delete("/{automation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_automation(
    automation_id: UUID,
    current_user: DBUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    automation = (await db.execute(select(Automation).where(Automation.id == automation_id))).scalar_one_or_none()
    if automation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Automation not found")

    await ensure_workspace_permission(automation.workspace_id, current_user, db, WorkspaceSection.WORKFLOWS, "delete")
    await db.delete(automation)
    await db.flush()


@router.get("/internal/workspaces/{workspace_id}/enabled", response_model=InternalEnabledAutomationsResponse)
async def get_enabled_automations_internal(
    workspace_id: UUID,
    trigger_type: str = Query(...),
    _: None = Depends(_require_internal_token),
    db: AsyncSession = Depends(get_db),
):
    rows = await db.execute(
        select(Automation)
        .where(
            Automation.workspace_id == workspace_id,
            Automation.trigger_type == trigger_type,
            Automation.enabled.is_(True),
        )
        .order_by(Automation.created_at.asc())
    )
    return InternalEnabledAutomationsResponse(
        automations=[AutomationResponse(**serialize_automation(automation)) for automation in rows.scalars().all()]
    )


@router.get("/internal/workspaces/resolve/{workspace_slug}", response_model=InternalWorkspaceResolutionResponse)
async def resolve_workspace_internal(
    workspace_slug: str,
    _: None = Depends(_require_internal_token),
    db: AsyncSession = Depends(get_db),
):
    query = select(Workspace).where(
        or_(
            Workspace.slug == workspace_slug,
            Workspace.id == _uuid_or_none(workspace_slug),
        )
    )
    workspace = (await db.execute(query)).scalar_one_or_none()
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    return InternalWorkspaceResolutionResponse(
        id=str(workspace.id),
        slug=workspace.slug or str(workspace.id),
        name=workspace.name,
    )


@router.post("/internal/actions")
async def execute_automation_action_internal(
    payload: InternalActionRequest,
    _: None = Depends(_require_internal_token),
    db: AsyncSession = Depends(get_db),
):
    result = await execute_backend_action(
        db,
        action_type=payload.action_type,
        config=payload.config,
        context=payload.context,
        automation_id=payload.automation_id,
        action_id=payload.action_id,
    )
    await db.commit()
    return result


def _uuid_or_none(value: str) -> Optional[UUID]:
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None
