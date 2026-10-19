"""Live Thinking Session API routes."""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncGenerator, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.auth import get_current_active_user
from app.database.models import (
    ThinkingSessionPhase,
    User as DBUser,
)
from app.database.session import get_db
from app.services.billing import enforce_entitlement_limit, increment_usage_counter
from app.services.thinking_sessions import (
    build_thinking_session_snapshot,
    create_contribution,
    create_thinking_session,
    ensure_thinking_session_permission,
    fail_synthesis_run,
    get_thinking_session_or_404,
    get_thinking_synthesis_run_or_404,
    list_thinking_sessions_for_workspace,
    mark_thinking_session_participant_seen,
    persist_synthesis_progress,
    resolve_thinking_session_access,
    toggle_contribution_vote,
    transition_thinking_session_phase,
    update_refined_output,
)


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/thinking-sessions", tags=["Thinking Sessions"])


class ThinkingUserSummaryResponse(BaseModel):
    id: str
    email: str
    name: str


class ThinkingParticipantResponse(BaseModel):
    id: str
    user_id: str
    user: Optional[ThinkingUserSummaryResponse] = None
    joined_at: Optional[str] = None
    last_seen_at: Optional[str] = None


class ThinkingContributionResponse(BaseModel):
    id: str
    session_id: str
    author_user_id: str
    author: Optional[ThinkingUserSummaryResponse] = None
    content: str
    created_phase: str
    vote_count: int
    voter_user_ids: list[str] = Field(default_factory=list)
    rank: int
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class ThinkingSynthesisRunResponse(BaseModel):
    id: str
    session_id: str
    triggered_by_user_id: Optional[str] = None
    triggered_by: Optional[ThinkingUserSummaryResponse] = None
    status: str
    model: str
    contribution_count: int
    output_text: str
    error_message: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    failed_at: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class ThinkingSessionStateResponse(BaseModel):
    id: str
    workspace_id: str
    note_id: Optional[str] = None
    room_id: str
    title: str
    prompt_context: Optional[str] = None
    created_by_user_id: str
    host_user_id: str
    creator: Optional[ThinkingUserSummaryResponse] = None
    host: Optional[ThinkingUserSummaryResponse] = None
    phase: str
    phase_entered_at: Optional[str] = None
    waiting_started_at: Optional[str] = None
    gathering_started_at: Optional[str] = None
    synthesizing_started_at: Optional[str] = None
    refining_started_at: Optional[str] = None
    completed_at: Optional[str] = None
    active_synthesis_run_id: Optional[str] = None
    synthesis_output: str = ""
    refined_output: str = ""
    final_output: str = ""
    last_refined_by_user_id: Optional[str] = None
    last_refined_by: Optional[ThinkingUserSummaryResponse] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    participants: list[ThinkingParticipantResponse] = Field(default_factory=list)
    contributions: list[ThinkingContributionResponse] = Field(default_factory=list)
    synthesis_runs: list[ThinkingSynthesisRunResponse] = Field(default_factory=list)
    active_synthesis_run: Optional[ThinkingSynthesisRunResponse] = None


class ThinkingSessionSummaryResponse(BaseModel):
    id: str
    workspace_id: str
    note_id: Optional[str] = None
    room_id: str
    title: str
    phase: str
    host_user_id: str
    active_synthesis_run_id: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class ThinkingSessionAccessResponse(BaseModel):
    session_id: str
    workspace_id: str
    room_id: str
    can_view: bool
    can_participate: bool
    can_control: bool
    is_host: bool
    phase: str


class ThinkingSessionCreateRequest(BaseModel):
    workspace_id: UUID
    title: str = Field(..., min_length=1, max_length=255)
    prompt_context: Optional[str] = Field(default=None, max_length=4000)
    note_id: Optional[UUID] = None


class ThinkingContributionCreateRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=4000)


class ThinkingVoteRequest(BaseModel):
    contribution_id: UUID


class ThinkingTransitionRequest(BaseModel):
    target_phase: ThinkingSessionPhase


class ThinkingTransitionResponse(BaseModel):
    session: ThinkingSessionStateResponse
    synthesis_run_id: Optional[str] = None


class ThinkingRefinementUpdateRequest(BaseModel):
    refined_output: str = Field(default="", max_length=20000)


class ThinkingSynthesisStreamRequest(BaseModel):
    run_id: UUID


class ThinkingSynthesisProgressRequest(BaseModel):
    partial_output: str = Field(default="", max_length=20000)


class ThinkingAckResponse(BaseModel):
    ok: bool


def serialize_thinking_session_summary(snapshot: dict[str, Any]) -> ThinkingSessionSummaryResponse:
    return ThinkingSessionSummaryResponse(
        id=snapshot["id"],
        workspace_id=snapshot["workspace_id"],
        note_id=snapshot.get("note_id"),
        room_id=snapshot["room_id"],
        title=snapshot["title"],
        phase=snapshot["phase"],
        host_user_id=snapshot["host_user_id"],
        active_synthesis_run_id=snapshot.get("active_synthesis_run_id"),
        created_at=snapshot.get("created_at"),
        updated_at=snapshot.get("updated_at"),
    )


@router.post("", response_model=ThinkingSessionStateResponse, status_code=status.HTTP_201_CREATED)
async def create_thinking_session_endpoint(
    payload: ThinkingSessionCreateRequest,
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> ThinkingSessionStateResponse:
    await enforce_entitlement_limit(
        db,
        workspace_id=payload.workspace_id,
        metric_key="thinking_sessions_created_monthly",
        increment=1,
        upgrade_url=f"{settings.FRONTEND_URL}",
    )
    session = await create_thinking_session(
        workspace_id=payload.workspace_id,
        title=payload.title,
        prompt_context=payload.prompt_context,
        note_id=payload.note_id,
        user=current_user,
        db=db,
    )
    await increment_usage_counter(
        db,
        workspace_id=payload.workspace_id,
        metric_key="thinking_sessions_created_monthly",
        amount=1,
    )
    snapshot = await build_thinking_session_snapshot(session, db)
    return ThinkingSessionStateResponse.model_validate(snapshot)


@router.get("/workspace/{workspace_id}", response_model=list[ThinkingSessionSummaryResponse])
async def list_thinking_sessions_for_workspace_endpoint(
    workspace_id: UUID,
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> list[ThinkingSessionSummaryResponse]:
    sessions = await list_thinking_sessions_for_workspace(workspace_id, current_user, db)
    summaries: list[ThinkingSessionSummaryResponse] = []
    for session in sessions:
        snapshot = await build_thinking_session_snapshot(session, db)
        summaries.append(serialize_thinking_session_summary(snapshot))
    return summaries


@router.get("/{session_id}", response_model=ThinkingSessionStateResponse)
async def get_thinking_session_endpoint(
    session_id: UUID,
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> ThinkingSessionStateResponse:
    session = await get_thinking_session_or_404(session_id, db)
    await ensure_thinking_session_permission(session, current_user, db, "view")
    snapshot = await build_thinking_session_snapshot(session, db)
    return ThinkingSessionStateResponse.model_validate(snapshot)


@router.get("/{session_id}/access", response_model=ThinkingSessionAccessResponse)
async def get_thinking_session_access_endpoint(
    session_id: UUID,
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> ThinkingSessionAccessResponse:
    session = await get_thinking_session_or_404(session_id, db)
    access = await resolve_thinking_session_access(session, current_user, db)
    return ThinkingSessionAccessResponse(
        session_id=str(session.id),
        workspace_id=str(session.workspace_id),
        room_id=session.room_id,
        can_view=access.can_view,
        can_participate=access.can_participate,
        can_control=access.can_control,
        is_host=access.is_host,
        phase=str(session.phase.value if isinstance(session.phase, ThinkingSessionPhase) else session.phase),
    )


@router.post("/{session_id}/participants/ping", response_model=ThinkingSessionStateResponse)
async def ping_thinking_session_participant_endpoint(
    session_id: UUID,
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> ThinkingSessionStateResponse:
    session = await get_thinking_session_or_404(session_id, db)
    await ensure_thinking_session_permission(session, current_user, db, "view")
    await mark_thinking_session_participant_seen(session, current_user, db)
    await db.commit()
    snapshot = await build_thinking_session_snapshot(session, db)
    return ThinkingSessionStateResponse.model_validate(snapshot)


@router.post("/{session_id}/contributions", response_model=ThinkingSessionStateResponse)
async def create_thinking_contribution_endpoint(
    session_id: UUID,
    payload: ThinkingContributionCreateRequest,
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> ThinkingSessionStateResponse:
    session = await get_thinking_session_or_404(session_id, db)
    await create_contribution(session, current_user, payload.content, db)
    refreshed_session = await get_thinking_session_or_404(session_id, db)
    snapshot = await build_thinking_session_snapshot(refreshed_session, db)
    return ThinkingSessionStateResponse.model_validate(snapshot)


@router.post("/{session_id}/votes", response_model=ThinkingSessionStateResponse)
async def toggle_thinking_vote_endpoint(
    session_id: UUID,
    payload: ThinkingVoteRequest,
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> ThinkingSessionStateResponse:
    session = await get_thinking_session_or_404(session_id, db)
    await toggle_contribution_vote(session, payload.contribution_id, current_user, db)
    refreshed_session = await get_thinking_session_or_404(session_id, db)
    snapshot = await build_thinking_session_snapshot(refreshed_session, db)
    return ThinkingSessionStateResponse.model_validate(snapshot)


@router.post("/{session_id}/transitions", response_model=ThinkingTransitionResponse)
async def transition_thinking_session_endpoint(
    session_id: UUID,
    payload: ThinkingTransitionRequest,
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> ThinkingTransitionResponse:
    session = await get_thinking_session_or_404(session_id, db)
    updated_session, synthesis_run = await transition_thinking_session_phase(
        session,
        current_user,
        payload.target_phase,
        db,
    )
    snapshot = await build_thinking_session_snapshot(updated_session, db)
    return ThinkingTransitionResponse(
        session=ThinkingSessionStateResponse.model_validate(snapshot),
        synthesis_run_id=str(synthesis_run.id) if synthesis_run is not None else None,
    )


@router.patch("/{session_id}/refinement", response_model=ThinkingSessionStateResponse)
async def update_thinking_refinement_endpoint(
    session_id: UUID,
    payload: ThinkingRefinementUpdateRequest,
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> ThinkingSessionStateResponse:
    session = await get_thinking_session_or_404(session_id, db)
    updated_session = await update_refined_output(session, current_user, payload.refined_output, db)
    snapshot = await build_thinking_session_snapshot(updated_session, db)
    return ThinkingSessionStateResponse.model_validate(snapshot)


@router.patch("/{session_id}/synthesis/{run_id}/progress", response_model=ThinkingAckResponse)
async def update_thinking_synthesis_progress_endpoint(
    session_id: UUID,
    run_id: UUID,
    payload: ThinkingSynthesisProgressRequest,
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> ThinkingAckResponse:
    session = await get_thinking_session_or_404(session_id, db)
    await ensure_thinking_session_permission(session, current_user, db, "control")
    run = await get_thinking_synthesis_run_or_404(session_id, run_id, db)
    await persist_synthesis_progress(session, run, payload.partial_output, db)
    return ThinkingAckResponse(ok=True)


@router.post("/{session_id}/synthesis/stream")
async def stream_thinking_session_synthesis_endpoint(
    session_id: UUID,
    payload: ThinkingSynthesisStreamRequest,
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    session = await get_thinking_session_or_404(session_id, db)
    await ensure_thinking_session_permission(session, current_user, db, "control")
    run = await get_thinking_synthesis_run_or_404(session_id, payload.run_id, db)
    run_id = run.id
    # Enqueue before streaming; the task owns its database session and provider request.
    # Duplicate dispatch is harmless because the producer atomically claims PENDING.
    if str(getattr(run.status, "value", run.status)) == "pending":
        from app.tasks.thinking_synthesis import run_thinking_synthesis
        try:
            run_thinking_synthesis.apply_async(
                args=[str(session_id), str(run_id), str(current_user.id)],
                task_id=f"thinking-synthesis-{run_id}",
            )
        except Exception:
            raise HTTPException(503, "Synthesis queue unavailable; reconnect to retry") from None

    model = run.model
    # Do not retain the request connection throughout a long-lived observer.
    await db.close()

    async def event_stream() -> AsyncGenerator[str, None]:
        import asyncio
        from datetime import datetime, timezone
        from app.database.session import async_session_scope
        # Replaying full text is safe: Worker consumes absolute full_text snapshots.
        previous = ""
        yield f"data: {json.dumps({'type': 'start', 'run_id': str(run_id), 'session_id': str(session_id), 'model': model})}\n\n"
        while True:
            async with async_session_scope() as poll_db:
                current = await get_thinking_session_or_404(session_id, poll_db)
                await ensure_thinking_session_permission(current, current_user, poll_db, "control")
                current_run = await get_thinking_synthesis_run_or_404(session_id, run_id, poll_db)
                run_status = str(getattr(current_run.status, "value", current_run.status))
                output = current_run.output_text or ""
                if output != previous:
                    yield f"data: {json.dumps({'type': 'token', 'text': output[len(previous):] if output.startswith(previous) else output, 'full_text': output, 'run_id': str(run_id)})}\n\n"
                    previous = output
                # Interrupted producers retain their last committed partial output.
                updated_at = current_run.updated_at
                if updated_at and updated_at.tzinfo is None:
                    updated_at = updated_at.replace(tzinfo=timezone.utc)
                stale_after = max(120, settings.THINKING_SESSION_SYNTHESIS_TIMEOUT_SECONDS * 2)
                if run_status == "streaming" and updated_at and (datetime.now(timezone.utc) - updated_at).total_seconds() > stale_after:
                    current = await fail_synthesis_run(current, current_run, "Synthesis worker interrupted; partial output retained", poll_db)
                    run_status = "failed"
                if run_status in {"completed", "failed"}:
                    snapshot = await build_thinking_session_snapshot(current, poll_db)
                    event = {"type": "done" if run_status == "completed" else "error", "run_id": str(run_id), "text": output, "session": snapshot}
                    if run_status == "failed":
                        event["message"] = "Synthesis interrupted; partial output is retained"
                    yield f"data: {json.dumps(event)}\n\n"
                    return
            yield ": heartbeat\n\n"
            await asyncio.sleep(0.75)

    return StreamingResponse(event_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
