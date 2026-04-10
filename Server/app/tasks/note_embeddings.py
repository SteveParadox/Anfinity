"""Celery tasks for generating note embeddings.

Changes from v1
───────────────
- `UUID(...)` calls are now validated upfront; an invalid ID returns an error
  payload immediately instead of raising an unhandled ValueError.
- Dead `Session` import removed.
- Batch task closes the DB session *before* dispatching `.delay()` calls so the
  connection is not held open during queue round-trips.
- `.delay()` return values (child task IDs) are collected and included in the
  batch result for observability.
- Dimension is read from `embedding_service.dimension` rather than a live probe
  API call — no wasted network round-trip.
- `updated_at` is refreshed whenever the embedding is written.
- Log calls use `%s` lazy formatting instead of eager f-strings.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import select, text

from app.celery_app import celery_app
from app.database.session import SyncSessionLocal
from app.database.models import Note
from app.services.embeddings import get_embedding_service

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_uuid(value: str, field: str) -> UUID | None:
    """Return a UUID or ``None`` (logging the problem) if *value* is invalid."""
    try:
        return UUID(value)
    except (ValueError, AttributeError):
        logger.error("Invalid %s UUID: %r", field, value)
        return None


def _queue_connection_suggestions(note_id: str) -> None:
    """Queue note-connection suggestion refresh without failing the embedding task."""
    try:
        from app.tasks.connection_suggestions import generate_connection_suggestions
        generate_connection_suggestions.delay(note_id)
    except Exception as exc:
        logger.warning("Failed to queue connection suggestions for note %s: %s", note_id, exc)


# ---------------------------------------------------------------------------
# Single-note embedding
# ---------------------------------------------------------------------------


@celery_app.task(
    bind=True,
    max_retries=3,
    name="generate_note_embedding",
    acks_late=True,
)
def generate_note_embedding(
    self,
    note_id: str,
    provider: Optional[str] = None,
) -> dict[str, Any]:
    """Generate and store an embedding vector for a single note.

    Args:
        note_id: UUID of the note to embed.
        provider: Embedding provider (openai, cohere, local).
                  Defaults to the environment-level configuration.

    Returns:
        Result dict with ``status`` and associated metadata.
    """
    note_uuid = _parse_uuid(note_id, "note_id")
    if note_uuid is None:
        return {"status": "error", "note_id": note_id, "message": "Invalid UUID"}

    logger.info(
        "Generating embedding for note %s (provider=%s, task=%s)",
        note_id, provider or "default", self.request.id,
    )

    db = SyncSessionLocal()
    try:
        note = db.execute(
            select(Note).where(Note.id == note_uuid)
        ).scalar_one_or_none()

        if note is None:
            logger.warning("Note not found: %s", note_id)
            return {"status": "not_found", "note_id": note_id, "message": "Note does not exist"}

        # Build embedding text: title + body + optional tags
        parts = [note.title, note.content]
        if note.tags:
            parts.append(" ".join(note.tags))
        embedding_text = "\n".join(parts)

        embedding_service = get_embedding_service(provider=provider)
        actual_model = embedding_service.model
        actual_dim = embedding_service.dimension  # No live API probe needed

        logger.debug(
            "Embedding note %s with model=%s dim=%d",
            note_id, actual_model, actual_dim,
        )

        embedding_vector: list[float] = embedding_service.embed_text(embedding_text)

        if not embedding_vector:
            raise ValueError("Embedding service returned an empty vector")

        note.embedding = json.dumps(embedding_vector)
        note.updated_at = datetime.now(timezone.utc)
        try:
            db.execute(
                text(
                    f"""
                    UPDATE notes
                    SET embedding_vector = CAST(:embedding AS vector({len(embedding_vector)}))
                    WHERE id = :note_id
                    """
                ),
                {
                    "embedding": f"[{','.join(map(str, embedding_vector))}]",
                    "note_id": note.id,
                },
            )
        except Exception as exc:
            logger.warning(
                "Skipping embedding_vector sync for note %s (dim=%d): %s",
                note_id,
                len(embedding_vector),
                exc,
            )

        db.commit()
        _queue_connection_suggestions(note_id)

        logger.info(
            "Embedding stored for note %s (dim=%d, model=%s, task=%s)",
            note_id, len(embedding_vector), actual_model, self.request.id,
        )

        return {
            "status": "success",
            "note_id": note_id,
            "embedding_dimension": len(embedding_vector),
            "model_used": actual_model,
            "provider": embedding_service.provider,
            "text_length": len(embedding_text),
        }

    except Exception as exc:
        db.rollback()
        logger.error(
            "Failed to generate embedding for note %s (attempt %d): %s",
            note_id, self.request.retries + 1, exc,
            exc_info=True,
        )
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc, countdown=300 * (2 ** self.request.retries))

        logger.error("Max retries exceeded for note %s", note_id)
        return {
            "status": "failed",
            "note_id": note_id,
            "error": str(exc),
            "retries_exceeded": True,
        }

    finally:
        db.close()


# ---------------------------------------------------------------------------
# Workspace-wide batch embedding
# ---------------------------------------------------------------------------


@celery_app.task(
    bind=True,
    max_retries=3,
    name="generate_workspace_note_embeddings",
    acks_late=True,
)
def generate_workspace_note_embeddings(
    self,
    workspace_id: str,
    limit: Optional[int] = None,
    provider: Optional[str] = None,
) -> dict[str, Any]:
    """Queue embedding tasks for all notes in a workspace that lack embeddings.

    Args:
        workspace_id: UUID of the target workspace.
        limit: Maximum number of notes to enqueue (``None`` = all).
        provider: Embedding provider to use.

    Returns:
        Result dict with queued/failed counts and child task IDs.
    """
    ws_uuid = _parse_uuid(workspace_id, "workspace_id")
    if ws_uuid is None:
        return {"status": "error", "workspace_id": workspace_id, "message": "Invalid UUID"}

    logger.info(
        "Batch embedding workspace %s (limit=%s, provider=%s, task=%s)",
        workspace_id, limit or "unlimited", provider or "default", self.request.id,
    )

    # Resolve provider config once — read dimension from service, no live probe.
    embedding_service = get_embedding_service(provider=provider)
    actual_model = embedding_service.model
    actual_dim = embedding_service.dimension

    logger.debug(
        "Embedding config: model=%s dim=%d provider=%s",
        actual_model, actual_dim, embedding_service.provider,
    )

    # ── Fetch note IDs, then close the session before dispatching ──────────
    # Holding an open DB connection across many .delay() calls wastes pool
    # resources and can cause pool exhaustion under load.
    db = SyncSessionLocal()
    try:
        query = select(Note.id).where(
            (Note.workspace_id == ws_uuid)
            & ((Note.embedding.is_(None)) | (Note.embedding == ""))
        )
        if limit:
            query = query.limit(limit)

        note_ids: list[str] = [str(row) for row in db.execute(query).scalars().all()]

        logger.info(
            "Found %d notes without embeddings in workspace %s",
            len(note_ids), workspace_id,
        )
    except Exception as exc:
        db.rollback()
        logger.error("Failed to query notes for workspace %s: %s", workspace_id, exc, exc_info=True)
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc, countdown=300 * (2 ** self.request.retries))
        return {"status": "failed", "workspace_id": workspace_id, "error": str(exc)}
    finally:
        db.close()

    # ── Dispatch child tasks (no DB session held) ───────────────────────────
    queued_task_ids: list[str] = []
    failed_dispatches: list[dict[str, str]] = []

    for nid in note_ids:
        try:
            async_result = generate_note_embedding.delay(nid, provider=provider)
            queued_task_ids.append(async_result.id)
            logger.debug("Queued embedding task %s for note %s", async_result.id, nid)
        except Exception as dispatch_exc:
            failed_dispatches.append({"note_id": nid, "error": str(dispatch_exc)})
            logger.error("Failed to queue embedding for note %s: %s", nid, dispatch_exc)

    logger.info(
        "Batch dispatch complete for workspace %s: queued=%d failed=%d",
        workspace_id, len(queued_task_ids), len(failed_dispatches),
    )

    return {
        "status": "success",
        "workspace_id": workspace_id,
        "notes_queued": len(queued_task_ids),
        "notes_failed": len(failed_dispatches),
        "embedding_dimension": actual_dim,
        "embedding_model": actual_model,
        "child_task_ids": queued_task_ids,
        "failed_details": failed_dispatches or None,
    }
