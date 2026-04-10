"""Notes API routes for user note management."""
from typing import Optional, List, Any, Dict
from datetime import datetime
from uuid import UUID
import logging
from fastapi import APIRouter, Depends, HTTPException, status, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, func, text

from app.database.session import get_db
from app.database.models import Note, NoteConnectionSuggestion, User as DBUser
from app.core.auth import get_current_user, get_workspace_context
from app.core.audit import log_audit_event, AuditAction, EntityType
from app.services.graph_service import get_graph_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/notes", tags=["Notes"])

# Helper function for lazy task imports to avoid Celery import hang at startup
def queue_note_embedding(note_id: str) -> None:
    """Queue note embedding generation (lazy import to avoid startup hang)."""
    try:
        from app.tasks.note_embeddings import generate_note_embedding
        generate_note_embedding.delay(str(note_id))
    except Exception as e:
        # Log but don't fail if task queueing fails
        print(f"Warning: Failed to queue embedding task: {e}")

def queue_note_summary(note_id: str) -> None:
    """Queue note summary generation (lazy import to avoid startup hang)."""
    try:
        from app.tasks.note_summaries import generate_note_summary
        generate_note_summary.delay(str(note_id))
    except Exception as e:
        # Log but don't fail if task queueing fails
        print(f"Warning: Failed to queue summary task: {e}")


def queue_note_connection_suggestions(note_id: str) -> None:
    """Queue async note-connection suggestions (lazy import to avoid startup hangs)."""
    try:
        from app.tasks.connection_suggestions import generate_connection_suggestions
        generate_connection_suggestions.delay(str(note_id))
    except Exception as e:
        print(f"Warning: Failed to queue connection suggestion task: {e}")


# ==================== Schemas ====================

class NoteCreate(BaseModel):
    """Schema for creating a note."""
    title: str = Field(..., min_length=1, max_length=500)
    content: str = Field(..., min_length=1)
    workspace_id: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    source_url: Optional[str] = None
    note_type: str = Field(default='note', pattern='^(note|web-clip|document|voice|ai-generated)$')


class NoteUpdate(BaseModel):
    """Schema for updating a note."""
    title: Optional[str] = Field(None, min_length=1, max_length=500)
    content: Optional[str] = None
    tags: Optional[List[str]] = None
    connections: Optional[List[str]] = None
    note_type: Optional[str] = Field(None, pattern='^(note|web-clip|document|voice|ai-generated)$')


class NoteResponse(BaseModel):
    """Response schema for a note."""
    id: str
    workspace_id: Optional[str]
    user_id: str
    title: str
    content: str
    summary: Optional[str]
    tags: List[str]
    connections: List[str]
    note_type: str
    word_count: int
    ai_generated: bool
    confidence_score: Optional[float]
    source_url: Optional[str]
    created_at: str
    updated_at: str

    class Config:
        from_attributes = True


class NoteListResponse(BaseModel):
    """Paginated notes response."""
    items: List[NoteResponse]
    total: int
    page: int
    page_size: int


class SuggestedNoteSnippet(BaseModel):
    id: str
    title: str
    content_preview: str
    tags: List[str] = Field(default_factory=list)
    created_at: str


class ConnectionSuggestionResponse(BaseModel):
    id: str
    workspace_id: str
    note_id: str
    suggested_note: SuggestedNoteSnippet
    similarity_score: float
    reason: str
    status: str
    metadata: Dict[str, Any] = Field(default_factory=dict)
    responded_at: Optional[str] = None
    created_at: str


class ConnectionSuggestionActionResponse(BaseModel):
    success: bool
    suggestion_id: str
    note_id: str
    status: str
    connections: List[str] = Field(default_factory=list)


# ==================== Routes ====================

# ==================== Utility Functions ====================

def calculate_word_count(content: str) -> int:
    """Calculate word count from note content.
    
    Args:
        content: Note content text
        
    Returns:
        Number of words
    """
    if not content:
        return 0
    return len(content.strip().split())


async def sync_note_search_index(db: AsyncSession, note_id: UUID) -> None:
    """Keep the PostgreSQL full-text search vector aligned with note content."""
    try:
        await db.execute(
            text(
                """
                UPDATE notes
                SET content_tsv = to_tsvector('english', COALESCE(title, '') || ' ' || COALESCE(content, ''))
                WHERE id = :note_id
                """
            ),
            {"note_id": note_id},
        )
    except Exception as exc:
        logger.warning("Skipping content_tsv sync for note %s: %s", note_id, exc)


async def sync_note_graph(db: AsyncSession, note: Note) -> None:
    """Best-effort graph sync that never blocks note persistence."""
    try:
        await get_graph_service().sync_note_to_graph(db, note)
    except Exception as exc:
        await db.rollback()
        logger.warning("Skipping graph sync for note %s in workspace %s: %s", note.id, note.workspace_id, exc)


async def remove_note_graph(db: AsyncSession, workspace_id: UUID, note_id: UUID) -> None:
    """Best-effort graph cleanup that never blocks note deletion."""
    try:
        await get_graph_service().remove_note_from_graph(db, workspace_id, note_id)
    except Exception as exc:
        await db.rollback()
        logger.warning("Skipping graph cleanup for note %s in workspace %s: %s", note_id, workspace_id, exc)


def serialize_connection_suggestion(suggestion: NoteConnectionSuggestion) -> ConnectionSuggestionResponse:
    suggested_note = suggestion.suggested_note
    preview = ""
    if suggested_note and suggested_note.content:
        preview = suggested_note.content[:160]
        if len(suggested_note.content) > 160:
            preview += "..."

    return ConnectionSuggestionResponse(
        id=str(suggestion.id),
        workspace_id=str(suggestion.workspace_id),
        note_id=str(suggestion.source_note_id),
        suggested_note=SuggestedNoteSnippet(
            id=str(suggested_note.id) if suggested_note else "",
            title=suggested_note.title if suggested_note else "Deleted note",
            content_preview=preview,
            tags=list(suggested_note.tags or []) if suggested_note else [],
            created_at=suggested_note.created_at.isoformat() if suggested_note and suggested_note.created_at else datetime.utcnow().isoformat(),
        ),
        similarity_score=float(suggestion.similarity_score or 0.0),
        reason=suggestion.reason,
        status=suggestion.status,
        metadata=dict(suggestion.suggestion_metadata or {}),
        responded_at=suggestion.responded_at.isoformat() if suggestion.responded_at else None,
        created_at=suggestion.created_at.isoformat() if suggestion.created_at else datetime.utcnow().isoformat(),
    )


# ==================== Endpoints ====================

@router.post("", response_model=NoteResponse, status_code=status.HTTP_201_CREATED)
async def create_note(
    note_data: NoteCreate,
    current_user: DBUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Create a new note in a specific workspace.
    
    Args:
        note_data: Note creation data (includes workspace_id)
        current_user: Current authenticated user
        db: Database session
        
    Returns:
        Created note with workspace delegation
        
    Raises:
        HTTPException: If workspace_id is not provided or user lacks access
    """
    
    # Validate workspace - REQUIRED for workspace delegation
    if not note_data.workspace_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="workspace_id is required when creating notes"
        )
    
    try:
        workspace_id = UUID(note_data.workspace_id)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid workspace_id format"
        )
    
    logger.info(f"📝 [NOTE CREATE] User {current_user.id} creating note in workspace {workspace_id}")
    
    # Verify workspace membership and permission
    context = await get_workspace_context(workspace_id, current_user, db)
    logger.debug(f"✅ [WORKSPACE VERIFIED] User has {context.role} role in workspace {workspace_id}")
    
    # Create note
    new_note = Note(
        workspace_id=workspace_id,
        user_id=current_user.id,
        title=note_data.title,
        content=note_data.content,
        tags=note_data.tags,
        source_url=note_data.source_url,
        note_type=note_data.note_type,
        word_count=calculate_word_count(note_data.content),
        ai_generated=False,
    )
    
    db.add(new_note)
    await db.flush()
    await sync_note_search_index(db, new_note.id)
    
    # Log action
    await log_audit_event(
        user_id=current_user.id,
        workspace_id=workspace_id,
        action=AuditAction.NOTE_CREATED,
        entity_type=EntityType.NOTE,
        entity_id=new_note.id,
        db=db
    )
    
    await db.commit()
    await sync_note_graph(db, new_note)
    
    # Queue background tasks for data enrichment
    # Always generate embedding
    queue_note_embedding(str(new_note.id))
    queue_note_connection_suggestions(str(new_note.id))
    
    # Generate summary for longer notes
    if len(note_data.content.split()) > 20:  # >20 words is substantive
        queue_note_summary(str(new_note.id))
    
    return NoteResponse(
        id=str(new_note.id),
        workspace_id=str(new_note.workspace_id) if new_note.workspace_id else None,
        user_id=str(new_note.user_id),
        title=new_note.title,
        content=new_note.content,
        summary=new_note.summary,
        tags=new_note.tags or [],
        connections=new_note.connections or [],
        note_type=new_note.note_type,
        word_count=new_note.word_count or 0,
        ai_generated=bool(new_note.ai_generated),
        confidence_score=new_note.confidence_score,
        source_url=new_note.source_url,
        created_at=new_note.created_at.isoformat(),
        updated_at=new_note.updated_at.isoformat() if new_note.updated_at else new_note.created_at.isoformat(),
    )


@router.get("", response_model=NoteListResponse)
async def list_notes(
    workspace_id: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    tags: Optional[List[str]] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: DBUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """List user's notes with filtering.
    
    Args:
        workspace_id: Filter by workspace
        search: Search in title and content
        tags: Filter by tags
        page: Page number
        page_size: Items per page
        current_user: Current user
        db: Database session
        
    Returns:
        Paginated notes
    """
    query = select(Note).where(Note.user_id == current_user.id)
    
    # Filter by workspace if provided
    if workspace_id:
        query = query.where(Note.workspace_id == UUID(workspace_id))
    
    # Search filter
    if search:
        search_term = f"%{search}%"
        query = query.where(
            or_(
                Note.title.ilike(search_term),
                Note.content.ilike(search_term)
            )
        )
    
    # Tags filter - notes must have ALL specified tags
    if tags:
        for tag in tags:
            query = query.where(Note.tags.contains([tag]))
    
    # Get total count - build count query with same filters
    count_query = select(func.count()).select_from(Note).where(Note.user_id == current_user.id)
    if workspace_id:
        count_query = count_query.where(Note.workspace_id == UUID(workspace_id))
    if search:
        search_term = f"%{search}%"
        count_query = count_query.where(
            or_(
                Note.title.ilike(search_term),
                Note.content.ilike(search_term)
            )
        )
    if tags:
        for tag in tags:
            count_query = count_query.where(Note.tags.contains([tag]))
    
    count_result = await db.execute(count_query)
    total = count_result.scalar() or 0
    
    # Apply pagination
    offset = (page - 1) * page_size
    query = query.order_by(Note.updated_at.desc()).offset(offset).limit(page_size)
    
    result = await db.execute(query)
    notes = result.scalars().all()
    
    return NoteListResponse(
        items=[
            NoteResponse(
                id=str(note.id),
                workspace_id=str(note.workspace_id) if note.workspace_id else None,
                user_id=str(note.user_id),
                title=note.title,
                content=note.content,
                summary=note.summary,
                tags=note.tags or [],
                connections=note.connections or [],
                note_type=note.note_type,
                word_count=note.word_count or 0,
                ai_generated=bool(note.ai_generated),
                confidence_score=note.confidence_score,
                source_url=note.source_url,
                created_at=note.created_at.isoformat(),
                updated_at=note.updated_at.isoformat() if note.updated_at else note.created_at.isoformat(),
            )
            for note in notes
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{note_id}", response_model=NoteResponse)
async def get_note(
    note_id: str,
    current_user: DBUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get a specific note.
    
    Args:
        note_id: Note ID
        current_user: Current user
        db: Database session
        
    Returns:
        Note details
    """
    result = await db.execute(
        select(Note).where(
            and_(
                Note.id == UUID(note_id),
                Note.user_id == current_user.id
            )
        )
    )
    note = result.scalar_one_or_none()
    
    if not note:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Note not found"
        )
    
    return NoteResponse(
        id=str(note.id),
        workspace_id=str(note.workspace_id) if note.workspace_id else None,
        user_id=str(note.user_id),
        title=note.title,
        content=note.content,
        summary=note.summary,
        tags=note.tags or [],
        connections=note.connections or [],
        note_type=note.note_type,
        word_count=note.word_count or 0,
        ai_generated=bool(note.ai_generated),
        confidence_score=note.confidence_score,
        source_url=note.source_url,
        created_at=note.created_at.isoformat(),
        updated_at=note.updated_at.isoformat() if note.updated_at else note.created_at.isoformat(),
    )


@router.patch("/{note_id}", response_model=NoteResponse)
async def update_note(
    note_id: str,
    updates: NoteUpdate,
    current_user: DBUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Update a note.
    
    Args:
        note_id: Note ID
        updates: Fields to update
        current_user: Current user
        db: Database session
        
    Returns:
        Updated note
    """
    result = await db.execute(
        select(Note).where(
            and_(
                Note.id == UUID(note_id),
                Note.user_id == current_user.id
            )
        )
    )
    note = result.scalar_one_or_none()
    
    if not note:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Note not found"
        )
    
    # Update fields
    semantic_fields_updated = False
    if updates.title is not None:
        note.title = updates.title
        semantic_fields_updated = True
    if updates.content is not None:
        note.content = updates.content
        note.word_count = calculate_word_count(updates.content)
        semantic_fields_updated = True
    if updates.tags is not None:
        note.tags = updates.tags
        semantic_fields_updated = True
    if updates.connections is not None:
        note.connections = updates.connections
    if updates.note_type is not None:
        note.note_type = updates.note_type
    
    note.updated_at = datetime.utcnow()
    if semantic_fields_updated:
        await sync_note_search_index(db, note.id)
    
    # Log action
    await log_audit_event(
        user_id=current_user.id,
        workspace_id=note.workspace_id,
        action=AuditAction.NOTE_UPDATED,
        entity_type=EntityType.NOTE,
        entity_id=note.id,
        db=db
    )
    
    await db.commit()
    await sync_note_graph(db, note)
    
    # Queue background tasks if content changed
    queue_note_connection_suggestions(str(note.id))
    if semantic_fields_updated:
        # Re-generate embedding for updated content
        queue_note_embedding(str(note.id))
        # Re-generate summary if content is substantive
        if len(note.content.split()) > 20:
            queue_note_summary(str(note.id))
    
    return NoteResponse(
        id=str(note.id),
        workspace_id=str(note.workspace_id) if note.workspace_id else None,
        user_id=str(note.user_id),
        title=note.title,
        content=note.content,
        summary=note.summary,
        tags=note.tags or [],
        connections=note.connections or [],
        note_type=note.note_type,
        word_count=note.word_count or 0,
        ai_generated=bool(note.ai_generated),
        confidence_score=note.confidence_score,
        source_url=note.source_url,
        created_at=note.created_at.isoformat(),
        updated_at=note.updated_at.isoformat() if note.updated_at else note.created_at.isoformat(),
    )


@router.delete("/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_note(
    note_id: str,
    current_user: DBUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Delete a note.
    
    Args:
        note_id: Note ID
        current_user: Current user
        db: Database session
    """
    result = await db.execute(
        select(Note).where(
            and_(
                Note.id == UUID(note_id),
                Note.user_id == current_user.id
            )
        )
    )
    note = result.scalar_one_or_none()
    
    if not note:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Note not found"
        )
    
    # Log action before deletion
    await log_audit_event(
        user_id=current_user.id,
        workspace_id=note.workspace_id,
        action=AuditAction.NOTE_DELETED,
        entity_type=EntityType.NOTE,
        entity_id=note.id,
        db=db
    )
    
    workspace_id = note.workspace_id
    await db.delete(note)
    await db.commit()
    if workspace_id:
        await remove_note_graph(db, workspace_id, note.id)


@router.get("/{note_id}/connection-suggestions", response_model=List[ConnectionSuggestionResponse])
async def list_connection_suggestions(
    note_id: str,
    status_filter: str = Query("pending", alias="status"),
    current_user: DBUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """List persisted connection suggestions for a note."""
    try:
        note_uuid = UUID(note_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid note ID format")
    note_result = await db.execute(select(Note).where(Note.id == note_uuid))
    note = note_result.scalar_one_or_none()

    if not note:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Note not found")
    if note.workspace_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Note has no workspace")

    await get_workspace_context(note.workspace_id, current_user, db)

    query = select(NoteConnectionSuggestion).where(NoteConnectionSuggestion.source_note_id == note.id)
    if status_filter:
        query = query.where(NoteConnectionSuggestion.status == status_filter)
    query = query.order_by(NoteConnectionSuggestion.similarity_score.desc(), NoteConnectionSuggestion.created_at.desc())

    result = await db.execute(query)
    suggestions = result.scalars().all()
    return [serialize_connection_suggestion(suggestion) for suggestion in suggestions]


@router.post("/{note_id}/connection-suggestions/{suggestion_id}/confirm", response_model=ConnectionSuggestionActionResponse)
async def confirm_connection_suggestion(
    note_id: str,
    suggestion_id: str,
    current_user: DBUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Confirm a suggested connection and persist the user signal."""
    try:
        note_uuid = UUID(note_id)
        suggestion_uuid = UUID(suggestion_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid note or suggestion ID format")

    result = await db.execute(
        select(NoteConnectionSuggestion).where(
            NoteConnectionSuggestion.id == suggestion_uuid,
            NoteConnectionSuggestion.source_note_id == note_uuid,
        )
    )
    suggestion = result.scalar_one_or_none()
    if not suggestion:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Connection suggestion not found")

    source_note_result = await db.execute(select(Note).where(Note.id == suggestion.source_note_id))
    source_note = source_note_result.scalar_one_or_none()
    target_note_result = await db.execute(select(Note).where(Note.id == suggestion.suggested_note_id))
    target_note = target_note_result.scalar_one_or_none()

    if not source_note or not target_note:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Connected note no longer exists")
    if source_note.workspace_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Source note has no workspace")

    await get_workspace_context(source_note.workspace_id, current_user, db)

    source_connections = [str(connection_id) for connection_id in (source_note.connections or [])]
    target_connections = [str(connection_id) for connection_id in (target_note.connections or [])]

    if str(target_note.id) not in source_connections:
        source_connections.append(str(target_note.id))
    if str(source_note.id) not in target_connections:
        target_connections.append(str(source_note.id))

    source_note.connections = source_connections
    target_note.connections = target_connections

    suggestion.status = "confirmed"
    suggestion.responded_by = current_user.id
    suggestion.responded_at = datetime.utcnow()

    reverse_result = await db.execute(
        select(NoteConnectionSuggestion).where(
            NoteConnectionSuggestion.source_note_id == target_note.id,
            NoteConnectionSuggestion.suggested_note_id == source_note.id,
        )
    )
    reverse_suggestion = reverse_result.scalar_one_or_none()
    if reverse_suggestion and reverse_suggestion.status == "pending":
        reverse_suggestion.status = "confirmed"
        reverse_suggestion.responded_by = current_user.id
        reverse_suggestion.responded_at = datetime.utcnow()

    await db.commit()
    await sync_note_graph(db, source_note)
    await sync_note_graph(db, target_note)
    queue_note_connection_suggestions(str(source_note.id))
    queue_note_connection_suggestions(str(target_note.id))

    return ConnectionSuggestionActionResponse(
        success=True,
        suggestion_id=str(suggestion.id),
        note_id=str(source_note.id),
        status=suggestion.status,
        connections=[str(connection_id) for connection_id in (source_note.connections or [])],
    )


@router.post("/{note_id}/connection-suggestions/{suggestion_id}/dismiss", response_model=ConnectionSuggestionActionResponse)
async def dismiss_connection_suggestion(
    note_id: str,
    suggestion_id: str,
    current_user: DBUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Dismiss a suggested connection and preserve that user signal."""
    try:
        note_uuid = UUID(note_id)
        suggestion_uuid = UUID(suggestion_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid note or suggestion ID format")

    result = await db.execute(
        select(NoteConnectionSuggestion).where(
            NoteConnectionSuggestion.id == suggestion_uuid,
            NoteConnectionSuggestion.source_note_id == note_uuid,
        )
    )
    suggestion = result.scalar_one_or_none()
    if not suggestion:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Connection suggestion not found")

    source_note_result = await db.execute(select(Note).where(Note.id == suggestion.source_note_id))
    source_note = source_note_result.scalar_one_or_none()
    if not source_note:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source note not found")
    if source_note.workspace_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Source note has no workspace")

    await get_workspace_context(source_note.workspace_id, current_user, db)

    suggestion.status = "dismissed"
    suggestion.responded_by = current_user.id
    suggestion.responded_at = datetime.utcnow()
    await db.commit()

    return ConnectionSuggestionActionResponse(
        success=True,
        suggestion_id=str(suggestion.id),
        note_id=str(source_note.id),
        status=suggestion.status,
        connections=[str(connection_id) for connection_id in (source_note.connections or [])],
    )


@router.get("/workspace/{workspace_id}", response_model=NoteListResponse)
async def get_workspace_notes(
    workspace_id: str,
    search: Optional[str] = Query(None),
    tags: Optional[List[str]] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: DBUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get all notes in a workspace with proper authorization.
    
    Args:
        workspace_id: Workspace UUID
        search: Search in title and content
        tags: Filter by tags
        page: Page number
        page_size: Items per page
        current_user: Current user
        db: Database session
        
    Returns:
        Paginated notes for the workspace
    """
    # Verify workspace membership
    workspace_uuid = UUID(workspace_id)
    await get_workspace_context(workspace_uuid, current_user, db)
    
    # Build base filters
    filters = [Note.workspace_id == workspace_uuid]
    
    # Search filter
    if search:
        search_term = f"%{search}%"
        filters.append(
            or_(
                Note.title.ilike(search_term),
                Note.content.ilike(search_term)
            )
        )
    
    # Tags filter - notes must have ALL specified tags
    if tags:
        for tag in tags:
            filters.append(Note.tags.contains([tag]))
    
    # Build count query with all filters
    count_query = select(func.count()).select_from(Note).where(and_(*filters))
    count_result = await db.execute(count_query)
    total = count_result.scalar() or 0
    
    # Build data query with all filters and pagination
    query = select(Note).where(and_(*filters)).order_by(Note.updated_at.desc())
    offset = (page - 1) * page_size
    query = query.offset(offset).limit(page_size)
    
    result = await db.execute(query)
    notes = result.scalars().all()
    
    return NoteListResponse(
        items=[
            NoteResponse(
                id=str(note.id),
                workspace_id=str(note.workspace_id) if note.workspace_id else None,
                user_id=str(note.user_id),
                title=note.title,
                content=note.content,
                summary=note.summary,
                tags=note.tags or [],
                connections=note.connections or [],
                note_type=note.note_type,
                word_count=note.word_count or 0,
                ai_generated=bool(note.ai_generated),
                confidence_score=note.confidence_score,
                source_url=note.source_url,
                created_at=note.created_at.isoformat(),
                updated_at=note.updated_at.isoformat() if note.updated_at else note.created_at.isoformat(),
            )
            for note in notes
        ],
        total=total,
        page=page,
        page_size=page_size,
    )
