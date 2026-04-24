"""Domain helpers for note comments, mentions, reactions, and notifications."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
import re
from typing import Dict, Iterable, List, Mapping, Optional, Sequence
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models import (
    Note,
    NoteComment,
    NoteCollaborator,
    NoteCommentMention,
    NoteCommentReaction,
    NoteCommentReactionType,
    User as DBUser,
    UserNotification,
    UserNotificationType,
    WorkspaceMember,
)


MAX_COMMENT_DEPTH = 4
MENTION_TOKEN_PATTERN = re.compile(r"(?<![\w@])@([A-Za-z0-9][A-Za-z0-9._-]{0,63})")
MENTION_MIN_PREFIX_LENGTH = 3
MENTION_FUZZY_MIN_LENGTH = 5
MENTION_FUZZY_THRESHOLD = 0.92
MENTION_FUZZY_MARGIN = 0.08

REACTION_EMOJI_MAP: Mapping[NoteCommentReactionType, str] = {
    NoteCommentReactionType.THUMBS_UP: "👍",
    NoteCommentReactionType.HEART: "❤️",
    NoteCommentReactionType.LAUGH: "😂",
    NoteCommentReactionType.HOORAY: "🎉",
    NoteCommentReactionType.EYES: "👀",
    NoteCommentReactionType.ROCKET: "🚀",
}


@dataclass(frozen=True, slots=True)
class ParsedMentionToken:
    token: str
    start_offset: int
    end_offset: int


@dataclass(frozen=True, slots=True)
class WorkspaceMentionCandidate:
    user_id: UUID
    email: str
    full_name: Optional[str]
    aliases: tuple[str, ...]
    exact_keys: frozenset[str]
    fold_key: str

    @property
    def display_name(self) -> str:
        return (self.full_name or self.email).strip() or self.email


@dataclass(frozen=True, slots=True)
class ResolvedMention:
    user_id: UUID
    token: str
    start_offset: int
    end_offset: int
    matched_alias: str
    match_type: str


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def normalize_mention_key(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def fold_mention_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", normalize_mention_key(value))


def parse_comment_mentions(body: str) -> List[ParsedMentionToken]:
    mentions: List[ParsedMentionToken] = []
    for match in MENTION_TOKEN_PATTERN.finditer(body or ""):
        token = match.group(1).strip()
        if not token:
            continue
        mentions.append(
            ParsedMentionToken(
                token=token,
                start_offset=match.start(),
                end_offset=match.end(),
            )
        )
    return mentions


def _build_aliases(full_name: Optional[str], email: str) -> tuple[str, ...]:
    normalized_email = normalize_mention_key(email)
    local_part = normalized_email.split("@", 1)[0]
    aliases = {
        normalized_email,
        local_part,
    }

    normalized_full_name = normalize_mention_key(full_name or "")
    if normalized_full_name:
        aliases.add(normalized_full_name)
        aliases.add(normalized_full_name.replace(" ", "."))
        aliases.add(normalized_full_name.replace(" ", "_"))
        aliases.add(normalized_full_name.replace(" ", "-"))
        parts = [part for part in normalized_full_name.split(" ") if part]
        aliases.update(parts)
        aliases.add("".join(parts))

    return tuple(sorted(alias for alias in aliases if alias))


def build_workspace_mention_candidate(user: DBUser) -> WorkspaceMentionCandidate:
    aliases = _build_aliases(user.full_name, user.email)
    exact_keys = frozenset(normalize_mention_key(alias) for alias in aliases)
    fold_keys = {fold_mention_key(alias) for alias in aliases if fold_mention_key(alias)}
    preferred_fold = fold_mention_key(user.full_name or "") or fold_mention_key(user.email.split("@", 1)[0])
    if preferred_fold:
        fold_keys.add(preferred_fold)

    return WorkspaceMentionCandidate(
        user_id=user.id,
        email=user.email,
        full_name=user.full_name,
        aliases=aliases,
        exact_keys=exact_keys,
        fold_key=preferred_fold or next(iter(fold_keys), ""),
    )


@asynccontextmanager
async def begin_nested_if_supported(db: AsyncSession):
    """Use a savepoint when the session supports it, otherwise no-op."""
    begin_nested = getattr(db, "begin_nested", None)
    if callable(begin_nested):
        async with begin_nested():
            yield
        return
    yield


def resolve_mentions(
    tokens: Sequence[ParsedMentionToken],
    candidates: Sequence[WorkspaceMentionCandidate],
) -> List[ResolvedMention]:
    resolved: List[ResolvedMention] = []
    seen_user_ids: set[UUID] = set()

    for token in tokens:
        match = _resolve_single_mention(token, candidates)
        if match is None or match.user_id in seen_user_ids:
            continue
        seen_user_ids.add(match.user_id)
        resolved.append(match)

    return resolved


async def list_note_comment_notification_recipient_ids(db: AsyncSession, note: Note) -> set[UUID]:
    """Return explicit note followers for broad comment notifications."""
    recipient_ids: set[UUID] = {note.user_id}

    result = await db.execute(
        select(NoteCollaborator.user_id).where(NoteCollaborator.note_id == note.id)
    )
    for user_id in result.scalars().all():
        recipient_ids.add(user_id)

    return recipient_ids


def add_comment_notifications(
    db: AsyncSession,
    *,
    note: Note,
    comment: NoteComment,
    actor: DBUser,
    recipient_ids: Iterable[UUID],
    notification_type: UserNotificationType,
    body: str,
    parent_comment_id: Optional[UUID] = None,
    skip_user_ids: Optional[Iterable[UUID]] = None,
    payload_extra: Optional[Mapping[str, str]] = None,
) -> set[UUID]:
    skipped = set(skip_user_ids or [])
    notified_user_ids: set[UUID] = set()

    for recipient_id in recipient_ids:
        if recipient_id == actor.id or recipient_id in skipped or recipient_id in notified_user_ids:
            continue

        payload = {
            "comment_id": str(comment.id),
            "note_id": str(note.id),
            "note_title": note.title,
            "comment_excerpt": build_comment_excerpt(body),
        }
        if parent_comment_id is not None:
            payload["parent_comment_id"] = str(parent_comment_id)
        if payload_extra:
            payload.update(payload_extra)

        db.add(
            UserNotification(
                user_id=recipient_id,
                actor_user_id=actor.id,
                workspace_id=note.workspace_id,
                note_id=note.id,
                comment_id=comment.id,
                notification_type=notification_type,
                payload=payload,
            )
        )
        notified_user_ids.add(recipient_id)

    return notified_user_ids


def _resolve_single_mention(
    token: ParsedMentionToken,
    candidates: Sequence[WorkspaceMentionCandidate],
) -> Optional[ResolvedMention]:
    token_key = normalize_mention_key(token.token)
    token_fold = fold_mention_key(token.token)
    if not token_key or not token_fold:
        return None

    exact_matches = [candidate for candidate in candidates if token_key in candidate.exact_keys]
    if len(exact_matches) == 1:
        candidate = exact_matches[0]
        return ResolvedMention(
            user_id=candidate.user_id,
            token=token.token,
            start_offset=token.start_offset,
            end_offset=token.end_offset,
            matched_alias=token_key,
            match_type="exact",
        )
    if len(exact_matches) > 1:
        return None

    folded_matches = [candidate for candidate in candidates if token_fold == candidate.fold_key]
    if len(folded_matches) == 1:
        candidate = folded_matches[0]
        return ResolvedMention(
            user_id=candidate.user_id,
            token=token.token,
            start_offset=token.start_offset,
            end_offset=token.end_offset,
            matched_alias=candidate.fold_key,
            match_type="folded",
        )
    if len(folded_matches) > 1:
        return None

    if len(token_key) >= MENTION_MIN_PREFIX_LENGTH:
        prefix_matches = [
            candidate
            for candidate in candidates
            if any("@" not in alias and alias.startswith(token_key) for alias in candidate.aliases)
        ]
        unique_prefix_users = {candidate.user_id: candidate for candidate in prefix_matches}
        if len(unique_prefix_users) == 1:
            candidate = next(iter(unique_prefix_users.values()))
            return ResolvedMention(
                user_id=candidate.user_id,
                token=token.token,
                start_offset=token.start_offset,
                end_offset=token.end_offset,
                matched_alias=token_key,
                match_type="prefix",
            )
        if len(unique_prefix_users) > 1:
            return None

    if len(token_fold) < MENTION_FUZZY_MIN_LENGTH:
        return None

    scored_matches: List[tuple[float, WorkspaceMentionCandidate, str]] = []
    for candidate in candidates:
        best_alias = ""
        best_score = 0.0
        for alias in candidate.aliases:
            if "@" in alias:
                continue
            alias_fold = fold_mention_key(alias)
            if not alias_fold:
                continue
            if alias_fold[0] != token_fold[0]:
                continue
            if abs(len(alias_fold) - len(token_fold)) > 1:
                continue
            score = SequenceMatcher(None, token_fold, alias_fold).ratio()
            if score > best_score:
                best_score = score
                best_alias = alias
        if best_score > 0.0:
            scored_matches.append((best_score, candidate, best_alias))

    scored_matches.sort(key=lambda item: item[0], reverse=True)
    if not scored_matches:
        return None

    best_score, best_candidate, best_alias = scored_matches[0]
    second_score = scored_matches[1][0] if len(scored_matches) > 1 else 0.0
    if best_score < MENTION_FUZZY_THRESHOLD or (best_score - second_score) < MENTION_FUZZY_MARGIN:
        return None

    return ResolvedMention(
        user_id=best_candidate.user_id,
        token=token.token,
        start_offset=token.start_offset,
        end_offset=token.end_offset,
        matched_alias=best_alias,
        match_type="fuzzy",
    )


async def list_note_mention_candidates(db: AsyncSession, note: Note) -> List[WorkspaceMentionCandidate]:
    candidate_filters = [DBUser.id == note.user_id]
    if note.workspace_id is not None:
        candidate_filters.append(
            DBUser.id.in_(
                select(WorkspaceMember.user_id).where(WorkspaceMember.workspace_id == note.workspace_id)
            )
        )
    candidate_filters.append(
        DBUser.id.in_(
            select(NoteCollaborator.user_id).where(NoteCollaborator.note_id == note.id)
        )
    )

    result = await db.execute(
        select(DBUser)
        .where(or_(*candidate_filters))
        .order_by(DBUser.full_name.asc(), DBUser.email.asc(), DBUser.id.asc())
    )
    users = result.scalars().all()
    unique_users: Dict[UUID, DBUser] = {}
    for user in users:
        if user.email and user.id not in unique_users:
            unique_users[user.id] = user
    return [build_workspace_mention_candidate(user) for user in unique_users.values()]


async def load_note_comment_or_404(
    db: AsyncSession,
    *,
    note_id: UUID,
    comment_id: UUID,
) -> NoteComment:
    result = await db.execute(
        select(NoteComment)
        .where(
            NoteComment.id == comment_id,
            NoteComment.note_id == note_id,
            NoteComment.deleted_at.is_(None),
        )
        .options(
            selectinload(NoteComment.author),
            selectinload(NoteComment.resolved_by),
            selectinload(NoteComment.mentions).selectinload(NoteCommentMention.mentioned_user),
            selectinload(NoteComment.reactions).selectinload(NoteCommentReaction.user),
        )
    )
    comment = result.scalar_one_or_none()
    if comment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Comment not found")
    return comment


async def create_note_comment(
    db: AsyncSession,
    *,
    note: Note,
    author: DBUser,
    body: str,
    parent_comment_id: Optional[UUID] = None,
) -> NoteComment:
    normalized_body = (body or "").strip()
    if not normalized_body:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Comment body is required")

    async with begin_nested_if_supported(db):
        parent_comment: Optional[NoteComment] = None
        depth = 0
        if parent_comment_id is not None:
            parent_result = await db.execute(
                select(NoteComment)
                .where(
                    NoteComment.id == parent_comment_id,
                    NoteComment.note_id == note.id,
                    NoteComment.deleted_at.is_(None),
                )
                .with_for_update()
            )
            parent_comment = parent_result.scalar_one_or_none()
            if parent_comment is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Parent comment not found")
            if parent_comment.is_resolved:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Cannot reply to a resolved thread until it is reopened",
                )
            depth = int(parent_comment.depth or 0) + 1
            if depth > MAX_COMMENT_DEPTH:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Replies may be nested at most {MAX_COMMENT_DEPTH} levels deep",
                )

        comment = NoteComment(
            note_id=note.id,
            author_user_id=author.id,
            parent_comment_id=parent_comment.id if parent_comment is not None else None,
            depth=depth,
            body=normalized_body,
        )
        db.add(comment)
        await db.flush()

        mentioned_user_ids: set[UUID] = set()
        mention_tokens = parse_comment_mentions(normalized_body)
        if mention_tokens:
            candidates = await list_note_mention_candidates(db, note)
            resolved_mentions = resolve_mentions(mention_tokens, candidates)
            for mention in resolved_mentions:
                db.add(
                    NoteCommentMention(
                        comment_id=comment.id,
                        mentioned_user_id=mention.user_id,
                        mention_token=mention.token,
                        start_offset=mention.start_offset,
                        end_offset=mention.end_offset,
                    )
                )
                mentioned_user_ids.add(mention.user_id)
                add_comment_notifications(
                    db,
                    note=note,
                    comment=comment,
                    actor=author,
                    recipient_ids={mention.user_id},
                    notification_type=UserNotificationType.COMMENT_MENTION,
                    body=normalized_body,
                    payload_extra={"mention_token": mention.token},
                )

        if parent_comment is None:
            recipient_ids = await list_note_comment_notification_recipient_ids(db, note)
            add_comment_notifications(
                db,
                note=note,
                comment=comment,
                actor=author,
                recipient_ids=recipient_ids,
                notification_type=UserNotificationType.NOTE_COMMENT,
                body=normalized_body,
                skip_user_ids=mentioned_user_ids,
            )
        else:
            add_comment_notifications(
                db,
                note=note,
                comment=comment,
                actor=author,
                recipient_ids={parent_comment.author_user_id},
                notification_type=UserNotificationType.COMMENT_REPLY,
                body=normalized_body,
                parent_comment_id=parent_comment.id,
                skip_user_ids=mentioned_user_ids,
            )

        try:
            await db.flush()
        except IntegrityError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Comment creation conflicted with another update; please retry",
            ) from exc

    return await load_note_comment_or_404(db, note_id=note.id, comment_id=comment.id)


async def list_note_comments(
    db: AsyncSession,
    *,
    note_id: UUID,
    current_user_id: UUID,
) -> List[dict]:
    result = await db.execute(
        select(NoteComment)
        .where(
            NoteComment.note_id == note_id,
            NoteComment.deleted_at.is_(None),
        )
        .order_by(NoteComment.created_at.asc(), NoteComment.id.asc())
        .options(
            selectinload(NoteComment.author),
            selectinload(NoteComment.resolved_by),
            selectinload(NoteComment.mentions).selectinload(NoteCommentMention.mentioned_user),
            selectinload(NoteComment.reactions).selectinload(NoteCommentReaction.user),
        )
    )
    comments = list(result.scalars().all())
    return build_note_comment_tree(comments, current_user_id=current_user_id)


def build_note_comment_tree(comments: Sequence[NoteComment], *, current_user_id: UUID) -> List[dict]:
    serialized_by_id: Dict[UUID, dict] = {}
    roots: List[dict] = []

    for comment in comments:
        serialized_by_id[comment.id] = serialize_note_comment(comment, current_user_id=current_user_id)

    for comment in comments:
        serialized = serialized_by_id[comment.id]
        parent_id = comment.parent_comment_id
        if parent_id and parent_id in serialized_by_id:
            serialized_by_id[parent_id]["replies"].append(serialized)
        else:
            roots.append(serialized)

    return roots


def serialize_note_comment(comment: NoteComment, *, current_user_id: UUID) -> dict:
    reaction_counts: Dict[NoteCommentReactionType, int] = {reaction_type: 0 for reaction_type in NoteCommentReactionType}
    reacted_types: set[NoteCommentReactionType] = set()
    for reaction in comment.reactions or []:
        reaction_type = (
            reaction.emoji
            if isinstance(reaction.emoji, NoteCommentReactionType)
            else NoteCommentReactionType(str(reaction.emoji))
        )
        reaction_counts[reaction_type] += 1
        if reaction.user_id == current_user_id:
            reacted_types.add(reaction_type)

    mentions = sorted(comment.mentions or [], key=lambda mention: (mention.start_offset, mention.end_offset))
    return {
        "id": str(comment.id),
        "note_id": str(comment.note_id),
        "author_user_id": str(comment.author_user_id),
        "parent_comment_id": str(comment.parent_comment_id) if comment.parent_comment_id else None,
        "depth": int(comment.depth or 0),
        "body": comment.body,
        "is_resolved": bool(comment.is_resolved),
        "resolved_by_user_id": str(comment.resolved_by_user_id) if comment.resolved_by_user_id else None,
        "resolved_at": comment.resolved_at.isoformat() if comment.resolved_at else None,
        "created_at": comment.created_at.isoformat() if comment.created_at else None,
        "updated_at": comment.updated_at.isoformat() if comment.updated_at else None,
        "author": {
            "id": str(comment.author.id),
            "email": comment.author.email,
            "name": comment.author.full_name or comment.author.email,
        } if comment.author is not None else None,
        "resolved_by": {
            "id": str(comment.resolved_by.id),
            "email": comment.resolved_by.email,
            "name": comment.resolved_by.full_name or comment.resolved_by.email,
        } if comment.resolved_by is not None else None,
        "mentions": [
            {
                "id": str(mention.id),
                "comment_id": str(mention.comment_id),
                "mentioned_user_id": str(mention.mentioned_user_id),
                "mention_token": mention.mention_token,
                "start_offset": mention.start_offset,
                "end_offset": mention.end_offset,
                "user": {
                    "id": str(mention.mentioned_user.id),
                    "email": mention.mentioned_user.email,
                    "name": mention.mentioned_user.full_name or mention.mentioned_user.email,
                } if mention.mentioned_user is not None else None,
            }
            for mention in mentions
        ],
        "reactions": [
            {
                "emoji": reaction_type.value,
                "emoji_value": REACTION_EMOJI_MAP[reaction_type],
                "count": reaction_counts[reaction_type],
                "reacted_by_current_user": reaction_type in reacted_types,
            }
            for reaction_type in NoteCommentReactionType
        ],
        "replies": [],
    }


async def toggle_comment_reaction(
    db: AsyncSession,
    *,
    comment: NoteComment,
    user_id: UUID,
    emoji: str,
) -> bool:
    reaction_type = coerce_reaction_type(emoji)
    async with begin_nested_if_supported(db):
        await db.execute(
            select(NoteComment.id)
            .where(NoteComment.id == comment.id)
            .with_for_update()
        )
        result = await db.execute(
            select(NoteCommentReaction)
            .where(
                NoteCommentReaction.comment_id == comment.id,
                NoteCommentReaction.user_id == user_id,
                NoteCommentReaction.emoji == reaction_type,
            )
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            await db.delete(existing)
            await db.flush()
            return False

        db.add(
            NoteCommentReaction(
                comment_id=comment.id,
                user_id=user_id,
                emoji=reaction_type,
            )
        )
        try:
            await db.flush()
        except IntegrityError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Reaction update conflicted with another request; please retry",
            ) from exc
        return True


async def load_note_comment_thread(
    db: AsyncSession,
    *,
    note_id: UUID,
    comment_id: UUID,
    lock_rows: bool = False,
) -> List[NoteComment]:
    query = (
        select(NoteComment)
        .where(
            NoteComment.note_id == note_id,
            NoteComment.deleted_at.is_(None),
        )
        .order_by(NoteComment.created_at.asc(), NoteComment.id.asc())
    )
    if lock_rows:
        query = query.with_for_update()

    result = await db.execute(query)
    comments = list(result.scalars().all())
    comments_by_id = {item.id: item for item in comments}
    anchor = comments_by_id.get(comment_id)
    if anchor is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Comment not found")

    root = anchor
    while root.parent_comment_id and root.parent_comment_id in comments_by_id:
        root = comments_by_id[root.parent_comment_id]

    children_by_parent: Dict[Optional[UUID], List[NoteComment]] = {}
    for item in comments:
        children_by_parent.setdefault(item.parent_comment_id, []).append(item)

    thread_comments: List[NoteComment] = []
    queue: List[NoteComment] = [root]
    while queue:
        current = queue.pop(0)
        thread_comments.append(current)
        queue.extend(children_by_parent.get(current.id, []))

    return thread_comments


async def set_comment_resolution(
    db: AsyncSession,
    *,
    comment: NoteComment,
    resolved: bool,
    actor_user_id: UUID,
) -> NoteComment:
    async with begin_nested_if_supported(db):
        thread_comments = await load_note_comment_thread(
            db,
            note_id=comment.note_id,
            comment_id=comment.id,
            lock_rows=True,
        )

        changed_at = utc_now()
        for thread_comment in thread_comments:
            thread_comment.is_resolved = resolved
            thread_comment.resolved_by_user_id = actor_user_id if resolved else None
            thread_comment.resolved_at = changed_at if resolved else None
            thread_comment.updated_at = changed_at

        try:
            await db.flush()
        except IntegrityError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Thread resolution conflicted with another update; please retry",
            ) from exc

        for thread_comment in thread_comments:
            if thread_comment.id == comment.id:
                return thread_comment
    return comment


async def list_user_notifications(
    db: AsyncSession,
    *,
    user_id: UUID,
    limit: int = 50,
) -> List[UserNotification]:
    result = await db.execute(
        select(UserNotification)
        .where(UserNotification.user_id == user_id)
        .order_by(UserNotification.created_at.desc(), UserNotification.id.desc())
        .limit(limit)
        .options(
            selectinload(UserNotification.actor),
            selectinload(UserNotification.comment),
            selectinload(UserNotification.note),
        )
    )
    return list(result.scalars().all())


async def mark_notification_read(
    db: AsyncSession,
    *,
    notification_id: UUID,
    user_id: UUID,
) -> UserNotification:
    result = await db.execute(
        select(UserNotification)
        .where(
            UserNotification.id == notification_id,
            UserNotification.user_id == user_id,
        )
        .with_for_update()
        .options(
            selectinload(UserNotification.actor),
            selectinload(UserNotification.comment),
            selectinload(UserNotification.note),
        )
    )
    notification = result.scalar_one_or_none()
    if notification is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found")

    if not notification.is_read:
        notification.is_read = True
        notification.read_at = utc_now()
        await db.flush()
    return notification


def serialize_notification(notification: UserNotification) -> dict:
    return {
        "id": str(notification.id),
        "user_id": str(notification.user_id),
        "actor_user_id": str(notification.actor_user_id) if notification.actor_user_id else None,
        "workspace_id": str(notification.workspace_id) if notification.workspace_id else None,
        "note_id": str(notification.note_id) if notification.note_id else None,
        "comment_id": str(notification.comment_id) if notification.comment_id else None,
        "notification_type": (
            notification.notification_type.value
            if isinstance(notification.notification_type, UserNotificationType)
            else str(notification.notification_type)
        ),
        "payload": dict(notification.payload or {}),
        "is_read": bool(notification.is_read),
        "read_at": notification.read_at.isoformat() if notification.read_at else None,
        "created_at": notification.created_at.isoformat() if notification.created_at else None,
        "actor": {
            "id": str(notification.actor.id),
            "email": notification.actor.email,
            "name": notification.actor.full_name or notification.actor.email,
        } if notification.actor is not None else None,
    }


def coerce_reaction_type(value: str | NoteCommentReactionType) -> NoteCommentReactionType:
    try:
        return value if isinstance(value, NoteCommentReactionType) else NoteCommentReactionType(str(value))
    except ValueError as exc:
        allowed = ", ".join(reaction.value for reaction in NoteCommentReactionType)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid reaction. Allowed reactions: {allowed}",
        ) from exc


def build_comment_excerpt(body: str, *, limit: int = 160) -> str:
    compact = re.sub(r"\s+", " ", (body or "").strip())
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 1].rstrip()}…"
