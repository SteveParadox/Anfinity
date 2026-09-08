"""Regression coverage for database ownership on request and stream paths."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from starlette.websockets import WebSocketDisconnect

from app.api import chat as chat_api
from app.api import embeddings as embeddings_api
from app.api import monitoring as monitoring_api
from app.core import auth as auth_module
from app.database import session as session_module
from app.events import websocket as websocket_module


class _TrackedSession:
    def __init__(self, factory: "_TrackedSessionFactory", *, commit_error: Exception | None = None):
        self.factory = factory
        self.commit_error = commit_error
        self.commit_calls = 0
        self.rollback_calls = 0
        self.close_calls = 0
        self.factory.checked_out += 1

    async def commit(self) -> None:
        self.commit_calls += 1
        if self.commit_error:
            raise self.commit_error

    async def rollback(self) -> None:
        self.rollback_calls += 1

    async def close(self) -> None:
        self.close_calls += 1
        if self.close_calls == 1:
            self.factory.checked_out -= 1


class _TrackedSessionFactory:
    def __init__(self, *, commit_error: Exception | None = None):
        self.checked_out = 0
        self.commit_error = commit_error
        self.sessions: list[_TrackedSession] = []

    def __call__(self) -> _TrackedSession:
        session = _TrackedSession(self, commit_error=self.commit_error)
        self.sessions.append(session)
        return session


async def _finish_dependency(dependency) -> None:
    with pytest.raises(StopAsyncIteration):
        await anext(dependency)


@pytest.mark.asyncio
async def test_normal_requests_do_not_accumulate_checked_out_sessions(monkeypatch):
    factory = _TrackedSessionFactory()
    monkeypatch.setattr(session_module, "AsyncSessionLocal", factory)

    for _ in range(10):
        dependency = session_module.get_db()
        await anext(dependency)
        await _finish_dependency(dependency)

    assert factory.checked_out == 0
    assert all(item.commit_calls == 1 and item.close_calls == 1 for item in factory.sessions)


@pytest.mark.asyncio
async def test_database_exception_rolls_back_and_releases_session(monkeypatch):
    factory = _TrackedSessionFactory()
    monkeypatch.setattr(session_module, "AsyncSessionLocal", factory)
    dependency = session_module.get_db()
    await anext(dependency)

    with pytest.raises(RuntimeError, match="query failed"):
        await dependency.athrow(RuntimeError("query failed"))

    tracked = factory.sessions[0]
    assert factory.checked_out == 0
    assert tracked.rollback_calls == 1
    assert tracked.close_calls == 1


@pytest.mark.asyncio
async def test_commit_failure_rolls_back_and_releases_session(monkeypatch):
    factory = _TrackedSessionFactory(commit_error=RuntimeError("commit failed"))
    monkeypatch.setattr(session_module, "AsyncSessionLocal", factory)
    dependency = session_module.get_db()
    await anext(dependency)

    with pytest.raises(RuntimeError, match="commit failed"):
        await anext(dependency)

    tracked = factory.sessions[0]
    assert factory.checked_out == 0
    assert tracked.rollback_calls == 1
    assert tracked.close_calls == 1


@pytest.mark.asyncio
async def test_request_cancellation_rolls_back_and_releases_session(monkeypatch):
    factory = _TrackedSessionFactory()
    monkeypatch.setattr(session_module, "AsyncSessionLocal", factory)
    dependency = session_module.get_db()
    await anext(dependency)

    with pytest.raises(asyncio.CancelledError):
        await dependency.athrow(asyncio.CancelledError())

    tracked = factory.sessions[0]
    assert factory.checked_out == 0
    assert tracked.rollback_calls == 1
    assert tracked.close_calls == 1


@pytest.mark.asyncio
async def test_system_health_check_releases_its_database_session(monkeypatch):
    closed = False
    executed = []

    class _HealthSession:
        async def execute(self, statement) -> None:
            executed.append(str(statement))

    @asynccontextmanager
    async def health_scope(*, commit_on_success=False):
        nonlocal closed
        del commit_on_success
        try:
            yield _HealthSession()
        finally:
            closed = True

    class _Broadcaster:
        _redis = None

    async def get_broadcaster():
        return _Broadcaster()

    monkeypatch.setattr(monitoring_api, "async_session_scope", health_scope)
    monkeypatch.setattr("app.events.get_broadcaster", get_broadcaster)
    monkeypatch.setattr("app.services.vector_db.get_vector_db_client", lambda **_kwargs: object())

    health = await monitoring_api.system_health(SimpleNamespace())

    assert health["components"]["database"] == "healthy"
    assert executed
    assert closed


def test_websocket_auth_prefers_the_non_url_subprotocol_token():
    websocket = SimpleNamespace(
        headers={"sec-websocket-protocol": "chat, anfinity.jwt.subprotocol-token"},
        query_params={"token": "legacy-url-token"},
    )

    token, subprotocol = auth_module.get_websocket_auth_token(websocket)

    assert token == "subprotocol-token"
    assert subprotocol == "anfinity.jwt.subprotocol-token"


class _FakeWebSocket:
    def __init__(self):
        self.closed: list[tuple[int | None, str | None]] = []
        self.accepted = False
        self.headers = {}
        self.query_params = {}

    async def accept(self, subprotocol=None) -> None:
        del subprotocol
        self.accepted = True

    async def close(self, code=None, reason=None) -> None:
        self.closed.append((code, reason))

    async def receive_text(self) -> str:
        raise WebSocketDisconnect()

    async def send_json(self, _message) -> None:
        return None


class _FakePubSub:
    def __init__(self):
        self.unsubscribed = False
        self.closed = False

    async def listen(self):
        await asyncio.Event().wait()
        yield {}

    async def unsubscribe(self) -> None:
        self.unsubscribed = True

    async def aclose(self) -> None:
        self.closed = True


class _FakeBroadcaster:
    def __init__(self):
        self.subscriptions: list[_FakePubSub] = []

    async def subscribe(self, _channel: str) -> _FakePubSub:
        pubsub = _FakePubSub()
        self.subscriptions.append(pubsub)
        return pubsub


@pytest.mark.asyncio
async def test_websocket_disconnect_releases_db_before_stream_and_reconnects_cleanly(monkeypatch):
    factory = _TrackedSessionFactory()
    broadcaster = _FakeBroadcaster()
    user_id = uuid4()

    @asynccontextmanager
    async def tracked_scope(*, commit_on_success=False):
        del commit_on_success
        db = factory()
        try:
            yield db
        finally:
            await db.close()

    async def authenticate(_websocket, _db):
        return SimpleNamespace(id=user_id)

    async def authorize(_workspace_id, received_user_id, _db):
        assert received_user_id == user_id

    async def get_broadcaster():
        return broadcaster

    monkeypatch.setattr(websocket_module, "async_session_scope", tracked_scope)
    monkeypatch.setattr(websocket_module, "get_websocket_user", authenticate)
    monkeypatch.setattr(websocket_module, "_assert_workspace_access", authorize)
    monkeypatch.setattr(websocket_module, "get_broadcaster", get_broadcaster)
    monkeypatch.setattr(websocket_module, "manager", websocket_module.ConnectionManager())

    workspace_id = str(uuid4())
    await websocket_module.websocket_ingestion_events(_FakeWebSocket(), workspace_id)
    await websocket_module.websocket_ingestion_events(_FakeWebSocket(), workspace_id)

    assert factory.checked_out == 0
    assert websocket_module.manager.total_connections == 0
    assert len(broadcaster.subscriptions) == 2
    assert all(item.unsubscribed and item.closed for item in broadcaster.subscriptions)


@pytest.mark.asyncio
async def test_background_embedding_work_owns_a_new_session(monkeypatch):
    task_db = object()
    closed = False
    processor_dbs: list[object] = []

    @asynccontextmanager
    async def task_scope(*, commit_on_success=False):
        nonlocal closed
        del commit_on_success
        try:
            yield task_db
        finally:
            closed = True

    class _Embedder:
        _provider = object()
        dimension = 42

        def __init__(self, **_kwargs):
            pass

    class _Processor:
        def __init__(self, *, db, **_kwargs):
            processor_dbs.append(db)

        async def process_document_chunks(self, document_id, workspace_id):
            return {"document_id": document_id, "workspace_id": workspace_id}

    monkeypatch.setattr(embeddings_api, "async_session_scope", task_scope)
    monkeypatch.setattr(embeddings_api, "Embedder", _Embedder)
    monkeypatch.setattr(embeddings_api, "BatchEmbeddingProcessor", _Processor)
    monkeypatch.setattr(embeddings_api, "get_vector_db_client", lambda **_kwargs: object())

    document_id, workspace_id = uuid4(), uuid4()
    result = await embeddings_api._process_document_embeddings_in_background(
        document_id,
        workspace_id,
        10,
    )

    assert result == {"document_id": document_id, "workspace_id": workspace_id}
    assert processor_dbs == [task_db]
    assert closed


@pytest.mark.asyncio
async def test_ask_past_self_stream_owns_and_closes_its_database_session(monkeypatch, caplog):
    factory = _TrackedSessionFactory()
    workspace_id = uuid4()
    user_id = uuid4()
    streamed_dbs: list[object] = []
    seen_correlation_ids: list[str] = []

    @asynccontextmanager
    async def tracked_scope(*, commit_on_success=False):
        del commit_on_success
        db = factory()
        try:
            yield db
        finally:
            await db.close()

    async def allow_workspace_access(*_args, **_kwargs) -> None:
        return None

    async def fake_rag_stream(**kwargs):
        streamed_dbs.append(kwargs["db"])
        seen_correlation_ids.append(kwargs["correlation_id"])
        yield chat_api._sse_event("start", {"correlationId": kwargs["correlation_id"]})
        yield chat_api._sse_event("done", {"correlationId": kwargs["correlation_id"]})

    caplog.set_level("INFO", logger=chat_api.logger.name)
    monkeypatch.setattr(chat_api, "async_session_scope", tracked_scope)
    monkeypatch.setattr(chat_api, "_verify_workspace_access", allow_workspace_access)
    monkeypatch.setattr(chat_api, "_rag_stream", fake_rag_stream)

    response = await chat_api.ask_past_self(
        chat_api.AskPastSelfRequest(
            workspace_id=workspace_id,
            query="What did I write about ranking?",
        ),
        current_user=SimpleNamespace(id=user_id),
        db=SimpleNamespace(),
    )

    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk)

    assert factory.checked_out == 0
    assert len(factory.sessions) == 1
    assert factory.sessions[0].close_calls == 1
    assert streamed_dbs == [factory.sessions[0]]
    assert seen_correlation_ids and all(seen_correlation_ids)
    assert any("ask_past_self_stream_session_opening" in record.message for record in caplog.records)
    assert any("ask_past_self_stream_session_closed" in record.message for record in caplog.records)
    assert any("event: done" in chunk for chunk in chunks)
