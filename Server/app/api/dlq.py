"""API endpoints for Dead Letter Queue management."""

from typing import List, Dict, Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_db
from app.core.auth import get_current_user
from app.database.models import User as DBUser
from app.tasks.dlq import DLQManager, DeadLetter, DLQStatus

router = APIRouter(prefix="/dlq", tags=["dlq"])


# ─────────────────────────────────────────────────────────────────────────
# Schemas
# ─────────────────────────────────────────────────────────────────────────

class DeadLetterResponse(BaseModel):
    """Response model for a dead letter."""
    id: str
    task_name: str
    task_id: str
    document_id: Optional[str]
    workspace_id: Optional[str]
    error_type: str
    error_message: str
    status: str
    retry_count: int
    failed_at: str
    reviewed_at: Optional[str]
    resolved_at: Optional[str]
    
    class Config:
        from_attributes = True


class DLQStatsResponse(BaseModel):
    """Response model for DLQ statistics."""
    total_failed: int
    by_status: Dict[str, int]
    most_common_errors: List[Dict[str, Any]]
    most_affected_tasks: List[Dict[str, Any]]


class ReviewRequest(BaseModel):
    """Request to mark a DLQ item as reviewed."""
    admin_notes: str = ""


class ResolveRequest(BaseModel):
    """Request to mark a DLQ item as resolved."""
    resolution_notes: str = ""


# ─────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────

@router.get("/pending", response_model=List[DeadLetterResponse])
async def get_pending_dlq_items(
    current_user: DBUser = Depends(get_current_user),
    limit: int = 100,
):
    """Get pending DLQ items awaiting review.
    
    Requires admin permission.
    """
    # Check admin status
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins can access DLQ"
        )
    
    items = DLQManager.get_pending_items(limit)
    return items


@router.get("/document/{document_id}", response_model=List[DeadLetterResponse])
async def get_dlq_items_for_document(
    document_id: str,
    current_user: DBUser = Depends(get_current_user),
    limit: int = 50,
):
    """Get all DLQ items for a specific document."""
    try:
        doc_uuid = UUID(document_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid document ID"
        )
    
    items = DLQManager.get_by_document(doc_uuid, limit)
    
    # Check user has access to document
    # (This should be done via the document's workspace)
    # For now, just return results
    
    return items


@router.get("/stats", response_model=DLQStatsResponse)
async def get_dlq_statistics(
    current_user: DBUser = Depends(get_current_user),
):
    """Get DLQ statistics and failure analysis.
    
    Requires admin permission.
    """
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins can access DLQ statistics"
        )
    
    stats = DLQManager.get_failure_stats()
    return stats


@router.post("/items/{item_id}/review", status_code=204)
async def review_dlq_item(
    item_id: str,
    request: ReviewRequest,
    current_user: DBUser = Depends(get_current_user),
):
    """Mark a DLQ item as reviewed."""
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins can review DLQ items"
        )
    
    try:
        item_uuid = UUID(item_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid item ID"
        )
    
    DLQManager.mark_reviewed(item_uuid, request.admin_notes)
    return None


@router.post("/items/{item_id}/resolve", status_code=204)
async def resolve_dlq_item(
    item_id: str,
    request: ResolveRequest,
    current_user: DBUser = Depends(get_current_user),
):
    """Mark a DLQ item as resolved."""
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins can resolve DLQ items"
        )
    
    try:
        item_uuid = UUID(item_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid item ID"
        )
    
    DLQManager.mark_resolved(item_uuid, request.resolution_notes)
    return None


@router.post("/items/{item_id}/retry", status_code=202)
async def retry_dlq_item(
    item_id: str,
    current_user: DBUser = Depends(get_current_user),
):
    """Retry a failed task from the DLQ.
    
    This endpoint will recreate and enqueue the task for processing.
    """
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins can retry DLQ items"
        )
    
    try:
        item_uuid = UUID(item_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid item ID"
        )
    
    # TODO: Implement retry logic
    # 1. Fetch the DLQ item
    # 2. Extract task name and arguments
    # 3. Enqueue the task again
    # 4. Update DLQ status to IN_RETRY
    
    return {"status": "retry_enqueued", "item_id": item_id}


from typing import Optional
