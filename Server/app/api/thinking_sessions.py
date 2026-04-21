"""Live Thinking Session API routes."""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncGenerator, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from openai import AsyncOpenAI
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.auth import get_current_active_user
from app.database.models import (
    ThinkingSessionPhase,
    User as DBUser,
)
from app.database.session import get_db
from app.services.thinking_sessions import (
    build_facilitation_prompt,
    build_thinking_session_snapshot,
    claim_synthesis_run_for_streaming,
    complete_synthesis_run,
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
    session = await create_thinking_session(
        workspace_id=payload.workspace_id,
        title=payload.title,
        prompt_context=payload.prompt_context,
        note_id=payload.note_id,
        user=current_user,
        db=db,
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
    run = await claim_synthesis_run_for_streaming(session, payload.run_id, current_user, db)
    prompt_text, messages = build_facilitation_prompt(session, run)
    run.facilitation_prompt = prompt_text
    await db.commit()
    await db.refresh(run)

    if not settings.OPENAI_API_KEY:
        await fail_synthesis_run(session, run, "OpenAI API key is not configured for synthesis", db)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OpenAI API key is not configured for synthesis",
        )

    client = AsyncOpenAI(
        api_key=settings.OPENAI_API_KEY,
        timeout=float(settings.THINKING_SESSION_SYNTHESIS_TIMEOUT_SECONDS),
    )

    async def event_stream() -> AsyncGenerator[str, None]:
        full_output_parts: list[str] = []
        try:
            yield f"data: {json.dumps({'type': 'start', 'run_id': str(run.id), 'session_id': str(session.id), 'model': run.model})}\n\n"
            stream = await client.chat.completions.create(
                model=run.model,
                messages=messages,
                temperature=0.3,
                stream=True,
            )
            async for chunk in stream:
                delta = ((chunk.choices[0].delta.content or "") if chunk.choices else "")
                if not delta:
                    continue
                full_output_parts.append(delta)
                yield f"data: {json.dumps({'type': 'token', 'text': delta, 'run_id': str(run.id)})}\n\n"

            full_output = "".join(full_output_parts).strip()
            updated_session = await complete_synthesis_run(session, run, full_output, db)
            updated_snapshot = await build_thinking_session_snapshot(updated_session, db)
            yield f"data: {json.dumps({'type': 'done', 'run_id': str(run.id), 'text': full_output, 'session': updated_snapshot})}\n\n"
        except Exception as exc:
            logger.exception("Thinking session synthesis failed: session_id=%s run_id=%s", session.id, run.id)
            failed_session = await fail_synthesis_run(session, run, str(exc), db)
            failed_snapshot = await build_thinking_session_snapshot(failed_session, db)
            yield f"data: {json.dumps({'type': 'error', 'run_id': str(run.id), 'message': str(exc), 'session': failed_snapshot})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )
