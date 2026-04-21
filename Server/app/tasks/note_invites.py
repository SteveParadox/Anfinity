"""Lifecycle helpers for secure note collaboration invites."""
from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import secrets
from typing import Optional
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    Note,
    NoteCollaborator,
    NoteCollaborationRole,
    NoteInvite,
    NoteInviteStatus,
    User as DBUser,
)
from app.database.session import bind_db_rls_bypass, bind_db_user_context, get_session_info
from app.services.note_access import NoteAccessContext, resolve_note_access


DEFAULT_NOTE_INVITE_TTL = timedelta(days=7)


@dataclass(slots=True)
class CreatedNoteInvite:
    invite: Optional[NoteInvite]
    token: Optional[str]
    created: bool
    updated_collaborator: Optional[NoteCollaborator] = None


def normalize_email(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    normalized = value.strip().lower()
    return normalized or None


def hash_note_invite_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_note_invite_token() -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    return token, hash_note_invite_token(token)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _coerce_collaboration_role(role: NoteCollaborationRole | str) -> NoteCollaborationRole:
    return role if isinstance(role, NoteCollaborationRole) else NoteCollaborationRole(str(role))


def role_rank(role: NoteCollaborationRole | str) -> int:
    normalized = _coerce_collaboration_role(role)
    return {
        NoteCollaborationRole.VIEWER: 1,
        NoteCollaborationRole.EDITOR: 2,
    }[normalized]


def choose_broader_role(
    left: NoteCollaborationRole | str,
    right: NoteCollaborationRole | str,
) -> NoteCollaborationRole:
    left_role = _coerce_collaboration_role(left)
    right_role = _coerce_collaboration_role(right)
    return left_role if role_rank(left_role) >= role_rank(right_role) else right_role


@asynccontextmanager
async def temporary_rls_bypass(db: AsyncSession):
    """Temporarily bypass row-level security for internal invite resolution steps."""

    session_info = get_session_info(db)
    previous_bypass = bool(session_info.get("app_rls_bypass", False))
    previous_user_id = session_info.get("app_current_user_id")

    bind_db_rls_bypass(db, True)
    await db.execute(text("select set_config('app.rls_bypass', 'true', true)"))
    await db.execute(text("select set_config('app.current_user_id', '', true)"))

    try:
        yield
    finally:
        if previous_bypass:
            bind_db_rls_bypass(db, True)
            await db.execute(text("select set_config('app.rls_bypass', 'true', true)"))
        else:
            session_info["app_rls_bypass"] = False
            await db.execute(text("select set_config('app.rls_bypass', 'false', true)"))

        if previous_user_id:
            bind_db_user_context(db, previous_user_id)
            await db.execute(
                text("select set_config('app.current_user_id', :value, true)"),
                {"value": str(previous_user_id)},
            )
        else:
            session_info.pop("app_current_user_id", None)
            await db.execute(text("select set_config('app.current_user_id', '', true)"))


async def get_note_with_bypass(db: AsyncSession, note_id: UUID) -> Optional[Note]:
    async with temporary_rls_bypass(db):
        result = await db.execute(select(Note).where(Note.id == note_id))
        return result.scalar_one_or_none()


async def get_note_invite_by_token(db: AsyncSession, token: str) -> Optional[NoteInvite]:
    token_hash = hash_note_invite_token(token)
    result = await db.execute(select(NoteInvite).where(NoteInvite.token_hash == token_hash))
    return result.scalar_one_or_none()


async def expire_note_invite_if_needed(invite: NoteInvite, db: AsyncSession) -> NoteInvite:
    if invite.status == NoteInviteStatus.PENDING and invite.expires_at <= utc_now():
        invite.status = NoteInviteStatus.EXPIRED
        invite.updated_at = utc_now()
        await db.flush()
    return invite


def ensure_note_invite_status_usable(invite: NoteInvite) -> None:
    if invite.status == NoteInviteStatus.REVOKED:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Invite has been revoked")
    if invite.status == NoteInviteStatus.EXPIRED:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Invite has expired")
    if invite.status == NoteInviteStatus.ACCEPTED:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Invite has already been accepted")


def validate_invite_target(invite: NoteInvite, user: DBUser) -> None:
    normalized_user_email = normalize_email(user.email)
    normalized_invite_email = normalize_email(invite.invitee_email)

    if invite.invitee_user_id is not None and invite.invitee_user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invite does not belong to this account",
        )

    if normalized_invite_email is not None and normalized_invite_email != normalized_user_email:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invite does not belong to this account",
        )


async def resolve_invite_target_user(
    db: AsyncSession,
    invitee_email: Optional[str],
    invitee_user_id: Optional[UUID],
) -> Optional[DBUser]:
    if invitee_user_id is not None:
        result = await db.execute(select(DBUser).where(DBUser.id == invitee_user_id))
        user = result.scalar_one_or_none()
        if user is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invitee user not found")
        return user

    normalized_email = normalize_email(invitee_email)
    if normalized_email is None:
        return None

    result = await db.execute(select(DBUser).where(DBUser.email == normalized_email))
    return result.scalar_one_or_none()


async def ensure_invitable_target(
    note: Note,
    target_user: Optional[DBUser],
    requested_role: NoteCollaborationRole,
    db: AsyncSession,
) -> Optional[NoteCollaborator]:
    if target_user is None:
        return None

    access = await resolve_note_access(note, target_user, db)
    if access.can_manage and access.access_source in {"owner", "workspace", "superuser"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Target user already has equal or broader access",
        )

    if access.can_update and role_rank(requested_role) <= role_rank(NoteCollaborationRole.EDITOR):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Target user already has equal or broader access",
        )

    if access.can_view and requested_role == NoteCollaborationRole.VIEWER:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Target user already has equal or broader access",
        )

    return access.collaborator_record


async def create_note_invite(
    note: Note,
    inviter: DBUser,
    invitee_email: Optional[str],
    invitee_user_id: Optional[UUID],
    role: NoteCollaborationRole,
    db: AsyncSession,
    *,
    message: Optional[str] = None,
    expires_in: timedelta = DEFAULT_NOTE_INVITE_TTL,
) -> CreatedNoteInvite:
    normalized_email = normalize_email(invitee_email)
    target_user = await resolve_invite_target_user(db, normalized_email, invitee_user_id)
    collaborator_record = await ensure_invitable_target(note, target_user, role, db)

    if target_user is not None and target_user.id == inviter.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="You already have access to this note")

    if collaborator_record is not None:
        existing_role = _coerce_collaboration_role(collaborator_record.role)
        if existing_role == role:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Target user already has this note access")

        collaborator_record.role = choose_broader_role(existing_role, role)
        collaborator_record.granted_by_user_id = inviter.id
        collaborator_record.updated_at = utc_now()
        await db.flush()
        return CreatedNoteInvite(
            invite=None,
            token=None,
            created=False,
            updated_collaborator=collaborator_record,
        )

    pending_query = select(NoteInvite).where(
        NoteInvite.note_id == note.id,
        NoteInvite.status == NoteInviteStatus.PENDING,
    )
    if target_user is not None:
        pending_query = pending_query.where(
            or_(
                NoteInvite.invitee_user_id == target_user.id,
                NoteInvite.invitee_email == normalize_email(target_user.email),
            )
        )
    else:
        pending_query = pending_query.where(NoteInvite.invitee_email == normalized_email)

    pending_result = await db.execute(
        pending_query.order_by(NoteInvite.created_at.desc()).with_for_update()
    )
    existing_invite = pending_result.scalars().first()
    if existing_invite is not None:
        await expire_note_invite_if_needed(existing_invite, db)
        if existing_invite.status == NoteInviteStatus.PENDING:
            token, token_hash = generate_note_invite_token()
            existing_invite.inviter_user_id = inviter.id
            existing_invite.invitee_email = normalized_email or existing_invite.invitee_email
            existing_invite.invitee_user_id = target_user.id if target_user is not None else existing_invite.invitee_user_id
            existing_invite.role = role
            existing_invite.token_hash = token_hash
            existing_invite.message = message
            existing_invite.expires_at = utc_now() + expires_in
            existing_invite.updated_at = utc_now()
            try:
                await db.flush()
            except IntegrityError as exc:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="An active invite for this target already exists",
                ) from exc
            return CreatedNoteInvite(invite=existing_invite, token=token, created=False)

    token, token_hash = generate_note_invite_token()
    invite = NoteInvite(
        note_id=note.id,
        inviter_user_id=inviter.id,
        invitee_email=normalized_email,
        invitee_user_id=target_user.id if target_user is not None else invitee_user_id,
        role=role,
        status=NoteInviteStatus.PENDING,
        token_hash=token_hash,
        expires_at=utc_now() + expires_in,
        message=message,
    )
    db.add(invite)
    try:
        await db.flush()
    except IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An active invite for this target already exists",
        ) from exc
    return CreatedNoteInvite(invite=invite, token=token, created=True)


async def accept_note_invite(
    token: str,
    user: DBUser,
    db: AsyncSession,
) -> tuple[NoteInvite, Note, NoteAccessContext]:
    token_hash = hash_note_invite_token(token)
    invite_result = await db.execute(
        select(NoteInvite)
        .where(NoteInvite.token_hash == token_hash)
        .with_for_update()
    )
    invite = invite_result.scalar_one_or_none()
    if invite is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invite not found")

    await expire_note_invite_if_needed(invite, db)

    if invite.status == NoteInviteStatus.ACCEPTED:
        validate_invite_target(invite, user)
        note = await get_note_with_bypass(db, invite.note_id)
        if note is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Note not found")
        access = await resolve_note_access(note, user, db)
        if access.can_view:
            return invite, note, access
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Invite has already been accepted")

    ensure_note_invite_status_usable(invite)
    validate_invite_target(invite, user)

    note = await get_note_with_bypass(db, invite.note_id)
    if note is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Note not found")

    collaborator_result = await db.execute(
        select(NoteCollaborator).where(
            NoteCollaborator.note_id == note.id,
            NoteCollaborator.user_id == user.id,
        ).with_for_update()
    )
    collaborator = collaborator_result.scalar_one_or_none()

    if collaborator is None and note.user_id != user.id:
        collaborator = NoteCollaborator(
            note_id=note.id,
            user_id=user.id,
            role=invite.role,
            granted_by_user_id=invite.inviter_user_id,
        )
        db.add(collaborator)
    elif collaborator is not None:
        collaborator.role = choose_broader_role(collaborator.role, invite.role)
        collaborator.granted_by_user_id = invite.inviter_user_id or collaborator.granted_by_user_id
        collaborator.updated_at = utc_now()

    invite.status = NoteInviteStatus.ACCEPTED
    invite.accepted_at = utc_now()
    invite.invitee_user_id = user.id
    invite.invitee_email = normalize_email(user.email)
    invite.updated_at = utc_now()
    try:
        await db.flush()
    except IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Invite acceptance conflicted with another update; please retry",
        ) from exc

    access = await resolve_note_access(note, user, db)
    return invite, note, access


async def revoke_note_invite(
    invite: NoteInvite,
    db: AsyncSession,
) -> NoteInvite:
    await expire_note_invite_if_needed(invite, db)

    if invite.status == NoteInviteStatus.ACCEPTED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Accepted invites cannot be revoked",
        )
    if invite.status == NoteInviteStatus.REVOKED:
        return invite
    if invite.status == NoteInviteStatus.EXPIRED:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Invite has already expired",
        )

    invite.status = NoteInviteStatus.REVOKED
    invite.revoked_at = utc_now()
    invite.updated_at = utc_now()
    await db.flush()
    return invite
