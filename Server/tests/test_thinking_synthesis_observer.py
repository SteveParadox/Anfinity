from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.api import thinking_sessions as api
from app.database import session as database


@pytest.mark.asyncio
async def test_observer_revalidates_user_and_releases_db_before_yielding(monkeypatch):
    user = SimpleNamespace(id=uuid4(), is_active=True)
    refreshed_user = SimpleNamespace(id=user.id, is_active=True)
    session_id, run_id = uuid4(), uuid4()
    run = SimpleNamespace(id=run_id, model="model", status="completed", output_text="result", updated_at=datetime.now(timezone.utc))
    session = SimpleNamespace(id=session_id)
    monkeypatch.setattr(api, "get_thinking_session_or_404", AsyncMock(return_value=session))
    monkeypatch.setattr(api, "get_thinking_synthesis_run_or_404", AsyncMock(return_value=run))
    permission = AsyncMock()
    monkeypatch.setattr(api, "ensure_thinking_session_permission", permission)
    monkeypatch.setattr(api, "build_thinking_session_snapshot", AsyncMock(return_value={"id": str(session_id)}))
    request_db = SimpleNamespace(close=AsyncMock())
    poll_db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: refreshed_user)), refresh=AsyncMock())
    active = False
    @asynccontextmanager
    async def scope():
        nonlocal active
        active = True
        try: yield poll_db
        finally: active = False
    monkeypatch.setattr(database, "async_session_scope", scope)
    monkeypatch.setattr(database, "bind_db_user_context", lambda *_: None)
    response = await api.stream_thinking_session_synthesis_endpoint(session_id, api.ThinkingSynthesisStreamRequest(run_id=run_id), user, request_db)
    request_db.close.assert_awaited_once()
    iterator = response.body_iterator
    assert '"type": "start"' in await iterator.__anext__()
    assert '"type": "token"' in await iterator.__anext__()
    assert active is False
    assert permission.call_args.args[1] is refreshed_user
    assert '"type": "done"' in await iterator.__anext__()
    assert active is False
    poll_db.refresh.assert_awaited_once_with(session)
    await iterator.aclose()


@pytest.mark.asyncio
async def test_observer_rejects_superseded_pending_run(monkeypatch):
    from fastapi import HTTPException
    session_id, run_id = uuid4(), uuid4()
    session = SimpleNamespace(id=session_id, active_synthesis_run_id=uuid4(), phase="synthesizing")
    run = SimpleNamespace(id=run_id, status="pending")
    monkeypatch.setattr(api, "get_thinking_session_or_404", AsyncMock(return_value=session))
    monkeypatch.setattr(api, "get_thinking_synthesis_run_or_404", AsyncMock(return_value=run))
    monkeypatch.setattr(api, "ensure_thinking_session_permission", AsyncMock())
    with pytest.raises(HTTPException) as error:
        await api.stream_thinking_session_synthesis_endpoint(session_id, api.ThinkingSynthesisStreamRequest(run_id=run_id), object(), object())
    assert error.value.status_code == 409
