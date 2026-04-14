"""Workspace permission matrix and enforcement helpers."""
from __future__ import annotations

from copy import deepcopy
from typing import Annotated, Dict, Iterable, Literal, Optional
from uuid import UUID

from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import WorkspaceContext, get_current_active_user, get_workspace_context
from app.database.models import (
    User as DBUser,
    WorkspaceMember,
    WorkspacePermissionOverride,
    WorkspaceRole,
    WorkspaceSection,
)
from app.database.session import get_db

PermissionAction = Literal["view", "create", "update", "delete", "manage"]
PermissionState = Dict[PermissionAction, bool]
WorkspacePermissionMap = Dict[str, PermissionState]

WORKSPACE_PERMISSION_ACTIONS: tuple[PermissionAction, ...] = ("view", "create", "update", "delete", "manage")
WORKSPACE_PERMISSION_SECTIONS: tuple[WorkspaceSection, ...] = (
    WorkspaceSection.WORKSPACE,
    WorkspaceSection.DOCUMENTS,
    WorkspaceSection.NOTES,
    WorkspaceSection.SEARCH,
    WorkspaceSection.KNOWLEDGE_GRAPH,
    WorkspaceSection.CHAT,
)

DEFAULT_PERMISSION_MATRIX: Dict[WorkspaceRole, WorkspacePermissionMap] = {
    WorkspaceRole.OWNER: {
        "workspace": {"view": True, "create": False, "update": True, "delete": True, "manage": True},
        "documents": {"view": True, "create": True, "update": True, "delete": True, "manage": True},
        "notes": {"view": True, "create": True, "update": True, "delete": True, "manage": True},
        "search": {"view": True, "create": True, "update": False, "delete": False, "manage": True},
        "knowledge_graph": {"view": True, "create": False, "update": True, "delete": False, "manage": True},
        "chat": {"view": True, "create": True, "update": False, "delete": False, "manage": True},
    },
    WorkspaceRole.ADMIN: {
        "workspace": {"view": True, "create": False, "update": True, "delete": False, "manage": True},
        "documents": {"view": True, "create": True, "update": True, "delete": True, "manage": True},
        "notes": {"view": True, "create": True, "update": True, "delete": True, "manage": True},
        "search": {"view": True, "create": True, "update": False, "delete": False, "manage": True},
        "knowledge_graph": {"view": True, "create": False, "update": True, "delete": False, "manage": True},
        "chat": {"view": True, "create": True, "update": False, "delete": False, "manage": True},
    },
    WorkspaceRole.MEMBER: {
        "workspace": {"view": True, "create": False, "update": False, "delete": False, "manage": False},
        "documents": {"view": True, "create": True, "update": True, "delete": False, "manage": False},
        "notes": {"view": True, "create": True, "update": True, "delete": True, "manage": False},
        "search": {"view": True, "create": True, "update": False, "delete": False, "manage": False},
        "knowledge_graph": {"view": True, "create": False, "update": False, "delete": False, "manage": False},
        "chat": {"view": True, "create": True, "update": False, "delete": False, "manage": False},
    },
    WorkspaceRole.VIEWER: {
        "workspace": {"view": True, "create": False, "update": False, "delete": False, "manage": False},
        "documents": {"view": True, "create": False, "update": False, "delete": False, "manage": False},
        "notes": {"view": True, "create": False, "update": False, "delete": False, "manage": False},
        "search": {"view": True, "create": True, "update": False, "delete": False, "manage": False},
        "knowledge_graph": {"view": True, "create": False, "update": False, "delete": False, "manage": False},
        "chat": {"view": True, "create": True, "update": False, "delete": False, "manage": False},
    },
}


def _coerce_role(role: WorkspaceRole | str) -> WorkspaceRole:
    return role if isinstance(role, WorkspaceRole) else WorkspaceRole(str(role))


def build_role_permissions(role: WorkspaceRole | str) -> WorkspacePermissionMap:
    resolved_role = _coerce_role(role)
    return deepcopy(DEFAULT_PERMISSION_MATRIX[resolved_role])


def _apply_override_value(current: bool, override: Optional[bool]) -> bool:
    if override is None:
        return current
    return bool(override)


def apply_permission_overrides(
    permissions: WorkspacePermissionMap,
    overrides: Iterable[WorkspacePermissionOverride],
) -> WorkspacePermissionMap:
    next_permissions = deepcopy(permissions)
    for override in overrides:
        section_key = override.section.value if isinstance(override.section, WorkspaceSection) else str(override.section)
        if section_key not in next_permissions:
            continue
        next_permissions[section_key]["view"] = _apply_override_value(next_permissions[section_key]["view"], override.can_view)
        next_permissions[section_key]["create"] = _apply_override_value(next_permissions[section_key]["create"], override.can_create)
        next_permissions[section_key]["update"] = _apply_override_value(next_permissions[section_key]["update"], override.can_update)
        next_permissions[section_key]["delete"] = _apply_override_value(next_permissions[section_key]["delete"], override.can_delete)
        next_permissions[section_key]["manage"] = _apply_override_value(next_permissions[section_key]["manage"], override.can_manage)
    return next_permissions


async def get_workspace_permissions_for_user(
    db: AsyncSession,
    workspace_id: UUID,
    user: DBUser,
    context: Optional[WorkspaceContext] = None,
) -> WorkspacePermissionMap:
    if user.is_superuser:
        return build_role_permissions(WorkspaceRole.OWNER)

    workspace_context = context or await get_workspace_context(workspace_id, user, db)
    permissions = build_role_permissions(workspace_context.role)
    override_result = await db.execute(
        select(WorkspacePermissionOverride).where(
            WorkspacePermissionOverride.workspace_id == workspace_id,
            WorkspacePermissionOverride.user_id == user.id,
        )
    )
    overrides = override_result.scalars().all()
    return apply_permission_overrides(permissions, overrides)


async def get_bulk_workspace_permissions_for_user(
    db: AsyncSession,
    user: DBUser,
    workspace_ids: Optional[Iterable[UUID]] = None,
) -> Dict[str, Dict[str, object]]:
    workspace_filter = list(workspace_ids or [])

    membership_query = select(WorkspaceMember).where(WorkspaceMember.user_id == user.id)
    if workspace_filter:
        membership_query = membership_query.where(WorkspaceMember.workspace_id.in_(workspace_filter))

    membership_result = await db.execute(membership_query)
    memberships = membership_result.scalars().all()

    if not memberships and not user.is_superuser:
        return {}

    resolved_workspace_ids = [member.workspace_id for member in memberships]
    override_query = select(WorkspacePermissionOverride).where(
        WorkspacePermissionOverride.user_id == user.id,
    )
    if resolved_workspace_ids:
        override_query = override_query.where(WorkspacePermissionOverride.workspace_id.in_(resolved_workspace_ids))
    override_result = await db.execute(override_query)
    override_rows = override_result.scalars().all()

    overrides_by_workspace: Dict[str, list[WorkspacePermissionOverride]] = {}
    for row in override_rows:
        overrides_by_workspace.setdefault(str(row.workspace_id), []).append(row)

    payload: Dict[str, Dict[str, object]] = {}
    for membership in memberships:
        workspace_key = str(membership.workspace_id)
        role = _coerce_role(membership.role)
        permissions = build_role_permissions(role)
        permissions = apply_permission_overrides(permissions, overrides_by_workspace.get(workspace_key, []))
        payload[workspace_key] = {
            "workspace_id": workspace_key,
            "role": role.value,
            "permissions": permissions,
        }

    return payload


async def ensure_workspace_permission(
    workspace_id: UUID,
    user: DBUser,
    db: AsyncSession,
    section: WorkspaceSection | str,
    action: PermissionAction,
    context: Optional[WorkspaceContext] = None,
) -> WorkspaceContext:
    workspace_context = context or await get_workspace_context(workspace_id, user, db)
    permissions = await get_workspace_permissions_for_user(db, workspace_id, user, workspace_context)
    section_key = section.value if isinstance(section, WorkspaceSection) else str(section)

    if permissions.get(section_key, {}).get(action):
        return workspace_context

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=f"Insufficient permissions for {section_key}:{action}",
    )


def require_permission(section: WorkspaceSection | str, action: PermissionAction):
    async def permission_checker(
        workspace_id: UUID,
        current_user: Annotated[DBUser, Depends(get_current_active_user)],
        db: Annotated[AsyncSession, Depends(get_db)],
    ) -> WorkspaceContext:
        return await ensure_workspace_permission(
            workspace_id=workspace_id,
            user=current_user,
            db=db,
            section=section,
            action=action,
        )

    return permission_checker


requirePermission = require_permission
