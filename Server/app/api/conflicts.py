"""Conflict Detection API endpoints."""
from typing import Optional, Dict, Any
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, and_, func

from app.database.session import get_db
from app.database.models import ConflictReport, Note, User as DBUser
from app.core.auth import get_current_user, get_workspace_context
from app.tasks.conflict_detection import run_conflict_detection
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/conflicts", tags=["Conflict Detection"])


# ==================== Schemas ====================

class NoteSnippet(BaseModel):
    """Snippet of a note for conflict display."""
    id: str
    title: str
    content: str
    created_at: datetime


class ConflictResponse(BaseModel):
    """Response schema for a conflict report."""
    id: str
    workspace_id: str
    note_a: NoteSnippet
    note_b: NoteSnippet
    conflict_type: str  # factual | opinion | date | numerical
    conflict_summary: str
    conflict_quote_a: Optional[str]
    conflict_quote_b: Optional[str]
    similarity_score: float
    severity: str  # low | medium | high
    status: str  # pending | resolved | dismissed
    resolution_note: Optional[str]
    resolved_at: Optional[datetime]
    resolved_by: Optional[str]
    created_at: datetime
    
    class Config:
        from_attributes = True


class ConflictResolveRequest(BaseModel):
    """Request to resolve or dismiss a conflict."""
    action: str = Field(..., description="Action: 'resolved' or 'dismissed'")
    resolution_note: Optional[str] = Field(None, description="Optional notes about resolution")


class ConflictStatsResponse(BaseModel):
    """Statistics about conflicts in workspace."""
    total_pending: int
    total_resolved: int
    total_dismissed: int
    by_severity: Dict[str, int]
    by_type: Dict[str, int]


# ==================== Endpoints ====================

@router.get("/", response_model=Dict[str, Any])
async def list_conflicts(
    workspace_id: str = Query(..., description="Workspace ID"),
    status: Optional[str] = Query(None, description="Filter by status: pending, resolved, dismissed"),
    severity: Optional[str] = Query(None, description="Filter by severity: low, medium, high"),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    current_user: DBUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """
    List conflicts in a workspace.
    
    Filters:
    - status: pending, resolved, dismissed
    - severity: low, medium, high
    
    Returns paginated list of conflicts.
    """
    # Parse and verify workspace_id format
    try:
        workspace_uuid = UUID(workspace_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid workspace ID format"
        )
    
    # CRITICAL FIX: Verify user has access to workspace (correct parameter order)
    await get_workspace_context(workspace_uuid, current_user, db)
    
    from sqlalchemy import select, and_
    
    query = select(ConflictReport).where(
        ConflictReport.workspace_id == workspace_uuid
    )
    
    # Apply filters
    if status:
        query = query.where(ConflictReport.status == status)
    if severity:
        query = query.where(ConflictReport.severity == severity)
    
    # Order by severity (high first) and date (newest first)
    query = query.order_by(
        ConflictReport.severity.desc(),
        ConflictReport.created_at.desc()
    )
    
    # Get total count
    count_query = select(func.count()).select_from(ConflictReport).where(
        ConflictReport.workspace_id == workspace_uuid
    )
    if status:
        count_query = count_query.where(ConflictReport.status == status)
    if severity:
        count_query = count_query.where(ConflictReport.severity == severity)
    
    count_result = await db.execute(count_query)
    total = count_result.scalar() or 0
    
    # Apply pagination
    query = query.offset(skip).limit(limit)
    result = await db.execute(query)
    conflicts = result.scalars().all()
    
    # Convert to response format
    response_conflicts = []
    for conflict in conflicts:
        response_conflicts.append(
            ConflictResponse(
                id=str(conflict.id),
                workspace_id=str(conflict.workspace_id),
                note_a=NoteSnippet(
                    id=str(conflict.note_a.id),
                    title=conflict.note_a.title,
                    content=conflict.note_a.content[:200],  # Preview
                    created_at=conflict.note_a.created_at
                ),
                note_b=NoteSnippet(
                    id=str(conflict.note_b.id),
                    title=conflict.note_b.title,
                    content=conflict.note_b.content[:200],  # Preview
                    created_at=conflict.note_b.created_at
                ),
                conflict_type=conflict.conflict_type,
                conflict_summary=conflict.conflict_summary,
                conflict_quote_a=conflict.conflict_quote_a,
                conflict_quote_b=conflict.conflict_quote_b,
                similarity_score=conflict.similarity_score,
                severity=conflict.severity,
                status=conflict.status,
                resolution_note=conflict.resolution_note,
                resolved_at=conflict.resolved_at,
                resolved_by=str(conflict.resolved_by) if conflict.resolved_by else None,
                created_at=conflict.created_at
            )
        )
    
    return {
        "total": total,
        "skip": skip,
        "limit": limit,
        "conflicts": response_conflicts
    }


@router.get("/{conflict_id}", response_model=ConflictResponse)
async def get_conflict(
    conflict_id: str,
    workspace_id: str = Query(..., description="Workspace ID"),
    current_user: DBUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> ConflictResponse:
    """Get details of a specific conflict."""
    from sqlalchemy import select
    
    # Parse and verify IDs
    try:
        conflict_uuid = UUID(conflict_id)
        workspace_uuid = UUID(workspace_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid conflict or workspace ID format"
        )
    
    # CRITICAL FIX: Verify user has access to workspace FIRST
    await get_workspace_context(workspace_uuid, current_user, db)
    
    # Query conflict with workspace filter (prevent cross-workspace access)
    result = await db.execute(
        select(ConflictReport).where(
            ConflictReport.id == conflict_uuid,
            ConflictReport.workspace_id == workspace_uuid
        )
    )
    conflict = result.scalar_one_or_none()
    
    if not conflict:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conflict not found"
        )
    
    # Load full note content
    note_a_result = await db.execute(
        select(Note).where(Note.id == conflict.note_id_a)
    )
    note_a = note_a_result.scalar_one_or_none()
    
    note_b_result = await db.execute(
        select(Note).where(Note.id == conflict.note_id_b)
    )
    note_b = note_b_result.scalar_one_or_none()
    
    conflict.note_a = note_a
    conflict.note_b = note_b
    
    return ConflictResponse(
        id=str(conflict.id),
        workspace_id=str(conflict.workspace_id),
        note_a=NoteSnippet(
            id=str(conflict.note_a.id) if conflict.note_a else "unknown",
            title=conflict.note_a.title if conflict.note_a else "Deleted",
            content=conflict.note_a.content if conflict.note_a else "",  # Full content
            created_at=conflict.note_a.created_at if conflict.note_a else datetime.utcnow()
        ),
        note_b=NoteSnippet(
            id=str(conflict.note_b.id) if conflict.note_b else "unknown",
            title=conflict.note_b.title if conflict.note_b else "Deleted",
            content=conflict.note_b.content if conflict.note_b else "",  # Full content
            created_at=conflict.note_b.created_at if conflict.note_b else datetime.utcnow()
        ),
        conflict_type=conflict.conflict_type,
        conflict_summary=conflict.conflict_summary,
        conflict_quote_a=conflict.conflict_quote_a,
        conflict_quote_b=conflict.conflict_quote_b,
        similarity_score=conflict.similarity_score,
        severity=conflict.severity,
        status=conflict.status,
        resolution_note=conflict.resolution_note,
        resolved_at=conflict.resolved_at,
        resolved_by=str(conflict.resolved_by) if conflict.resolved_by else None,
        created_at=conflict.created_at
    )


@router.patch("/{conflict_id}", response_model=Dict[str, Any])
async def resolve_conflict(
    conflict_id: str,
    request: ConflictResolveRequest,
    workspace_id: str = Query(..., description="Workspace ID"),
    current_user: DBUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """
    Resolve or dismiss a conflict.
    
    Actions:
    - resolved: Marked as resolved by user
    - dismissed: User acknowledges but doesn't need to act
    """
    from sqlalchemy import select, update
    from datetime import datetime
    
    # Parse and verify IDs
    try:
        conflict_uuid = UUID(conflict_id)
        workspace_uuid = UUID(workspace_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid conflict or workspace ID format"
        )
    
    # CRITICAL FIX: Verify user has access to workspace FIRST
    await get_workspace_context(workspace_uuid, current_user, db)
    
    # Query conflict with workspace filter
    result = await db.execute(
        select(ConflictReport).where(
            ConflictReport.id == conflict_uuid,
            ConflictReport.workspace_id == workspace_uuid
        )
    )
    conflict = result.scalar_one_or_none()
    
    if not conflict:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conflict not found"
        )
    
    # Validate action
    if request.action not in ["resolved", "dismissed"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Action must be 'resolved' or 'dismissed'"
        )
    
    # Update conflict
    conflict.status = request.action
    conflict.resolution_note = request.resolution_note
    conflict.resolved_at = datetime.utcnow()
    conflict.resolved_by = current_user.id
    
    await db.commit()
    
    return {
        "success": True,
        "conflict_id": str(conflict.id),
        "status": conflict.status,
        "resolved_at": conflict.resolved_at.isoformat()
    }


@router.get("/{workspace_id}/stats", response_model=ConflictStatsResponse)
async def get_conflict_stats(
    workspace_id: str,
    current_user: DBUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> ConflictStatsResponse:
    """
    Get conflict statistics for a workspace.
    
    Shows:
    - Total pending, resolved, dismissed
    - Breakdown by severity
    - Breakdown by conflict type
    """
    try:
        workspace_uuid = UUID(workspace_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid workspace ID format"
        )

    await get_workspace_context(workspace_uuid, current_user, db)

    conflicts_result = await db.execute(
        select(ConflictReport).where(ConflictReport.workspace_id == workspace_uuid)
    )
    conflicts = conflicts_result.scalars().all()

    total_pending = sum(1 for c in conflicts if c.status == "pending")
    total_resolved = sum(1 for c in conflicts if c.status == "resolved")
    total_dismissed = sum(1 for c in conflicts if c.status == "dismissed")

    by_severity: Dict[str, int] = {}
    for conflict in conflicts:
        by_severity[conflict.severity] = by_severity.get(conflict.severity, 0) + 1

    by_type: Dict[str, int] = {}
    for conflict in conflicts:
        by_type[conflict.conflict_type] = by_type.get(conflict.conflict_type, 0) + 1
    
    return ConflictStatsResponse(
        total_pending=total_pending,
        total_resolved=total_resolved,
        total_dismissed=total_dismissed,
        by_severity=by_severity,
        by_type=by_type
    )


@router.post("/{workspace_id}/run-detection", response_model=Dict[str, Any])
async def trigger_conflict_detection(
    workspace_id: str,
    current_user: DBUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """
    Manually trigger conflict detection for a workspace.
    
    WARNING: This runs immediately (not queued).
    For production, use scheduled/background processing.
    
    Normal flow: Nightly job runs automatically.
    Use this for testing or after bulk note imports.
    """
    try:
        workspace_uuid = UUID(workspace_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid workspace ID format"
        )

    workspace_context = await get_workspace_context(workspace_uuid, current_user, db)
    if workspace_context.role.value != "owner":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only workspace owner can trigger detection"
        )
    
    # Queue the detection task
    task = run_conflict_detection.delay(workspace_id)
    
    return {
        "status": "queued",
        "workspace_id": workspace_id,
        "task_id": task.id,
        "message": "Conflict detection started in background"
    }


@router.get("/{workspace_id}/pending-count", response_model=Dict[str, Any])
async def get_pending_conflict_count(
    workspace_id: str,
    current_user: DBUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """
    Get count of pending conflicts.
    
    Quick endpoint for dashboard badge.
    """
    try:
        workspace_uuid = UUID(workspace_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid workspace ID format"
        )

    await get_workspace_context(workspace_uuid, current_user, db)

    pending_result = await db.execute(
        select(func.count())
        .select_from(ConflictReport)
        .where(
            and_(
                ConflictReport.workspace_id == workspace_uuid,
                ConflictReport.status == "pending"
            )
        )
    )
    pending_count = pending_result.scalar() or 0

    high_severity_result = await db.execute(
        select(func.count())
        .select_from(ConflictReport)
        .where(
            and_(
                ConflictReport.workspace_id == workspace_uuid,
                ConflictReport.status == "pending",
                ConflictReport.severity == "high"
            )
        )
    )
    high_severity_count = high_severity_result.scalar() or 0
    
    return {
        "workspace_id": workspace_id,
        "pending_count": pending_count,
        "high_severity_count": high_severity_count,
        "action": "View conflicts" if pending_count > 0 else "No pending conflicts"
    }
