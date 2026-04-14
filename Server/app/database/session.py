"""Database session management."""
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy import create_engine, event, text

from app.config import settings


def _to_async_database_url(url: str) -> str:
    """Return an async SQLAlchemy URL for FastAPI request handlers."""
    if url.startswith("postgresql+asyncpg://"):
        return url
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


def _to_sync_database_url(url: str) -> str:
    """Return a sync SQLAlchemy URL for Celery/background workers."""
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
    pool_reset_on_return=None,  # FIX: Skip ROLLBACK on connection return (improves performance)
    echo=settings.DEBUG,
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
    pool_reset_on_return=None,  # FIX: Skip ROLLBACK on connection return (improves performance)
    echo=settings.DEBUG,
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


@event.listens_for(Session, "after_begin")
def _apply_session_security_context(session: Session, transaction, connection) -> None:
    current_user_id = session.info.get("app_current_user_id")
    rls_bypass = session.info.get("app_rls_bypass", False)

    connection.execute(
        text("select set_config('app.rls_bypass', :value, true)"),
        {"value": "true" if rls_bypass else "false"},
    )

    connection.execute(
        text("select set_config('app.current_user_id', :value, true)"),
        {"value": str(current_user_id) if current_user_id else ""},
    )


def bind_db_user_context(db: AsyncSession | Session, user_id) -> None:
    if isinstance(db, AsyncSession):
        db.sync_session.info["app_current_user_id"] = str(user_id)
        db.sync_session.info["app_rls_bypass"] = False
        return
    db.info["app_current_user_id"] = str(user_id)
    db.info["app_rls_bypass"] = False


def bind_db_rls_bypass(db: AsyncSession | Session, enabled: bool = True) -> None:
    if isinstance(db, AsyncSession):
        db.sync_session.info["app_rls_bypass"] = enabled
        if enabled:
            db.sync_session.info.pop("app_current_user_id", None)
        return
    db.info["app_rls_bypass"] = enabled
    if enabled:
        db.info.pop("app_current_user_id", None)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency for getting async database sessions."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


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
