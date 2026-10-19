from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import HTTPException
from unittest.mock import AsyncMock

from app.api import notes


def test_parse_uuid_or_422_returns_uuid_for_valid_value() -> None:
    identifier = uuid4()

    parsed = notes.parse_uuid_or_422(str(identifier), "note_id")

    assert parsed == identifier


def test_parse_uuid_or_422_raises_http_422_for_invalid_value() -> None:
    with pytest.raises(HTTPException) as exc_info:
        notes.parse_uuid_or_422("not-a-uuid", "note_id")

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == "Invalid note_id format"


@pytest.mark.asyncio
async def test_create_note_comment_endpoint_requires_update_permission(monkeypatch: pytest.MonkeyPatch) -> None:
    note_obj = type("NoteStub", (), {"id": uuid4(), "workspace_id": uuid4()})()
    user = type("UserStub", (), {"id": uuid4()})()
    db = object()
    ensure_permission = AsyncMock()

    monkeypatch.setattr(notes, "load_note_or_404", AsyncMock(return_value=note_obj))
    monkeypatch.setattr(notes, "ensure_note_permission", ensure_permission)
    monkeypatch.setattr(notes, "create_note_comment", AsyncMock(side_effect=RuntimeError("stop-after-permission")))

    with pytest.raises(RuntimeError, match="stop-after-permission"):
        await notes.create_note_comment_endpoint(
            note_id=str(note_obj.id),
            payload=notes.NoteCommentCreateRequest(body="Hello"),
            current_user=user,
            db=db,
        )

    ensure_permission.assert_awaited_once_with(note_obj, user, db, "update")


@pytest.mark.asyncio
async def test_create_note_reply_endpoint_requires_update_permission(monkeypatch: pytest.MonkeyPatch) -> None:
    note_obj = type("NoteStub", (), {"id": uuid4(), "workspace_id": uuid4()})()
    user = type("UserStub", (), {"id": uuid4()})()
    db = object()
    ensure_permission = AsyncMock()
    comment_id = uuid4()

    monkeypatch.setattr(notes, "load_note_or_404", AsyncMock(return_value=note_obj))
    monkeypatch.setattr(notes, "ensure_note_permission", ensure_permission)
    monkeypatch.setattr(notes, "create_note_comment", AsyncMock(side_effect=RuntimeError("stop-after-permission")))

    with pytest.raises(RuntimeError, match="stop-after-permission"):
        await notes.create_note_reply_endpoint(
            note_id=str(note_obj.id),
            comment_id=str(comment_id),
            payload=notes.NoteCommentCreateRequest(body="Reply"),
            current_user=user,
            db=db,
        )

    ensure_permission.assert_awaited_once_with(note_obj, user, db, "update")


@pytest.mark.asyncio
async def test_toggle_note_comment_reaction_endpoint_requires_update_permission(monkeypatch: pytest.MonkeyPatch) -> None:
    note_obj = type("NoteStub", (), {"id": uuid4(), "workspace_id": uuid4()})()
    user = type("UserStub", (), {"id": uuid4()})()
    db = object()
    ensure_permission = AsyncMock()
    comment_id = uuid4()

    monkeypatch.setattr(notes, "load_note_or_404", AsyncMock(return_value=note_obj))
    monkeypatch.setattr(notes, "ensure_note_permission", ensure_permission)
    monkeypatch.setattr(notes, "load_note_comment_or_404", AsyncMock(side_effect=RuntimeError("stop-after-permission")))

    with pytest.raises(RuntimeError, match="stop-after-permission"):
        await notes.toggle_note_comment_reaction_endpoint(
            note_id=str(note_obj.id),
            comment_id=str(comment_id),
            emoji="thumbs_up",
            current_user=user,
            db=db,
        )

    ensure_permission.assert_awaited_once_with(note_obj, user, db, "update")


def test_collaboration_payload_requires_explicit_content():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        notes.NoteCollaborationSync.model_validate({})
    assert notes.NoteCollaborationSync(content="").content == ""


@pytest.mark.asyncio
async def test_collaboration_cas_is_checked_after_locked_read(monkeypatch):
    from fastapi import BackgroundTasks
    note = type("NoteStub", (), {"id": uuid4(), "content": "newer"})()
    load = AsyncMock(return_value=note)
    monkeypatch.setattr(notes, "load_note_or_404", load)
    monkeypatch.setattr(notes, "ensure_note_permission", AsyncMock())
    with pytest.raises(HTTPException) as error:
        await notes.sync_note_collaboration(str(note.id), notes.NoteCollaborationSync(content="stale", base_content="old"),
                                            BackgroundTasks(), object(), object(), object())
    assert error.value.status_code == 409
    assert load.call_args.kwargs["for_update"] is True
