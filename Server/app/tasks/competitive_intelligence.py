"""Celery tasks for competitive intelligence monitoring."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select

from app.celery_app import celery_app
from app.config import settings
from app.database.models import CompetitiveSource
from app.database.session import AsyncSessionLocal
from app.services.competitive_intelligence import CompetitiveIntelligenceService

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=2, name="run_competitive_intelligence_source")
def run_competitive_intelligence_source(self, source_id: str) -> dict:
    try:
        return asyncio.run(_run_source(source_id))
    except Exception as exc:
        logger.exception("Competitive intelligence source run failed: %s", source_id)
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc, countdown=60 * (self.request.retries + 1))
        return {"status": "failed", "source_id": source_id, "error": str(exc)}


@celery_app.task(name="run_competitive_intelligence_monitoring")
def run_competitive_intelligence_monitoring() -> dict:
    return asyncio.run(_queue_due_sources())


async def _run_source(source_id: str) -> dict:
    async with AsyncSessionLocal() as db:
        try:
            result = await CompetitiveIntelligenceService(db).run_source(UUID(str(source_id)))
            await db.commit()
            return result.__dict__
        except Exception:
            await db.rollback()
            raise


async def _queue_due_sources() -> dict:
    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as db:
        rows = await db.execute(select(CompetitiveSource).where(CompetitiveSource.is_active.is_(True)))
        sources = list(rows.scalars().all())

    queued = 0
    skipped = 0
    for source in sources:
        interval = max(15, int(source.check_interval_minutes or 1440))
        due_at = (source.last_checked_at or datetime.min.replace(tzinfo=timezone.utc)) + timedelta(minutes=interval)
        lease_active = source.run_status == "processing" and source.lease_until and source.lease_until > now
        if due_at > now or lease_active:
            skipped += 1
            continue
        run_competitive_intelligence_source.delay(str(source.id))
        queued += 1

    return {"status": "queued", "queued": queued, "skipped": skipped, "checked_at": now.isoformat()}


def competitive_monitor_interval_seconds() -> int:
    return max(300, int(settings.COMPETITIVE_MONITOR_INTERVAL_SECONDS or 3600))
