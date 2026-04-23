"""Approval workflow engine for workspace-backed notes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Sequence
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.audit import AuditAction, log_audit_event
from app.core.permissions import ensure_workspace_permission, get_workspace_permissions_for_user
from app.database.models import (
    ApprovalWorkflowPriority,
    ApprovalWorkflowStatus,
    Note,
    NoteApprovalTransition,
    NoteCollaborator,
    NoteCollaborationRole,
    User as DBUser,
    UserNotification,
    UserNotificationType,
    Workspace,
    WorkspaceMember,
    WorkspaceRole,
)
from app.services.note_access import ensure_note_permission


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def coerce_approval_status(value: str | ApprovalWorkflowStatus) -> ApprovalWorkflowStatus:
    try:
        return value if isinstance(value, ApprovalWorkflowStatus) else ApprovalWorkflowStatus(str(value))
    except ValueError as exc:
        allowed = ", ".join(item.value for item in ApprovalWorkflowStatus)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid approval status. Allowed values: {allowed}",
        ) from exc


def coerce_approval_priority(value: str | ApprovalWorkflowPriority) -> ApprovalWorkflowPriority:
    try:
        return value if isinstance(value, ApprovalWorkflowPriority) else ApprovalWorkflowPriority(str(value))
    except ValueError as exc:
        allowed = ", ".join(item.value for item in ApprovalWorkflowPriority)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid approval priority. Allowed values: {allowed}",
        ) from exc


def normalize_due_at(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def validate_submission_due_at(value: Optional[datetime]) -> Optional[datetime]:
    normalized = normalize_due_at(value)
    if normalized is None:
        return None
    if normalized <= utc_now():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Due date must be in the future",
        )
    return normalized


def is_workflow_overdue(
    workflow_status: ApprovalWorkflowStatus,
    due_at: Optional[datetime],
    *,
    now: Optional[datetime] = None,
) -> bool:
    if due_at is None:
        return False
    return workflow_status in {
        ApprovalWorkflowStatus.SUBMITTED,
        ApprovalWorkflowStatus.NEEDS_CHANGES,
    } and due_at < (now or utc_now())


@dataclass(frozen=True, slots=True)
class ApprovalWorkflowSummary:
    counts_by_status: Dict[str, int]
    total: int
    overdue: int


@dataclass(frozen=True, slots=True)
class ApprovalDashboardPermissions:
    workflow_can_create: bool
    workflow_can_manage: bool
    workspace_can_view_notes: bool
    workspace_can_update_notes: bool


class ApprovalWorkflowService:
    """Authoritative workflow layer for note approval state changes."""

    TRANSITIONS: Dict[ApprovalWorkflowStatus, frozenset[ApprovalWorkflowStatus]] = {
        ApprovalWorkflowStatus.DRAFT: frozenset({ApprovalWorkflowStatus.SUBMITTED}),
        ApprovalWorkflowStatus.SUBMITTED: frozenset(
            {
                ApprovalWorkflowStatus.APPROVED,
                ApprovalWorkflowStatus.REJECTED,
                ApprovalWorkflowStatus.NEEDS_CHANGES,
                ApprovalWorkflowStatus.CANCELLED,
            }
        ),
        ApprovalWorkflowStatus.NEEDS_CHANGES: frozenset(
            {
                ApprovalWorkflowStatus.SUBMITTED,
                ApprovalWorkflowStatus.CANCELLED,
            }
        ),
        ApprovalWorkflowStatus.APPROVED: frozenset(),
        ApprovalWorkflowStatus.REJECTED: frozenset(),
        ApprovalWorkflowStatus.CANCELLED: frozenset(),
    }

    _PRIORITY_ORDER: Dict[ApprovalWorkflowPriority, int] = {
        ApprovalWorkflowPriority.CRITICAL: 0,
        ApprovalWorkflowPriority.HIGH: 1,
        ApprovalWorkflowPriority.NORMAL: 2,
        ApprovalWorkflowPriority.LOW: 3,
    }

    def __init__(self, db: AsyncSession):
        self.db = db

    async def submit(
        self,
        *,
        note_id: UUID,
        actor: DBUser,
        expected_current_status: ApprovalWorkflowStatus,
        priority: Optional[ApprovalWorkflowPriority] = None,
        due_at: Optional[datetime] = None,
        due_at_provided: bool = False,
        comment: Optional[str] = None,
    ) -> Note:
        note = await self._load_note_for_update(note_id)
        await self._ensure_can_submit(note, actor)
        self._ensure_expected_status(note, expected_current_status)
        self._validate_transition(note.approval_status, ApprovalWorkflowStatus.SUBMITTED)
        next_due_at = validate_submission_due_at(due_at) if due_at_provided else None
        await self._apply_transition(
            note=note,
            actor=actor,
            target_status=ApprovalWorkflowStatus.SUBMITTED,
            priority=priority,
            due_at=next_due_at,
            replace_due_at=due_at_provided,
            comment=comment,
            audit_action=AuditAction.NOTE_APPROVAL_SUBMITTED,
            notification_type=UserNotificationType.APPROVAL_SUBMITTED,
            fanout=self._submission_recipients,
        )
        return await self._load_note_detail(note.id)

    async def resubmit(
        self,
        *,
        note_id: UUID,
        actor: DBUser,
        expected_current_status: ApprovalWorkflowStatus,
        priority: Optional[ApprovalWorkflowPriority] = None,
        due_at: Optional[datetime] = None,
        due_at_provided: bool = False,
        comment: Optional[str] = None,
    ) -> Note:
        note = await self._load_note_for_update(note_id)
        await self._ensure_can_submit(note, actor)
        self._ensure_expected_status(note, expected_current_status)
        self._validate_transition(note.approval_status, ApprovalWorkflowStatus.SUBMITTED)
        next_due_at = validate_submission_due_at(due_at) if due_at_provided else None
        await self._apply_transition(
            note=note,
            actor=actor,
            target_status=ApprovalWorkflowStatus.SUBMITTED,
            priority=priority,
            due_at=next_due_at,
            replace_due_at=due_at_provided,
            comment=comment,
            audit_action=AuditAction.NOTE_APPROVAL_RESUBMITTED,
            notification_type=UserNotificationType.APPROVAL_SUBMITTED,
            fanout=self._submission_recipients,
        )
        return await self._load_note_detail(note.id)

    async def approve(
        self,
        *,
        note_id: UUID,
        actor: DBUser,
        expected_current_status: ApprovalWorkflowStatus,
        comment: Optional[str] = None,
    ) -> Note:
        note = await self._load_note_for_update(note_id)
        await self._ensure_can_review(note, actor)
        self._ensure_expected_status(note, expected_current_status)
        self._validate_transition(note.approval_status, ApprovalWorkflowStatus.APPROVED)
        await self._apply_transition(
            note=note,
            actor=actor,
            target_status=ApprovalWorkflowStatus.APPROVED,
            comment=comment,
            audit_action=AuditAction.NOTE_APPROVAL_APPROVED,
            notification_type=UserNotificationType.APPROVAL_APPROVED,
            fanout=self._submitter_recipient,
        )
        return await self._load_note_detail(note.id)

    async def reject(
        self,
        *,
        note_id: UUID,
        actor: DBUser,
        expected_current_status: ApprovalWorkflowStatus,
        comment: str,
    ) -> Note:
        if not (comment or "").strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="A rejection reason is required",
            )
        note = await self._load_note_for_update(note_id)
        await self._ensure_can_review(note, actor)
        self._ensure_expected_status(note, expected_current_status)
        self._validate_transition(note.approval_status, ApprovalWorkflowStatus.REJECTED)
        await self._apply_transition(
            note=note,
            actor=actor,
            target_status=ApprovalWorkflowStatus.REJECTED,
            comment=comment,
            audit_action=AuditAction.NOTE_APPROVAL_REJECTED,
            notification_type=UserNotificationType.APPROVAL_REJECTED,
            fanout=self._submitter_recipient,
        )
        return await self._load_note_detail(note.id)

    async def request_changes(
        self,
        *,
        note_id: UUID,
        actor: DBUser,
        expected_current_status: ApprovalWorkflowStatus,
        comment: str,
    ) -> Note:
        if not (comment or "").strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="A review comment is required when requesting changes",
            )
        note = await self._load_note_for_update(note_id)
        await self._ensure_can_review(note, actor)
        self._ensure_expected_status(note, expected_current_status)
        self._validate_transition(note.approval_status, ApprovalWorkflowStatus.NEEDS_CHANGES)
        await self._apply_transition(
            note=note,
            actor=actor,
            target_status=ApprovalWorkflowStatus.NEEDS_CHANGES,
            comment=comment,
            audit_action=AuditAction.NOTE_APPROVAL_NEEDS_CHANGES,
            notification_type=UserNotificationType.APPROVAL_NEEDS_CHANGES,
            fanout=self._submitter_recipient,
        )
        return await self._load_note_detail(note.id)

    async def cancel(
        self,
        *,
        note_id: UUID,
        actor: DBUser,
        expected_current_status: ApprovalWorkflowStatus,
        comment: Optional[str] = None,
    ) -> Note:
        note = await self._load_note_for_update(note_id)
        await self._ensure_can_submit(note, actor)
        self._ensure_expected_status(note, expected_current_status)
        self._validate_transition(note.approval_status, ApprovalWorkflowStatus.CANCELLED)
        await self._apply_transition(
            note=note,
            actor=actor,
            target_status=ApprovalWorkflowStatus.CANCELLED,
            comment=comment,
            audit_action=AuditAction.NOTE_APPROVAL_CANCELLED,
        )
        return await self._load_note_detail(note.id)

    async def list_dashboard_items(
        self,
        *,
        workspace_id: UUID,
        current_user: DBUser,
        workflow_status: Optional[ApprovalWorkflowStatus] = None,
        limit: int = 100,
    ) -> List[dict]:
        dashboard_permissions = await self._get_dashboard_permissions(
            workspace_id=workspace_id,
            current_user=current_user,
        )
        priority_order = case(
            *(
                (Note.approval_priority == priority, order)
                for priority, order in self._PRIORITY_ORDER.items()
            ),
            else_=99,
        )
        query = (
            select(Note)
            .where(Note.workspace_id == workspace_id)
            .options(
                selectinload(Note.owner),
                selectinload(Note.approval_submitted_by),
                selectinload(Note.approval_decided_by),
            )
            .order_by(
                case((Note.approval_due_at.is_not(None), 0), else_=1),
                Note.approval_due_at.asc().nulls_last(),
                priority_order.asc(),
                Note.approval_submitted_at.desc().nulls_last(),
                Note.updated_at.desc().nulls_last(),
                Note.created_at.desc(),
            )
            .limit(max(1, min(limit, 250)))
        )
        visibility_predicate = self._dashboard_visibility_predicate(
            current_user=current_user,
            permissions=dashboard_permissions,
        )
        if visibility_predicate is not None:
            query = query.where(visibility_predicate)
        if workflow_status is not None:
            query = query.where(Note.approval_status == workflow_status)

        result = await self.db.execute(query)
        notes = list(result.scalars().all())
        collaborator_roles = await self._load_collaborator_roles(
            note_ids=[note.id for note in notes],
            current_user=current_user,
            permissions=dashboard_permissions,
        )
        return [
            serialize_approval_dashboard_item(
                note,
                current_user=current_user,
                can_submit=self._can_submit_note(
                    note,
                    current_user=current_user,
                    permissions=dashboard_permissions,
                    collaborator_role=collaborator_roles.get(note.id),
                ),
                can_review=dashboard_permissions.workflow_can_manage,
            )
            for note in notes
        ]

    async def get_dashboard_summary(
        self,
        *,
        workspace_id: UUID,
        current_user: DBUser,
    ) -> ApprovalWorkflowSummary:
        dashboard_permissions = await self._get_dashboard_permissions(
            workspace_id=workspace_id,
            current_user=current_user,
        )
        visibility_predicate = self._dashboard_visibility_predicate(
            current_user=current_user,
            permissions=dashboard_permissions,
        )
        count_query = (
            select(Note.approval_status, func.count())
            .where(Note.workspace_id == workspace_id)
        )
        if visibility_predicate is not None:
            count_query = count_query.where(visibility_predicate)
        rows = await self.db.execute(
            count_query.group_by(Note.approval_status)
        )
        counts_by_status = {
            workflow_status.value: 0
            for workflow_status in ApprovalWorkflowStatus
        }
        for workflow_status, count in rows.all():
            resolved_status = coerce_approval_status(workflow_status)
            counts_by_status[resolved_status.value] = int(count or 0)

        overdue_query = (
            select(func.count())
            .select_from(Note)
            .where(
                Note.workspace_id == workspace_id,
                Note.approval_due_at.is_not(None),
                Note.approval_due_at < utc_now(),
                Note.approval_status.in_(
                    [
                        ApprovalWorkflowStatus.SUBMITTED,
                        ApprovalWorkflowStatus.NEEDS_CHANGES,
                    ]
                ),
            )
        )
        if visibility_predicate is not None:
            overdue_query = overdue_query.where(visibility_predicate)
        overdue_result = await self.db.execute(overdue_query)
        overdue = int(overdue_result.scalar() or 0)
        return ApprovalWorkflowSummary(
            counts_by_status=counts_by_status,
            total=sum(counts_by_status.values()),
            overdue=overdue,
        )

    async def list_history(
        self,
        *,
        note_id: UUID,
        current_user: DBUser,
    ) -> List[dict]:
        note = await self._load_note_detail(note_id)
        await ensure_note_permission(note, current_user, self.db, "view")
        result = await self.db.execute(
            select(NoteApprovalTransition)
            .where(NoteApprovalTransition.note_id == note_id)
            .order_by(NoteApprovalTransition.created_at.desc(), NoteApprovalTransition.id.desc())
            .options(selectinload(NoteApprovalTransition.actor))
        )
        transitions = list(result.scalars().all())
        return [serialize_approval_transition(entry) for entry in transitions]

    async def serialize_item_for_user(self, *, note: Note, current_user: DBUser) -> dict:
        self._require_workspace_note(note)
        access = await ensure_note_permission(note, current_user, self.db, "view")
        workspace_context = await ensure_workspace_permission(note.workspace_id, current_user, self.db, "workflows", "view")
        workspace_permissions = await get_workspace_permissions_for_user(
            self.db,
            note.workspace_id,
            current_user,
            workspace_context,
        )
        workflow_permissions = workspace_permissions.get("workflows", {})
        return serialize_approval_dashboard_item(
            note,
            current_user=current_user,
            can_submit=bool(workflow_permissions.get("create")) and access.can_update,
            can_review=bool(workflow_permissions.get("manage")),
        )

    async def _load_note_for_update(self, note_id: UUID) -> Note:
        result = await self.db.execute(
            select(Note)
            .where(Note.id == note_id)
            .with_for_update()
            .options(selectinload(Note.owner))
        )
        note = result.scalar_one_or_none()
        if note is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Note not found")
        return note

    async def _load_note_detail(self, note_id: UUID) -> Note:
        result = await self.db.execute(
            select(Note)
            .where(Note.id == note_id)
            .options(
                selectinload(Note.owner),
                selectinload(Note.approval_submitted_by),
                selectinload(Note.approval_decided_by),
            )
        )
        note = result.scalar_one_or_none()
        if note is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Note not found")
        return note

    async def _ensure_can_submit(self, note: Note, actor: DBUser) -> None:
        self._require_workspace_note(note)
        await ensure_note_permission(note, actor, self.db, "update")
        await ensure_workspace_permission(note.workspace_id, actor, self.db, "workflows", "create")

    async def _ensure_can_review(self, note: Note, actor: DBUser) -> None:
        self._require_workspace_note(note)
        if actor.is_superuser:
            return
        await ensure_workspace_permission(note.workspace_id, actor, self.db, "workflows", "manage")

    def _require_workspace_note(self, note: Note) -> None:
        if note.workspace_id is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Approval workflows are only available for notes inside a workspace",
            )

    def _ensure_expected_status(self, note: Note, expected_current_status: ApprovalWorkflowStatus) -> None:
        current = coerce_approval_status(note.approval_status)
        expected = coerce_approval_status(expected_current_status)
        if current != expected:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Approval state changed from {expected.value} to {current.value}; refresh and try again",
            )

    def _validate_transition(
        self,
        current_status: ApprovalWorkflowStatus,
        target_status: ApprovalWorkflowStatus,
    ) -> None:
        current = coerce_approval_status(current_status)
        target = coerce_approval_status(target_status)
        if target not in self.TRANSITIONS[current]:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Invalid approval transition: {current.value} -> {target.value}",
            )

    async def _apply_transition(
        self,
        *,
        note: Note,
        actor: DBUser,
        target_status: ApprovalWorkflowStatus,
        audit_action: AuditAction,
        priority: Optional[ApprovalWorkflowPriority] = None,
        due_at: Optional[datetime] = None,
        replace_due_at: bool = False,
        comment: Optional[str] = None,
        notification_type: Optional[UserNotificationType] = None,
        fanout=None,
    ) -> None:
        changed_at = utc_now()
        current_status = coerce_approval_status(note.approval_status)
        next_priority = coerce_approval_priority(priority or note.approval_priority or ApprovalWorkflowPriority.NORMAL)
        next_due_at = normalize_due_at(due_at) if replace_due_at else note.approval_due_at

        note.approval_status = target_status
        note.approval_priority = next_priority
        note.approval_due_at = next_due_at
        note.updated_at = changed_at

        if target_status == ApprovalWorkflowStatus.SUBMITTED:
            note.approval_submitted_at = changed_at
            note.approval_submitted_by_user_id = actor.id
            note.approval_decided_at = None
            note.approval_decided_by_user_id = None
        elif target_status in {
            ApprovalWorkflowStatus.APPROVED,
            ApprovalWorkflowStatus.REJECTED,
        }:
            note.approval_decided_at = changed_at
            note.approval_decided_by_user_id = actor.id
        elif target_status == ApprovalWorkflowStatus.NEEDS_CHANGES:
            note.approval_decided_at = changed_at
            note.approval_decided_by_user_id = actor.id
        elif target_status == ApprovalWorkflowStatus.CANCELLED:
            note.approval_decided_at = changed_at
            note.approval_decided_by_user_id = actor.id

        self.db.add(
            NoteApprovalTransition(
                note_id=note.id,
                workspace_id=note.workspace_id,
                actor_user_id=actor.id,
                from_status=current_status,
                to_status=target_status,
                comment=(comment or "").strip() or None,
                due_at_snapshot=next_due_at,
                priority_snapshot=next_priority,
            )
        )

        if notification_type is not None and callable(fanout):
            seen_recipient_ids: set[UUID] = set()
            for recipient_id in await fanout(note, actor):
                if recipient_id is None or recipient_id == actor.id or recipient_id in seen_recipient_ids:
                    continue
                seen_recipient_ids.add(recipient_id)
                self.db.add(
                    UserNotification(
                        user_id=recipient_id,
                        actor_user_id=actor.id,
                        workspace_id=note.workspace_id,
                        note_id=note.id,
                        notification_type=notification_type,
                        payload={
                            "note_id": str(note.id),
                            "note_title": note.title,
                            "approval_status": target_status.value,
                            "priority": next_priority.value,
                            "due_at": next_due_at.isoformat() if next_due_at else None,
                            "comment": (comment or "").strip() or None,
                        },
                    )
                )

        await log_audit_event(
            self.db,
            action=audit_action,
            user_id=actor.id,
            workspace_id=note.workspace_id,
            entity_type="note",
            entity_id=note.id,
            note_id=note.id,
            metadata={
                "from_status": current_status.value,
                "to_status": target_status.value,
                "priority": next_priority.value,
                "due_at": next_due_at.isoformat() if next_due_at else None,
                "comment": (comment or "").strip() or None,
            },
            source="services.approval_workflows",
        )
        await self.db.flush()

    async def _get_dashboard_permissions(
        self,
        *,
        workspace_id: UUID,
        current_user: DBUser,
    ) -> ApprovalDashboardPermissions:
        workspace_context = await ensure_workspace_permission(
            workspace_id,
            current_user,
            self.db,
            "workflows",
            "view",
        )
        workspace_permissions = await get_workspace_permissions_for_user(
            self.db,
            workspace_id,
            current_user,
            workspace_context,
        )
        workflow_permissions = workspace_permissions.get("workflows", {})
        note_permissions = workspace_permissions.get("notes", {})
        return ApprovalDashboardPermissions(
            workflow_can_create=bool(workflow_permissions.get("create")),
            workflow_can_manage=bool(workflow_permissions.get("manage")),
            workspace_can_view_notes=bool(note_permissions.get("view")),
            workspace_can_update_notes=bool(note_permissions.get("update")),
        )

    def _dashboard_visibility_predicate(
        self,
        *,
        current_user: DBUser,
        permissions: ApprovalDashboardPermissions,
    ):
        if current_user.is_superuser or permissions.workspace_can_view_notes:
            return None
        return or_(
            Note.user_id == current_user.id,
            Note.id.in_(
                select(NoteCollaborator.note_id).where(
                    NoteCollaborator.user_id == current_user.id,
                )
            ),
        )

    async def _load_collaborator_roles(
        self,
        *,
        note_ids: Sequence[UUID],
        current_user: DBUser,
        permissions: ApprovalDashboardPermissions,
    ) -> Dict[UUID, NoteCollaborationRole]:
        if not note_ids or current_user.is_superuser or permissions.workspace_can_update_notes:
            return {}
        rows = await self.db.execute(
            select(NoteCollaborator.note_id, NoteCollaborator.role).where(
                NoteCollaborator.user_id == current_user.id,
                NoteCollaborator.note_id.in_(list(note_ids)),
            )
        )
        role_map: Dict[UUID, NoteCollaborationRole] = {}
        for note_id, raw_role in rows.all():
            role_map[note_id] = (
                raw_role
                if isinstance(raw_role, NoteCollaborationRole)
                else NoteCollaborationRole(str(raw_role))
            )
        return role_map

    def _can_submit_note(
        self,
        note: Note,
        *,
        current_user: DBUser,
        permissions: ApprovalDashboardPermissions,
        collaborator_role: Optional[NoteCollaborationRole],
    ) -> bool:
        if not permissions.workflow_can_create:
            return False
        if current_user.is_superuser:
            return True
        if note.user_id == current_user.id:
            return True
        if permissions.workspace_can_update_notes:
            return True
        return collaborator_role == NoteCollaborationRole.EDITOR

    async def _submission_recipients(self, note: Note, actor: DBUser) -> List[UUID]:
        self._require_workspace_note(note)
        workspace_result = await self.db.execute(
            select(Workspace.owner_id).where(Workspace.id == note.workspace_id)
        )
        owner_id = workspace_result.scalar_one_or_none()
        admin_rows = await self.db.execute(
            select(WorkspaceMember.user_id)
            .where(
                WorkspaceMember.workspace_id == note.workspace_id,
                WorkspaceMember.role.in_([WorkspaceRole.OWNER, WorkspaceRole.ADMIN]),
            )
        )
        recipient_ids = {
            user_id
            for user_id in admin_rows.scalars().all()
            if user_id is not None
        }
        if owner_id is not None:
            recipient_ids.add(owner_id)
        recipient_ids.add(note.user_id)
        recipient_ids.discard(actor.id)
        return sorted(recipient_ids, key=str)

    async def _submitter_recipient(self, note: Note, actor: DBUser) -> List[UUID]:
        recipient_ids = {
            user_id
            for user_id in [note.approval_submitted_by_user_id, note.user_id]
            if user_id is not None and user_id != actor.id
        }
        return sorted(recipient_ids, key=str)


APPROVAL_ACTION_RULES: Dict[str, tuple[frozenset[ApprovalWorkflowStatus], ApprovalWorkflowStatus, str]] = {
    "submit": (
        frozenset({ApprovalWorkflowStatus.DRAFT}),
        ApprovalWorkflowStatus.SUBMITTED,
        "submit",
    ),
    "resubmit": (
        frozenset({ApprovalWorkflowStatus.NEEDS_CHANGES}),
        ApprovalWorkflowStatus.SUBMITTED,
        "submit",
    ),
    "cancel": (
        frozenset({ApprovalWorkflowStatus.SUBMITTED, ApprovalWorkflowStatus.NEEDS_CHANGES}),
        ApprovalWorkflowStatus.CANCELLED,
        "submit",
    ),
    "approve": (
        frozenset({ApprovalWorkflowStatus.SUBMITTED}),
        ApprovalWorkflowStatus.APPROVED,
        "review",
    ),
    "reject": (
        frozenset({ApprovalWorkflowStatus.SUBMITTED}),
        ApprovalWorkflowStatus.REJECTED,
        "review",
    ),
    "request_changes": (
        frozenset({ApprovalWorkflowStatus.SUBMITTED}),
        ApprovalWorkflowStatus.NEEDS_CHANGES,
        "review",
    ),
}


def build_available_approval_actions(
    note: Note,
    *,
    current_user: Optional[DBUser] = None,
    can_submit: bool = False,
    can_review: bool = False,
) -> Dict[str, bool]:
    del current_user
    workflow_status = coerce_approval_status(note.approval_status)
    permission_map = {
        "submit": can_submit,
        "review": can_review,
    }
    return {
        action: (
            permission_map[required_permission]
            and workflow_status in source_statuses
            and target_status in ApprovalWorkflowService.TRANSITIONS[workflow_status]
        )
        for action, (source_statuses, target_status, required_permission) in APPROVAL_ACTION_RULES.items()
    }


def serialize_approval_transition(transition: NoteApprovalTransition) -> dict:
    from_status = coerce_approval_status(transition.from_status)
    to_status = coerce_approval_status(transition.to_status)
    priority = coerce_approval_priority(transition.priority_snapshot)
    return {
        "id": str(transition.id),
        "note_id": str(transition.note_id),
        "workspace_id": str(transition.workspace_id) if transition.workspace_id else None,
        "actor_user_id": str(transition.actor_user_id) if transition.actor_user_id else None,
        "from_status": from_status.value,
        "to_status": to_status.value,
        "comment": transition.comment,
        "due_at_snapshot": transition.due_at_snapshot.isoformat() if transition.due_at_snapshot else None,
        "priority_snapshot": priority.value,
        "created_at": transition.created_at.isoformat() if transition.created_at else None,
        "actor": {
            "id": str(transition.actor.id),
            "email": transition.actor.email,
            "name": transition.actor.full_name or transition.actor.email,
        } if transition.actor is not None else None,
    }


def serialize_approval_dashboard_item(
    note: Note,
    *,
    current_user: Optional[DBUser] = None,
    can_submit: bool = False,
    can_review: bool = False,
) -> dict:
    workflow_status = coerce_approval_status(note.approval_status)
    priority = coerce_approval_priority(note.approval_priority)
    return {
        "note_id": str(note.id),
        "workspace_id": str(note.workspace_id) if note.workspace_id else None,
        "title": note.title,
        "summary": note.summary,
        "note_type": note.note_type,
        "author_user_id": str(note.user_id),
        "approval_status": workflow_status.value,
        "approval_priority": priority.value,
        "approval_due_at": note.approval_due_at.isoformat() if note.approval_due_at else None,
        "approval_submitted_at": note.approval_submitted_at.isoformat() if note.approval_submitted_at else None,
        "approval_submitted_by_user_id": str(note.approval_submitted_by_user_id) if note.approval_submitted_by_user_id else None,
        "approval_decided_at": note.approval_decided_at.isoformat() if note.approval_decided_at else None,
        "approval_decided_by_user_id": str(note.approval_decided_by_user_id) if note.approval_decided_by_user_id else None,
        "is_overdue": is_workflow_overdue(workflow_status, note.approval_due_at),
        "available_actions": build_available_approval_actions(
            note,
            current_user=current_user,
            can_submit=can_submit,
            can_review=can_review,
        ),
        "author": {
            "id": str(note.owner.id),
            "email": note.owner.email,
            "name": note.owner.full_name or note.owner.email,
        } if note.owner is not None else None,
        "submitted_by": {
            "id": str(note.approval_submitted_by.id),
            "email": note.approval_submitted_by.email,
            "name": note.approval_submitted_by.full_name or note.approval_submitted_by.email,
        } if note.approval_submitted_by is not None else None,
        "decided_by": {
            "id": str(note.approval_decided_by.id),
            "email": note.approval_decided_by.email,
            "name": note.approval_decided_by.full_name or note.approval_decided_by.email,
        } if note.approval_decided_by is not None else None,
    }
