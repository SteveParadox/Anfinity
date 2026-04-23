from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.core.audit import AuditAction
from app.database.models import ThinkingSessionPhase, ThinkingSynthesisStatus
from app.services import thinking_sessions as service


@dataclass
class FakeExecuteResult:
    rowcount: int = 1
    value: object | None = None

    def scalar_one_or_none(self):
        return self.value


class FakeDb:
    def __init__(self, *results: FakeExecuteResult):
        self._results = list(results)
        self.add = lambda *_args, **_kwargs: None
        self.flush = AsyncMock()
        self.commit = AsyncMock()
        self.refresh = AsyncMock()
        self.execute = AsyncMock(side_effect=self._execute)

    async def _execute(self, *_args, **_kwargs):
        if self._results:
            return self._results.pop(0)
        return FakeExecuteResult()


def make_contribution(identifier: str, created_at: datetime):
    return SimpleNamespace(
        id=identifier,
        created_at=created_at,
    )


def make_session(**overrides):
    now = datetime.now(timezone.utc)
    payload = {
        "id": uuid4(),
        "workspace_id": uuid4(),
        "title": "Weekly Synthesis",
        "prompt_context": "What matters next?",
        "created_by_user_id": uuid4(),
        "host_user_id": uuid4(),
        "phase": ThinkingSessionPhase.GATHERING,
        "phase_entered_at": now,
        "gathering_started_at": now,
        "synthesizing_started_at": None,
        "refining_started_at": None,
        "completed_at": None,
        "active_synthesis_run_id": None,
        "synthesis_output": "",
        "refined_output": "",
        "final_output": "",
        "updated_at": now,
        "creator": None,
        "host": None,
        "last_refined_by_user_id": None,
        "last_refined_by": None,
        "created_at": now,
        "note_id": None,
        "room_id": "thinking-session:test",
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


def make_run(**overrides):
    now = datetime.now(timezone.utc)
    payload = {
        "id": uuid4(),
        "session_id": uuid4(),
        "status": ThinkingSynthesisStatus.STREAMING,
        "output_text": "",
        "error_message": None,
        "started_at": now,
        "completed_at": None,
        "failed_at": None,
        "updated_at": now,
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


@pytest.mark.asyncio
async def test_transition_to_synthesizing_creates_snapshot_and_run(monkeypatch: pytest.MonkeyPatch):
    session = make_session()
    user = SimpleNamespace(id=session.host_user_id)
    db = FakeDb()
    added_objects = []
    db.add = added_objects.append

    async def fake_mark_participant_seen(*_args, **_kwargs):
        return None

    monkeypatch.setattr(service, "ensure_thinking_session_permission", AsyncMock(return_value=SimpleNamespace()))
    monkeypatch.setattr(service, "get_thinking_session_for_update", AsyncMock(return_value=session))
    monkeypatch.setattr(
        service,
        "build_contribution_snapshot_for_synthesis",
        AsyncMock(
            return_value=[
                {"id": "b", "vote_count": 4, "content": "B"},
                {"id": "a", "vote_count": 2, "content": "A"},
            ]
        ),
    )
    monkeypatch.setattr(service, "mark_thinking_session_participant_seen", fake_mark_participant_seen)

    updated_session, synthesis_run = await service.transition_thinking_session_phase(
        session,
        user,
        ThinkingSessionPhase.SYNTHESIZING,
        db,
    )

    assert updated_session.phase == ThinkingSessionPhase.SYNTHESIZING
    assert synthesis_run is not None
    assert synthesis_run.status == ThinkingSynthesisStatus.PENDING
    assert synthesis_run.snapshot_payload["contributions"][0]["id"] == "b"
    assert added_objects
    pending_events = db.info.get("pending_audit_events") or []
    assert len(pending_events) == 1
    assert pending_events[0].action_type == AuditAction.THINKING_SESSION_PHASE_TRANSITIONED
    db.commit.assert_awaited()


@pytest.mark.asyncio
async def test_transition_rejects_invalid_phase_change(monkeypatch: pytest.MonkeyPatch):
    session = make_session(phase=ThinkingSessionPhase.WAITING)
    user = SimpleNamespace(id=session.host_user_id)
    db = FakeDb()

    monkeypatch.setattr(service, "ensure_thinking_session_permission", AsyncMock(return_value=SimpleNamespace()))
    monkeypatch.setattr(service, "get_thinking_session_for_update", AsyncMock(return_value=session))

    with pytest.raises(HTTPException) as exc_info:
        await service.transition_thinking_session_phase(
            session,
            user,
            ThinkingSessionPhase.COMPLETED,
            db,
        )

    assert exc_info.value.status_code == 409


def test_order_contributions_prefers_votes_then_creation_then_id():
    early = datetime(2026, 4, 21, 8, 0, tzinfo=timezone.utc)
    late = datetime(2026, 4, 21, 9, 0, tzinfo=timezone.utc)
    contributions = [
        make_contribution("c", late),
        make_contribution("a", early),
        make_contribution("b", early),
    ]
    vote_counts = {
        "c": 5,
        "a": 2,
        "b": 2,
    }

    ordered = service.order_contributions(contributions, vote_counts)

    assert [entry.id for entry in ordered] == ["c", "a", "b"]


@pytest.mark.asyncio
async def test_claim_synthesis_run_for_streaming_rejects_duplicate_start(monkeypatch: pytest.MonkeyPatch):
    run_id = uuid4()
    session = make_session(
        phase=ThinkingSessionPhase.SYNTHESIZING,
        active_synthesis_run_id=run_id,
    )
    user = SimpleNamespace(id=session.host_user_id)
    db = FakeDb(FakeExecuteResult(rowcount=0))

    monkeypatch.setattr(service, "ensure_thinking_session_permission", AsyncMock(return_value=SimpleNamespace()))
    monkeypatch.setattr(service, "get_thinking_session_for_update", AsyncMock(return_value=session))

    with pytest.raises(HTTPException) as exc_info:
        await service.claim_synthesis_run_for_streaming(
            session,
            run_id,
            user,
            db,
        )

    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_transition_uses_locked_session_state_before_creating_run(monkeypatch: pytest.MonkeyPatch):
    stale_session = make_session()
    locked_session = make_session(
        id=stale_session.id,
        phase=ThinkingSessionPhase.SYNTHESIZING,
        active_synthesis_run_id=uuid4(),
    )
    user = SimpleNamespace(id=stale_session.host_user_id)
    db = FakeDb()

    monkeypatch.setattr(service, "ensure_thinking_session_permission", AsyncMock(return_value=SimpleNamespace()))
    monkeypatch.setattr(service, "get_thinking_session_for_update", AsyncMock(return_value=locked_session))

    with pytest.raises(HTTPException) as exc_info:
        await service.transition_thinking_session_phase(
            stale_session,
            user,
            ThinkingSessionPhase.SYNTHESIZING,
            db,
        )

    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_persist_progress_is_ignored_after_completion(monkeypatch: pytest.MonkeyPatch):
    run_id = uuid4()
    stale_session = make_session(
        phase=ThinkingSessionPhase.SYNTHESIZING,
        active_synthesis_run_id=run_id,
        synthesis_output="Partial output",
    )
    locked_session = make_session(
        id=stale_session.id,
        phase=ThinkingSessionPhase.REFINING,
        active_synthesis_run_id=None,
        synthesis_output="Final synthesis",
        refined_output="Final synthesis",
    )
    stale_run = make_run(id=run_id, session_id=stale_session.id, status=ThinkingSynthesisStatus.STREAMING)
    locked_run = make_run(
        id=run_id,
        session_id=stale_session.id,
        status=ThinkingSynthesisStatus.COMPLETED,
        output_text="Final synthesis",
    )
    db = FakeDb()

    monkeypatch.setattr(service, "get_thinking_session_for_update", AsyncMock(return_value=locked_session))
    monkeypatch.setattr(service, "get_thinking_synthesis_run_for_update", AsyncMock(return_value=locked_run))

    result = await service.persist_synthesis_progress(
        stale_session,
        stale_run,
        "Older partial output",
        db,
    )

    assert result is locked_session
    assert locked_session.synthesis_output == "Final synthesis"
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_persist_progress_does_not_regress_to_shorter_output(monkeypatch: pytest.MonkeyPatch):
    run_id = uuid4()
    session = make_session(
        phase=ThinkingSessionPhase.SYNTHESIZING,
        active_synthesis_run_id=run_id,
        synthesis_output="Already longer output",
    )
    run = make_run(
        id=run_id,
        session_id=session.id,
        status=ThinkingSynthesisStatus.STREAMING,
        output_text="Already longer output",
    )
    db = FakeDb()

    monkeypatch.setattr(service, "get_thinking_session_for_update", AsyncMock(return_value=session))
    monkeypatch.setattr(service, "get_thinking_synthesis_run_for_update", AsyncMock(return_value=run))

    result = await service.persist_synthesis_progress(
        session,
        run,
        "Short",
        db,
    )

    assert result is session
    assert session.synthesis_output == "Already longer output"
    assert run.output_text == "Already longer output"
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_contribution_stages_audit_event(monkeypatch: pytest.MonkeyPatch):
    session = make_session(note_id=uuid4())
    user = SimpleNamespace(id=uuid4())
    db = FakeDb()
    added_objects = []
    db.add = added_objects.append

    monkeypatch.setattr(service, "get_thinking_session_for_update", AsyncMock(return_value=session))
    monkeypatch.setattr(service, "ensure_thinking_session_permission", AsyncMock(return_value=SimpleNamespace()))
    monkeypatch.setattr(service, "mark_thinking_session_participant_seen", AsyncMock(return_value=None))

    contribution = await service.create_contribution(session, user, "A strong idea", db)

    assert contribution.content == "A strong idea"
    assert added_objects
    pending_events = db.info.get("pending_audit_events") or []
    assert len(pending_events) == 1
    assert pending_events[0].action_type == AuditAction.CONTRIBUTION_SUBMITTED


@pytest.mark.asyncio
async def test_toggle_vote_stages_vote_cast_event(monkeypatch: pytest.MonkeyPatch):
    contribution_id = uuid4()
    session = make_session(note_id=uuid4())
    user = SimpleNamespace(id=uuid4())
    contribution = SimpleNamespace(id=contribution_id)
    db = FakeDb(
        FakeExecuteResult(value=contribution),
        FakeExecuteResult(value=None),
    )

    monkeypatch.setattr(service, "get_thinking_session_for_update", AsyncMock(return_value=session))
    monkeypatch.setattr(service, "ensure_thinking_session_permission", AsyncMock(return_value=SimpleNamespace()))
    monkeypatch.setattr(service, "mark_thinking_session_participant_seen", AsyncMock(return_value=None))

    voted = await service.toggle_contribution_vote(session, contribution_id, user, db)

    assert voted is True
    pending_events = db.info.get("pending_audit_events") or []
    assert len(pending_events) == 1
    assert pending_events[0].action_type == AuditAction.VOTE_CAST
