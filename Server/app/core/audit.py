"""Typed audit logging with immutable post-commit persistence."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Sequence
from uuid import UUID

from sqlalchemy import desc, func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import AuditEvent
from app.database.session import AsyncSessionLocal, SyncSessionLocal, get_session_info


logger = logging.getLogger(__name__)

PENDING_AUDIT_EVENTS_KEY = "pending_audit_events"


class AuditAction(str, Enum):
    """Normalized audit action types."""

    USER_REGISTERED = "user.registered"
    USER_LOGIN = "user.login"
    USER_LOGOUT = "user.logout"
    PASSWORD_CHANGED = "user.password_changed"

    WORKSPACE_CREATED = "workspace.created"
    WORKSPACE_UPDATED = "workspace.updated"
    WORKSPACE_DELETED = "workspace.deleted"
    MEMBER_INVITED = "workspace.member_invited"
    MEMBER_JOINED = "workspace.member_joined"
    MEMBER_REMOVED = "workspace.member_removed"
    MEMBER_ROLE_CHANGED = "workspace.member_role_changed"
    WORKSPACE_PERMISSION_OVERRIDE_UPDATED = "workspace.permission_override_updated"

    DOCUMENT_UPLOADED = "document.uploaded"
    DOCUMENT_DELETED = "document.deleted"
    DOCUMENT_PROCESSED = "document.processed"
    DOCUMENT_FAILED = "document.failed"

    NOTE_CREATED = "note.created"
    NOTE_UPDATED = "note.updated"
    NOTE_RESTORED = "note.restored"
    NOTE_APPROVAL_SUBMITTED = "note.approval_submitted"
    NOTE_APPROVAL_RESUBMITTED = "note.approval_resubmitted"
    NOTE_APPROVAL_APPROVED = "note.approval_approved"
    NOTE_APPROVAL_REJECTED = "note.approval_rejected"
    NOTE_APPROVAL_NEEDS_CHANGES = "note.approval_needs_changes"
    NOTE_APPROVAL_CANCELLED = "note.approval_cancelled"
    NOTE_COLLABORATION_SYNCED = "note.collaboration_synced"
    NOTE_DELETED = "note.deleted"
    NOTE_COLLABORATOR_INVITED = "note.collaborator_invited"
    NOTE_COLLABORATOR_INVITE_ACCEPTED = "note.collaborator_invite_accepted"
    NOTE_COLLABORATOR_INVITE_REVOKED = "note.collaborator_invite_revoked"
    NOTE_COLLABORATOR_ROLE_CHANGED = "note.collaborator_role_changed"

    QUERY_EXECUTED = "query.executed"
    ANSWER_GENERATED = "answer.generated"
    ANSWER_VERIFIED = "answer.verified"
    ANSWER_REJECTED = "answer.rejected"
    ANSWER_FAILED = "answer.failed"
    FEEDBACK_SUBMITTED = "feedback.submitted"

    CONNECTOR_CREATED = "connector.created"
    CONNECTOR_UPDATED = "connector.updated"
    CONNECTOR_DELETED = "connector.deleted"
    CONNECTOR_SYNCED = "connector.synced"

    THINKING_SESSION_STARTED = "thinking_session.started"
    THINKING_SESSION_PHASE_TRANSITIONED = "thinking_session.phase_transitioned"
    THINKING_SESSION_SYNTHESIS_COMPLETED = "thinking_session.synthesis_completed"
    THINKING_SESSION_SYNTHESIS_FAILED = "thinking_session.synthesis_failed"
    CONTRIBUTION_SUBMITTED = "thinking_session.contribution_submitted"
    VOTE_CAST = "thinking_session.vote_cast"
    VOTE_REMOVED = "thinking_session.vote_removed"
    SESSION_REFINEMENT_UPDATED = "thinking_session.refinement_updated"


class EntityType(str, Enum):
    """Entity types for audit logging."""

    SYSTEM = "system"
    USER = "user"
    WORKSPACE = "workspace"
    WORKSPACE_PERMISSION = "workspace_permission"
    DOCUMENT = "document"
    NOTE = "note"
    NOTE_INVITE = "note_invite"
    NOTE_COLLABORATOR = "note_collaborator"
    CHUNK = "chunk"
    QUERY = "query"
    ANSWER = "answer"
    CONNECTOR = "connector"
    THINKING_SESSION = "thinking_session"
    THINKING_CONTRIBUTION = "thinking_contribution"
    THINKING_VOTE = "thinking_vote"
    THINKING_SYNTHESIS_RUN = "thinking_synthesis_run"


NOTE_CONTRIBUTION_ACTIONS = frozenset(
    {
        AuditAction.NOTE_CREATED,
        AuditAction.NOTE_UPDATED,
        AuditAction.NOTE_RESTORED,
        AuditAction.CONTRIBUTION_SUBMITTED,
        AuditAction.VOTE_CAST,
    }
)


def _coerce_action(value: AuditAction | str) -> AuditAction:
    return value if isinstance(value, AuditAction) else AuditAction(str(value))


def _coerce_entity_type(value: EntityType | str | None) -> EntityType:
    if value is None:
        return EntityType.SYSTEM
    return value if isinstance(value, EntityType) else EntityType(str(value))


def _copy_metadata(metadata: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    if not metadata:
        return {}
    return dict(metadata)


def _stringify_entity_id(value: UUID | str | None) -> Optional[str]:
    if value is None:
        return None
    return str(value)


@dataclass(frozen=True, slots=True)
class AuditRequestContext:
    """Request-scoped audit context attached to queued audit events."""

    request_id: Optional[str] = None
    session_id: Optional[str] = None
    source: Optional[str] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None

    @classmethod
    def from_request(cls, request, *, source: Optional[str] = None, session_id: Optional[str] = None) -> "AuditRequestContext":
        client = getattr(request, "client", None)
        request_id = (
            request.headers.get("x-request-id")
            or request.headers.get("x-correlation-id")
            or getattr(getattr(request, "state", None), "request_id", None)
        )
        return cls(
            request_id=request_id or None,
            session_id=session_id,
            source=source,
            ip_address=getattr(client, "host", None),
            user_agent=request.headers.get("user-agent"),
        )


@dataclass(frozen=True, slots=True)
class AuditEventPayload:
    """Normalized audit event queued for immutable post-commit persistence."""

    action_type: AuditAction
    entity_type: EntityType
    entity_id: Optional[str]
    actor_user_id: Optional[UUID] = None
    workspace_id: Optional[UUID] = None
    note_id: Optional[UUID] = None
    target_user_id: Optional[UUID] = None
    metadata_json: Dict[str, Any] = field(default_factory=dict)
    request_id: Optional[str] = None
    session_id: Optional[str] = None
    source: Optional[str] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata_json", _copy_metadata(self.metadata_json))

    @property
    def counts_toward_note_contributions(self) -> bool:
        return self.note_id is not None and self.action_type in NOTE_CONTRIBUTION_ACTIONS

    def to_record(self) -> Dict[str, Any]:
        return {
            "actor_user_id": self.actor_user_id,
            "workspace_id": self.workspace_id,
            "note_id": self.note_id,
            "target_user_id": self.target_user_id,
            "action_type": self.action_type.value,
            "entity_type": self.entity_type.value,
            "entity_id": self.entity_id,
            "metadata_json": dict(self.metadata_json or {}),
            "request_id": self.request_id,
            "session_id": self.session_id,
            "source": self.source,
            "ip_address": self.ip_address,
            "user_agent": self.user_agent,
        }


def stage_audit_event(db: AsyncSession, payload: AuditEventPayload) -> AuditEventPayload:
    """Stage one audit event for post-commit persistence."""

    session_info = get_session_info(db)
    pending = session_info.setdefault(PENDING_AUDIT_EVENTS_KEY, [])
    pending.append(payload)
    return payload


def pop_pending_audit_events(session_info: Mapping[str, Any]) -> list[AuditEventPayload]:
    raw_pending = session_info.get(PENDING_AUDIT_EVENTS_KEY) or []
    if not isinstance(raw_pending, list):
        return []
    return list(raw_pending)


def clear_pending_audit_events(session_info: Dict[str, Any]) -> None:
    session_info.pop(PENDING_AUDIT_EVENTS_KEY, None)


async def _schedule_note_contribution_refresh_if_needed(events: Sequence[AuditEventPayload]) -> None:
    if not any(event.counts_toward_note_contributions for event in events):
        return

    try:
        from app.services.note_contributions import schedule_note_contributions_refresh

        schedule_note_contributions_refresh()
    except Exception:
        logger.exception("Failed to schedule note_contributions refresh after audit persistence")


async def _persist_audit_events_async(events: Sequence[AuditEventPayload]) -> None:
    if not events:
        return

    rows = [event.to_record() for event in events]
    async with AsyncSessionLocal() as db:
        try:
            await db.execute(insert(AuditEvent), rows)
            await db.commit()
        except Exception:
            await db.rollback()
            logger.exception("Audit persistence failed for %s queued events", len(events))
            return

    await _schedule_note_contribution_refresh_if_needed(events)


def _persist_audit_events_sync(events: Sequence[AuditEventPayload]) -> None:
    if not events:
        return

    rows = [event.to_record() for event in events]
    db = SyncSessionLocal()
    try:
        db.execute(insert(AuditEvent), rows)
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Synchronous audit persistence failed for %s queued events", len(events))
        return
    finally:
        db.close()

    try:
        from app.services.note_contributions import schedule_note_contributions_refresh

        schedule_note_contributions_refresh()
    except Exception:
        logger.exception("Failed to schedule note_contributions refresh after sync audit persistence")


def dispatch_pending_audit_events(events: Sequence[AuditEventPayload]) -> None:
    """Persist queued audit events outside the primary mutation transaction."""

    queued_events = list(events)
    if not queued_events:
        return

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        _persist_audit_events_sync(queued_events)
        return

    loop.create_task(_persist_audit_events_async(queued_events))


async def log_audit_event(
    db: AsyncSession,
    action: AuditAction | str,
    user_id: Optional[UUID] = None,
    workspace_id: Optional[UUID] = None,
    entity_type: Optional[EntityType | str] = None,
    entity_id: Optional[UUID | str] = None,
    metadata: Optional[Mapping[str, Any]] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    *,
    actor_user_id: Optional[UUID] = None,
    note_id: Optional[UUID] = None,
    target_user_id: Optional[UUID] = None,
    request_id: Optional[str] = None,
    session_id: Optional[str] = None,
    source: Optional[str] = None,
) -> AuditEventPayload:
    """Queue one audit event for post-commit persistence.

    The public API remains async for compatibility with existing call sites,
    but this function intentionally performs no direct write. Events are staged
    on the request transaction and inserted after the owning transaction
    commits, so domain correctness does not depend on audit latency.
    """

    payload = AuditEventPayload(
        action_type=_coerce_action(action),
        entity_type=_coerce_entity_type(entity_type),
        entity_id=_stringify_entity_id(entity_id),
        actor_user_id=actor_user_id or user_id,
        workspace_id=workspace_id,
        note_id=note_id,
        target_user_id=target_user_id,
        metadata_json=_copy_metadata(metadata),
        request_id=request_id,
        session_id=session_id,
        source=source,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return stage_audit_event(db, payload)


async def get_audit_logs(
    db: AsyncSession,
    workspace_id: Optional[UUID] = None,
    user_id: Optional[UUID] = None,
    action: Optional[AuditAction | str] = None,
    entity_type: Optional[EntityType | str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    limit: int = 100,
    offset: int = 0,
) -> List[AuditEvent]:
    """Query immutable audit events with common filters."""

    query = select(AuditEvent)

    if workspace_id:
        query = query.where(AuditEvent.workspace_id == workspace_id)

    if user_id:
        query = query.where(AuditEvent.actor_user_id == user_id)

    if action:
        query = query.where(AuditEvent.action_type == _coerce_action(action).value)

    if entity_type:
        query = query.where(AuditEvent.entity_type == _coerce_entity_type(entity_type).value)

    if start_date:
        query = query.where(AuditEvent.created_at >= start_date)

    if end_date:
        query = query.where(AuditEvent.created_at <= end_date)

    query = query.order_by(desc(AuditEvent.created_at)).offset(offset).limit(limit)
    result = await db.execute(query)
    return list(result.scalars().all())


async def get_recent_activity(
    db: AsyncSession,
    workspace_id: UUID,
    limit: int = 20,
) -> List[AuditEvent]:
    return await get_audit_logs(
        db=db,
        workspace_id=workspace_id,
        limit=limit,
    )


async def count_audit_logs(
    db: AsyncSession,
    workspace_id: Optional[UUID] = None,
    user_id: Optional[UUID] = None,
    action: Optional[AuditAction | str] = None,
    entity_type: Optional[EntityType | str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
) -> int:
    """Return the number of immutable audit events matching the supplied filters."""

    query = select(func.count()).select_from(AuditEvent)

    if workspace_id:
        query = query.where(AuditEvent.workspace_id == workspace_id)

    if user_id:
        query = query.where(AuditEvent.actor_user_id == user_id)

    if action:
        query = query.where(AuditEvent.action_type == _coerce_action(action).value)

    if entity_type:
        query = query.where(AuditEvent.entity_type == _coerce_entity_type(entity_type).value)

    if start_date:
        query = query.where(AuditEvent.created_at >= start_date)

    if end_date:
        query = query.where(AuditEvent.created_at <= end_date)

    result = await db.execute(query)
    return int(result.scalar() or 0)


class AuditLogger:
    """Ergonomic request-aware audit logger for route handlers."""

    def __init__(self, db: AsyncSession, user_id: Optional[UUID] = None):
        self.db = db
        self.user_id = user_id
        self.request_context = AuditRequestContext()

    def with_request(self, request, *, source: Optional[str] = None, session_id: Optional[str] = None) -> "AuditLogger":
        self.request_context = AuditRequestContext.from_request(
            request,
            source=source,
            session_id=session_id,
        )
        return self

    async def log(
        self,
        action: AuditAction | str,
        workspace_id: Optional[UUID] = None,
        entity_type: Optional[EntityType | str] = None,
        entity_id: Optional[UUID | str] = None,
        metadata: Optional[Mapping[str, Any]] = None,
        *,
        note_id: Optional[UUID] = None,
        target_user_id: Optional[UUID] = None,
        actor_user_id: Optional[UUID] = None,
        session_id: Optional[str] = None,
        source: Optional[str] = None,
    ) -> AuditEventPayload:
        request_context = self.request_context
        return await log_audit_event(
            db=self.db,
            action=action,
            user_id=actor_user_id or self.user_id,
            workspace_id=workspace_id,
            entity_type=entity_type,
            entity_id=entity_id,
            metadata=metadata,
            ip_address=request_context.ip_address,
            user_agent=request_context.user_agent,
            note_id=note_id,
            target_user_id=target_user_id,
            request_id=request_context.request_id,
            session_id=session_id or request_context.session_id,
            source=source or request_context.source,
        )

    async def log_upload(
        self,
        workspace_id: UUID,
        document_id: UUID,
        filename: str,
    ) -> AuditEventPayload:
        return await self.log(
            action=AuditAction.DOCUMENT_UPLOADED,
            workspace_id=workspace_id,
            entity_type=EntityType.DOCUMENT,
            entity_id=document_id,
            metadata={"filename": filename},
        )

    async def log_delete(
        self,
        workspace_id: UUID,
        document_id: UUID,
        title: str,
    ) -> AuditEventPayload:
        return await self.log(
            action=AuditAction.DOCUMENT_DELETED,
            workspace_id=workspace_id,
            entity_type=EntityType.DOCUMENT,
            entity_id=document_id,
            metadata={"title": title},
        )

    async def log_query(
        self,
        workspace_id: UUID,
        query: str,
        result_count: int,
    ) -> AuditEventPayload:
        return await self.log(
            action=AuditAction.QUERY_EXECUTED,
            workspace_id=workspace_id,
            entity_type=EntityType.QUERY,
            metadata={
                "query": query,
                "result_count": result_count,
            },
        )

    async def log_verification(
        self,
        workspace_id: UUID,
        answer_id: UUID,
        verified: bool,
        comment: Optional[str] = None,
    ) -> AuditEventPayload:
        return await self.log(
            action=AuditAction.ANSWER_VERIFIED if verified else AuditAction.ANSWER_REJECTED,
            workspace_id=workspace_id,
            entity_type=EntityType.ANSWER,
            entity_id=answer_id,
            metadata={
                "verified": verified,
                "comment": comment,
            },
        )


class AuditShortcutLibrary:
    """Typed shortcut methods for high-value domain mutations."""

    async def note_created(
        self,
        db: AsyncSession,
        *,
        actor_user_id: UUID,
        workspace_id: Optional[UUID],
        note_id: UUID,
        metadata: Optional[Mapping[str, Any]] = None,
        context: Optional[AuditRequestContext] = None,
    ) -> AuditEventPayload:
        return await self._log(
            db,
            action=AuditAction.NOTE_CREATED,
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            note_id=note_id,
            entity_type=EntityType.NOTE,
            entity_id=note_id,
            metadata=metadata,
            context=context,
        )

    async def note_updated(
        self,
        db: AsyncSession,
        *,
        actor_user_id: UUID,
        workspace_id: Optional[UUID],
        note_id: UUID,
        metadata: Optional[Mapping[str, Any]] = None,
        context: Optional[AuditRequestContext] = None,
    ) -> AuditEventPayload:
        return await self._log(
            db,
            action=AuditAction.NOTE_UPDATED,
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            note_id=note_id,
            entity_type=EntityType.NOTE,
            entity_id=note_id,
            metadata=metadata,
            context=context,
        )

    async def note_restored(
        self,
        db: AsyncSession,
        *,
        actor_user_id: UUID,
        workspace_id: Optional[UUID],
        note_id: UUID,
        metadata: Optional[Mapping[str, Any]] = None,
        context: Optional[AuditRequestContext] = None,
    ) -> AuditEventPayload:
        return await self._log(
            db,
            action=AuditAction.NOTE_RESTORED,
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            note_id=note_id,
            entity_type=EntityType.NOTE,
            entity_id=note_id,
            metadata=metadata,
            context=context,
        )

    async def note_collaboration_synced(
        self,
        db: AsyncSession,
        *,
        actor_user_id: UUID,
        workspace_id: Optional[UUID],
        note_id: UUID,
        metadata: Optional[Mapping[str, Any]] = None,
        context: Optional[AuditRequestContext] = None,
    ) -> AuditEventPayload:
        return await self._log(
            db,
            action=AuditAction.NOTE_COLLABORATION_SYNCED,
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            note_id=note_id,
            entity_type=EntityType.NOTE,
            entity_id=note_id,
            metadata=metadata,
            context=context,
        )

    async def note_deleted(
        self,
        db: AsyncSession,
        *,
        actor_user_id: UUID,
        workspace_id: Optional[UUID],
        note_id: UUID,
        metadata: Optional[Mapping[str, Any]] = None,
        context: Optional[AuditRequestContext] = None,
    ) -> AuditEventPayload:
        return await self._log(
            db,
            action=AuditAction.NOTE_DELETED,
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            note_id=note_id,
            entity_type=EntityType.NOTE,
            entity_id=note_id,
            metadata=metadata,
            context=context,
        )

    async def member_invited(
        self,
        db: AsyncSession,
        *,
        actor_user_id: UUID,
        workspace_id: UUID,
        target_user_id: Optional[UUID],
        metadata: Optional[Mapping[str, Any]] = None,
        context: Optional[AuditRequestContext] = None,
    ) -> AuditEventPayload:
        return await self._log(
            db,
            action=AuditAction.MEMBER_INVITED,
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            target_user_id=target_user_id,
            entity_type=EntityType.USER,
            entity_id=target_user_id,
            metadata=metadata,
            context=context,
        )

    async def member_removed(
        self,
        db: AsyncSession,
        *,
        actor_user_id: UUID,
        workspace_id: UUID,
        target_user_id: UUID,
        metadata: Optional[Mapping[str, Any]] = None,
        context: Optional[AuditRequestContext] = None,
    ) -> AuditEventPayload:
        return await self._log(
            db,
            action=AuditAction.MEMBER_REMOVED,
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            target_user_id=target_user_id,
            entity_type=EntityType.USER,
            entity_id=target_user_id,
            metadata=metadata,
            context=context,
        )

    async def member_role_changed(
        self,
        db: AsyncSession,
        *,
        actor_user_id: UUID,
        workspace_id: UUID,
        target_user_id: UUID,
        metadata: Optional[Mapping[str, Any]] = None,
        context: Optional[AuditRequestContext] = None,
    ) -> AuditEventPayload:
        return await self._log(
            db,
            action=AuditAction.MEMBER_ROLE_CHANGED,
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            target_user_id=target_user_id,
            entity_type=EntityType.USER,
            entity_id=target_user_id,
            metadata=metadata,
            context=context,
        )

    async def note_collaborator_invited(
        self,
        db: AsyncSession,
        *,
        actor_user_id: UUID,
        workspace_id: Optional[UUID],
        note_id: UUID,
        target_user_id: Optional[UUID],
        invite_id: Optional[UUID],
        metadata: Optional[Mapping[str, Any]] = None,
        context: Optional[AuditRequestContext] = None,
    ) -> AuditEventPayload:
        return await self._log(
            db,
            action=AuditAction.NOTE_COLLABORATOR_INVITED,
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            note_id=note_id,
            target_user_id=target_user_id,
            entity_type=EntityType.NOTE_INVITE,
            entity_id=invite_id or note_id,
            metadata=metadata,
            context=context,
        )

    async def note_collaborator_role_changed(
        self,
        db: AsyncSession,
        *,
        actor_user_id: UUID,
        workspace_id: Optional[UUID],
        note_id: UUID,
        target_user_id: UUID,
        collaborator_id: Optional[UUID],
        metadata: Optional[Mapping[str, Any]] = None,
        context: Optional[AuditRequestContext] = None,
    ) -> AuditEventPayload:
        return await self._log(
            db,
            action=AuditAction.NOTE_COLLABORATOR_ROLE_CHANGED,
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            note_id=note_id,
            target_user_id=target_user_id,
            entity_type=EntityType.NOTE_COLLABORATOR,
            entity_id=collaborator_id or target_user_id,
            metadata=metadata,
            context=context,
        )

    async def note_collaborator_invite_accepted(
        self,
        db: AsyncSession,
        *,
        actor_user_id: UUID,
        workspace_id: Optional[UUID],
        note_id: UUID,
        invite_id: UUID,
        target_user_id: UUID,
        metadata: Optional[Mapping[str, Any]] = None,
        context: Optional[AuditRequestContext] = None,
    ) -> AuditEventPayload:
        return await self._log(
            db,
            action=AuditAction.NOTE_COLLABORATOR_INVITE_ACCEPTED,
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            note_id=note_id,
            target_user_id=target_user_id,
            entity_type=EntityType.NOTE_INVITE,
            entity_id=invite_id,
            metadata=metadata,
            context=context,
        )

    async def note_collaborator_invite_revoked(
        self,
        db: AsyncSession,
        *,
        actor_user_id: UUID,
        workspace_id: Optional[UUID],
        note_id: UUID,
        invite_id: UUID,
        target_user_id: Optional[UUID],
        metadata: Optional[Mapping[str, Any]] = None,
        context: Optional[AuditRequestContext] = None,
    ) -> AuditEventPayload:
        return await self._log(
            db,
            action=AuditAction.NOTE_COLLABORATOR_INVITE_REVOKED,
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            note_id=note_id,
            target_user_id=target_user_id,
            entity_type=EntityType.NOTE_INVITE,
            entity_id=invite_id,
            metadata=metadata,
            context=context,
        )

    async def session_started(
        self,
        db: AsyncSession,
        *,
        actor_user_id: UUID,
        workspace_id: UUID,
        thinking_session_id: UUID,
        note_id: Optional[UUID],
        metadata: Optional[Mapping[str, Any]] = None,
        context: Optional[AuditRequestContext] = None,
    ) -> AuditEventPayload:
        return await self._log(
            db,
            action=AuditAction.THINKING_SESSION_STARTED,
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            note_id=note_id,
            entity_type=EntityType.THINKING_SESSION,
            entity_id=thinking_session_id,
            metadata=metadata,
            context=context,
            session_id=str(thinking_session_id),
        )

    async def session_phase_transitioned(
        self,
        db: AsyncSession,
        *,
        actor_user_id: UUID,
        workspace_id: UUID,
        thinking_session_id: UUID,
        note_id: Optional[UUID],
        metadata: Optional[Mapping[str, Any]] = None,
        context: Optional[AuditRequestContext] = None,
    ) -> AuditEventPayload:
        return await self._log(
            db,
            action=AuditAction.THINKING_SESSION_PHASE_TRANSITIONED,
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            note_id=note_id,
            entity_type=EntityType.THINKING_SESSION,
            entity_id=thinking_session_id,
            metadata=metadata,
            context=context,
            session_id=str(thinking_session_id),
        )

    async def contribution_submitted(
        self,
        db: AsyncSession,
        *,
        actor_user_id: UUID,
        workspace_id: UUID,
        thinking_session_id: UUID,
        note_id: Optional[UUID],
        contribution_id: UUID,
        metadata: Optional[Mapping[str, Any]] = None,
        context: Optional[AuditRequestContext] = None,
    ) -> AuditEventPayload:
        return await self._log(
            db,
            action=AuditAction.CONTRIBUTION_SUBMITTED,
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            note_id=note_id,
            entity_type=EntityType.THINKING_CONTRIBUTION,
            entity_id=contribution_id,
            metadata=metadata,
            context=context,
            session_id=str(thinking_session_id),
        )

    async def vote_cast(
        self,
        db: AsyncSession,
        *,
        actor_user_id: UUID,
        workspace_id: UUID,
        thinking_session_id: UUID,
        note_id: Optional[UUID],
        contribution_id: UUID,
        metadata: Optional[Mapping[str, Any]] = None,
        context: Optional[AuditRequestContext] = None,
    ) -> AuditEventPayload:
        return await self._log(
            db,
            action=AuditAction.VOTE_CAST,
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            note_id=note_id,
            entity_type=EntityType.THINKING_VOTE,
            entity_id=contribution_id,
            metadata=metadata,
            context=context,
            session_id=str(thinking_session_id),
        )

    async def vote_removed(
        self,
        db: AsyncSession,
        *,
        actor_user_id: UUID,
        workspace_id: UUID,
        thinking_session_id: UUID,
        note_id: Optional[UUID],
        contribution_id: UUID,
        metadata: Optional[Mapping[str, Any]] = None,
        context: Optional[AuditRequestContext] = None,
    ) -> AuditEventPayload:
        return await self._log(
            db,
            action=AuditAction.VOTE_REMOVED,
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            note_id=note_id,
            entity_type=EntityType.THINKING_VOTE,
            entity_id=contribution_id,
            metadata=metadata,
            context=context,
            session_id=str(thinking_session_id),
        )

    async def session_refinement_updated(
        self,
        db: AsyncSession,
        *,
        actor_user_id: UUID,
        workspace_id: UUID,
        thinking_session_id: UUID,
        note_id: Optional[UUID],
        metadata: Optional[Mapping[str, Any]] = None,
        context: Optional[AuditRequestContext] = None,
    ) -> AuditEventPayload:
        return await self._log(
            db,
            action=AuditAction.SESSION_REFINEMENT_UPDATED,
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            note_id=note_id,
            entity_type=EntityType.THINKING_SESSION,
            entity_id=thinking_session_id,
            metadata=metadata,
            context=context,
            session_id=str(thinking_session_id),
        )

    async def session_synthesis_completed(
        self,
        db: AsyncSession,
        *,
        actor_user_id: Optional[UUID],
        workspace_id: UUID,
        thinking_session_id: UUID,
        note_id: Optional[UUID],
        run_id: UUID,
        metadata: Optional[Mapping[str, Any]] = None,
        context: Optional[AuditRequestContext] = None,
    ) -> AuditEventPayload:
        return await self._log(
            db,
            action=AuditAction.THINKING_SESSION_SYNTHESIS_COMPLETED,
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            note_id=note_id,
            entity_type=EntityType.THINKING_SYNTHESIS_RUN,
            entity_id=run_id,
            metadata=metadata,
            context=context,
            session_id=str(thinking_session_id),
        )

    async def session_synthesis_failed(
        self,
        db: AsyncSession,
        *,
        actor_user_id: Optional[UUID],
        workspace_id: UUID,
        thinking_session_id: UUID,
        note_id: Optional[UUID],
        run_id: UUID,
        metadata: Optional[Mapping[str, Any]] = None,
        context: Optional[AuditRequestContext] = None,
    ) -> AuditEventPayload:
        return await self._log(
            db,
            action=AuditAction.THINKING_SESSION_SYNTHESIS_FAILED,
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            note_id=note_id,
            entity_type=EntityType.THINKING_SYNTHESIS_RUN,
            entity_id=run_id,
            metadata=metadata,
            context=context,
            session_id=str(thinking_session_id),
        )

    async def _log(
        self,
        db: AsyncSession,
        *,
        action: AuditAction,
        actor_user_id: Optional[UUID],
        workspace_id: Optional[UUID],
        entity_type: EntityType,
        entity_id: Optional[UUID | str],
        metadata: Optional[Mapping[str, Any]],
        context: Optional[AuditRequestContext],
        note_id: Optional[UUID] = None,
        target_user_id: Optional[UUID] = None,
        session_id: Optional[str] = None,
    ) -> AuditEventPayload:
        return await log_audit_event(
            db=db,
            action=action,
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            note_id=note_id,
            target_user_id=target_user_id,
            entity_type=entity_type,
            entity_id=entity_id,
            metadata=metadata,
            request_id=context.request_id if context else None,
            session_id=session_id or (context.session_id if context else None),
            source=context.source if context else None,
            ip_address=context.ip_address if context else None,
            user_agent=context.user_agent if context else None,
        )


audit = AuditShortcutLibrary()
