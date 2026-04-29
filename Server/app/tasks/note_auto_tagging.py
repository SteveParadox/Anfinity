"""Classification-first note enrichment pipeline."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from celery import group
from sqlalchemy import select

from app.celery_app import celery_app
from app.database.models import Note, NoteCaptureEvent, Workspace
from app.database.session import SyncSessionLocal
from app.services.note_auto_tagging import classify_decay, classify_note_tags
from app.services.note_enrichment_state import (
    STEP_CLASSIFICATION,
    STEP_CONNECTION_SUGGESTIONS,
    STEP_DECAY_CLASSIFICATION,
    STEP_EMBEDDING,
    STEP_SUMMARY,
    get_or_create_step,
    mark_step_completed,
    mark_step_failed,
    mark_step_started,
    monotonic_ms,
    step_is_completed,
)
from app.services.settings_preferences import resolved_workspace_settings

logger = logging.getLogger(__name__)


def _parse_uuid(value: str | None, field: str) -> UUID | None:
    try:
        return UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        logger.error("Invalid %s UUID: %r", field, value)
        return None


def _workspace_settings_for_note(db, workspace_id: UUID) -> dict[str, Any]:
    raw_settings = db.execute(
        select(Workspace.settings).where(Workspace.id == workspace_id)
    ).scalar_one_or_none() or {}
    return resolved_workspace_settings(raw_settings)


@celery_app.task(bind=True, max_retries=3, name="run_note_auto_tagging_pipeline", acks_late=True)
def run_note_auto_tagging_pipeline(self, capture_event_id: str) -> dict[str, Any]:
    """Run the durable note/captured pipeline.

    Ordering is explicit:
    1. load the capture event and created note
    2. classify tags synchronously first
    3. dispatch embedding, summary, connection suggestions, and decay classification
    """
    event_uuid = _parse_uuid(capture_event_id, "capture_event_id")
    if event_uuid is None:
        return {"status": "error", "message": "Invalid capture_event_id", "capture_event_id": capture_event_id}

    db = SyncSessionLocal()
    try:
        capture_event = db.execute(
            select(NoteCaptureEvent).where(NoteCaptureEvent.id == event_uuid)
        ).scalar_one_or_none()
        if capture_event is None:
            return {"status": "not_found", "capture_event_id": capture_event_id}
        if capture_event.note_id is None:
            capture_event.status = "failed"
            capture_event.error = "Capture event has no note_id"
            db.commit()
            return {"status": "failed", "capture_event_id": capture_event_id, "error": capture_event.error}

        note = db.execute(select(Note).where(Note.id == capture_event.note_id)).scalar_one_or_none()
        if note is None:
            capture_event.status = "failed"
            capture_event.error = "Created note is missing"
            db.commit()
            return {"status": "failed", "capture_event_id": capture_event_id, "error": capture_event.error}

        note_id = str(note.id)
        workspace_settings = _workspace_settings_for_note(db, note.workspace_id)
        correlation_id = capture_event.correlation_id
        capture_event.status = "classification_running"
        capture_event.updated_at = datetime.now(timezone.utc)
        db.commit()

    except Exception as exc:
        db.rollback()
        logger.error("Failed to load note auto-tagging event %s: %s", capture_event_id, exc, exc_info=True)
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))
        return {"status": "failed", "capture_event_id": capture_event_id, "error": str(exc)}
    finally:
        db.close()

    notes_settings = workspace_settings.get("notes", {})
    if notes_settings.get("auto_tagging_enabled", True):
        classification_result = classify_note_tags_task.apply(
            args=(note_id,),
            kwargs={"capture_event_id": capture_event_id, "correlation_id": correlation_id},
        )
        classification = classification_result.result
    else:
        db = SyncSessionLocal()
        started_at = monotonic_ms()
        try:
            mark_step_started(
                db,
                note_id=UUID(note_id),
                step=STEP_CLASSIFICATION,
                capture_event_id=event_uuid,
                correlation_id=correlation_id,
            )
            mark_step_completed(
                db,
                note_id=UUID(note_id),
                step=STEP_CLASSIFICATION,
                metadata={"skipped": True, "reason": "workspace_auto_tagging_disabled"},
                capture_event_id=event_uuid,
                correlation_id=correlation_id,
                started_at_ms=started_at,
            )
            db.commit()
        finally:
            db.close()
        classification = {"status": "skipped", "note_id": note_id, "reason": "workspace_auto_tagging_disabled"}

    classification_status = classification.get("status") if isinstance(classification, dict) else "failed"
    if classification_status not in {"success", "skipped"}:
        db = SyncSessionLocal()
        try:
            event = db.execute(select(NoteCaptureEvent).where(NoteCaptureEvent.id == event_uuid)).scalar_one_or_none()
            if event:
                event.status = "classification_failed"
                event.error = (
                    str(classification.get("error") or classification.get("message") or "classification failed")
                    if isinstance(classification, dict)
                    else str(classification)
                )
                event.updated_at = datetime.now(timezone.utc)
                db.commit()
        finally:
            db.close()
        return {
            "status": "classification_failed",
            "capture_event_id": capture_event_id,
            "note_id": note_id,
            "classification": classification,
        }

    try:
        from app.tasks.connection_suggestions import generate_connection_suggestions
        from app.tasks.note_embeddings import generate_note_embedding
        from app.tasks.note_summaries import generate_note_summary

        downstream_steps = [
            (STEP_EMBEDDING, generate_note_embedding.s(note_id, capture_event_id=capture_event_id, correlation_id=correlation_id), True),
            (
                STEP_SUMMARY,
                generate_note_summary.s(note_id, capture_event_id=capture_event_id, correlation_id=correlation_id),
                notes_settings.get("summary_generation_enabled", True),
            ),
            (
                STEP_CONNECTION_SUGGESTIONS,
                generate_connection_suggestions.s(note_id, capture_event_id=capture_event_id, correlation_id=correlation_id),
                notes_settings.get("connection_suggestions_enabled", True),
            ),
            (
                STEP_DECAY_CLASSIFICATION,
                classify_note_decay_task.s(note_id, capture_event_id=capture_event_id, correlation_id=correlation_id),
                notes_settings.get("decay_classification_enabled", True),
            ),
        ]

        db = SyncSessionLocal()
        try:
            event = db.execute(select(NoteCaptureEvent).where(NoteCaptureEvent.id == event_uuid)).scalar_one_or_none()
            if event:
                for step, _signature, enabled in downstream_steps:
                    get_or_create_step(
                        db,
                        note_id=UUID(note_id),
                        step=step,
                        capture_event_id=event_uuid,
                        correlation_id=correlation_id,
                    )
                    if not enabled:
                        started_at = monotonic_ms()
                        mark_step_started(
                            db,
                            note_id=UUID(note_id),
                            step=step,
                            capture_event_id=event_uuid,
                            correlation_id=correlation_id,
                        )
                        mark_step_completed(
                            db,
                            note_id=UUID(note_id),
                            step=step,
                            metadata={"skipped": True, "reason": f"workspace_{step}_disabled"},
                            capture_event_id=event_uuid,
                            correlation_id=correlation_id,
                            started_at_ms=started_at,
                        )
                event.status = "enrichment_dispatching"
                event.error = None
                event.updated_at = datetime.now(timezone.utc)
                db.commit()
        finally:
            db.close()

        enabled_signatures = [signature for _step, signature, enabled in downstream_steps if enabled]
        async_results = group(*enabled_signatures).apply_async() if enabled_signatures else None

        db = SyncSessionLocal()
        try:
            event = db.execute(select(NoteCaptureEvent).where(NoteCaptureEvent.id == event_uuid)).scalar_one_or_none()
            if event:
                if event.status == "enrichment_dispatching":
                    event.status = "enrichment_dispatched"
                event.error = None
                event.updated_at = datetime.now(timezone.utc)
                event.payload_metadata = {
                    **dict(event.payload_metadata or {}),
                    "parallel_group_id": async_results.id if async_results is not None else None,
                    "classification": classification,
                    "disabled_enrichment_steps": [
                        step for step, _signature, enabled in downstream_steps if not enabled
                    ],
                }
                db.commit()
        finally:
            db.close()

        logger.info(
            "note.pipeline.dispatched note_id=%s event_id=%s group_id=%s correlation_id=%s",
            note_id,
            capture_event_id,
            async_results.id if async_results is not None else None,
            correlation_id,
        )
        return {
            "status": "enrichment_dispatched",
            "capture_event_id": capture_event_id,
            "note_id": note_id,
            "classification": classification,
            "group_id": async_results.id if async_results is not None else None,
            "disabled_enrichment_steps": [
                step for step, _signature, enabled in downstream_steps if not enabled
            ],
        }
    except Exception as exc:
        logger.error("Failed to dispatch note enrichment group for event %s: %s", capture_event_id, exc, exc_info=True)
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))
        db = SyncSessionLocal()
        try:
            event = db.execute(select(NoteCaptureEvent).where(NoteCaptureEvent.id == event_uuid)).scalar_one_or_none()
            if event:
                event.status = "enrichment_dispatch_failed"
                event.error = str(exc)[:4000]
                event.updated_at = datetime.now(timezone.utc)
                db.commit()
        finally:
            db.close()
        return {"status": "failed", "capture_event_id": capture_event_id, "note_id": note_id, "error": str(exc)}


@celery_app.task(bind=True, max_retries=2, name="classify_note_tags", acks_late=True)
def classify_note_tags_task(
    self,
    note_id: str,
    capture_event_id: Optional[str] = None,
    correlation_id: Optional[str] = None,
) -> dict[str, Any]:
    note_uuid = _parse_uuid(note_id, "note_id")
    event_uuid = _parse_uuid(capture_event_id, "capture_event_id") if capture_event_id else None
    if note_uuid is None:
        return {"status": "error", "note_id": note_id, "message": "Invalid UUID"}

    db = SyncSessionLocal()
    started_at = monotonic_ms()
    try:
        if step_is_completed(db, note_id=note_uuid, step=STEP_CLASSIFICATION):
            return {"status": "skipped", "note_id": note_id, "reason": "classification already completed"}
        mark_step_started(
            db,
            note_id=note_uuid,
            step=STEP_CLASSIFICATION,
            capture_event_id=event_uuid,
            correlation_id=correlation_id,
            task_id=getattr(self.request, "id", None),
        )
        note = db.execute(select(Note).where(Note.id == note_uuid)).scalar_one_or_none()
        if note is None:
            mark_step_failed(
                db,
                note_id=note_uuid,
                step=STEP_CLASSIFICATION,
                error="Note does not exist",
                capture_event_id=event_uuid,
                correlation_id=correlation_id,
                started_at_ms=started_at,
            )
            db.commit()
            return {"status": "not_found", "note_id": note_id}

        result = classify_note_tags(note.title, note.content, note.tags or [])
        note.tags = list(result["tags"])
        note.updated_at = datetime.now(timezone.utc)
        mark_step_completed(
            db,
            note_id=note_uuid,
            step=STEP_CLASSIFICATION,
            metadata=result,
            capture_event_id=event_uuid,
            correlation_id=correlation_id,
            started_at_ms=started_at,
        )
        db.commit()
        return {"status": "success", "note_id": note_id, **result}
    except Exception as exc:
        db.rollback()
        try:
            mark_step_failed(
                db,
                note_id=note_uuid,
                step=STEP_CLASSIFICATION,
                error=str(exc),
                capture_event_id=event_uuid,
                correlation_id=correlation_id,
                started_at_ms=started_at,
            )
            db.commit()
        except Exception:
            db.rollback()
        logger.error("Failed to classify note tags for %s: %s", note_id, exc, exc_info=True)
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))
        return {"status": "failed", "note_id": note_id, "error": str(exc)}
    finally:
        db.close()


@celery_app.task(bind=True, max_retries=2, name="classify_note_decay", acks_late=True)
def classify_note_decay_task(
    self,
    note_id: str,
    capture_event_id: Optional[str] = None,
    correlation_id: Optional[str] = None,
) -> dict[str, Any]:
    note_uuid = _parse_uuid(note_id, "note_id")
    event_uuid = _parse_uuid(capture_event_id, "capture_event_id") if capture_event_id else None
    if note_uuid is None:
        return {"status": "error", "note_id": note_id, "message": "Invalid UUID"}

    db = SyncSessionLocal()
    started_at = monotonic_ms()
    try:
        if step_is_completed(db, note_id=note_uuid, step=STEP_DECAY_CLASSIFICATION):
            return {"status": "skipped", "note_id": note_id, "reason": "decay classification already completed"}
        mark_step_started(
            db,
            note_id=note_uuid,
            step=STEP_DECAY_CLASSIFICATION,
            capture_event_id=event_uuid,
            correlation_id=correlation_id,
            task_id=getattr(self.request, "id", None),
        )
        note = db.execute(select(Note).where(Note.id == note_uuid)).scalar_one_or_none()
        if note is None:
            mark_step_failed(
                db,
                note_id=note_uuid,
                step=STEP_DECAY_CLASSIFICATION,
                error="Note does not exist",
                capture_event_id=event_uuid,
                correlation_id=correlation_id,
                started_at_ms=started_at,
            )
            db.commit()
            return {"status": "not_found", "note_id": note_id}

        result = classify_decay(note.created_at, note.content, note.tags or [])
        mark_step_completed(
            db,
            note_id=note_uuid,
            step=STEP_DECAY_CLASSIFICATION,
            metadata=result,
            capture_event_id=event_uuid,
            correlation_id=correlation_id,
            started_at_ms=started_at,
        )
        db.commit()
        return {"status": "success", "note_id": note_id, **result}
    except Exception as exc:
        db.rollback()
        try:
            mark_step_failed(
                db,
                note_id=note_uuid,
                step=STEP_DECAY_CLASSIFICATION,
                error=str(exc),
                capture_event_id=event_uuid,
                correlation_id=correlation_id,
                started_at_ms=started_at,
            )
            db.commit()
        except Exception:
            db.rollback()
        logger.error("Failed to classify note decay for %s: %s", note_id, exc, exc_info=True)
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))
        return {"status": "failed", "note_id": note_id, "error": str(exc)}
    finally:
        db.close()
