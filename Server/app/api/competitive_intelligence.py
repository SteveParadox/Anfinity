"""Competitive intelligence monitoring API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_active_user
from app.core.permissions import ensure_workspace_permission
from app.database.models import (
    CompetitiveAnalysis,
    CompetitiveSnapshot,
    CompetitiveSource,
    User as DBUser,
    WorkspaceSection,
)
from app.database.session import get_db
from app.services.competitive_intelligence import CompetitiveIntelligenceService

router = APIRouter(prefix="/competitive-intelligence", tags=["Competitive Intelligence"])


class CompetitiveSourceCreate(BaseModel):
    workspace_id: UUID
    name: str = Field(..., min_length=1, max_length=255)
    url: str = Field(..., min_length=8, max_length=1000)
    check_interval_minutes: int = Field(default=1440, ge=15, le=43_200)
    config: dict[str, Any] = Field(default_factory=dict)


class CompetitiveSourceUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    url: Optional[str] = Field(default=None, min_length=8, max_length=1000)
    check_interval_minutes: Optional[int] = Field(default=None, ge=15, le=43_200)
    is_active: Optional[bool] = None
    config: Optional[dict[str, Any]] = None


class CompetitiveSourceResponse(BaseModel):
    id: str
    workspace_id: str
    name: str
    url: str
    is_active: bool
    check_interval_minutes: int
    last_content_hash: Optional[str] = None
    last_processed_hash: Optional[str] = None
    last_successful_fetch_at: Optional[datetime] = None
    last_checked_at: Optional[datetime] = None
    last_changed_at: Optional[datetime] = None
    last_error: Optional[str] = None
    run_status: str
    config: dict[str, Any]
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class CompetitiveSnapshotResponse(BaseModel):
    id: str
    source_id: str
    workspace_id: str
    url: str
    reader_url: Optional[str] = None
    content_hash: Optional[str] = None
    extraction_status: str
    is_changed: bool
    content_length: int
    error_message: Optional[str] = None
    created_at: Optional[datetime] = None


class CompetitiveAnalysisResponse(BaseModel):
    id: str
    source_id: str
    snapshot_id: str
    previous_snapshot_id: Optional[str] = None
    workspace_id: str
    content_hash: str
    previous_content_hash: Optional[str] = None
    headline_summary: Optional[str] = None
    findings: list[dict[str, Any]]
    overall_urgency: float
    urgency_label: str
    should_trigger_immediate_workflow: bool
    workflow_dispatch_status: str
    slack_dispatch_status: str
    model_used: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class CompetitiveRunResponse(BaseModel):
    status: str
    source_id: str
    snapshot_id: Optional[str] = None
    analysis_id: Optional[str] = None
    content_hash: Optional[str] = None
    reason: Optional[str] = None
    model_called: bool
    workflow_dispatched: bool
    slack_dispatched: bool
    task_id: Optional[str] = None


@router.get("/sources", response_model=list[CompetitiveSourceResponse])
async def list_sources(
    workspace_id: UUID,
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> list[CompetitiveSourceResponse]:
    await ensure_workspace_permission(workspace_id, current_user, db, WorkspaceSection.WORKFLOWS, "view")
    rows = await db.execute(
        select(CompetitiveSource)
        .where(CompetitiveSource.workspace_id == workspace_id)
        .order_by(CompetitiveSource.created_at.desc())
    )
    return [_source_response(source) for source in rows.scalars().all()]


@router.post("/sources", response_model=CompetitiveSourceResponse, status_code=status.HTTP_201_CREATED)
async def create_source(
    payload: CompetitiveSourceCreate,
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> CompetitiveSourceResponse:
    await ensure_workspace_permission(payload.workspace_id, current_user, db, WorkspaceSection.WORKFLOWS, "create")
    source = CompetitiveSource(
        workspace_id=payload.workspace_id,
        created_by_user_id=current_user.id,
        name=payload.name,
        url=payload.url.strip(),
        check_interval_minutes=payload.check_interval_minutes,
        config=payload.config,
    )
    db.add(source)
    await db.flush()
    await db.refresh(source)
    return _source_response(source)


@router.patch("/sources/{source_id}", response_model=CompetitiveSourceResponse)
async def update_source(
    source_id: UUID,
    payload: CompetitiveSourceUpdate,
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> CompetitiveSourceResponse:
    source = await _load_source_or_404(db, source_id)
    await ensure_workspace_permission(source.workspace_id, current_user, db, WorkspaceSection.WORKFLOWS, "update")
    if payload.name is not None:
        source.name = payload.name
    if payload.url is not None:
        source.url = payload.url.strip()
    if payload.check_interval_minutes is not None:
        source.check_interval_minutes = payload.check_interval_minutes
    if payload.is_active is not None:
        source.is_active = payload.is_active
    if payload.config is not None:
        source.config = payload.config
    await db.flush()
    await db.refresh(source)
    return _source_response(source)


@router.post("/sources/{source_id}/run", response_model=CompetitiveRunResponse)
async def run_source(
    source_id: UUID,
    workspace_id: UUID,
    run_inline: bool = Query(False),
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> CompetitiveRunResponse:
    source = await _load_source_or_404(db, source_id)
    if source.workspace_id != workspace_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Competitive source not found")
    await ensure_workspace_permission(workspace_id, current_user, db, WorkspaceSection.WORKFLOWS, "update")

    if not run_inline:
        try:
            from app.tasks.competitive_intelligence import run_competitive_intelligence_source

            task = run_competitive_intelligence_source.delay(str(source.id))
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Failed to queue competitive intelligence run: {exc}",
            ) from exc
        return CompetitiveRunResponse(
            status="queued",
            source_id=str(source.id),
            model_called=False,
            workflow_dispatched=False,
            slack_dispatched=False,
            task_id=str(task.id),
        )

    try:
        result = await CompetitiveIntelligenceService(db).run_source(source.id)
        return CompetitiveRunResponse(**result.__dict__)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Competitive intelligence run failed: {exc}") from exc


@router.get("/sources/{source_id}/snapshots", response_model=list[CompetitiveSnapshotResponse])
async def list_snapshots(
    source_id: UUID,
    workspace_id: UUID,
    limit: int = Query(50, ge=1, le=200),
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> list[CompetitiveSnapshotResponse]:
    source = await _load_source_or_404(db, source_id)
    if source.workspace_id != workspace_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Competitive source not found")
    await ensure_workspace_permission(workspace_id, current_user, db, WorkspaceSection.WORKFLOWS, "view")
    rows = await db.execute(
        select(CompetitiveSnapshot)
        .where(CompetitiveSnapshot.source_id == source.id)
        .order_by(CompetitiveSnapshot.created_at.desc())
        .limit(limit)
    )
    return [_snapshot_response(snapshot) for snapshot in rows.scalars().all()]


@router.get("/analyses", response_model=list[CompetitiveAnalysisResponse])
async def list_analyses(
    workspace_id: UUID,
    source_id: Optional[UUID] = None,
    limit: int = Query(50, ge=1, le=200),
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> list[CompetitiveAnalysisResponse]:
    await ensure_workspace_permission(workspace_id, current_user, db, WorkspaceSection.WORKFLOWS, "view")
    filters = [CompetitiveAnalysis.workspace_id == workspace_id]
    if source_id is not None:
        filters.append(CompetitiveAnalysis.source_id == source_id)
    rows = await db.execute(
        select(CompetitiveAnalysis)
        .where(*filters)
        .order_by(CompetitiveAnalysis.created_at.desc())
        .limit(limit)
    )
    return [_analysis_response(analysis) for analysis in rows.scalars().all()]


async def _load_source_or_404(db: AsyncSession, source_id: UUID) -> CompetitiveSource:
    source = (await db.execute(select(CompetitiveSource).where(CompetitiveSource.id == source_id))).scalar_one_or_none()
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Competitive source not found")
    return source


def _source_response(source: CompetitiveSource) -> CompetitiveSourceResponse:
    return CompetitiveSourceResponse(
        id=str(source.id),
        workspace_id=str(source.workspace_id),
        name=source.name,
        url=source.url,
        is_active=bool(source.is_active),
        check_interval_minutes=int(source.check_interval_minutes or 0),
        last_content_hash=source.last_content_hash,
        last_processed_hash=source.last_processed_hash,
        last_successful_fetch_at=source.last_successful_fetch_at,
        last_checked_at=source.last_checked_at,
        last_changed_at=source.last_changed_at,
        last_error=source.last_error,
        run_status=source.run_status or "idle",
        config=dict(source.config or {}),
        created_at=source.created_at,
        updated_at=source.updated_at,
    )


def _snapshot_response(snapshot: CompetitiveSnapshot) -> CompetitiveSnapshotResponse:
    return CompetitiveSnapshotResponse(
        id=str(snapshot.id),
        source_id=str(snapshot.source_id),
        workspace_id=str(snapshot.workspace_id),
        url=snapshot.url,
        reader_url=snapshot.reader_url,
        content_hash=snapshot.content_hash,
        extraction_status=snapshot.extraction_status,
        is_changed=bool(snapshot.is_changed),
        content_length=int(snapshot.content_length or 0),
        error_message=snapshot.error_message,
        created_at=snapshot.created_at,
    )


def _analysis_response(analysis: CompetitiveAnalysis) -> CompetitiveAnalysisResponse:
    return CompetitiveAnalysisResponse(
        id=str(analysis.id),
        source_id=str(analysis.source_id),
        snapshot_id=str(analysis.snapshot_id),
        previous_snapshot_id=str(analysis.previous_snapshot_id) if analysis.previous_snapshot_id else None,
        workspace_id=str(analysis.workspace_id),
        content_hash=analysis.content_hash,
        previous_content_hash=analysis.previous_content_hash,
        headline_summary=analysis.headline_summary,
        findings=list(analysis.findings or []),
        overall_urgency=float(analysis.overall_urgency or 0.0),
        urgency_label=analysis.urgency_label,
        should_trigger_immediate_workflow=bool(analysis.should_trigger_immediate_workflow),
        workflow_dispatch_status=analysis.workflow_dispatch_status,
        slack_dispatch_status=analysis.slack_dispatch_status,
        model_used=analysis.model_used,
        created_at=analysis.created_at,
        updated_at=analysis.updated_at,
    )
