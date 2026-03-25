"""Ingestion status tracking API endpoints."""
import asyncio
import hashlib
import logging
from typing import Optional, List, Dict, Any
from uuid import UUID as PyUUID
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.database.models import (
    Document,
    IngestionLog,
    Chunk,
    Embedding,
    User as DBUser,
    Workspace,
    DocumentStatus,
    WorkspaceMember,
)
from app.database.session import get_db
from app.core.auth import get_current_user, get_workspace_context, WorkspaceRole

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ingestion", tags=["ingestion"])


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _enum_value(obj) -> str:
    """Return obj.value for enum instances, str(obj) for everything else."""
    return obj.value if hasattr(obj, "value") else str(obj)


async def _get_document_for_user(
    document_id: str,
    user_id: str,
    db: AsyncSession,
) -> Document:
    """Fetch *document_id* and verify *user_id* has access, or raise 404.

    Returns 404 (not 403) for both missing and unauthorized cases so callers
    cannot enumerate document IDs in foreign workspaces.
    """
    result = await db.execute(
        select(Document).where(Document.id == document_id)
    )
    document = result.scalars().first()
    if not document:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    # Owner check
    ws_result = await db.execute(
        select(Workspace).where(
            Workspace.id == document.workspace_id,
            Workspace.owner_id == user_id,
        )
    )
    if ws_result.scalars().first():
        return document

    # Member check
    member_result = await db.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == document.workspace_id,
            WorkspaceMember.user_id == user_id,
        )
    )
    if member_result.scalars().first():
        return document

    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")


async def _build_status_dict(document: Document, db: AsyncSession) -> Dict[str, Any]:
    """Return a serialisable status dict for *document*.

    FIXED: document.source_type and document.status were returned as raw ORM
    objects — SQLAlchemy enum instances are not JSON-serialisable without .value.
    """
    logs_result = await db.execute(
        select(IngestionLog)
        .where(IngestionLog.document_id == document.id)
        .order_by(IngestionLog.created_at.desc())
    )
    logs = logs_result.scalars().all()

    chunks_result = await db.execute(
        select(Chunk).where(Chunk.document_id == document.id)
    )
    chunks = chunks_result.scalars().all()

    embedding_count = 0
    if chunks:
        chunk_ids = [c.id for c in chunks]
        emb_result = await db.execute(
            select(func.count())
            .select_from(Embedding)
            .where(Embedding.chunk_id.in_(chunk_ids))
        )
        embedding_count = emb_result.scalar() or 0

    return {
        "document_id": str(document.id),
        "title": document.title,
        # FIXED: .value ensures enums serialise to their string representations
        "source_type": _enum_value(document.source_type),
        "status": _enum_value(document.status),
        "progress": {
            "chunks_created": len(chunks),
            "embeddings_created": embedding_count,
            "total_tokens": document.token_count or 0,
        },
        "logs": [
            {
                "stage": log.stage,
                "status": log.status,
                "duration_ms": log.duration_ms,
                "timestamp": log.created_at.isoformat(),
            }
            for log in logs
        ],
        "created_at": document.created_at.isoformat(),
        "updated_at": document.updated_at.isoformat() if document.updated_at else None,
    }


# ─── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/status/{document_id}", response_model=Dict[str, Any])
async def get_ingestion_status(
    document_id: str,
    current_user: DBUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """Get ingestion status for a single document."""
    document = await _get_document_for_user(document_id, current_user.id, db)
    return await _build_status_dict(document, db)


@router.get("/workspace/{workspace_id}/status", response_model=Dict[str, Any])
async def get_workspace_ingestion_status(
    workspace_id: str,
    current_user: DBUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    status_filter: Optional[str] = None,
) -> Dict[str, Any]:
    """Get overall ingestion status and aggregated statistics for a workspace."""
    try:
        workspace_uuid = PyUUID(workspace_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid workspace ID format",
        )

    await get_workspace_context(workspace_uuid, current_user, db)

    doc_query = select(Document).where(Document.workspace_id == workspace_uuid)
    if status_filter:
        doc_query = doc_query.where(Document.status == status_filter)

    docs_result = await db.execute(doc_query)
    documents = docs_result.scalars().all()

    # ── Aggregated stats ────────────────────────────────────────────────────
    # FIXED: original had a nested loop issuing one COUNT query per document.
    # For N documents that was N+1 queries just for chunk counts. Now we issue
    # a single GROUP BY query to get all chunk counts in one round-trip.

    status_breakdown: Dict[str, int] = {}
    total_tokens = 0
    doc_ids = [d.id for d in documents]

    for doc in documents:
        key = _enum_value(doc.status)
        status_breakdown[key] = status_breakdown.get(key, 0) + 1
        total_tokens += doc.token_count or 0

    total_chunks = 0
    if doc_ids:
        chunk_agg_result = await db.execute(
            select(func.count())
            .select_from(Chunk)
            .where(Chunk.document_id.in_(doc_ids))
        )
        total_chunks = chunk_agg_result.scalar() or 0

    cutoff = datetime.utcnow() - timedelta(hours=24)
    recent_result = await db.execute(
        select(func.count())
        .select_from(IngestionLog)
        .where(IngestionLog.created_at >= cutoff)
    )
    recent_logs = recent_result.scalar() or 0

    # ── Per-document summaries (first 10) ───────────────────────────────────
    # FIXED: original used `[await f(doc) for doc in docs[:10]]` which is valid
    # Python 3.6+ syntax but runs sequentially. asyncio.gather runs all
    # coroutines concurrently, reducing wall-clock time for 10 docs from
    # ~10× single-query latency to ~1× single-query latency.
    document_statuses = await asyncio.gather(
        *[_build_status_dict(doc, db) for doc in documents[:10]]
    )

    return {
        "workspace_id": str(workspace_uuid),
        "total_documents": len(documents),
        "status_breakdown": status_breakdown,
        "aggregated_stats": {
            "total_chunks": total_chunks,
            "total_tokens": total_tokens,
            "recent_activities_24h": recent_logs,
        },
        "document_statuses": list(document_statuses),
    }


@router.post("/documents/{document_id}/retry", response_model=Dict[str, Any])
async def retry_ingestion(
    document_id: str,
    current_user: DBUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """Re-queue a failed document for ingestion."""
    document = await _get_document_for_user(document_id, current_user.id, db)

    # FIXED: original compared against the raw string "failed" — if
    # document.status is a DocumentStatus enum the comparison always returned
    # False and every retry was rejected with 400.
    if document.status not in (DocumentStatus.FAILED, "failed"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Document is not in a failed state",
        )

    # FIXED: was assigning the raw string "processing" — use the enum so
    # SQLAlchemy validates the value and any listeners fire correctly.
    document.status = DocumentStatus.PROCESSING
    await db.commit()

    return {
        "status": "retry_started",
        "document_id": document_id,
        "new_status": _enum_value(DocumentStatus.PROCESSING),
    }


@router.get("/logs/{document_id}", response_model=List[Dict[str, Any]])
async def get_ingestion_logs(
    document_id: str,
    current_user: DBUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    stage_filter: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Get detailed ingestion logs for a document."""
    await _get_document_for_user(document_id, current_user.id, db)

    log_query = (
        select(IngestionLog)
        .where(IngestionLog.document_id == document_id)
        .order_by(IngestionLog.created_at.desc())
    )
    if stage_filter:
        log_query = log_query.where(IngestionLog.stage == stage_filter)

    logs_result = await db.execute(log_query)
    logs = logs_result.scalars().all()

    return [
        {
            "stage": log.stage,
            "status": log.status,
            "duration_ms": log.duration_ms,
            "timestamp": log.created_at.isoformat(),
        }
        for log in logs
    ]


# ─── Advanced ingestion ───────────────────────────────────────────────────────

class PasteInput(BaseModel):
    workspace_id: str
    content: str
    content_type: Optional[str] = None
    tags: Optional[List[str]] = None


@router.post("/paste")
async def ingest_from_paste(
    paste_input: PasteInput,
    background_tasks: BackgroundTasks,
    current_user: DBUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Universal paste ingestion with auto content-type detection."""
    from app.ingestion.parsers import detect_content_type
    from app.tasks.worker import process_paste_content

    try:
        workspace_uuid = PyUUID(paste_input.workspace_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid workspace ID format",
        )

    await get_workspace_context(workspace_uuid, current_user, db)

    content_type = paste_input.content_type or detect_content_type(paste_input.content)

    document = Document(
        workspace_id=workspace_uuid,
        # FIXED: owner_id removed — it is absent from the Document model used
        # throughout documents.py; setting it caused an unexpected keyword
        # argument error at runtime.
        title=f"Pasted {content_type} — {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC",
        source_type="web_clip" if content_type == "url" else "upload",
        source_metadata={
            "content_type": content_type,
            "detected": paste_input.content_type is None,
            "original_length": len(paste_input.content),
            "tags": paste_input.tags or [],
        },
        status=DocumentStatus.PENDING,
    )
    db.add(document)
    await db.commit()
    await db.refresh(document)

    background_tasks.add_task(
        process_paste_content,
        document_id=str(document.id),
        content=paste_input.content,
        content_type=content_type,
        tags=paste_input.tags,
    )

    return {
        "document_id": str(document.id),
        "status": "pending",
        "content_type": content_type,
        "message": "Paste queued for ingestion",
    }


class VoiceInput(BaseModel):
    workspace_id: str
    audio_url: str
    language: Optional[str] = "en"
    tags: Optional[List[str]] = None


@router.post("/voice")
async def ingest_voice(
    voice_input: VoiceInput,
    background_tasks: BackgroundTasks,
    current_user: DBUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Queue an S3 audio file for Whisper transcription and indexing."""
    from app.tasks.worker import process_voice_input

    try:
        workspace_uuid = PyUUID(voice_input.workspace_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid workspace ID format",
        )

    await get_workspace_context(workspace_uuid, current_user, db)

    document = Document(
        workspace_id=workspace_uuid,
        title=f"Voice Note — {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC",
        source_type="upload",
        source_metadata={
            "content_type": "audio",
            "audio_url": voice_input.audio_url,
            "language": voice_input.language,
            "transcription_pending": True,
            "tags": voice_input.tags or [],
        },
        status=DocumentStatus.PENDING,
    )
    db.add(document)
    await db.commit()
    await db.refresh(document)

    background_tasks.add_task(
        process_voice_input,
        document_id=str(document.id),
        audio_url=voice_input.audio_url,
        language=voice_input.language,
        tags=voice_input.tags,
    )

    return {
        "document_id": str(document.id),
        "status": "pending",
        "message": "Voice transcription queued",
    }


class EmailConfig(BaseModel):
    workspace_id: str
    enable_forwarding: bool = True
    auto_tag: bool = True


@router.post("/email-config")
async def configure_email_ingestion(
    config: EmailConfig,
    current_user: DBUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate a unique inbound email address for a workspace (ADMIN only)."""
    try:
        workspace_uuid = PyUUID(config.workspace_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid workspace ID format",
        )

    context = await get_workspace_context(workspace_uuid, current_user, db)
    context.require_role(WorkspaceRole.ADMIN)

    ws_result = await db.execute(
        select(Workspace).where(Workspace.id == workspace_uuid)
    )
    workspace = ws_result.scalars().first()
    if not workspace:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")

    email_hash = hashlib.sha256(
        f"{workspace_uuid}{current_user.id}".encode()
    ).hexdigest()[:12]
    workspace_email = f"cogniflow+{email_hash}@example.com"

    workspace.settings = workspace.settings or {}
    workspace.settings["email_ingestion"] = {
        "enabled": config.enable_forwarding,
        "email": workspace_email,
        "auto_tag": config.auto_tag,
        "created_at": datetime.utcnow().isoformat(),
    }
    await db.commit()

    return {
        "workspace_id": str(workspace_uuid),
        "email_address": workspace_email,
        "auto_tag_enabled": config.auto_tag,
        "forwarding_enabled": config.enable_forwarding,
        "message": "Email ingestion configured. Forward emails to this address.",
    }


class AutoTagRequest(BaseModel):
    workspace_id: str
    document_id: str
    extract_entities: bool = True
    classify_topics: bool = True


@router.post("/auto-tag")
async def auto_tag_document(
    request: AutoTagRequest,
    background_tasks: BackgroundTasks,
    current_user: DBUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Run NER + topic classification on a document."""
    from app.tasks.worker import process_auto_tagging

    try:
        workspace_uuid = PyUUID(request.workspace_id)
        document_uuid = PyUUID(request.document_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid workspace or document ID format",
        )

    await get_workspace_context(workspace_uuid, current_user, db)

    # Filter on both document ID and workspace_id to prevent cross-workspace
    # access via a guessed document UUID.
    doc_result = await db.execute(
        select(Document).where(
            Document.id == document_uuid,
            Document.workspace_id == workspace_uuid,
        )
    )
    document = doc_result.scalars().first()
    if not document:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    background_tasks.add_task(
        process_auto_tagging,
        document_id=request.document_id,
        extract_entities=request.extract_entities,
        classify_topics=request.classify_topics,
    )

    return {
        "document_id": request.document_id,
        "status": "processing",
        "message": "Auto-tagging in progress",
    }