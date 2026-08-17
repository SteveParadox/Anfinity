"""Single authoritative note creation service and capture event contract."""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Mapping, Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditRequestContext, audit
from app.database.models import Note, NoteCaptureEvent, NoteVersion
from app.database.session import get_session_info

logger = logging.getLogger(__name__)

PENDING_NOTE_CAPTURE_EVENTS_KEY = "pending_note_capture_event_ids"
NOTE_TYPES = {"note", "web-clip", "document", "voice", "ai-generated"}
CAPTURE_PATHS = {
    "api.notes",
    "automation.create_note",
    "integration.gmail",
    "integration.calendar",
    "integration.notion",
    "integration.shared",
}


class NoteCaptureIdempotencyConflict(ValueError):
    """Raised when an idempotency key is reused outside its original scope."""


class NoteCapturedEvent(BaseModel):
    """Typed contract for every note capture before persistence."""

    workspace_id: UUID
    user_id: UUID
    title: str = Field(..., min_length=1, max_length=500)
    content: str = Field(..., min_length=1)
    capture_source: str = Field(..., min_length=1, max_length=100)
    capture_path: str = Field(..., min_length=1, max_length=100)
    idempotency_key: Optional[str] = Field(default=None, max_length=255)
    correlation_id: Optional[str] = Field(default=None, max_length=255)
    tags: list[str] = Field(default_factory=list)
    note_type: str = "note"
    source_url: Optional[str] = Field(default=None, max_length=1000)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("title")
    @classmethod
    def _normalize_title(cls, value: str) -> str:
        return value.strip()[:500] or "Untitled note"

    @field_validator("capture_source", "capture_path", "note_type")
    @classmethod
    def _strip_string(cls, value: str) -> str:
        return value.strip()

    @field_validator("capture_path")
    @classmethod
    def _validate_capture_path(cls, value: str) -> str:
        if value not in CAPTURE_PATHS:
            raise ValueError(f"Unsupported capture_path: {value}")
        return value

    @field_validator("note_type")
    @classmethod
    def _validate_note_type(cls, value: str) -> str:
        if value not in NOTE_TYPES:
            raise ValueError(f"Unsupported note_type: {value}")
        return value


class CreateNoteResult(BaseModel):
    note: Note
    capture_event: NoteCaptureEvent
    created: bool

    class Config:
        arbitrary_types_allowed = True


def calculate_word_count(content: str) -> int:
    return len((content or "").strip().split())


def content_hash(title: str, content: str) -> str:
    return hashlib.sha256(f"{title}\n{content}".encode("utf-8")).hexdigest()


def build_default_idempotency_key(event: NoteCapturedEvent) -> str:
    digest = content_hash(event.title, event.content)
    return ":".join(
        [
            "note-captured",
            str(event.workspace_id),
            str(event.user_id),
            event.capture_path,
            event.capture_source,
            digest,
        ]
    )[:255]


def normalize_note_captured_event(event: NoteCapturedEvent) -> NoteCapturedEvent:
    if event.idempotency_key and event.correlation_id:
        return event
    idempotency_key = event.idempotency_key or build_default_idempotency_key(event)
    correlation_id = event.correlation_id or idempotency_key
    return event.model_copy(update={"idempotency_key": idempotency_key, "correlation_id": correlation_id})


def ensure_idempotency_scope(existing_event: NoteCaptureEvent, event: NoteCapturedEvent) -> None:
    if existing_event.workspace_id == event.workspace_id and existing_event.user_id == event.user_id:
        return
    raise NoteCaptureIdempotencyConflict(
        "Idempotency key was already used for a different workspace or user"
    )


def stage_note_capture_event_for_pipeline(db: AsyncSession, capture_event_id: UUID) -> None:
    session_info = get_session_info(db)
    pending = session_info.setdefault(PENDING_NOTE_CAPTURE_EVENTS_KEY, [])
    event_id = str(capture_event_id)
    if event_id not in pending:
        pending.append(event_id)


async def get_latest_note_version(db: AsyncSession, note_id: UUID) -> Optional[NoteVersion]:
    version_result = await db.execute(
        select(NoteVersion)
        .where(NoteVersion.note_id == note_id)
        .order_by(NoteVersion.version_number.desc())
        .limit(1)
    )
    return version_result.scalar_one_or_none()


async def create_note_version_snapshot(
    db: AsyncSession,
    *,
    note: Note,
    user_id: UUID,
    change_reason: str,
    extra_metadata: Optional[Mapping[str, Any]] = None,
) -> Optional[NoteVersion]:
    latest_version = await get_latest_note_version(db, note.id)
    if latest_version is not None:
        comparable_fields = (
            latest_version.title == note.title,
            latest_version.content == note.content,
            list(latest_version.tags or []) == list(note.tags or []),
            list(latest_version.connections or []) == list(note.connections or []),
            latest_version.note_type == note.note_type,
            latest_version.source_url == note.source_url,
        )
        if all(comparable_fields):
            return None

    version = NoteVersion(
        note_id=note.id,
        workspace_id=note.workspace_id,
        user_id=user_id,
        version_number=(latest_version.version_number if latest_version else 0) + 1,
        change_reason=change_reason,
        title=note.title,
        content=note.content,
        summary=note.summary,
        tags=list(note.tags or []),
        connections=[str(connection_id) for connection_id in (note.connections or [])],
        note_type=note.note_type,
        source_url=note.source_url,
        word_count=note.word_count or calculate_word_count(note.content),
        diff_segments=[],
        version_metadata={"trigger": "create_note", **dict(extra_metadata or {})},
    )
    db.add(version)
    await db.flush()
    return version


async def create_note(
    db: AsyncSession,
    event: NoteCapturedEvent,
    *,
    audit_context: Optional[AuditRequestContext] = None,
    enqueue_pipeline: bool = True,
) -> CreateNoteResult:
    """Create or load one note from the durable note/captured contract."""
    event = normalize_note_captured_event(event)
    digest = content_hash(event.title, event.content)

    existing_event = (
        await db.execute(
            select(NoteCaptureEvent)
            .where(NoteCaptureEvent.idempotency_key == event.idempotency_key)
            .with_for_update(of=NoteCaptureEvent)
        )
    ).scalar_one_or_none()
    if existing_event is not None:
        ensure_idempotency_scope(existing_event, event)
        if existing_event.note_id:
            note = (
                await db.execute(select(Note).where(Note.id == existing_event.note_id))
            ).scalar_one()
            if enqueue_pipeline:
                stage_note_capture_event_for_pipeline(db, existing_event.id)
            return CreateNoteResult(note=note, capture_event=existing_event, created=False)
        capture_event = existing_event
    else:
        capture_event = NoteCaptureEvent(
            id=uuid.uuid4(),
            idempotency_key=event.idempotency_key,
            correlation_id=event.correlation_id,
            capture_source=event.capture_source,
            capture_path=event.capture_path,
            workspace_id=event.workspace_id,
            user_id=event.user_id,
            title=event.title,
            content_hash=digest,
            payload_metadata=dict(event.metadata or {}),
            status="received",
        )
        try:
            async with db.begin_nested():
                db.add(capture_event)
                await db.flush()
        except IntegrityError:
            logger.info(
                "note.capture.idempotency_race key=%s correlation_id=%s",
                event.idempotency_key,
                event.correlation_id,
            )
            existing_event = (
                await db.execute(
                    select(NoteCaptureEvent)
                    .where(NoteCaptureEvent.idempotency_key == event.idempotency_key)
                    .with_for_update(of=NoteCaptureEvent)
                )
            ).scalar_one_or_none()
            if existing_event is None:
                raise
            ensure_idempotency_scope(existing_event, event)
            if existing_event.note_id:
                note = (
                    await db.execute(select(Note).where(Note.id == existing_event.note_id))
                ).scalar_one()
                if enqueue_pipeline:
                    stage_note_capture_event_for_pipeline(db, existing_event.id)
                return CreateNoteResult(note=note, capture_event=existing_event, created=False)
            capture_event = existing_event

    new_note = Note(
        workspace_id=event.workspace_id,
        user_id=event.user_id,
        title=event.title,
        content=event.content,
        tags=list(dict.fromkeys(event.tags or [])),
        source_url=event.source_url,
        note_type=event.note_type,
        word_count=calculate_word_count(event.content),
        ai_generated=0,
        connections=[],
    )
    db.add(new_note)
    await db.flush()
    await db.refresh(new_note)

    capture_event.note_id = new_note.id
    capture_event.status = "note_created"
    capture_event.updated_at = datetime.now(timezone.utc)
    capture_event.payload_metadata = {
        **dict(capture_event.payload_metadata or {}),
        "note_type": new_note.note_type,
        "source_url": new_note.source_url,
        "initial_tags": list(new_note.tags or []),
    }

    created_version = await create_note_version_snapshot(
        db,
        note=new_note,
        user_id=event.user_id,
        change_reason="created",
        extra_metadata={
            "capture_event_id": str(capture_event.id),
            "capture_path": event.capture_path,
            "correlation_id": event.correlation_id,
        },
    )
    await audit.note_created(
        db,
        actor_user_id=event.user_id,
        workspace_id=event.workspace_id,
        note_id=new_note.id,
        metadata={
            "title": new_note.title,
            "note_type": new_note.note_type,
            "tag_count": len(new_note.tags or []),
            "word_count": new_note.word_count or 0,
            "version_id": str(created_version.id) if created_version is not None else None,
            "source": event.capture_path,
            "capture_event_id": str(capture_event.id),
            "correlation_id": event.correlation_id,
        },
        context=audit_context,
    )
    if enqueue_pipeline:
        stage_note_capture_event_for_pipeline(db, capture_event.id)

    logger.info(
        "note.capture.created note_id=%s event_id=%s source=%s path=%s correlation_id=%s",
        new_note.id,
        capture_event.id,
        event.capture_source,
        event.capture_path,
        event.correlation_id,
    )
    return CreateNoteResult(note=new_note, capture_event=capture_event, created=True)
