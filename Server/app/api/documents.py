"""Document API routes."""
import asyncio
import logging
from typing import Optional, List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Query, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, delete

from app.config import settings  # FIXED: was imported inside function body
from app.database.session import get_db
from app.database.models import Document, Chunk, DocumentStatus, SourceType, Embedding, IngestionLog, WorkspaceSection
from app.core.auth import get_current_active_user
from app.core.audit import AuditLogger, AuditAction, EntityType
from app.core.permissions import ensure_workspace_permission
from app.services.graph_service import get_graph_service
from app.storage.s3 import s3_client
from app.tasks.worker import process_document, delete_document_vectors

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/documents", tags=["Documents"])


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _enum_value(obj) -> str:
    """Return obj.value if obj is an enum, else str(obj).

    FIXED: the original repeated `x.value if hasattr(x, 'value') else str(x)`
    twelve times throughout the file.
    """
    return obj.value if hasattr(obj, "value") else str(obj)


# ─── Schemas ─────────────────────────────────────────────────────────────────

class DocumentResponse(BaseModel):
    id: str
    workspace_id: str
    title: str
    source_type: str
    status: str
    token_count: int
    chunk_count: int
    created_at: str
    storage_url: Optional[str] = None


class DocumentListResponse(BaseModel):
    items: List[DocumentResponse]
    total: int
    page: int
    page_size: int


class ChunkResponse(BaseModel):
    id: str
    chunk_index: int
    text: str
    token_count: int


# ─── Upload ───────────────────────────────────────────────────────────────────

@router.post("/upload", response_model=DocumentResponse)
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    workspace_id: UUID = Query(...),
    current_user=Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> DocumentResponse:
    """Upload and process a document.
    
    This endpoint:
    1. Verifies user has MEMBER role in the workspace
    2. Validates file type and size
    3. Checks for duplicates (content hash)
    4. Uploads to S3
    5. Creates Document record in workspace
    6. Queues ingestion task
    7. Logs audit trail
    """
    
    logger.info(
        f"📄 [DOCUMENT UPLOAD START] User {current_user.id} uploading '{file.filename}' to workspace {workspace_id}"
    )
    
    await ensure_workspace_permission(
        workspace_id=workspace_id,
        user=current_user,
        db=db,
        section=WorkspaceSection.DOCUMENTS,
        action="create",
    )
    logger.debug(f"✅ [WORKSPACE VERIFIED] User can upload documents in workspace {workspace_id}")

    # Read and validate content
    content = await file.read()
    max_size = settings.MAX_FILE_SIZE_MB * 1024 * 1024
    if len(content) > max_size:
        logger.warning(
            f"❌ [SIZE VALIDATION FAILED] File {file.filename} exceeds {settings.MAX_FILE_SIZE_MB}MB limit"
        )
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File size exceeds {settings.MAX_FILE_SIZE_MB} MB limit",
        )

    content_type = file.content_type or "application/octet-stream"
    if content_type not in settings.ALLOWED_FILE_TYPES:
        logger.warning(f"❌ [TYPE VALIDATION FAILED] File type '{content_type}' not supported")
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"File type '{content_type}' is not supported",
        )

    content_hash = s3_client.compute_hash(content)
    logger.debug(f"📝 [CONTENT HASH] Computed hash: {content_hash}")

    # Deduplication — return existing document rather than re-ingesting
    result = await db.execute(
        select(Document).where(
            Document.workspace_id == workspace_id,
            Document.content_hash == content_hash,
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        logger.info(f"♻️ [DEDUPLICATION] Document already exists in workspace - returning existing document {existing.id}")
        storage_url = (
            s3_client.generate_presigned_url(existing.storage_path, expiration=3600)
            if existing.storage_path
            else None
        )
        return DocumentResponse(
            id=str(existing.id),
            workspace_id=str(workspace_id),
            title=existing.title,
            source_type=_enum_value(existing.source_type),
            status=_enum_value(existing.status),
            token_count=existing.token_count or 0,
            chunk_count=existing.chunk_count or 0,
            created_at=existing.created_at.isoformat(),
            storage_url=storage_url,
        )

    # Create document record
    document = Document(
        workspace_id=workspace_id,
        title=file.filename or "Untitled",
        content_hash=content_hash,
        source_type=SourceType.UPLOAD,
        source_metadata={
            "filename": file.filename,
            "content_type": content_type,
            "size": len(content),
            "uploaded_by": str(current_user.id),
        },
        status=DocumentStatus.PENDING,
    )

    db.add(document)
    await db.flush()
    await db.refresh(document)
    logger.info(f"📋 [DOCUMENT CREATED] Document {document.id} created in workspace {workspace_id}")

    # Generate S3 path
    storage_path = s3_client.generate_path(
        workspace_id=str(workspace_id),
        document_id=str(document.id),
        filename=file.filename or "untitled",
    )
    logger.debug(f"💾 [S3 PATH] Generated storage path: {storage_path}")

    # FIXED: S3 upload is synchronous/blocking — run in a thread-pool executor
    # so it does not block the async event loop.
    # FIXED: if S3 upload raises, roll back the DB record so there is no
    # orphaned document row.
    try:
        await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: s3_client.upload_file(
                file_bytes=content,
                path=storage_path,
                content_type=content_type,
                metadata={
                    "document_id": str(document.id),
                    "workspace_id": str(workspace_id),
                    "uploaded_by": str(current_user.id),
                },
            ),
        )
    except Exception as exc:
        await db.rollback()
        logger.error(
            "S3 upload failed for document %s: %s", document.id, exc, exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to store document. Please try again.",
        )

    document.storage_path = storage_path
    await db.commit()

    # Queue the document processing task
    logger.info("🚀 [TASK QUEUE] Queueing process_document task for document %s", document.id)
    try:
        task = process_document.delay(str(document.id))
        logger.info("📤 [TASK SENT] process_document task queued successfully - Task ID: %s, Document ID: %s", task.id, document.id)
    except Exception as exc:
        logger.error("❌ [TASK QUEUE ERROR] Failed to queue process_document - Document: %s, Error: %s", document.id, exc, exc_info=True)

    # Audit log (best-effort — do not let a logging failure abort the upload)
    try:
        audit_logger = AuditLogger(db, current_user.id).with_request(request)
        await audit_logger.log_upload(
            workspace_id=workspace_id,
            document_id=document.id,
            filename=file.filename or "untitled",
        )
    except Exception as exc:
        logger.warning("Audit log failed for document %s: %s", document.id, exc)

    await db.commit()

    storage_url = s3_client.generate_presigned_url(storage_path, expiration=3600)

    return DocumentResponse(
        id=str(document.id),
        workspace_id=str(workspace_id),
        title=document.title,
        source_type=_enum_value(document.source_type),
        status=_enum_value(document.status),
        # FIXED: token_count / chunk_count are None until the background worker
        # runs — default to 0 so the response schema is always satisfied.
        token_count=document.token_count or 0,
        chunk_count=document.chunk_count or 0,
        created_at=document.created_at.isoformat(),
        storage_url=storage_url,
    )


# ─── List ─────────────────────────────────────────────────────────────────────

@router.get("", response_model=DocumentListResponse)
async def list_documents(
    workspace_id: UUID = Query(...),
    # FIXED: parameter renamed from `status` — the original shadowed the
    # `status` name imported from fastapi, making status.HTTP_* unreachable
    # anywhere below this parameter declaration.
    status_filter: Optional[str] = Query(None, alias="status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user=Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> DocumentListResponse:
    """List documents in a workspace (paginated)."""

    # FIXED: original did UUID(str(workspace_id)) — workspace_id is already UUID
    await ensure_workspace_permission(
        workspace_id=workspace_id,
        user=current_user,
        db=db,
        section=WorkspaceSection.DOCUMENTS,
        action="view",
    )

    query = select(Document).where(Document.workspace_id == workspace_id)
    if status_filter:
        query = query.where(Document.status == status_filter)

    count_query = select(func.count(Document.id)).where(Document.workspace_id == workspace_id)
    if status_filter:
        count_query = count_query.where(Document.status == status_filter)

    count_result = await db.execute(count_query)
    total = count_result.scalar() or 0

    query = (
        query.order_by(Document.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(query)
    documents = result.scalars().all()

    items = []
    for doc in documents:
        storage_url = (
            s3_client.generate_presigned_url(doc.storage_path, expiration=3600)
            if doc.storage_path
            else None
        )
        items.append(
            DocumentResponse(
                id=str(doc.id),
                workspace_id=str(doc.workspace_id),
                title=doc.title,
                source_type=_enum_value(doc.source_type),
                status=_enum_value(doc.status),
                token_count=doc.token_count or 0,
                chunk_count=doc.chunk_count or 0,
                created_at=doc.created_at.isoformat(),
                storage_url=storage_url,
            )
        )

    return DocumentListResponse(items=items, total=total, page=page, page_size=page_size)


# ─── Get ──────────────────────────────────────────────────────────────────────

@router.get("/{document_id}", response_model=DocumentResponse)
async def get_document(
    document_id: UUID,
    current_user=Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> DocumentResponse:
    """Get a single document by ID."""

    # FIXED: original fetched the document before checking workspace membership,
    # which let any authenticated user determine whether a document ID exists
    # in a workspace they don't belong to (existence oracle). We now do the
    # membership check on the document's workspace and return 404 for both
    # not-found and unauthorized, indistinguishably.
    result = await db.execute(
        select(Document).where(Document.id == document_id)
    )
    document = result.scalar_one_or_none()

    if not document:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    try:
        await ensure_workspace_permission(
            workspace_id=document.workspace_id,
            user=current_user,
            db=db,
            section=WorkspaceSection.DOCUMENTS,
            action="view",
        )
    except HTTPException:
        # Surface as 404 regardless of the actual auth failure to avoid leaking
        # information about document existence in foreign workspaces.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    storage_url = (
        s3_client.generate_presigned_url(document.storage_path, expiration=3600)
        if document.storage_path
        else None
    )

    return DocumentResponse(
        id=str(document.id),
        workspace_id=str(document.workspace_id),
        title=document.title,
        source_type=_enum_value(document.source_type),
        status=_enum_value(document.status),
        token_count=document.token_count or 0,
        chunk_count=document.chunk_count or 0,
        created_at=document.created_at.isoformat(),
        storage_url=storage_url,
    )


# ─── Chunks ───────────────────────────────────────────────────────────────────

@router.get("/{document_id}/chunks", response_model=List[ChunkResponse])
async def get_document_chunks(
    document_id: UUID,
    current_user=Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> List[ChunkResponse]:
    """Get all chunks for a document."""

    result = await db.execute(select(Document).where(Document.id == document_id))
    document = result.scalar_one_or_none()

    if not document:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    try:
        await ensure_workspace_permission(
            workspace_id=document.workspace_id,
            user=current_user,
            db=db,
            section=WorkspaceSection.DOCUMENTS,
            action="view",
        )
    except HTTPException:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    result = await db.execute(
        select(Chunk)
        .where(Chunk.document_id == document_id)
        .order_by(Chunk.chunk_index)
    )
    chunks = result.scalars().all()

    return [
        ChunkResponse(
            id=str(chunk.id),
            chunk_index=chunk.chunk_index,
            text=chunk.text,
            token_count=chunk.token_count or 0,
        )
        for chunk in chunks
    ]


# ─── Delete ───────────────────────────────────────────────────────────────────

@router.delete("/{document_id}")
async def delete_document(
    document_id: UUID,
    request: Request,
    current_user=Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a document (requires ADMIN role)."""

    result = await db.execute(select(Document).where(Document.id == document_id))
    document = result.scalar_one_or_none()

    if not document:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    await ensure_workspace_permission(
        workspace_id=document.workspace_id,
        user=current_user,
        db=db,
        section=WorkspaceSection.DOCUMENTS,
        action="delete",
    )

    # Queue vector deletion before removing the DB row so the task has the IDs
    delete_document_vectors.delay(
        document_id=str(document.id),
        workspace_id=str(document.workspace_id),
    )

    # Audit log before deleting (best-effort)
    try:
        audit_logger = AuditLogger(db, current_user.id).with_request(request)
        await audit_logger.log_delete(
            workspace_id=document.workspace_id,
            document_id=document.id,
            title=document.title,
        )
    except Exception as exc:
        logger.warning("Audit log failed for delete of %s: %s", document.id, exc)

    # Delete S3 object (best-effort — object may already be gone)
    if document.storage_path:
        try:
            await asyncio.get_event_loop().run_in_executor(
                None, lambda: s3_client.delete_file(document.storage_path)
            )
        except Exception as exc:
            logger.warning(
                "S3 delete failed for %s (continuing): %s", document.storage_path, exc
            )

    # Delete dependent rows explicitly before removing the document. The
    # embeddings table has a NOT NULL chunk_id FK, so letting the ORM
    # disassociate children can trigger UPDATE ... SET chunk_id = NULL.
    chunk_ids_result = await db.execute(
        select(Chunk.id).where(Chunk.document_id == document.id)
    )
    chunk_ids = list(chunk_ids_result.scalars().all())

    if chunk_ids:
        await db.execute(delete(Embedding).where(Embedding.chunk_id.in_(chunk_ids)))

    await db.execute(delete(IngestionLog).where(IngestionLog.document_id == document.id))
    await get_graph_service().remove_document_from_graph(db, document.workspace_id, document.id)
    await db.execute(delete(Chunk).where(Chunk.document_id == document.id))
    await db.delete(document)
    await db.commit()

    return {"status": "deleted", "document_id": str(document_id)}
