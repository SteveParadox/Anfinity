"""Database session management."""
import logging
import ssl
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncGenerator
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy import create_engine, event, text

from app.config import settings

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CERT_PATH = PROJECT_ROOT / "certs" / "global-bundle.pem"

if CERT_PATH.exists():
    ssl_context = ssl.create_default_context(cafile=str(CERT_PATH))
    SSL_CONNECT_ARGS = {
        "sslmode": "verify-full",
        "sslrootcert": str(CERT_PATH),
    }
else:
    ssl_context = ssl.create_default_context()
    SSL_CONNECT_ARGS = {
        "sslmode": "require",
    }


def _to_async_database_url(url: str) -> str:
    """Return an async SQLAlchemy URL for FastAPI request handlers."""
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)

    parsed = urlparse(url)
    query = [(k, v) for k, v in parse_qsl(parsed.query) if k != "sslmode"]

    return urlunparse(parsed._replace(query=urlencode(query)))


def _to_sync_database_url(url: str) -> str:
    """Return a sync SQLAlchemy URL for Celery/background workers."""
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql://", 1)
    if url.startswith("postgresql+asyncpg://"):
        return url.replace("postgresql+asyncpg://", "postgresql://", 1)
    return url


ASYNC_DATABASE_URL = _to_async_database_url(settings.DATABASE_URL)
SYNC_DATABASE_URL = _to_sync_database_url(settings.DATABASE_URL)

# Async engine for FastAPI
async_engine = create_async_engine(
    ASYNC_DATABASE_URL,
    pool_size=settings.DATABASE_POOL_SIZE,
    max_overflow=settings.DATABASE_MAX_OVERFLOW,
    pool_pre_ping=True,
    # Keep the pool's rollback-on-return safety net enabled. A correctly
    # closed AsyncSession already rolls back, but this protects direct engine
    # users if their cleanup path changes or fails.
    pool_reset_on_return="rollback",
    echo=settings.DEBUG,
    connect_args={"ssl": ssl_context},
)

# Async session factory
AsyncSessionLocal = async_sessionmaker(
    async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
    info={"app_rls_bypass": False},
)

# Sync engine for Celery workers
sync_engine = create_engine(
    SYNC_DATABASE_URL,
    pool_size=10,
    max_overflow=5,
    pool_pre_ping=True,
    pool_reset_on_return="rollback",
    echo=settings.DEBUG,
    connect_args=SSL_CONNECT_ARGS,
)

# Sync session factory for background tasks
SyncSessionLocal = sessionmaker(
    bind=sync_engine,
    autocommit=False,
    autoflush=False,
    info={"app_rls_bypass": True},
)

# Alias for backwards compatibility
SessionLocal = SyncSessionLocal


def get_session_info(db: AsyncSession | Session) -> dict:
    """Return the mutable session info store for async or sync sessions."""
    if isinstance(db, AsyncSession):
        return db.sync_session.info
    info = getattr(db, "info", None)
    if isinstance(info, dict):
        return info
    info = {}
    try:
        setattr(db, "info", info)
    except Exception:
        pass
    return info


def _normalize_sql_for_metrics(statement: Any) -> str:
    raw = " ".join(str(statement).split())
    if len(raw) > 400:
        return raw[:397] + "..."
    return raw


def _record_sql_metrics(session_info: dict, statement: Any) -> None:
    metrics = session_info.setdefault(
        "sql_metrics",
        {
            "count": 0,
            "statements": {},
        },
    )
    normalized = _normalize_sql_for_metrics(statement)
    metrics["count"] += 1
    statement_counts = metrics["statements"]
    statement_counts[normalized] = statement_counts.get(normalized, 0) + 1


def get_session_query_metrics(db: AsyncSession | Session) -> dict[str, Any]:
    """Return aggregate SQL metrics for the current request/session."""
    session_info = get_session_info(db)
    raw_metrics = session_info.get("sql_metrics") or {}
    statements = raw_metrics.get("statements") or {}
    repeated = [
        {"statement": statement, "count": count}
        for statement, count in sorted(statements.items(), key=lambda item: item[1], reverse=True)
        if count > 1
    ]
    return {
        "count": int(raw_metrics.get("count", 0) or 0),
        "repeated": repeated,
        "workspace_context_cache_size": len(session_info.get("workspace_context_cache", {})),
        "workspace_permission_cache_size": len(session_info.get("workspace_permission_cache", {})),
    }


def log_session_query_metrics(db: AsyncSession | Session, label: str, *, level: int = logging.INFO) -> None:
    """Emit request-scoped SQL metrics for the current handler."""
    metrics = get_session_query_metrics(db)
    repeated = metrics["repeated"][:3]
    repeated_summary = [f'{item["count"]}x {item["statement"]}' for item in repeated]
    logger.log(
        level,
        "%s sql_count=%s repeated=%s workspace_ctx_cache=%s workspace_perm_cache=%s",
        label,
        metrics["count"],
        repeated_summary,
        metrics["workspace_context_cache_size"],
        metrics["workspace_permission_cache_size"],
    )


@event.listens_for(Session, "after_begin")
def _apply_session_security_context(session: Session, transaction, connection) -> None:
    current_user_id = session.info.get("app_current_user_id")
    rls_bypass = session.info.get("app_rls_bypass", False)
    connection.info["app_session_info"] = session.info

    connection.execute(
        text("select set_config('app.rls_bypass', :value, true)"),
        {"value": "true" if rls_bypass else "false"},
    )

    connection.execute(
        text("select set_config('app.current_user_id', :value, true)"),
        {"value": str(current_user_id) if current_user_id else ""},
    )


def _track_connection_sql(conn, cursor, statement, parameters, context, executemany) -> None:
    del cursor, parameters, context, executemany
    session_info = conn.info.get("app_session_info")
    if isinstance(session_info, dict):
        _record_sql_metrics(session_info, statement)


event.listen(async_engine.sync_engine, "before_cursor_execute", _track_connection_sql)
event.listen(sync_engine, "before_cursor_execute", _track_connection_sql)


@event.listens_for(Session, "after_commit")
def _dispatch_pending_audit_events_after_commit(session: Session) -> None:
    session_info = getattr(session, "info", None)
    if not isinstance(session_info, dict):
        return

    from app.core.audit import (
        clear_pending_audit_events,
        dispatch_pending_audit_events,
        pop_pending_audit_events,
    )

    pending_events = pop_pending_audit_events(session_info)
    clear_pending_audit_events(session_info)
    if pending_events:
        dispatch_pending_audit_events(pending_events)

    pending_note_capture_event_ids = list(session_info.get("pending_note_capture_event_ids") or [])
    session_info["pending_note_capture_event_ids"] = []
    if pending_note_capture_event_ids:
        try:
            from app.tasks.note_auto_tagging import run_note_auto_tagging_pipeline

            for event_id in dict.fromkeys(str(item) for item in pending_note_capture_event_ids if item):
                run_note_auto_tagging_pipeline.delay(event_id)
        except Exception as exc:
            logger.warning("Failed to dispatch note auto-tagging pipeline after commit: %s", exc)


@event.listens_for(Session, "after_rollback")
def _clear_pending_audit_events_after_rollback(session: Session) -> None:
    session_info = getattr(session, "info", None)
    if not isinstance(session_info, dict):
        return

    from app.core.audit import clear_pending_audit_events

    clear_pending_audit_events(session_info)
    session_info["pending_note_capture_event_ids"] = []


def bind_db_user_context(db: AsyncSession | Session, user_id) -> None:
    session_info = get_session_info(db)
    session_info["app_current_user_id"] = str(user_id)
    session_info["app_rls_bypass"] = False


def bind_db_rls_bypass(db: AsyncSession | Session, enabled: bool = True) -> None:
    session_info = get_session_info(db)
    session_info["app_rls_bypass"] = enabled
    if enabled:
        session_info.pop("app_current_user_id", None)


@asynccontextmanager
async def async_session_scope(*, commit_on_success: bool = False) -> AsyncGenerator[AsyncSession, None]:
    """Own one AsyncSession and always return its connection to the pool.

    Sessions are task-local resources. Callers must not retain them beyond
    this scope or pass them to background work. ``BaseException`` is
    intentional: cancellation must use the same rollback/close path as a
    database exception.
    """
    session = AsyncSessionLocal()
    try:
        yield session
        if commit_on_success:
            await session.commit()
    except BaseException:
        # rollback() is safe when no transaction was opened and guarantees a
        # connection cannot be returned with an open transaction.
        await session.rollback()
        raise
    finally:
        # close() releases the connection even if rollback/commit failed.
        await session.close()


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI request dependency with deterministic transaction ownership."""
    async with async_session_scope(commit_on_success=True) as session:
        yield session


def get_sync_db():
    """Get sync database session for background tasks."""
    db = SyncSessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


async def init_db():
    """Initialize database tables."""
    from app.database.models import Base
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
