from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import Column, String, create_engine, update, event
from sqlalchemy.orm import declarative_base, sessionmaker
from app.services import thinking_sessions as service, note_invites

Base = declarative_base()
class Record(Base):
    __tablename__ = "realtime_refresh"
    id = Column(String, primary_key=True)
    session_id = Column(String)
    phase = Column(String)
    status = Column(String)
    content = Column(String)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["session", "run", "note"])
async def test_locked_reads_refresh_existing_identity_map(kind, monkeypatch):
    # Real SQLAlchemy identity-map behavior, without requiring a running PostgreSQL service.
    engine = create_engine("sqlite://")
    # App-wide transaction hooks set PostgreSQL RLS context; emulate only that SQL function.
    event.listen(engine, "connect", lambda connection, _record: connection.create_function("set_config", 3, lambda _key, value, _local: value))
    Base.metadata.create_all(engine)
    try:
        with sessionmaker(engine, expire_on_commit=False)() as db:
            record = Record(id="record", session_id="session", phase="synthesizing", status="streaming", content="old")
            db.add(record); db.commit()
            db.execute(update(Record).values(phase="completed", status="completed", content="new").execution_options(synchronize_session=False))
            assert record.content == "old"  # An ordinary SELECT would reuse this stale object.
            statements = []
            async def execute(statement):
                statements.append(statement)
                return db.execute(statement)
            adapter = SimpleNamespace(execute=execute)
            if kind == "session":
                monkeypatch.setattr(service, "ThinkingSession", Record)
                result = await service.get_thinking_session_for_update("record", adapter)
            elif kind == "run":
                monkeypatch.setattr(service, "ThinkingSessionSynthesisRun", Record)
                result = await service.get_thinking_synthesis_run_for_update("session", "record", adapter)
            else:
                @asynccontextmanager
                async def bypass(_db): yield
                monkeypatch.setattr(note_invites, "Note", Record)
                monkeypatch.setattr(note_invites, "temporary_rls_bypass", bypass)
                result = await note_invites.get_note_with_bypass(adapter, "record", for_update=True)
            assert result.content == "new"
            assert result.phase == "completed"
            assert result.status == "completed"
            assert statements[0]._for_update_arg is not None
    finally:
        engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("fresh", [True, False])
async def test_stalled_recovery_rechecks_progress_under_lock(monkeypatch, fresh):
    run = SimpleNamespace(id=uuid4(), status=service.ThinkingSynthesisStatus.STREAMING,
                          updated_at=datetime.now(timezone.utc) - timedelta(seconds=0 if fresh else 1000))
    session = SimpleNamespace(id=uuid4(), active_synthesis_run_id=run.id, phase=service.ThinkingSessionPhase.SYNTHESIZING)
    monkeypatch.setattr(service, "get_thinking_session_for_update", AsyncMock(return_value=session))
    monkeypatch.setattr(service, "get_thinking_synthesis_run_for_update", AsyncMock(return_value=run))
    failure = AsyncMock(return_value=session)
    monkeypatch.setattr(service, "fail_synthesis_run", failure)
    _, failed = await service.fail_stalled_synthesis_run(session.id, run.id, 120, object())
    assert failed is not fresh
    assert failure.await_count == (0 if fresh else 1)
