"""Approval workflow API routes for workspace note reviews."""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, status, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.database.models import ApprovalWorkflowPriority, ApprovalWorkflowStatus, User as DBUser
from app.database.session import get_db
from app.services.approval_workflows import ApprovalWorkflowService


router = APIRouter(prefix="/approval-workflows", tags=["Approval Workflows"])


class ApprovalActorResponse(BaseModel):
    id: str
    email: str
    name: str


class ApprovalAvailableActionsResponse(BaseModel):
    submit: bool = False
    resubmit: bool = False
    cancel: bool = False
    approve: bool = False
    reject: bool = False
    request_changes: bool = False


class ApprovalWorkflowItemResponse(BaseModel):
    note_id: str
    workspace_id: Optional[str] = None
    title: str
    summary: Optional[str] = None
    note_type: str
    author_user_id: str
    approval_status: ApprovalWorkflowStatus
    approval_priority: ApprovalWorkflowPriority
    approval_due_at: Optional[str] = None
    approval_submitted_at: Optional[str] = None
    approval_submitted_by_user_id: Optional[str] = None
    approval_decided_at: Optional[str] = None
    approval_decided_by_user_id: Optional[str] = None
    is_overdue: bool
    available_actions: ApprovalAvailableActionsResponse
    author: Optional[ApprovalActorResponse] = None
    submitted_by: Optional[ApprovalActorResponse] = None
    decided_by: Optional[ApprovalActorResponse] = None


class ApprovalWorkflowSummaryResponse(BaseModel):
    counts_by_status: Dict[str, int] = Field(default_factory=dict)
    total: int
    overdue: int


class ApprovalTransitionResponse(BaseModel):
    id: str
    note_id: str
    workspace_id: Optional[str] = None
    actor_user_id: Optional[str] = None
    from_status: ApprovalWorkflowStatus
    to_status: ApprovalWorkflowStatus
    comment: Optional[str] = None
    due_at_snapshot: Optional[str] = None
    priority_snapshot: ApprovalWorkflowPriority
    created_at: Optional[str] = None
    actor: Optional[ApprovalActorResponse] = None


class ApprovalSubmissionRequest(BaseModel):
    current_status: ApprovalWorkflowStatus
    priority: Optional[ApprovalWorkflowPriority] = None
    due_at: Optional[datetime] = None
    comment: Optional[str] = Field(default=None, max_length=2000)


class ApprovalDecisionRequest(BaseModel):
    current_status: ApprovalWorkflowStatus
    comment: Optional[str] = Field(default=None, max_length=2000)


def _service(db: AsyncSession) -> ApprovalWorkflowService:
    return ApprovalWorkflowService(db)


@router.get("", response_model=List[ApprovalWorkflowItemResponse])
async def list_approval_workflows(
    workspace_id: UUID,
    status_filter: Optional[ApprovalWorkflowStatus] = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=250),
    current_user: DBUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    service = _service(db)
    items = await service.list_dashboard_items(
        workspace_id=workspace_id,
        current_user=current_user,
        workflow_status=status_filter,
        limit=limit,
    )
    return [ApprovalWorkflowItemResponse(**item) for item in items]


@router.get("/summary", response_model=ApprovalWorkflowSummaryResponse)
async def get_approval_workflow_summary(
    workspace_id: UUID,
    current_user: DBUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    service = _service(db)
    summary = await service.get_dashboard_summary(workspace_id=workspace_id, current_user=current_user)
    return ApprovalWorkflowSummaryResponse(
        counts_by_status=summary.counts_by_status,
        total=summary.total,
        overdue=summary.overdue,
    )


@router.get("/notes/{note_id}/history", response_model=List[ApprovalTransitionResponse])
async def list_approval_workflow_history(
    note_id: UUID,
    current_user: DBUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    service = _service(db)
    history = await service.list_history(note_id=note_id, current_user=current_user)
    return [ApprovalTransitionResponse(**entry) for entry in history]


@router.post("/notes/{note_id}/submit", response_model=ApprovalWorkflowItemResponse, status_code=status.HTTP_200_OK)
async def submit_note_for_approval(
    note_id: UUID,
    payload: ApprovalSubmissionRequest,
    current_user: DBUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    service = _service(db)
    note = await service.submit(
        note_id=note_id,
        actor=current_user,
        expected_current_status=payload.current_status,
        priority=payload.priority,
        due_at=payload.due_at,
        due_at_provided="due_at" in payload.model_fields_set,
        comment=payload.comment,
    )
    await db.commit()
    item = await service.serialize_item_for_user(note=note, current_user=current_user)
    return ApprovalWorkflowItemResponse(**item)


@router.post("/notes/{note_id}/resubmit", response_model=ApprovalWorkflowItemResponse, status_code=status.HTTP_200_OK)
async def resubmit_note_for_approval(
    note_id: UUID,
    payload: ApprovalSubmissionRequest,
    current_user: DBUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    service = _service(db)
    note = await service.resubmit(
        note_id=note_id,
        actor=current_user,
        expected_current_status=payload.current_status,
        priority=payload.priority,
        due_at=payload.due_at,
        due_at_provided="due_at" in payload.model_fields_set,
        comment=payload.comment,
    )
    await db.commit()
    item = await service.serialize_item_for_user(note=note, current_user=current_user)
    return ApprovalWorkflowItemResponse(**item)


@router.post("/notes/{note_id}/approve", response_model=ApprovalWorkflowItemResponse, status_code=status.HTTP_200_OK)
async def approve_note_workflow(
    note_id: UUID,
    payload: ApprovalDecisionRequest,
    current_user: DBUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    service = _service(db)
    note = await service.approve(
        note_id=note_id,
        actor=current_user,
        expected_current_status=payload.current_status,
        comment=payload.comment,
    )
    await db.commit()
    item = await service.serialize_item_for_user(note=note, current_user=current_user)
    return ApprovalWorkflowItemResponse(**item)


@router.post("/notes/{note_id}/reject", response_model=ApprovalWorkflowItemResponse, status_code=status.HTTP_200_OK)
async def reject_note_workflow(
    note_id: UUID,
    payload: ApprovalDecisionRequest,
    current_user: DBUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    service = _service(db)
    note = await service.reject(
        note_id=note_id,
        actor=current_user,
        expected_current_status=payload.current_status,
        comment=payload.comment or "",
    )
    await db.commit()
    item = await service.serialize_item_for_user(note=note, current_user=current_user)
    return ApprovalWorkflowItemResponse(**item)


@router.post("/notes/{note_id}/request-changes", response_model=ApprovalWorkflowItemResponse, status_code=status.HTTP_200_OK)
async def request_changes_for_note_workflow(
    note_id: UUID,
    payload: ApprovalDecisionRequest,
    current_user: DBUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    service = _service(db)
    note = await service.request_changes(
        note_id=note_id,
        actor=current_user,
        expected_current_status=payload.current_status,
        comment=payload.comment or "",
    )
    await db.commit()
    item = await service.serialize_item_for_user(note=note, current_user=current_user)
    return ApprovalWorkflowItemResponse(**item)


@router.post("/notes/{note_id}/cancel", response_model=ApprovalWorkflowItemResponse, status_code=status.HTTP_200_OK)
async def cancel_note_workflow(
    note_id: UUID,
    payload: ApprovalDecisionRequest,
    current_user: DBUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    service = _service(db)
    note = await service.cancel(
        note_id=note_id,
        actor=current_user,
        expected_current_status=payload.current_status,
        comment=payload.comment,
    )
    await db.commit()
    item = await service.serialize_item_for_user(note=note, current_user=current_user)
    return ApprovalWorkflowItemResponse(**item)
