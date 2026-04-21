from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from app.database.models import NoteCollaborationRole, NoteInviteStatus
from app.services import note_invites as service


@dataclass
class FakeResult:
    value: object

    def scalar_one_or_none(self):
        return self.value

    def scalars(self):
        return self

    def first(self):
        return self.value


class FakeDb:
    def __init__(self, *results: object):
        self._results = list(results)
        self.flush = AsyncMock()
        self.execute = AsyncMock(side_effect=self._execute)

    async def _execute(self, *_args, **_kwargs):
        if not self._results:
            raise AssertionError("Unexpected execute() call")
        return FakeResult(self._results.pop(0))


@pytest.mark.asyncio
async def test_expire_note_invite_if_needed_marks_pending_expired():
    invite = SimpleNamespace(
        status=NoteInviteStatus.PENDING,
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        updated_at=None,
    )
    db = FakeDb()

    expired = await service.expire_note_invite_if_needed(invite, db)

    assert expired.status == NoteInviteStatus.EXPIRED
    db.flush.assert_awaited_once()


def test_validate_invite_target_rejects_wrong_account():
    invite = SimpleNamespace(
        invitee_user_id=None,
        invitee_email="owner@example.com",
    )
    user = SimpleNamespace(id="user-1", email="viewer@example.com")

    with pytest.raises(HTTPException) as exc_info:
        service.validate_invite_target(invite, user)

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_accept_note_invite_is_idempotent_for_already_accepted_invite(monkeypatch: pytest.MonkeyPatch):
    user = SimpleNamespace(id="user-1", email="invitee@example.com", is_superuser=False)
    invite = SimpleNamespace(
        note_id="note-1",
        status=NoteInviteStatus.ACCEPTED,
        invitee_user_id="user-1",
        invitee_email="invitee@example.com",
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        accepted_at=datetime.now(timezone.utc),
    )
    note = SimpleNamespace(id="note-1", user_id="owner-1")
    access = SimpleNamespace(can_view=True, can_update=False)
    db = FakeDb(invite)

    monkeypatch.setattr(service, "get_note_with_bypass", AsyncMock(return_value=note))
    monkeypatch.setattr(service, "resolve_note_access", AsyncMock(return_value=access))

    resolved_invite, resolved_note, resolved_access = await service.accept_note_invite("raw-token", user, db)

    assert resolved_invite is invite
    assert resolved_note is note
    assert resolved_access is access


@pytest.mark.asyncio
async def test_accept_note_invite_creates_collaborator_for_pending_invite(monkeypatch: pytest.MonkeyPatch):
    user = SimpleNamespace(id="user-1", email="invitee@example.com", is_superuser=False)
    invite = SimpleNamespace(
        note_id="note-1",
        inviter_user_id="owner-1",
        role=NoteCollaborationRole.EDITOR,
        status=NoteInviteStatus.PENDING,
        invitee_user_id=None,
        invitee_email="invitee@example.com",
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        accepted_at=None,
        updated_at=None,
    )
    note = SimpleNamespace(id="note-1", user_id="owner-1")
    access = SimpleNamespace(can_view=True, can_update=True)
    db = FakeDb(invite, None)
    db.add = Mock()

    monkeypatch.setattr(service, "get_note_with_bypass", AsyncMock(return_value=note))
    monkeypatch.setattr(service, "resolve_note_access", AsyncMock(return_value=access))

    resolved_invite, resolved_note, resolved_access = await service.accept_note_invite("raw-token", user, db)

    assert resolved_invite.status == NoteInviteStatus.ACCEPTED
    assert resolved_invite.invitee_user_id == "user-1"
    assert resolved_note is note
    assert resolved_access is access
    db.add.assert_called_once()
    db.flush.assert_awaited_once()
