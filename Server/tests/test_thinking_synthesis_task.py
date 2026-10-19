from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from app.tasks import thinking_synthesis as task


@pytest.fixture
def producer(monkeypatch):
    user = SimpleNamespace(id=uuid4(), is_active=True)
    session = SimpleNamespace(id=uuid4())
    run = SimpleNamespace(id=uuid4(), model="test-model")
    engine = SimpleNamespace(dispose=AsyncMock())
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: user)),
                         commit=AsyncMock(), rollback=AsyncMock())
    class Context:
        async def __aenter__(self): return db
        async def __aexit__(self, *_): pass
    monkeypatch.setattr(task, "create_async_engine", MagicMock(return_value=engine))
    monkeypatch.setattr(task, "async_sessionmaker", lambda *_args, **_kw: Context)
    monkeypatch.setattr(task, "get_thinking_session_or_404", AsyncMock(return_value=session))
    monkeypatch.setattr(task, "claim_synthesis_run_for_streaming", AsyncMock(return_value=run))
    monkeypatch.setattr(task, "get_thinking_synthesis_run_or_404", AsyncMock(return_value=run))
    monkeypatch.setattr(task, "build_facilitation_prompt", lambda *_: ("prompt", []))
    monkeypatch.setattr(task, "complete_synthesis_run", AsyncMock())
    monkeypatch.setattr(task, "fail_synthesis_run", AsyncMock())
    return user, session, run, engine


@pytest.mark.asyncio
async def test_duplicate_queue_delivery_does_not_restart_provider(producer, monkeypatch):
    user, session, run, engine = producer
    monkeypatch.setattr(task, "claim_synthesis_run_for_streaming", AsyncMock(side_effect=HTTPException(409, "Already started")))
    provider = MagicMock()
    monkeypatch.setattr(task, "AsyncOpenAI", provider)
    await task.produce_synthesis(str(session.id), str(run.id), str(user.id))
    provider.assert_not_called()
    engine.dispose.assert_awaited_once()


@pytest.mark.asyncio
async def test_provider_failure_is_durable_and_safe(producer, monkeypatch):
    user, session, run, _ = producer
    monkeypatch.setattr(task.settings, "OPENAI_API_KEY", "")
    await task.produce_synthesis(str(session.id), str(run.id), str(user.id))
    task.get_thinking_synthesis_run_or_404.assert_awaited_once()
    task.fail_synthesis_run.assert_awaited_once()
    assert "review backend logs" in task.fail_synthesis_run.call_args.args[2]
    task.complete_synthesis_run.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("interrupted", [True, False])
async def test_producer_keeps_output_when_provider_finishes_or_fails(producer, monkeypatch, interrupted):
    user, session, run, _ = producer
    monkeypatch.setattr(task.settings, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(task, "monotonic", lambda: 0)
    monkeypatch.setattr(task, "persist_synthesis_progress", AsyncMock())
    async def chunks():
        yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="partial"))])
        if interrupted: raise RuntimeError("provider interrupted")
    class Provider:
        chat = SimpleNamespace(completions=SimpleNamespace(create=AsyncMock(return_value=chunks())))
        async def __aenter__(self): return self
        async def __aexit__(self, *_): pass
    monkeypatch.setattr(task, "AsyncOpenAI", lambda **_: Provider())
    await task.produce_synthesis(str(session.id), str(run.id), str(user.id))
    if interrupted:
        assert task.persist_synthesis_progress.call_args.args[2] == "partial"
        task.fail_synthesis_run.assert_awaited_once()
    else:
        assert task.complete_synthesis_run.call_args.args[2] == "partial"
