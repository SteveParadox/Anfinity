"""Durable synthesis producer; WebSockets/SSE only observe PostgreSQL progress."""
from __future__ import annotations

import asyncio
import logging
from time import monotonic
from uuid import UUID

from fastapi import HTTPException
from openai import AsyncOpenAI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.celery_app import celery_app
from app.config import settings
from app.database.models import User
from app.database.session import ASYNC_DATABASE_URL, ssl_context, bind_db_user_context
from app.services.thinking_sessions import (
    build_facilitation_prompt, claim_synthesis_run_for_streaming,
    complete_synthesis_run, fail_synthesis_run, get_thinking_session_or_404, get_thinking_synthesis_run_or_404,
    persist_synthesis_progress,
)

logger = logging.getLogger(__name__)


async def produce_synthesis(session_id: str, run_id: str, user_id: str) -> None:
    # Task-local engine prevents asyncpg connections crossing Celery event loops.
    engine = create_async_engine(ASYNC_DATABASE_URL, poolclass=NullPool,
                                 connect_args={"ssl": ssl_context})
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as db:
            bind_db_user_context(db, UUID(user_id))
            session = await get_thinking_session_or_404(UUID(session_id), db)
            user = (await db.execute(select(User).where(User.id == UUID(user_id)))).scalar_one_or_none()
            if user is None or not user.is_active:
                return
            try:
                # Atomic PENDING -> STREAMING claim: redelivery cannot run the model twice.
                run = await claim_synthesis_run_for_streaming(session, UUID(run_id), user, db)
            except HTTPException as exc:
                if exc.status_code == 409:
                    return
                raise
            parts: list[str] = []
            try:
                prompt, messages = build_facilitation_prompt(session, run)
                run.facilitation_prompt = prompt
                await db.commit()
                if not settings.OPENAI_API_KEY:
                    raise RuntimeError("Synthesis provider is not configured")
                options = {"api_key": settings.OPENAI_API_KEY,
                           "timeout": float(settings.THINKING_SESSION_SYNTHESIS_TIMEOUT_SECONDS)}
                if getattr(settings, "OPENAI_BASE_URL", None):
                    options["base_url"] = settings.OPENAI_BASE_URL
                last_save = monotonic()
                async with AsyncOpenAI(**options) as client:
                    stream = await client.chat.completions.create(model=run.model, messages=messages, temperature=0.3, stream=True)
                    async for chunk in stream:
                        delta = chunk.choices[0].delta.content if chunk.choices else None
                        if delta:
                            parts.append(delta)
                        if monotonic() - last_save >= 0.75:
                            await persist_synthesis_progress(session, run, "".join(parts), db)
                            last_save = monotonic()
                await complete_synthesis_run(session, run, "".join(parts).strip(), db)
            except Exception:
                logger.error("Synthesis producer failed: run_id=%s", run_id)
                await db.rollback()
                # Rollback expires ORM attributes: reload before accessing ids or marking failure.
                session = await get_thinking_session_or_404(UUID(session_id), db)
                run = await get_thinking_synthesis_run_or_404(UUID(session_id), UUID(run_id), db)
                if parts:
                    await persist_synthesis_progress(session, run, "".join(parts), db)
                # Keep provider errors/secrets out of WebSocket messages.
                await fail_synthesis_run(session, run, "Synthesis execution failed; review backend logs", db)
    finally:
        await engine.dispose()


@celery_app.task(name="anfinity.thinking_synthesis", acks_late=True, reject_on_worker_lost=True)
def run_thinking_synthesis(session_id: str, run_id: str, user_id: str) -> None:
    asyncio.run(produce_synthesis(session_id, run_id, user_id))
