from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.core import audit as audit_module
from app.database import session as session_module


@pytest.mark.asyncio
async def test_log_audit_event_stages_normalized_payload() -> None:
    db = SimpleNamespace(info={})
    actor_user_id = uuid4()
    workspace_id = uuid4()
    note_id = uuid4()

    payload = await audit_module.log_audit_event(
        db=db,
        action=audit_module.AuditAction.NOTE_CREATED,
        actor_user_id=actor_user_id,
        workspace_id=workspace_id,
        note_id=note_id,
        entity_type=audit_module.EntityType.NOTE,
        entity_id=note_id,
        metadata={"title": "Draft"},
        request_id="req-123",
        source="test.audit",
    )

    assert payload.action_type == audit_module.AuditAction.NOTE_CREATED
    assert payload.entity_type == audit_module.EntityType.NOTE
    assert payload.entity_id == str(note_id)
    assert payload.actor_user_id == actor_user_id
    assert payload.workspace_id == workspace_id
    assert payload.note_id == note_id
    assert payload.metadata_json == {"title": "Draft"}
    assert payload.request_id == "req-123"
    assert payload.source == "test.audit"
    assert payload.counts_toward_note_contributions is True
    assert db.info[audit_module.PENDING_AUDIT_EVENTS_KEY] == [payload]


@pytest.mark.asyncio
async def test_typed_shortcut_for_vote_removed_does_not_count_toward_contributions() -> None:
    db = SimpleNamespace(info={})
    payload = await audit_module.audit.vote_removed(
        db,
        actor_user_id=uuid4(),
        workspace_id=uuid4(),
        thinking_session_id=uuid4(),
        note_id=uuid4(),
        contribution_id=uuid4(),
        metadata={"source": "test"},
    )

    assert payload.action_type == audit_module.AuditAction.VOTE_REMOVED
    assert payload.counts_toward_note_contributions is False


def test_after_commit_dispatches_pending_events_and_clears_session_info(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = audit_module.AuditEventPayload(
        action_type=audit_module.AuditAction.NOTE_UPDATED,
        entity_type=audit_module.EntityType.NOTE,
        entity_id=str(uuid4()),
        actor_user_id=uuid4(),
        workspace_id=uuid4(),
        note_id=uuid4(),
        metadata_json={"changed_fields": ["content"]},
    )
    session = SimpleNamespace(info={audit_module.PENDING_AUDIT_EVENTS_KEY: [payload]})
    dispatched: list[list[audit_module.AuditEventPayload]] = []

    monkeypatch.setattr(
        audit_module,
        "dispatch_pending_audit_events",
        lambda events: dispatched.append(list(events)),
    )

    session_module._dispatch_pending_audit_events_after_commit(session)

    assert dispatched == [[payload]]
    assert session.info.get(audit_module.PENDING_AUDIT_EVENTS_KEY) is None


def test_dispatch_pending_events_without_loop_uses_sync_persistence(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = audit_module.AuditEventPayload(
        action_type=audit_module.AuditAction.NOTE_DELETED,
        entity_type=audit_module.EntityType.NOTE,
        entity_id=str(uuid4()),
        actor_user_id=uuid4(),
        workspace_id=uuid4(),
        note_id=uuid4(),
        metadata_json={},
    )
    persisted: list[list[audit_module.AuditEventPayload]] = []

    monkeypatch.setattr(
        audit_module,
        "_persist_audit_events_sync",
        lambda events: persisted.append(list(events)),
    )

    audit_module.dispatch_pending_audit_events([payload])

    assert persisted == [[payload]]
