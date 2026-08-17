"""Durable state helpers for the note enrichment pipeline."""

from __future__ import annotations

from datetime import datetime, timezone
from time import monotonic
from typing import Any, Mapping, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import NoteCaptureEvent, NoteEnrichmentStep


STEP_CLASSIFICATION = "classification"
STEP_EMBEDDING = "embedding"
STEP_SUMMARY = "summary"
STEP_CONNECTION_SUGGESTIONS = "connection_suggestions"
STEP_DECAY_CLASSIFICATION = "decay_classification"

ALL_NOTE_ENRICHMENT_STEPS = (
    STEP_CLASSIFICATION,
    STEP_EMBEDDING,
    STEP_SUMMARY,
    STEP_CONNECTION_SUGGESTIONS,
    STEP_DECAY_CLASSIFICATION,
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def monotonic_ms() -> int:
    return int(monotonic() * 1000)


def get_or_create_step(
    db: Session,
    *,
    note_id: UUID,
    step: str,
    capture_event_id: Optional[UUID] = None,
    correlation_id: Optional[str] = None,
) -> NoteEnrichmentStep:
    row = db.execute(
        select(NoteEnrichmentStep).where(
            NoteEnrichmentStep.note_id == note_id,
            NoteEnrichmentStep.step == step,
        )
    ).scalar_one_or_none()
    if row is None:
        row = NoteEnrichmentStep(
            note_id=note_id,
            step=step,
            capture_event_id=capture_event_id,
            correlation_id=correlation_id,
            status="pending",
            result_metadata={},
        )
        db.add(row)
        db.flush()
    else:
        if capture_event_id and not row.capture_event_id:
            row.capture_event_id = capture_event_id
        if correlation_id and not row.correlation_id:
            row.correlation_id = correlation_id
    return row


def mark_step_started(
    db: Session,
    *,
    note_id: UUID,
    step: str,
    capture_event_id: Optional[UUID] = None,
    correlation_id: Optional[str] = None,
    task_id: Optional[str] = None,
) -> NoteEnrichmentStep:
    row = get_or_create_step(
        db,
        note_id=note_id,
        step=step,
        capture_event_id=capture_event_id,
        correlation_id=correlation_id,
    )
    row.status = "running"
    row.attempts = int(row.attempts or 0) + 1
    row.task_id = task_id or row.task_id
    row.error = None
    row.started_at = utcnow()
    row.failed_at = None
    db.flush()
    return row


def mark_step_completed(
    db: Session,
    *,
    note_id: UUID,
    step: str,
    metadata: Optional[Mapping[str, Any]] = None,
    capture_event_id: Optional[UUID] = None,
    correlation_id: Optional[str] = None,
    started_at_ms: Optional[int] = None,
) -> NoteEnrichmentStep:
    row = get_or_create_step(
        db,
        note_id=note_id,
        step=step,
        capture_event_id=capture_event_id,
        correlation_id=correlation_id,
    )
    row.status = "completed"
    row.error = None
    row.result_metadata = dict(metadata or {})
    row.completed_at = utcnow()
    row.failed_at = None
    if started_at_ms is not None:
        row.duration_ms = max(0, monotonic_ms() - started_at_ms)
    db.flush()
    _refresh_capture_event_status(db, capture_event_id=capture_event_id)
    return row


def mark_step_failed(
    db: Session,
    *,
    note_id: UUID,
    step: str,
    error: str,
    metadata: Optional[Mapping[str, Any]] = None,
    capture_event_id: Optional[UUID] = None,
    correlation_id: Optional[str] = None,
    started_at_ms: Optional[int] = None,
) -> NoteEnrichmentStep:
    row = get_or_create_step(
        db,
        note_id=note_id,
        step=step,
        capture_event_id=capture_event_id,
        correlation_id=correlation_id,
    )
    row.status = "failed"
    row.error = error[:4000]
    row.result_metadata = dict(metadata or {})
    row.failed_at = utcnow()
    if started_at_ms is not None:
        row.duration_ms = max(0, monotonic_ms() - started_at_ms)
    db.flush()
    _refresh_capture_event_status(db, capture_event_id=capture_event_id)
    return row


def step_is_completed(db: Session, *, note_id: UUID, step: str) -> bool:
    row = db.execute(
        select(NoteEnrichmentStep.status).where(
            NoteEnrichmentStep.note_id == note_id,
            NoteEnrichmentStep.step == step,
        )
    ).scalar_one_or_none()
    return row == "completed"


def _refresh_capture_event_status(db: Session, *, capture_event_id: Optional[UUID]) -> None:
    if not capture_event_id:
        return
    event = db.execute(
        select(NoteCaptureEvent).where(NoteCaptureEvent.id == capture_event_id)
    ).scalar_one_or_none()
    if event is None or event.note_id is None:
        return
    rows = db.execute(
        select(NoteEnrichmentStep.step, NoteEnrichmentStep.status).where(
            NoteEnrichmentStep.note_id == event.note_id,
            NoteEnrichmentStep.step.in_(ALL_NOTE_ENRICHMENT_STEPS),
        )
    ).all()
    statuses = {step: status for step, status in rows}
    if len(statuses) < len(ALL_NOTE_ENRICHMENT_STEPS):
        return
    if all(status == "completed" for status in statuses.values()):
        event.status = "enrichment_completed"
        event.error = None
    elif all(status in {"completed", "failed"} for status in statuses.values()) and any(
        status == "failed" for status in statuses.values()
    ):
        event.status = "enrichment_partial_failure"
        event.error = "One or more enrichment steps failed"
    event.updated_at = utcnow()
    db.flush()
