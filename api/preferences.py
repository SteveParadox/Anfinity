"""User and workspace settings APIs."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditAction, AuditLogger, EntityType
from app.core.auth import get_current_active_user
from app.core.permissions import ensure_workspace_permission
from app.database.models import User as DBUser, Workspace, WorkspaceSection
from app.database.session import get_db
from app.services.settings_preferences import (
    USER_SETTINGS_DEFAULTS,
    WORKSPACE_SETTINGS_DEFAULTS,
    apply_settings_patch,
    settings_patch_paths,
    resolved_user_settings,
    resolved_workspace_settings,
)

router = APIRouter(prefix="/settings", tags=["Settings"])

# View-only workspace members do not need administrative knobs in their payload.
# This keeps future integration/provider configuration from accidentally appearing
# in read-only Settings screens.
NON_ADMIN_VISIBLE_WORKSPACE_SECTIONS = frozenset({"ai_search", "notes", "collaboration"})


def _filtered_workspace_settings(settings: Dict[str, Any], *, can_update: bool) -> Dict[str, Any]:
    if can_update:
        return deepcopy(settings)
    return {section: deepcopy(value) for section, value in settings.items() if section in NON_ADMIN_VISIBLE_WORKSPACE_SECTIONS}


def _workspace_response(workspace: Workspace, *, can_update: bool) -> WorkspaceSettingsResponse:
    settings = resolved_workspace_settings(workspace.settings or {})
    return WorkspaceSettingsResponse(
        workspace_id=str(workspace.id),
        settings=_filtered_workspace_settings(settings, can_update=can_update),
        defaults=_filtered_workspace_settings(WORKSPACE_SETTINGS_DEFAULTS, can_update=can_update),
        can_update=can_update,
    )


class SettingsPatch(BaseModel):
    settings: Dict[str, Any] = Field(default_factory=dict)

    class Config:
        extra = "forbid"


class UserSettingsResponse(BaseModel):
    user_id: str
    settings: Dict[str, Any]
    defaults: Dict[str, Any]


class WorkspaceSettingsResponse(BaseModel):
    workspace_id: str
    settings: Dict[str, Any]
    defaults: Dict[str, Any]
    can_update: bool


@router.get("/me", response_model=UserSettingsResponse)
async def get_my_settings(
    current_user: DBUser = Depends(get_current_active_user),
) -> UserSettingsResponse:
    return UserSettingsResponse(
        user_id=str(current_user.id),
        settings=resolved_user_settings(current_user.settings or {}),
        defaults=USER_SETTINGS_DEFAULTS,
    )


@router.patch("/me", response_model=UserSettingsResponse)
async def update_my_settings(
    payload: SettingsPatch,
    request: Request,
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> UserSettingsResponse:
    try:
        current_user.settings = apply_settings_patch(
            USER_SETTINGS_DEFAULTS,
            current_user.settings or {},
            payload.settings,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    await db.flush()
    await db.refresh(current_user)
    
    # Log user settings update for audit trail without storing preference values.
    await AuditLogger(db, current_user.id).with_request(request, source="api.preferences.update_my_settings").log(
        action=AuditAction.USER_UPDATED,
        entity_type=EntityType.USER,
        entity_id=current_user.id,
        metadata={"updated_settings": settings_patch_paths(payload.settings)},
    )
    
    return UserSettingsResponse(
        user_id=str(current_user.id),
        settings=resolved_user_settings(current_user.settings or {}),
        defaults=USER_SETTINGS_DEFAULTS,
    )


@router.post("/me/reset", response_model=UserSettingsResponse)
async def reset_my_settings(
    request: Request,
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> UserSettingsResponse:
    # Store previous settings for audit trail
    previous_settings = current_user.settings or {}
    
    current_user.settings = {}
    await db.flush()
    await db.refresh(current_user)
    
    # Log user settings reset for audit trail and compliance.
    await AuditLogger(db, current_user.id).with_request(request, source="api.preferences.reset_my_settings").log(
        action=AuditAction.USER_UPDATED,
        entity_type=EntityType.USER,
        entity_id=current_user.id,
        metadata={"reset_settings": True, "previous_sections": sorted(previous_settings.keys())},
    )
    
    return UserSettingsResponse(
        user_id=str(current_user.id),
        settings=resolved_user_settings({}),
        defaults=USER_SETTINGS_DEFAULTS,
    )


@router.get("/workspaces/{workspace_id}", response_model=WorkspaceSettingsResponse)
async def get_workspace_settings(
    workspace_id: UUID,
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> WorkspaceSettingsResponse:
    await ensure_workspace_permission(
        workspace_id=workspace_id,
        user=current_user,
        db=db,
        section=WorkspaceSection.SETTINGS,
        action="view",
    )
    can_update = True
    try:
        await ensure_workspace_permission(
            workspace_id=workspace_id,
            user=current_user,
            db=db,
            section=WorkspaceSection.SETTINGS,
            action="update",
        )
    except HTTPException:
        can_update = False

    workspace = await _load_workspace(workspace_id, db)
    return _workspace_response(workspace, can_update=can_update)


@router.patch("/workspaces/{workspace_id}", response_model=WorkspaceSettingsResponse)
async def update_workspace_settings(
    workspace_id: UUID,
    payload: SettingsPatch,
    request: Request,
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> WorkspaceSettingsResponse:
    # Enforce permission check before allowing update
    await ensure_workspace_permission(
        workspace_id=workspace_id,
        user=current_user,
        db=db,
        section=WorkspaceSection.SETTINGS,
        action="update",
    )
    workspace = await _load_workspace(workspace_id, db)
    try:
        workspace.settings = apply_settings_patch(
            WORKSPACE_SETTINGS_DEFAULTS,
            workspace.settings or {},
            payload.settings,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    await db.flush()
    await db.refresh(workspace)
    await AuditLogger(db, current_user.id).with_request(request, source="api.preferences.update_workspace_settings").log(
        action=AuditAction.WORKSPACE_UPDATED,
        workspace_id=workspace_id,
        entity_type=EntityType.WORKSPACE,
        entity_id=workspace_id,
        metadata={"updated_settings": settings_patch_paths(payload.settings)},
    )

    return _workspace_response(workspace, can_update=True)


@router.post("/workspaces/{workspace_id}/reset", response_model=WorkspaceSettingsResponse)
async def reset_workspace_settings(
    workspace_id: UUID,
    request: Request,
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> WorkspaceSettingsResponse:
    # Enforce permission check before allowing reset
    await ensure_workspace_permission(
        workspace_id=workspace_id,
        user=current_user,
        db=db,
        section=WorkspaceSection.SETTINGS,
        action="update",
    )
    workspace = await _load_workspace(workspace_id, db)
    
    # Store previous settings for audit trail
    previous_settings = workspace.settings or {}
    
    workspace.settings = {}
    await db.flush()
    await db.refresh(workspace)
    await AuditLogger(db, current_user.id).with_request(request, source="api.preferences.reset_workspace_settings").log(
        action=AuditAction.WORKSPACE_UPDATED,
        workspace_id=workspace_id,
        entity_type=EntityType.WORKSPACE,
        entity_id=workspace_id,
        metadata={"reset_settings": True, "previous_sections": sorted(previous_settings.keys())},
    )

    return _workspace_response(workspace, can_update=True)


async def _load_workspace(workspace_id: UUID, db: AsyncSession) -> Workspace:
    result = await db.execute(select(Workspace).where(Workspace.id == workspace_id))
    workspace = result.scalar_one_or_none()
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    return workspace
