"""Celery tasks for generating note summaries using an LLM.

Changes from v1
───────────────
- Runtime crash fixed: `SessionLocal()` → `SyncSessionLocal()`.
- Broken `force` query expression fixed; operator-precedence bug caused the
  "fetch all notes" branch to silently append a redundant clause instead of
  dropping the summary-filter condition.
- `from sqlalchemy import and_` moved to module level (no lazy imports).
- `UUID(...)` calls are validated upfront via `_parse_uuid`; an invalid ID
  returns an error payload before touching the database.
- Dead `Session` import removed.
- Batch task no longer re-implements the `MIN_CONTENT_LENGTH` check that
  already lives inside `generate_note_summary`; the per-note skip is counted
  by inspecting the child task result synchronously (optional) or the batch
  simply enqueues all qualifying notes and lets the individual task gate.
- `updated_at` is refreshed whenever the summary is written.
- Log calls use `%s` lazy formatting.
- Retry logic unified: check `self.request.retries < self.max_retries` instead
  of catching `MaxRetriesExceededError`.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import and_, or_, select

from app.celery_app import celery_app
from app.database.session import SyncSessionLocal
from app.database.models import Note
from app.services.llm_service import get_llm_service

logger = logging.getLogger(__name__)

# Minimum content length for summary generation.
MIN_CONTENT_LENGTH = 100
SUMMARY_CONTEXT_CHAR_LIMIT = 1500
SUMMARY_CONTEXT_HEAD_CHARS = 1200
SUMMARY_CONTEXT_TAIL_CHARS = 250
SUMMARY_TIMEOUT_RETRY_BASE_SECONDS = 90


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


def _build_summary_prompt(content_length: int) -> str:
    """Return a prompt sized to the content length."""
    if content_length < 500:
        return (
            "Provide a single-sentence summary of this note. "
            "Be concise and capture the main point."
        )
    if content_length < 2000:
        return (
            "Provide a 2-3 sentence summary of this note. "
            "Capture the main points and key takeaways."
        )
    return (
        "Provide a 3-4 sentence summary of this note, highlighting the most important points. "
        "Organise as bullet points if there are multiple distinct topics."
    )


def _build_summary_context(content: str) -> str:
    """Bound the amount of text sent to local summary generation.

    Smaller local models are much more likely to stall on full-note payloads.
    Keep the start and end of long notes so the model still sees the main topic
    and the closing context.
    """
    normalized = (content or "").strip()
    if len(normalized) <= SUMMARY_CONTEXT_CHAR_LIMIT:
        return normalized

    head = normalized[:SUMMARY_CONTEXT_HEAD_CHARS].rstrip()
    tail = normalized[-SUMMARY_CONTEXT_TAIL_CHARS:].lstrip()
    return (
        f"{head}\n\n"
        "[... middle omitted for summary generation due to note length ...]\n\n"
        f"{tail}"
    )


# ---------------------------------------------------------------------------
# Single-note summary
# ---------------------------------------------------------------------------


@celery_app.task(
    bind=True,
    max_retries=3,
    name="generate_note_summary",
    acks_late=True,
)
def generate_note_summary(
    self,
    note_id: str,
    model: Optional[str] = None,
    force: bool = False,
) -> dict[str, Any]:
    """Generate and store an LLM summary for a single note.

    Args:
        note_id: UUID of the note to summarise.
        model: LLM model identifier. Defaults to Ollama phi3 (settings.OLLAMA_MODEL).
        force: Re-generate even if a summary already exists or content is short.

    Returns:
        Result dict with ``status`` and associated metadata.
    """
    note_uuid = _parse_uuid(note_id, "note_id")
    if note_uuid is None:
        return {"status": "error", "note_id": note_id, "message": "Invalid UUID"}

    logger.info(
        "Generating summary for note %s (model=%s, force=%s, task=%s)",
        note_id, model or "default", force, self.request.id,
    )

    db = SyncSessionLocal()
    try:
        note = db.execute(
            select(Note).where(Note.id == note_uuid)
        ).scalar_one_or_none()

        if note is None:
            logger.warning("Note not found: %s", note_id)
            return {"status": "not_found", "note_id": note_id, "message": "Note does not exist"}

        content_length = len(note.content)

        if content_length < MIN_CONTENT_LENGTH and not force:
            logger.info(
                "Skipping note %s — content too short (%d < %d chars)",
                note_id, content_length, MIN_CONTENT_LENGTH,
            )
            return {
                "status": "skipped",
                "note_id": note_id,
                "reason": f"Content length {content_length} chars below minimum {MIN_CONTENT_LENGTH}",
                "content_length": content_length,
            }

        if note.summary and not force:
            logger.info("Skipping note %s — summary already exists", note_id)
            return {
                "status": "skipped",
                "note_id": note_id,
                "reason": "Summary already exists",
                "existing_summary_length": len(note.summary),
            }

        llm_service = get_llm_service(
            model=model,
            primary_provider="ollama",
            use_fallback=False,
        )
        prompt = _build_summary_prompt(content_length)

        llm_response = llm_service.generate_answer(
            query=prompt,
            context_chunks=[_build_summary_context(note.content)],
            temperature=0.3,
            max_tokens=160,
        )

        summary_text = llm_response.answer.strip()

        note.summary = summary_text
        note.updated_at = datetime.now(timezone.utc)

        db.commit()

        logger.info(
            "Summary stored for note %s (len=%d tokens=%s model=%s task=%s)",
            note_id, len(summary_text), llm_response.tokens_used,
            llm_response.model, self.request.id,
        )

        return {
            "status": "success",
            "note_id": note_id,
            "summary": summary_text,
            "summary_length": len(summary_text),
            "tokens_used": llm_response.tokens_used,
            "model": llm_response.model,
            "content_length": content_length,
        }

    except Exception as exc:
        db.rollback()
        logger.error(
            "Failed to generate summary for note %s (attempt %d): %s",
            note_id, self.request.retries + 1, exc,
            exc_info=True,
        )
        if self.request.retries < self.max_retries:
            countdown = 300 * (2 ** self.request.retries)
            if "timed out" in str(exc).lower():
                countdown = SUMMARY_TIMEOUT_RETRY_BASE_SECONDS * (2 ** self.request.retries)
            raise self.retry(exc=exc, countdown=countdown)

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
# Workspace-wide batch summary
# ---------------------------------------------------------------------------


@celery_app.task(
    bind=True,
    max_retries=3,
    name="generate_workspace_note_summaries",
    acks_late=True,
)
def generate_workspace_note_summaries(
    self,
    workspace_id: str,
    limit: Optional[int] = None,
    model: Optional[str] = None,
    force: bool = False,
) -> dict[str, Any]:
    """Queue summary tasks for all eligible notes in a workspace.

    When ``force=False`` only notes with no summary (or an empty one) are
    selected.  When ``force=True`` the summary-presence filter is dropped and
    all notes in the workspace are selected regardless of existing summaries.

    Args:
        workspace_id: UUID of the target workspace.
        limit: Maximum number of notes to enqueue (``None`` = all).
        model: LLM model to use.
        force: Re-generate summaries even if they already exist.

    Returns:
        Result dict with queued/failed counts and child task IDs.
    """
    ws_uuid = _parse_uuid(workspace_id, "workspace_id")
    if ws_uuid is None:
        return {"status": "error", "workspace_id": workspace_id, "message": "Invalid UUID"}

    logger.info(
        "Batch summary workspace %s (limit=%s, model=%s, force=%s, task=%s)",
        workspace_id, limit or "unlimited", model or "default", force, self.request.id,
    )

    # ── Build the WHERE clause ────────────────────────────────────────────
    # When force=False: workspace matches AND (summary is NULL OR summary = "")
    # When force=True:  workspace matches only — all notes regardless of summary
    workspace_filter = Note.workspace_id == ws_uuid

    if force:
        where_clause = workspace_filter
    else:
        where_clause = and_(
            workspace_filter,
            or_(Note.summary.is_(None), Note.summary == ""),
        )

    # ── Fetch note IDs, then close the session before dispatching ─────────
    db = SyncSessionLocal()
    try:
        query = select(Note.id).where(where_clause)
        if limit:
            query = query.limit(limit)

        note_ids: list[str] = [str(row) for row in db.execute(query).scalars().all()]

        logger.info(
            "Found %d notes to summarise in workspace %s",
            len(note_ids), workspace_id,
        )
    except Exception as exc:
        db.rollback()
        logger.error(
            "Failed to query notes for workspace %s: %s",
            workspace_id, exc, exc_info=True,
        )
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc, countdown=300 * (2 ** self.request.retries))
        return {"status": "failed", "workspace_id": workspace_id, "error": str(exc)}
    finally:
        db.close()

    # ── Dispatch child tasks (no DB session held) ──────────────────────────
    queued_task_ids: list[str] = []
    failed_dispatches: list[dict[str, str]] = []

    for nid in note_ids:
        try:
            async_result = generate_note_summary.delay(nid, model=model, force=force)
            queued_task_ids.append(async_result.id)
            logger.debug("Queued summary task %s for note %s", async_result.id, nid)
        except Exception as dispatch_exc:
            failed_dispatches.append({"note_id": nid, "error": str(dispatch_exc)})
            logger.error("Failed to queue summary for note %s: %s", nid, dispatch_exc)

    logger.info(
        "Batch dispatch complete for workspace %s: queued=%d failed=%d",
        workspace_id, len(queued_task_ids), len(failed_dispatches),
    )

    return {
        "status": "success",
        "workspace_id": workspace_id,
        "notes_queued": len(queued_task_ids),
        "notes_failed": len(failed_dispatches),
        "child_task_ids": queued_task_ids,
        "failed_details": failed_dispatches or None,
    }
