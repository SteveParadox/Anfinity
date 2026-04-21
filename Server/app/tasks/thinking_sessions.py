"""Domain services for Live Thinking Sessions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal, Optional, Sequence
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import Select, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.auth import WorkspaceContext, get_workspace_context
from app.core.permissions import get_workspace_permissions_for_user
from app.database.models import (
    Note,
    ThinkingSession,
    ThinkingSessionContribution,
    ThinkingSessionParticipant,
    ThinkingSessionPhase,
    ThinkingSessionSynthesisRun,
    ThinkingSynthesisStatus,
    ThinkingSessionVote,
    User as DBUser,
    WorkspaceSection,
)
from app.services.note_access import ensure_note_permission


THINKING_SESSION_ROOM_PREFIX = "thinking-session:"
MAX_CONTRIBUTION_LENGTH = 4_000

AllowedThinkingAction = Literal["view", "participate", "control"]

ALLOWED_PHASE_TRANSITIONS: dict[ThinkingSessionPhase, set[ThinkingSessionPhase]] = {
    ThinkingSessionPhase.WAITING: {ThinkingSessionPhase.GATHERING},
    ThinkingSessionPhase.GATHERING: {ThinkingSessionPhase.SYNTHESIZING},
    ThinkingSessionPhase.SYNTHESIZING: set(),
    ThinkingSessionPhase.REFINING: {ThinkingSessionPhase.COMPLETED},
    ThinkingSessionPhase.COMPLETED: set(),
}


@dataclass(slots=True)
class ThinkingSessionAccessContext:
    """Resolved workspace-backed permissions for one thinking session."""

    session: ThinkingSession
    user: DBUser
    workspace_context: Optional[WorkspaceContext]
    can_view: bool
    can_participate: bool
    can_control: bool

    @property
    def is_host(self) -> bool:
        return self.session.host_user_id == self.user.id or self.session.created_by_user_id == self.user.id

    def require(self, action: AllowedThinkingAction) -> "ThinkingSessionAccessContext":
        allowed = False
        if action == "view":
            allowed = self.can_view
        elif action == "participate":
            allowed = self.can_participate
        elif action == "control":
            allowed = self.can_control

        if allowed:
            return self

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Insufficient permissions for thinking_session:{action}",
        )


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def build_thinking_session_room_id(session_id: UUID | str) -> str:
    return f"{THINKING_SESSION_ROOM_PREFIX}{str(session_id).strip()}"


def is_thinking_session_room_id(room_id: str) -> bool:
    return room_id.startswith(THINKING_SESSION_ROOM_PREFIX)


def extract_session_id_from_room_id(room_id: str) -> str:
    if not is_thinking_session_room_id(room_id):
        return room_id.strip()
    return room_id[len(THINKING_SESSION_ROOM_PREFIX):].strip()


def serialize_user_summary(user: Optional[DBUser]) -> Optional[dict[str, Any]]:
    if user is None:
        return None
    return {
        "id": str(user.id),
        "email": user.email,
        "name": (user.full_name or user.email or "").strip() or "Collaborator",
    }


def coerce_phase(value: ThinkingSessionPhase | str) -> ThinkingSessionPhase:
    return value if isinstance(value, ThinkingSessionPhase) else ThinkingSessionPhase(str(value))


def coerce_synthesis_status(value: ThinkingSynthesisStatus | str) -> ThinkingSynthesisStatus:
    return value if isinstance(value, ThinkingSynthesisStatus) else ThinkingSynthesisStatus(str(value))


async def resolve_thinking_session_access(
    session: ThinkingSession,
    user: DBUser,
    db: AsyncSession,
) -> ThinkingSessionAccessContext:
    """Resolve effective access using the existing workspace chat permission surface."""

    if user.is_superuser:
        return ThinkingSessionAccessContext(
            session=session,
            user=user,
            workspace_context=None,
            can_view=True,
            can_participate=True,
            can_control=True,
        )

    workspace_context = await get_workspace_context(session.workspace_id, user, db)
    permissions = await get_workspace_permissions_for_user(
        db,
        session.workspace_id,
        user,
        workspace_context,
    )
    chat_permissions = permissions.get(WorkspaceSection.CHAT.value, {})
    can_view = bool(chat_permissions.get("view"))
    can_participate = bool(chat_permissions.get("create"))
    can_control = bool(chat_permissions.get("manage")) or session.host_user_id == user.id or session.created_by_user_id == user.id

    return ThinkingSessionAccessContext(
        session=session,
        user=user,
        workspace_context=workspace_context,
        can_view=can_view,
        can_participate=can_participate,
        can_control=can_control,
    )


async def ensure_thinking_session_permission(
    session: ThinkingSession,
    user: DBUser,
    db: AsyncSession,
    action: AllowedThinkingAction,
) -> ThinkingSessionAccessContext:
    access = await resolve_thinking_session_access(session, user, db)
    return access.require(action)


async def get_thinking_session_or_404(session_id: UUID, db: AsyncSession) -> ThinkingSession:
    result = await db.execute(select(ThinkingSession).where(ThinkingSession.id == session_id))
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Thinking session not found")
    return session


async def get_thinking_synthesis_run_or_404(
    session_id: UUID,
    run_id: UUID,
    db: AsyncSession,
) -> ThinkingSessionSynthesisRun:
    result = await db.execute(
        select(ThinkingSessionSynthesisRun).where(
            ThinkingSessionSynthesisRun.id == run_id,
            ThinkingSessionSynthesisRun.session_id == session_id,
        )
    )
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Thinking synthesis run not found")
    return run


async def validate_optional_note_context(
    workspace_id: UUID,
    note_id: Optional[UUID],
    user: DBUser,
    db: AsyncSession,
) -> Optional[Note]:
    if note_id is None:
        return None

    note_result = await db.execute(select(Note).where(Note.id == note_id))
    note = note_result.scalar_one_or_none()
    if note is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Context note not found")
    if note.workspace_id != workspace_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Context note must belong to the same workspace",
        )

    await ensure_note_permission(note, user, db, "view")
    return note


async def mark_thinking_session_participant_seen(
    session: ThinkingSession,
    user: DBUser,
    db: AsyncSession,
) -> ThinkingSessionParticipant:
    existing_result = await db.execute(
        select(ThinkingSessionParticipant).where(
            ThinkingSessionParticipant.session_id == session.id,
            ThinkingSessionParticipant.user_id == user.id,
        )
    )
    participant = existing_result.scalar_one_or_none()
    now = utcnow()
    if participant is None:
        participant = ThinkingSessionParticipant(
            session_id=session.id,
            user_id=user.id,
            joined_at=now,
            last_seen_at=now,
        )
        db.add(participant)
    else:
        participant.last_seen_at = now

    await db.flush()
    await db.refresh(participant)
    return participant


async def list_thinking_sessions_for_workspace(
    workspace_id: UUID,
    user: DBUser,
    db: AsyncSession,
) -> Sequence[ThinkingSession]:
    workspace_context = await get_workspace_context(workspace_id, user, db)
    permissions = await get_workspace_permissions_for_user(db, workspace_id, user, workspace_context)
    if not bool(permissions.get(WorkspaceSection.CHAT.value, {}).get("view")):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient permissions for chat:view",
        )

    result = await db.execute(
        select(ThinkingSession)
        .where(ThinkingSession.workspace_id == workspace_id)
        .order_by(ThinkingSession.updated_at.desc(), ThinkingSession.created_at.desc())
    )
    return result.scalars().all()


async def create_thinking_session(
    *,
    workspace_id: UUID,
    title: str,
    prompt_context: Optional[str],
    note_id: Optional[UUID],
    user: DBUser,
    db: AsyncSession,
) -> ThinkingSession:
    workspace_context = await get_workspace_context(workspace_id, user, db)
    permissions = await get_workspace_permissions_for_user(db, workspace_id, user, workspace_context)
    if not bool(permissions.get(WorkspaceSection.CHAT.value, {}).get("create")):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient permissions for chat:create",
        )

    cleaned_title = title.strip()
    if not cleaned_title:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Session title is required",
        )

    note = await validate_optional_note_context(workspace_id, note_id, user, db)
    now = utcnow()
    session = ThinkingSession(
        workspace_id=workspace_id,
        note_id=note.id if note is not None else None,
        room_id="pending",
        title=cleaned_title,
        prompt_context=(prompt_context or "").strip() or None,
        created_by_user_id=user.id,
        host_user_id=user.id,
        phase=ThinkingSessionPhase.WAITING,
        phase_entered_at=now,
        waiting_started_at=now,
    )
    db.add(session)
    await db.flush()

    session.room_id = build_thinking_session_room_id(session.id)
    await mark_thinking_session_participant_seen(session, user, db)
    await db.commit()
    await db.refresh(session)
    return session


async def load_contributions_for_session(
    session_id: UUID,
    db: AsyncSession,
) -> list[ThinkingSessionContribution]:
    result = await db.execute(
        select(ThinkingSessionContribution)
        .where(ThinkingSessionContribution.session_id == session_id)
        .order_by(ThinkingSessionContribution.created_at.asc(), ThinkingSessionContribution.id.asc())
    )
    return list(result.scalars().all())


async def load_votes_for_session(
    session_id: UUID,
    db: AsyncSession,
) -> list[ThinkingSessionVote]:
    result = await db.execute(
        select(ThinkingSessionVote)
        .where(ThinkingSessionVote.session_id == session_id)
        .order_by(ThinkingSessionVote.created_at.asc(), ThinkingSessionVote.id.asc())
    )
    return list(result.scalars().all())


async def load_participants_for_session(
    session_id: UUID,
    db: AsyncSession,
) -> list[ThinkingSessionParticipant]:
    result = await db.execute(
        select(ThinkingSessionParticipant)
        .where(ThinkingSessionParticipant.session_id == session_id)
        .order_by(ThinkingSessionParticipant.last_seen_at.desc(), ThinkingSessionParticipant.joined_at.asc())
    )
    return list(result.scalars().all())


async def load_synthesis_runs_for_session(
    session_id: UUID,
    db: AsyncSession,
) -> list[ThinkingSessionSynthesisRun]:
    result = await db.execute(
        select(ThinkingSessionSynthesisRun)
        .where(ThinkingSessionSynthesisRun.session_id == session_id)
        .order_by(ThinkingSessionSynthesisRun.created_at.desc(), ThinkingSessionSynthesisRun.id.desc())
    )
    return list(result.scalars().all())


def build_vote_maps(
    votes: Sequence[ThinkingSessionVote],
) -> tuple[dict[str, int], dict[str, list[str]]]:
    counts: dict[str, int] = {}
    voter_user_ids: dict[str, list[str]] = {}
    for vote in votes:
        contribution_key = str(vote.contribution_id)
        counts[contribution_key] = counts.get(contribution_key, 0) + 1
        voter_user_ids.setdefault(contribution_key, []).append(str(vote.user_id))
    for key in voter_user_ids:
        voter_user_ids[key].sort()
    return counts, voter_user_ids


def order_contributions(
    contributions: Sequence[ThinkingSessionContribution],
    vote_counts: dict[str, int],
) -> list[ThinkingSessionContribution]:
    return sorted(
        contributions,
        key=lambda contribution: (
            -vote_counts.get(str(contribution.id), 0),
            contribution.created_at or datetime.min.replace(tzinfo=timezone.utc),
            str(contribution.id),
        ),
    )


def serialize_participant(participant: ThinkingSessionParticipant) -> dict[str, Any]:
    return {
        "id": str(participant.id),
        "user_id": str(participant.user_id),
        "user": serialize_user_summary(participant.user),
        "joined_at": participant.joined_at.isoformat() if participant.joined_at else None,
        "last_seen_at": participant.last_seen_at.isoformat() if participant.last_seen_at else None,
    }


def serialize_contribution(
    contribution: ThinkingSessionContribution,
    *,
    rank: int,
    vote_count: int,
    voter_user_ids: list[str],
) -> dict[str, Any]:
    return {
        "id": str(contribution.id),
        "session_id": str(contribution.session_id),
        "author_user_id": str(contribution.author_user_id),
        "author": serialize_user_summary(contribution.author),
        "content": contribution.content,
        "created_phase": coerce_phase(contribution.created_phase).value,
        "vote_count": vote_count,
        "voter_user_ids": voter_user_ids,
        "rank": rank,
        "created_at": contribution.created_at.isoformat() if contribution.created_at else None,
        "updated_at": contribution.updated_at.isoformat() if contribution.updated_at else None,
    }


def serialize_synthesis_run(run: ThinkingSessionSynthesisRun) -> dict[str, Any]:
    snapshot_payload = run.snapshot_payload or {}
    contribution_snapshot = snapshot_payload.get("contributions") if isinstance(snapshot_payload, dict) else None
    return {
        "id": str(run.id),
        "session_id": str(run.session_id),
        "triggered_by_user_id": str(run.triggered_by_user_id) if run.triggered_by_user_id else None,
        "triggered_by": serialize_user_summary(run.triggered_by),
        "status": coerce_synthesis_status(run.status).value,
        "model": run.model,
        "contribution_count": len(contribution_snapshot or []),
        "output_text": run.output_text or "",
        "error_message": run.error_message,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "failed_at": run.failed_at.isoformat() if run.failed_at else None,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "updated_at": run.updated_at.isoformat() if run.updated_at else None,
    }


async def build_thinking_session_snapshot(
    session: ThinkingSession,
    db: AsyncSession,
) -> dict[str, Any]:
    contributions = await load_contributions_for_session(session.id, db)
    votes = await load_votes_for_session(session.id, db)
    participants = await load_participants_for_session(session.id, db)
    runs = await load_synthesis_runs_for_session(session.id, db)
    vote_counts, voter_user_ids = build_vote_maps(votes)
    ordered_contributions = order_contributions(contributions, vote_counts)
    active_run = next((run for run in runs if str(run.id) == str(session.active_synthesis_run_id)), None)

    return {
        "id": str(session.id),
        "workspace_id": str(session.workspace_id),
        "note_id": str(session.note_id) if session.note_id else None,
        "room_id": session.room_id,
        "title": session.title,
        "prompt_context": session.prompt_context,
        "created_by_user_id": str(session.created_by_user_id),
        "host_user_id": str(session.host_user_id),
        "creator": serialize_user_summary(session.creator),
        "host": serialize_user_summary(session.host),
        "phase": coerce_phase(session.phase).value,
        "phase_entered_at": session.phase_entered_at.isoformat() if session.phase_entered_at else None,
        "waiting_started_at": session.waiting_started_at.isoformat() if session.waiting_started_at else None,
        "gathering_started_at": session.gathering_started_at.isoformat() if session.gathering_started_at else None,
        "synthesizing_started_at": session.synthesizing_started_at.isoformat() if session.synthesizing_started_at else None,
        "refining_started_at": session.refining_started_at.isoformat() if session.refining_started_at else None,
        "completed_at": session.completed_at.isoformat() if session.completed_at else None,
        "active_synthesis_run_id": str(session.active_synthesis_run_id) if session.active_synthesis_run_id else None,
        "synthesis_output": session.synthesis_output or "",
        "refined_output": session.refined_output or "",
        "final_output": session.final_output or "",
        "last_refined_by_user_id": str(session.last_refined_by_user_id) if session.last_refined_by_user_id else None,
        "last_refined_by": serialize_user_summary(session.last_refined_by),
        "created_at": session.created_at.isoformat() if session.created_at else None,
        "updated_at": session.updated_at.isoformat() if session.updated_at else None,
        "participants": [serialize_participant(participant) for participant in participants],
        "contributions": [
            serialize_contribution(
                contribution,
                rank=index + 1,
                vote_count=vote_counts.get(str(contribution.id), 0),
                voter_user_ids=voter_user_ids.get(str(contribution.id), []),
            )
            for index, contribution in enumerate(ordered_contributions)
        ],
        "synthesis_runs": [serialize_synthesis_run(run) for run in runs],
        "active_synthesis_run": serialize_synthesis_run(active_run) if active_run is not None else None,
    }


async def create_contribution(
    session: ThinkingSession,
    user: DBUser,
    content: str,
    db: AsyncSession,
) -> ThinkingSessionContribution:
    await ensure_thinking_session_permission(session, user, db, "participate")

    if coerce_phase(session.phase) != ThinkingSessionPhase.GATHERING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Contributions are only allowed during gathering",
        )

    cleaned_content = content.strip()
    if not cleaned_content:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Contribution content is required",
        )
    if len(cleaned_content) > MAX_CONTRIBUTION_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Contribution exceeds {MAX_CONTRIBUTION_LENGTH} characters",
        )

    contribution = ThinkingSessionContribution(
        session_id=session.id,
        author_user_id=user.id,
        content=cleaned_content,
        created_phase=ThinkingSessionPhase.GATHERING,
    )
    session.updated_at = utcnow()
    db.add(contribution)
    await mark_thinking_session_participant_seen(session, user, db)
    await db.flush()
    await db.commit()
    await db.refresh(contribution)
    return contribution


async def toggle_contribution_vote(
    session: ThinkingSession,
    contribution_id: UUID,
    user: DBUser,
    db: AsyncSession,
) -> bool:
    await ensure_thinking_session_permission(session, user, db, "participate")

    if coerce_phase(session.phase) != ThinkingSessionPhase.GATHERING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Voting is only allowed during gathering",
        )

    contribution_result = await db.execute(
        select(ThinkingSessionContribution).where(
            ThinkingSessionContribution.id == contribution_id,
            ThinkingSessionContribution.session_id == session.id,
        )
    )
    contribution = contribution_result.scalar_one_or_none()
    if contribution is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contribution not found")

    existing_vote_result = await db.execute(
        select(ThinkingSessionVote).where(
            ThinkingSessionVote.session_id == session.id,
            ThinkingSessionVote.contribution_id == contribution.id,
            ThinkingSessionVote.user_id == user.id,
        )
    )
    existing_vote = existing_vote_result.scalar_one_or_none()
    session.updated_at = utcnow()
    if existing_vote is None:
        vote = ThinkingSessionVote(
            session_id=session.id,
            contribution_id=contribution.id,
            user_id=user.id,
        )
        db.add(vote)
        voted = True
    else:
        await db.delete(existing_vote)
        voted = False

    await mark_thinking_session_participant_seen(session, user, db)
    await db.commit()
    return voted


async def build_contribution_snapshot_for_synthesis(
    session: ThinkingSession,
    db: AsyncSession,
) -> list[dict[str, Any]]:
    contributions = await load_contributions_for_session(session.id, db)
    votes = await load_votes_for_session(session.id, db)
    vote_counts, voter_user_ids = build_vote_maps(votes)
    ordered = order_contributions(contributions, vote_counts)
    return [
        {
            "id": str(contribution.id),
            "author_user_id": str(contribution.author_user_id),
            "author_name": (contribution.author.full_name or contribution.author.email or "").strip() if contribution.author else "Collaborator",
            "content": contribution.content,
            "vote_count": vote_counts.get(str(contribution.id), 0),
            "voter_user_ids": voter_user_ids.get(str(contribution.id), []),
            "created_at": contribution.created_at.isoformat() if contribution.created_at else None,
        }
        for contribution in ordered
    ]


async def transition_thinking_session_phase(
    session: ThinkingSession,
    user: DBUser,
    target_phase: ThinkingSessionPhase,
    db: AsyncSession,
) -> tuple[ThinkingSession, Optional[ThinkingSessionSynthesisRun]]:
    access = await ensure_thinking_session_permission(session, user, db, "control")
    current_phase = coerce_phase(session.phase)

    if target_phase not in ALLOWED_PHASE_TRANSITIONS.get(current_phase, set()):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot transition thinking session from {current_phase.value} to {target_phase.value}",
        )

    now = utcnow()
    session.phase = target_phase
    session.phase_entered_at = now
    session.updated_at = now
    synthesis_run: Optional[ThinkingSessionSynthesisRun] = None

    if target_phase == ThinkingSessionPhase.GATHERING:
        session.gathering_started_at = now
    elif target_phase == ThinkingSessionPhase.SYNTHESIZING:
        contribution_snapshot = await build_contribution_snapshot_for_synthesis(session, db)
        if not contribution_snapshot:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Cannot synthesize without at least one contribution",
            )
        if session.active_synthesis_run_id is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A synthesis run is already active for this session",
            )

        synthesis_run = ThinkingSessionSynthesisRun(
            session_id=session.id,
            triggered_by_user_id=user.id,
            status=ThinkingSynthesisStatus.PENDING,
            model=settings.THINKING_SESSION_SYNTHESIS_MODEL,
            snapshot_payload={"contributions": contribution_snapshot},
        )
        db.add(synthesis_run)
        await db.flush()
        session.active_synthesis_run_id = synthesis_run.id
        session.synthesizing_started_at = now
        session.synthesis_output = ""
        session.refined_output = session.refined_output or ""
    elif target_phase == ThinkingSessionPhase.COMPLETED:
        final_output = (session.refined_output or session.synthesis_output or "").strip()
        if not final_output:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Cannot complete a session without a synthesized or refined result",
            )
        session.final_output = final_output
        session.completed_at = now

    await mark_thinking_session_participant_seen(session, user, db)
    await db.commit()
    await db.refresh(session)
    if synthesis_run is not None:
        await db.refresh(synthesis_run)
    return session, synthesis_run


async def claim_synthesis_run_for_streaming(
    session: ThinkingSession,
    run_id: UUID,
    user: DBUser,
    db: AsyncSession,
) -> ThinkingSessionSynthesisRun:
    await ensure_thinking_session_permission(session, user, db, "control")

    if session.active_synthesis_run_id != run_id or coerce_phase(session.phase) != ThinkingSessionPhase.SYNTHESIZING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This session is not ready for synthesis streaming",
        )

    result = await db.execute(
        update(ThinkingSessionSynthesisRun)
        .where(
            ThinkingSessionSynthesisRun.id == run_id,
            ThinkingSessionSynthesisRun.session_id == session.id,
            ThinkingSessionSynthesisRun.status == ThinkingSynthesisStatus.PENDING,
        )
        .values(
            status=ThinkingSynthesisStatus.STREAMING,
            started_at=utcnow(),
            updated_at=utcnow(),
        )
    )
    if result.rowcount != 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Synthesis has already started for this session",
        )

    await db.commit()
    run = await get_thinking_synthesis_run_or_404(session.id, run_id, db)
    return run


def build_facilitation_prompt(
    session: ThinkingSession,
    run: ThinkingSessionSynthesisRun,
) -> tuple[str, list[dict[str, str]]]:
    snapshot = run.snapshot_payload if isinstance(run.snapshot_payload, dict) else {}
    contributions = snapshot.get("contributions") or []
    contribution_lines = []
    for index, contribution in enumerate(contributions, start=1):
        contribution_lines.append(
            "\n".join(
                [
                    f"{index}. Votes: {contribution.get('vote_count', 0)}",
                    f"Author: {contribution.get('author_name', 'Collaborator')}",
                    f"Created At: {contribution.get('created_at', 'unknown')}",
                    "Idea:",
                    str(contribution.get("content", "")).strip(),
                ]
            )
        )

    system_prompt = (
        "You are facilitating a live collaborative thinking session. "
        "You must synthesize participant contributions into a concise, high-value shared understanding. "
        "Pay attention to vote ranking, but do not discard insightful minority ideas if they add useful nuance. "
        "Group related ideas, call out consensus, identify tensions or unresolved disagreements, "
        "and end with a practical next direction. "
        "Use a structured markdown format with these headings exactly: "
        "'## Key Themes', '## Strongest Ideas', '## Unresolved Tensions', "
        "'## Suggested Next Direction', and '## Concise Synthesis'. "
        "Keep the tone facilitative, concrete, and appropriate for a live team session."
    )
    user_prompt = "\n\n".join(
        [
            f"Session title: {session.title}",
            f"Session context: {(session.prompt_context or 'No additional context provided.').strip()}",
            "Ordered contributions (already sorted by vote count descending with stable ties):",
            "\n\n".join(contribution_lines) or "No contributions were provided.",
        ]
    )
    return user_prompt, [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


async def persist_synthesis_progress(
    session: ThinkingSession,
    run: ThinkingSessionSynthesisRun,
    partial_output: str,
    db: AsyncSession,
) -> ThinkingSession:
    if session.active_synthesis_run_id != run.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This synthesis run is no longer active",
        )

    run.output_text = partial_output
    run.updated_at = utcnow()
    session.synthesis_output = partial_output
    session.updated_at = utcnow()
    await db.commit()
    await db.refresh(session)
    return session


async def complete_synthesis_run(
    session: ThinkingSession,
    run: ThinkingSessionSynthesisRun,
    full_output: str,
    db: AsyncSession,
) -> ThinkingSession:
    now = utcnow()
    run.status = ThinkingSynthesisStatus.COMPLETED
    run.output_text = full_output
    run.completed_at = now
    run.updated_at = now
    session.phase = ThinkingSessionPhase.REFINING
    session.phase_entered_at = now
    session.refining_started_at = now
    session.active_synthesis_run_id = None
    session.synthesis_output = full_output
    if not (session.refined_output or "").strip():
        session.refined_output = full_output
    session.updated_at = now
    await db.commit()
    await db.refresh(session)
    return session


async def fail_synthesis_run(
    session: ThinkingSession,
    run: ThinkingSessionSynthesisRun,
    error_message: str,
    db: AsyncSession,
) -> ThinkingSession:
    now = utcnow()
    run.status = ThinkingSynthesisStatus.FAILED
    run.error_message = error_message
    run.failed_at = now
    run.updated_at = now
    session.phase = ThinkingSessionPhase.GATHERING
    session.phase_entered_at = now
    session.active_synthesis_run_id = None
    session.synthesis_output = ""
    session.updated_at = now
    await db.commit()
    await db.refresh(session)
    return session


async def update_refined_output(
    session: ThinkingSession,
    user: DBUser,
    refined_output: str,
    db: AsyncSession,
) -> ThinkingSession:
    await ensure_thinking_session_permission(session, user, db, "participate")
    if coerce_phase(session.phase) != ThinkingSessionPhase.REFINING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Refinement is only allowed during the refining phase",
        )

    session.refined_output = refined_output.strip()
    session.last_refined_by_user_id = user.id
    session.updated_at = utcnow()
    await mark_thinking_session_participant_seen(session, user, db)
    await db.commit()
    await db.refresh(session)
    return session
