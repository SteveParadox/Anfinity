from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from app.database.models import NoteComment, NoteCommentMention, NoteCommentReaction, NoteCommentReactionType, User as DBUser, UserNotification, UserNotificationType
from app.services import note_comments as service


class FakeResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def scalars(self):
        return self

    def all(self):
        return self.value


class _AsyncNullContext:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeDb:
    def __init__(self, *results):
        self._results = list(results)
        self.added = []
        self.flush = AsyncMock(side_effect=self._flush)
        self.execute = AsyncMock(side_effect=self._execute)
        self.delete = AsyncMock()

    def add(self, obj):
        self.added.append(obj)

    def begin_nested(self):
        return _AsyncNullContext()

    async def _execute(self, *_args, **_kwargs):
        if not self._results:
            raise AssertionError("Unexpected execute() call")
        return FakeResult(self._results.pop(0))

    async def _flush(self):
        now = datetime.now(timezone.utc)
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid4()
            if getattr(obj, "created_at", None) is None:
                obj.created_at = now
            if hasattr(obj, "updated_at") and getattr(obj, "updated_at", None) is None:
                obj.updated_at = now


def _make_user(email: str, full_name: str) -> DBUser:
    user = DBUser(email=email, full_name=full_name)
    user.id = uuid4()
    return user


def test_parse_comment_mentions_skips_email_addresses():
    body = "Ask @Jane-Doe and @sam.dev, but do not parse test@example.com as a mention."

    parsed = service.parse_comment_mentions(body)

    assert [item.token for item in parsed] == ["Jane-Doe", "sam.dev"]
    assert body[parsed[0].start_offset:parsed[0].end_offset] == "@Jane-Doe"


def test_resolve_mentions_prefers_exact_and_rejects_ambiguous_prefix():
    jane = service.build_workspace_mention_candidate(_make_user("jane@example.com", "Jane Doe"))
    alex_one = service.build_workspace_mention_candidate(_make_user("alex.jones@example.com", "Alex Jones"))
    alex_two = service.build_workspace_mention_candidate(_make_user("alex.johnson@example.com", "Alex Johnson"))

    tokens = [
        service.ParsedMentionToken(token="jane", start_offset=0, end_offset=5),
        service.ParsedMentionToken(token="alex", start_offset=6, end_offset=11),
    ]

    resolved = service.resolve_mentions(tokens, [jane, alex_one, alex_two])

    assert len(resolved) == 1
    assert resolved[0].user_id == jane.user_id
    assert resolved[0].match_type == "exact"


def test_resolve_mentions_rejects_short_fuzzy_tokens():
    jane = service.build_workspace_mention_candidate(_make_user("jane@example.com", "Jane Doe"))

    tokens = [service.ParsedMentionToken(token="jnae", start_offset=0, end_offset=5)]

    resolved = service.resolve_mentions(tokens, [jane])

    assert resolved == []


@pytest.mark.asyncio
async def test_list_note_mention_candidates_includes_collaborators_and_dedupes_users():
    owner = _make_user("owner@example.com", "Owner")
    member = _make_user("member@example.com", "Member")
    collaborator = _make_user("collab@example.com", "Collaborator")
    note = SimpleNamespace(id=uuid4(), workspace_id=uuid4(), user_id=owner.id)
    db = FakeDb([owner, member, collaborator, owner])

    candidates = await service.list_note_mention_candidates(db, note)

    assert {candidate.user_id for candidate in candidates} == {owner.id, member.id, collaborator.id}


@pytest.mark.asyncio
async def test_create_note_comment_creates_mentions_and_dedupes_reply_notification(monkeypatch: pytest.MonkeyPatch):
    author = _make_user("author@example.com", "Author")
    parent_author = _make_user("jane@example.com", "Jane Doe")
    teammate = _make_user("sam@example.com", "Sam Dev")
    note = SimpleNamespace(id=uuid4(), workspace_id=uuid4(), title="Planning note", user_id=author.id)
    parent_comment = NoteComment(
        id=uuid4(),
        note_id=note.id,
        author_user_id=parent_author.id,
        depth=0,
        body="Original thread",
    )

    db = FakeDb(parent_comment)

    candidates = [
        service.build_workspace_mention_candidate(parent_author),
        service.build_workspace_mention_candidate(teammate),
    ]
    monkeypatch.setattr(service, "list_note_mention_candidates", AsyncMock(return_value=candidates))

    async def fake_load(db_obj, *, note_id, comment_id):
        comment = next(item for item in db_obj.added if isinstance(item, NoteComment) and item.id == comment_id)
        comment.author = author
        comment.resolved_by = None
        comment.mentions = [item for item in db_obj.added if isinstance(item, NoteCommentMention) and item.comment_id == comment_id]
        for mention in comment.mentions:
            mention.mentioned_user = parent_author if mention.mentioned_user_id == parent_author.id else teammate
        comment.reactions = []
        return comment

    monkeypatch.setattr(service, "load_note_comment_or_404", fake_load)

    created = await service.create_note_comment(
        db,
        note=note,
        author=author,
        body="Looping in @jane and @sam on this reply.",
        parent_comment_id=parent_comment.id,
    )

    notifications = [item for item in db.added if isinstance(item, UserNotification)]
    mentions = [item for item in db.added if isinstance(item, NoteCommentMention)]

    assert created.parent_comment_id == parent_comment.id
    assert created.depth == 1
    assert len(mentions) == 2
    assert len(notifications) == 2
    assert {notification.user_id for notification in notifications} == {parent_author.id, teammate.id}
    assert all(notification.notification_type == UserNotificationType.COMMENT_MENTION for notification in notifications)


def test_build_note_comment_tree_nests_replies_and_sets_reaction_state():
    author = _make_user("author@example.com", "Author")
    reacter = _make_user("reacter@example.com", "Reacter")
    note_id = uuid4()
    root = NoteComment(
        id=uuid4(),
        note_id=note_id,
        author_user_id=author.id,
        body="Root",
        depth=0,
        created_at=datetime.now(timezone.utc),
    )
    reply = NoteComment(
        id=uuid4(),
        note_id=note_id,
        author_user_id=author.id,
        parent_comment_id=root.id,
        body="Reply",
        depth=1,
        created_at=datetime.now(timezone.utc),
    )
    root.author = author
    root.resolved_by = None
    root.mentions = []
    root.reactions = [
        NoteCommentReaction(
            id=uuid4(),
            comment_id=root.id,
            user_id=reacter.id,
            emoji=NoteCommentReactionType.THUMBS_UP,
        ),
        NoteCommentReaction(
            id=uuid4(),
            comment_id=root.id,
            user_id=author.id,
            emoji=NoteCommentReactionType.THUMBS_UP,
        ),
    ]
    reply.author = author
    reply.resolved_by = None
    reply.mentions = []
    reply.reactions = []

    tree = service.build_note_comment_tree([root, reply], current_user_id=author.id)

    assert len(tree) == 1
    assert tree[0]["replies"][0]["id"] == str(reply.id)
    thumbs_up = next(reaction for reaction in tree[0]["reactions"] if reaction["emoji"] == "thumbs_up")
    assert thumbs_up["count"] == 2
    assert thumbs_up["reacted_by_current_user"] is True


@pytest.mark.asyncio
async def test_create_note_comment_rejects_reply_beyond_depth_limit():
    author = _make_user("author@example.com", "Author")
    note = SimpleNamespace(id=uuid4(), workspace_id=uuid4(), title="Deep note", user_id=author.id)
    parent_comment = NoteComment(
        id=uuid4(),
        note_id=note.id,
        author_user_id=author.id,
        depth=service.MAX_COMMENT_DEPTH,
        body="Too deep",
    )
    db = FakeDb(parent_comment)

    with pytest.raises(HTTPException) as exc_info:
        await service.create_note_comment(
            db,
            note=note,
            author=author,
            body="Another reply",
            parent_comment_id=parent_comment.id,
        )

    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_create_note_comment_rejects_reply_to_resolved_thread():
    author = _make_user("author@example.com", "Author")
    note = SimpleNamespace(id=uuid4(), workspace_id=uuid4(), title="Resolved note", user_id=author.id)
    parent_comment = NoteComment(
        id=uuid4(),
        note_id=note.id,
        author_user_id=author.id,
        depth=0,
        body="Closed thread",
        is_resolved=True,
    )
    db = FakeDb(parent_comment)

    with pytest.raises(HTTPException) as exc_info:
        await service.create_note_comment(
            db,
            note=note,
            author=author,
            body="Trying to reply",
            parent_comment_id=parent_comment.id,
        )

    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_set_comment_resolution_updates_entire_thread_with_single_bulk_load():
    actor = _make_user("actor@example.com", "Actor")
    note_id = uuid4()
    root = NoteComment(
        id=uuid4(),
        note_id=note_id,
        author_user_id=actor.id,
        body="Root",
        depth=0,
        created_at=datetime.now(timezone.utc),
    )
    reply = NoteComment(
        id=uuid4(),
        note_id=note_id,
        author_user_id=actor.id,
        parent_comment_id=root.id,
        body="Reply",
        depth=1,
        created_at=datetime.now(timezone.utc),
    )
    cousin = NoteComment(
        id=uuid4(),
        note_id=note_id,
        author_user_id=actor.id,
        body="Other root",
        depth=0,
        created_at=datetime.now(timezone.utc),
    )
    db = FakeDb([root, reply, cousin])

    updated = await service.set_comment_resolution(
        db,
        comment=reply,
        resolved=True,
        actor_user_id=actor.id,
    )

    assert db.execute.await_count == 1
    assert updated.id == reply.id
    assert root.is_resolved is True
    assert reply.is_resolved is True
    assert cousin.is_resolved is False
    assert root.resolved_by_user_id == actor.id
    assert reply.resolved_by_user_id == actor.id
    assert root.resolved_at is not None
    assert reply.resolved_at is not None


@pytest.mark.asyncio
async def test_toggle_comment_reaction_returns_conflict_on_duplicate_insert_race():
    actor = _make_user("actor@example.com", "Actor")
    comment = NoteComment(
        id=uuid4(),
        note_id=uuid4(),
        author_user_id=actor.id,
        body="React here",
        depth=0,
    )
    db = FakeDb(None, None)
    db.flush = AsyncMock(side_effect=IntegrityError("insert", {}, Exception("duplicate key")))

    with pytest.raises(HTTPException) as exc_info:
        await service.toggle_comment_reaction(
            db,
            comment=comment,
            user_id=actor.id,
            emoji=NoteCommentReactionType.THUMBS_UP.value,
        )

    assert exc_info.value.status_code == 409
