"""Central note access resolution for workspace members and note collaborators."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import WorkspaceContext, get_workspace_context
from app.core.permissions import PermissionAction, get_workspace_permissions_for_user
from app.database.models import (
    Note,
    NoteCollaborator,
    NoteCollaborationRole,
    User as DBUser,
)


AccessSource = Literal["superuser", "owner", "workspace", "collaborator", "none"]


@dataclass(slots=True)
class NoteAccessContext:
    """Resolved per-note access capabilities for a user."""

    note: Note
    user: DBUser
    access_source: AccessSource
    can_view: bool
    can_update: bool
    can_delete: bool
    can_manage: bool
    workspace_context: Optional[WorkspaceContext] = None
    collaborator_record: Optional[NoteCollaborator] = None

    @property
    def collaborator_role(self) -> Optional[NoteCollaborationRole]:
        if self.collaborator_record is None:
            return None
        role = self.collaborator_record.role
        return role if isinstance(role, NoteCollaborationRole) else NoteCollaborationRole(str(role))

    @property
    def is_workspace_member(self) -> bool:
        return self.workspace_context is not None

    def allows(self, action: PermissionAction) -> bool:
        if action == "view":
            return self.can_view
        if action == "update":
            return self.can_update
        if action == "delete":
            return self.can_delete
        if action == "manage":
            return self.can_manage
        if action == "create":
            return False
        return False

    def require(self, action: PermissionAction) -> "NoteAccessContext":
        if self.allows(action):
            return self
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Insufficient permissions for note:{action}",
        )


async def resolve_note_access(
    note: Note,
    user: DBUser,
    db: AsyncSession,
) -> NoteAccessContext:
    """Resolve effective note access from superuser, owner, workspace, and collaborator scopes."""

    if user.is_superuser:
        return NoteAccessContext(
            note=note,
            user=user,
            access_source="superuser",
            can_view=True,
            can_update=True,
            can_delete=True,
            can_manage=True,
        )

    collaborator_result = await db.execute(
        select(NoteCollaborator).where(
            NoteCollaborator.note_id == note.id,
            NoteCollaborator.user_id == user.id,
        )
    )
    collaborator_record = collaborator_result.scalar_one_or_none()
    collaborator_role: Optional[NoteCollaborationRole] = None
    if collaborator_record is not None:
        raw_role = collaborator_record.role
        collaborator_role = raw_role if isinstance(raw_role, NoteCollaborationRole) else NoteCollaborationRole(str(raw_role))

    is_owner = note.user_id == user.id
    workspace_context: Optional[WorkspaceContext] = None
    workspace_can_view = False
    workspace_can_update = False
    workspace_can_delete = False
    workspace_can_manage = False

    if note.workspace_id is not None:
        try:
            workspace_context = await get_workspace_context(note.workspace_id, user, db)
        except HTTPException as exc:
            if exc.status_code not in (status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND):
                raise
        else:
            workspace_permissions = await get_workspace_permissions_for_user(
                db,
                note.workspace_id,
                user,
                workspace_context,
            )
            note_permissions = workspace_permissions.get("notes", {})
            workspace_can_view = bool(note_permissions.get("view"))
            workspace_can_update = bool(note_permissions.get("update"))
            workspace_can_delete = bool(note_permissions.get("delete"))
            workspace_can_manage = bool(note_permissions.get("manage"))

    collaborator_can_view = collaborator_record is not None
    collaborator_can_update = collaborator_role == NoteCollaborationRole.EDITOR

    can_view = is_owner or workspace_can_view or collaborator_can_view
    can_update = is_owner or workspace_can_update or collaborator_can_update
    can_delete = is_owner or workspace_can_delete
    can_manage = is_owner or workspace_can_manage

    access_source: AccessSource = "none"
    if is_owner:
        access_source = "owner"
    elif workspace_context is not None and can_view:
        access_source = "workspace"
    elif collaborator_record is not None:
        access_source = "collaborator"

    return NoteAccessContext(
        note=note,
        user=user,
        access_source=access_source,
        can_view=can_view,
        can_update=can_update,
        can_delete=can_delete,
        can_manage=can_manage,
        workspace_context=workspace_context,
        collaborator_record=collaborator_record,
    )


async def ensure_note_permission(
    note: Note,
    user: DBUser,
    db: AsyncSession,
    action: PermissionAction,
) -> NoteAccessContext:
    """Raise 403 when the user lacks the requested note capability."""

    access = await resolve_note_access(note, user, db)
    return access.require(action)
