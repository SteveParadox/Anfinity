"""Query and refresh helpers for note contribution attribution."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional, Sequence
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import async_engine


logger = logging.getLogger(__name__)

NOTE_CONTRIBUTIONS_REFRESH_LOCK_KEY = 80420421
NOTE_CONTRIBUTIONS_REFRESH_MIN_INTERVAL_SECONDS = 20.0

_refresh_lock = asyncio.Lock()
_refresh_task: Optional[asyncio.Task[bool]] = None
_last_refresh_monotonic = 0.0


@dataclass(frozen=True, slots=True)
class NoteContributionSummary:
    note_id: UUID
    workspace_id: Optional[UUID]
    contributor_user_id: UUID
    contribution_count: int
    note_create_count: int
    note_update_count: int
    note_restore_count: int
    thinking_contribution_count: int
    vote_cast_count: int
    first_contribution_at: Optional[datetime]
    last_contribution_at: Optional[datetime]
    contributor_name: Optional[str] = None
    contributor_email: Optional[str] = None


def _row_to_summary(row: Any) -> NoteContributionSummary:
    mapping = row._mapping if hasattr(row, "_mapping") else row
    return NoteContributionSummary(
        note_id=mapping["note_id"],
        workspace_id=mapping.get("workspace_id"),
        contributor_user_id=mapping["contributor_user_id"],
        contribution_count=int(mapping.get("contribution_count") or 0),
        note_create_count=int(mapping.get("note_create_count") or 0),
        note_update_count=int(mapping.get("note_update_count") or 0),
        note_restore_count=int(mapping.get("note_restore_count") or 0),
        thinking_contribution_count=int(mapping.get("thinking_contribution_count") or 0),
        vote_cast_count=int(mapping.get("vote_cast_count") or 0),
        first_contribution_at=mapping.get("first_contribution_at"),
        last_contribution_at=mapping.get("last_contribution_at"),
        contributor_name=mapping.get("contributor_name"),
        contributor_email=mapping.get("contributor_email"),
    )


NOTE_CONTRIBUTIONS_SELECT_SQL = text(
    """
    SELECT
      nc.note_id,
      nc.workspace_id,
      nc.contributor_user_id,
      nc.contribution_count,
      nc.note_create_count,
      nc.note_update_count,
      nc.note_restore_count,
      nc.thinking_contribution_count,
      nc.vote_cast_count,
      nc.first_contribution_at,
      nc.last_contribution_at,
      u.full_name AS contributor_name,
      u.email AS contributor_email
    FROM note_contributions nc
    LEFT JOIN users u ON u.id = nc.contributor_user_id
    WHERE nc.note_id = :note_id
    ORDER BY
      nc.contribution_count DESC,
      nc.last_contribution_at DESC NULLS LAST,
      nc.contributor_user_id ASC
    """
)


NOTE_CONTRIBUTIONS_FALLBACK_SQL = text(
    """
    SELECT
      al.note_id,
      al.workspace_id,
      al.actor_user_id AS contributor_user_id,
      COUNT(*)::bigint AS contribution_count,
      COUNT(*) FILTER (WHERE al.action_type = 'note.created')::bigint AS note_create_count,
      COUNT(*) FILTER (WHERE al.action_type = 'note.updated')::bigint AS note_update_count,
      COUNT(*) FILTER (WHERE al.action_type = 'note.restored')::bigint AS note_restore_count,
      COUNT(*) FILTER (WHERE al.action_type = 'thinking_session.contribution_submitted')::bigint AS thinking_contribution_count,
      COUNT(*) FILTER (WHERE al.action_type = 'thinking_session.vote_cast')::bigint AS vote_cast_count,
      MIN(al.created_at) AS first_contribution_at,
      MAX(al.created_at) AS last_contribution_at,
      u.full_name AS contributor_name,
      u.email AS contributor_email
    FROM audit_log al
    LEFT JOIN users u ON u.id = al.actor_user_id
    WHERE al.note_id = :note_id
      AND al.actor_user_id IS NOT NULL
      AND al.action_type IN (
        'note.created',
        'note.updated',
        'note.restored',
        'thinking_session.contribution_submitted',
        'thinking_session.vote_cast'
      )
    GROUP BY al.note_id, al.workspace_id, al.actor_user_id, u.full_name, u.email
    ORDER BY
      contribution_count DESC,
      last_contribution_at DESC NULLS LAST,
      contributor_user_id ASC
    """
)


NOTE_CONTRIBUTIONS_LATEST_RAW_SQL = text(
    """
    SELECT MAX(al.created_at) AS latest_contribution_at
    FROM audit_log al
    WHERE al.note_id = :note_id
      AND al.actor_user_id IS NOT NULL
      AND al.action_type IN (
        'note.created',
        'note.updated',
        'note.restored',
        'thinking_session.contribution_submitted',
        'thinking_session.vote_cast'
      )
    """
)


async def list_note_contributions(db: AsyncSession, note_id: UUID) -> list[NoteContributionSummary]:
    """Read note contributions from the materialized view, with raw fallback."""

    result = await db.execute(NOTE_CONTRIBUTIONS_SELECT_SQL, {"note_id": note_id})
    rows = result.fetchall()
    latest_mv_at = None
    if rows:
        summaries = [_row_to_summary(row) for row in rows]
        latest_mv_at = max(
            (summary.last_contribution_at for summary in summaries if summary.last_contribution_at is not None),
            default=None,
        )
    else:
        summaries = []

    latest_raw_result = await db.execute(NOTE_CONTRIBUTIONS_LATEST_RAW_SQL, {"note_id": note_id})
    latest_raw_at = latest_raw_result.scalar_one_or_none()
    if summaries and (latest_raw_at is None or latest_mv_at == latest_raw_at):
        return summaries

    fallback = await db.execute(NOTE_CONTRIBUTIONS_FALLBACK_SQL, {"note_id": note_id})
    return [_row_to_summary(row) for row in fallback.fetchall()]


def _can_skip_refresh(force: bool) -> bool:
    if force:
        return False
    return (time.monotonic() - _last_refresh_monotonic) < NOTE_CONTRIBUTIONS_REFRESH_MIN_INTERVAL_SECONDS


async def refresh_note_contributions_materialized_view(*, force: bool = False) -> bool:
    """Refresh the materialized view with throttling and cross-process advisory locking."""

    global _last_refresh_monotonic

    if _can_skip_refresh(force):
        return False

    async with _refresh_lock:
        if _can_skip_refresh(force):
            return False

        conn = await async_engine.connect()
        autocommit_conn = conn
        lock_acquired = False
        try:
            autocommit_conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
            lock_result = await autocommit_conn.execute(
                text("SELECT pg_try_advisory_lock(:lock_key)"),
                {"lock_key": NOTE_CONTRIBUTIONS_REFRESH_LOCK_KEY},
            )
            lock_acquired = bool(lock_result.scalar())
            if not lock_acquired:
                return False

            await autocommit_conn.execute(text("REFRESH MATERIALIZED VIEW CONCURRENTLY note_contributions"))
            _last_refresh_monotonic = time.monotonic()
            return True
        except Exception:
            logger.exception("Failed to refresh note_contributions materialized view")
            return False
        finally:
            if lock_acquired:
                try:
                    await autocommit_conn.execute(
                        text("SELECT pg_advisory_unlock(:lock_key)"),
                        {"lock_key": NOTE_CONTRIBUTIONS_REFRESH_LOCK_KEY},
                    )
                except Exception:
                    logger.exception("Failed to release note_contributions advisory lock")
            await conn.close()


def schedule_note_contributions_refresh(*, force: bool = False) -> None:
    """Kick off a best-effort background refresh when the event loop is available."""

    global _refresh_task

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.debug("Skipped note_contributions refresh scheduling because no event loop is running")
        return

    if _refresh_task is not None and not _refresh_task.done():
        return

    _refresh_task = loop.create_task(refresh_note_contributions_materialized_view(force=force))


def build_note_contribution_breakdown(summary: NoteContributionSummary) -> dict[str, int]:
    return {
        "note_created": summary.note_create_count,
        "note_updated": summary.note_update_count,
        "note_restored": summary.note_restore_count,
        "thinking_contributions": summary.thinking_contribution_count,
        "votes_cast": summary.vote_cast_count,
    }
