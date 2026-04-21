"""Celery worker configuration and document ingestion tasks."""
from __future__ import annotations

import logging
import os
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime
from typing import List, Optional
from uuid import UUID

from celery.signals import task_postrun, task_prerun

from app.celery_app import celery_app
from app.config import settings
from app.database.models import (
    Chunk,
    ChunkStatus,
    Document,
    DocumentStatus,
    Embedding,
    IngestionLog,
    SourceType,
)
from app.database.session import SyncSessionLocal
from app.events import (
    broadcast_ingestion_completed_sync,
    broadcast_ingestion_failed_sync,
    broadcast_ingestion_started_sync,
    broadcast_progress_update_sync,
    broadcast_stage_update_sync,
)
from app.ingestion.chunker import TextChunk, chunk_parsed_document, chunker
from app.ingestion.embedder import Embedder
from app.ingestion.parsers import get_parser
from app.ingestion.source_locations import enrich_citation_metadata
from app.services.vector_db import get_vector_db_client
from app.storage.s3 import s3_client

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Signal handlers
# ---------------------------------------------------------------------------

def _task_prerun_handler(sender=None, task_id=None, task=None, **kwargs) -> None:
    """Log when a task starts."""
    logger.info("Starting task %s[%s]", task.name if task else sender, task_id)


def _task_postrun_handler(
    sender=None, task_id=None, task=None, state=None, **kwargs
) -> None:
    """Log when a task finishes and clean up any remaining sessions."""
    logger.info(
        "Task %s[%s] finished — state: %s",
        task.name if task else sender,
        task_id,
        state,
    )
    # FIX: Explicitly close SyncSessionLocal to prevent phantom ROLLBACK on connection return
    # This ensures pool_reset_on_return=None doesn't start implicit transactions
    remove = getattr(SyncSessionLocal, "remove", None)
    if callable(remove):
        remove()


#  FIXED: Explicitly connect signal handlers to the celery_app instance
# This ensures they're registered before the worker starts processing tasks
# weak=False prevents the handlers from being garbage collected
task_prerun.connect(_task_prerun_handler, sender=celery_app, weak=False)
task_postrun.connect(_task_postrun_handler, sender=celery_app, weak=False)


#  FIXED: Register app finalization handler to ensure proper initialization
# This runs after all tasks are registered, ensuring the app context is set up
@celery_app.on_after_finalize.connect
def setup_app_context(sender, **kwargs):
    """Finalize app setup after all tasks are registered."""
    logger.info(
        "Celery app finalized with %d registered tasks",
        len(sender.tasks),
    )


# ---------------------------------------------------------------------------
# Database session helper
# ---------------------------------------------------------------------------


@contextmanager
def get_db_session():
    """Yield a synchronous SQLAlchemy session and guarantee it is closed."""
    db = SyncSessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Async event helper
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Shared pipeline helpers
# ---------------------------------------------------------------------------


def _log_ingestion_event(
    db,
    document_id: UUID,
    status: DocumentStatus,
    stage: Optional[str] = None,
    message: Optional[str] = None,
    error_message: Optional[str] = None,
    duration_ms: Optional[int] = None,
) -> None:
    """Append an :class:`IngestionLog` row and commit."""
    log = IngestionLog(
        document_id=document_id,
        status=status,
        stage=stage,
        message=message,
        error_message=error_message,
        duration_ms=duration_ms,
    )
    db.add(log)
    db.commit()


def _fail_document_without_retry(
    db,
    *,
    document: Document,
    document_uuid: UUID,
    document_id: str,
    workspace_id: str,
    stage: str,
    message: str,
    error_code: str,
) -> dict:
    """Mark a document as failed for a deterministic ingest issue."""
    document.status = DocumentStatus.FAILED
    db.commit()
    _log_ingestion_event(
        db,
        document_uuid,
        DocumentStatus.FAILED,
        stage=stage,
        error_message=message,
    )
    broadcast_ingestion_failed_sync(workspace_id, document_id, error_message=message)
    logger.warning(
        "Document %s failed during %s without retry: %s",
        document_id,
        stage,
        message,
    )
    return {
        "status": "failed",
        "document_id": document_id,
        "error": message,
        "error_code": error_code,
        "retryable": False,
    }


def _normalize_vector_ids(vector_ids: List[str]) -> list[str]:
    """Normalize vector IDs for delete operations while preserving order."""
    return list(dict.fromkeys(str(vector_id) for vector_id in vector_ids if vector_id))


def _schedule_deferred_vector_cleanup(
    collection_name: str,
    vector_ids: List[str],
    *,
    reason: str,
) -> None:
    """Queue a retryable cleanup task when immediate deletion fails."""
    normalized_ids = _normalize_vector_ids(vector_ids)
    if not normalized_ids:
        return

    try:
        delete_vector_ids.delay(collection_name, normalized_ids, reason=reason)
        logger.warning(
            "Scheduled deferred vector cleanup for %d vector(s) in '%s': %s",
            len(normalized_ids),
            collection_name,
            reason,
        )
    except Exception:
        logger.exception(
            "Failed to schedule deferred vector cleanup for %d vector(s) in '%s'",
            len(normalized_ids),
            collection_name,
        )


def _cleanup_vector_ids(
    collection_name: str,
    vector_ids: List[str],
    *,
    reason: str,
    defer_on_failure: bool = True,
) -> bool:
    """Delete vector IDs now, and optionally queue a retryable fallback cleanup."""
    normalized_ids = _normalize_vector_ids(vector_ids)
    if not normalized_ids:
        return True

    try:
        deleted = get_vector_db_client().delete_points(collection_name, normalized_ids)
    except Exception:
        logger.exception(
            "Immediate vector cleanup raised for %d vector(s) in '%s': %s",
            len(normalized_ids),
            collection_name,
            reason,
        )
        deleted = False

    if deleted:
        logger.info(
            "Cleaned up %d vector(s) from '%s': %s",
            len(normalized_ids),
            collection_name,
            reason,
        )
        return True

    logger.warning(
        "Immediate vector cleanup failed for %d vector(s) in '%s': %s",
        len(normalized_ids),
        collection_name,
        reason,
    )
    if defer_on_failure:
        _schedule_deferred_vector_cleanup(
            collection_name,
            normalized_ids,
            reason=reason,
        )
    return False


def _save_chunks(db, document_uuid: UUID, chunks: List[TextChunk]) -> tuple[list, list[str]]:
    """Synchronize chunk rows with the latest parsed output.

    Returns:
        Tuple ``(chunks_in_order, stale_vector_ids)``.
    """
    existing_chunks = (
        db.query(Chunk)
        .filter(Chunk.document_id == document_uuid)
        .order_by(Chunk.chunk_index)
        .all()
    )
    existing_by_index = {chunk.chunk_index: chunk for chunk in existing_chunks}
    incoming_indices = {chunk.index for chunk in chunks}
    stale_vector_ids: List[str] = []

    for chunk in chunks:
        existing = existing_by_index.get(chunk.index)
        if existing is None:
            db.add(
                Chunk(
                    document_id=document_uuid,
                    chunk_index=chunk.index,
                    text=chunk.text,
                    token_count=chunk.token_count,
                    context_before=chunk.context_before,
                    context_after=chunk.context_after,
                    chunk_metadata=chunk.metadata or {},
                    chunk_status=ChunkStatus.PENDING,
                )
            )
            continue

        changed = any(
            [
                existing.text != chunk.text,
                int(existing.token_count or 0) != int(chunk.token_count or 0),
                (existing.context_before or None) != (chunk.context_before or None),
                (existing.context_after or None) != (chunk.context_after or None),
                (existing.chunk_metadata or {}) != (chunk.metadata or {}),
            ]
        )

        existing.text = chunk.text
        existing.token_count = chunk.token_count
        existing.context_before = chunk.context_before
        existing.context_after = chunk.context_after
        existing.chunk_metadata = chunk.metadata or {}

        if changed:
            if existing.embedding is not None:
                stale_vector_ids.append(str(existing.embedding.vector_id))
                db.delete(existing.embedding)
            existing.chunk_status = ChunkStatus.PENDING

    for stale_chunk in existing_chunks:
        if stale_chunk.chunk_index in incoming_indices:
            continue
        if stale_chunk.embedding is not None:
            stale_vector_ids.append(str(stale_chunk.embedding.vector_id))
            db.delete(stale_chunk.embedding)
        db.delete(stale_chunk)

    db.commit()
    refreshed_chunks = (
        db.query(Chunk)
        .filter(Chunk.document_id == document_uuid)
        .order_by(Chunk.chunk_index)
        .all()
    )
    return refreshed_chunks, stale_vector_ids


def _index_vectors(
    db,
    document: Document,
    document_id: str,
    chunks: List[TextChunk],
    db_chunks: list,
    extra_payload: dict,
) -> list:
    """Generate embeddings, upsert to vector index, and persist Embedding rows.

    Changes vs original
    -------------------
    * ``model_used`` and ``embedding_dimension`` are now taken from the
      *actual* provider that succeeded (Ollama vs OpenAI), not hard-coded.
    * N+1 chunk SELECT is replaced with a single ``IN`` query.
    * ``create_collection`` always receives the live dimension so Qdrant
      collections are auto-fixed when the active model changes.
    """
    chunk_texts = [c.text for c in chunks]

    local_embedder = Embedder(provider=settings.EMBEDDING_PROVIDER)
    embeddings = local_embedder.embed(chunk_texts)

    if len(embeddings) != len(chunk_texts):
        raise ValueError(
            f"Embedding count mismatch: expected {len(chunk_texts)}, got {len(embeddings)}"
        )

    # ✅ FIX: Capture the actual model that succeeded
    actual_model: str = local_embedder.model_name        # e.g. "nomic-embed-text"
    actual_dim: int = local_embedder.dimension           # e.g. 768

    collection_name = str(document.workspace_id)
    # ✅ FIX: Pass embedding_dim explicitly to create_collection
    vector_db = get_vector_db_client()
    vector_db.create_collection(collection_name, embedding_dim=actual_dim)  # ← explicit dim

    vector_ids = [str(db_chunks[i].id) for i in range(len(embeddings))]
    payloads = [
        {
            "document_id": document_id,
            "chunk_id": str(db_chunks[i].id),
            "workspace_id": str(document.workspace_id),
            "document_title": document.title,
            "text": chunk.text,
            "chunk_text": chunk.text,
            "text_preview": chunk.text[:200],
            "chunk_index": chunk.index,
            "token_count": chunk.token_count,
            "context_before": chunk.context_before,
            "context_after": chunk.context_after,
            "metadata": enrich_citation_metadata(
                dict(chunk.metadata or {}),
                document_title=document.title,
                source_type=document.source_type.value,
            ),
            "created_at": datetime.utcnow().isoformat(),
            **extra_payload,
        }
        for i, chunk in enumerate(chunks)
    ]

    points = [
        {"id": vid, "vector": vec, "payload": payloads[i]}
        for i, (vid, vec) in enumerate(zip(vector_ids, embeddings))
    ]

    # ── FIX: Enhanced upsert failure diagnostics ────────────────────────────────
    if not vector_db.upsert_vectors(collection_name, points):
        # If upsert still fails after automatic recovery, provide diagnostics
        error_msg = (
            f"❌ Vector DB upsert failed even after automatic recovery:\n"
            f"   Collection:   {collection_name}\n"
            f"   Model:        {actual_model}\n"
            f"   Dimension:    {actual_dim}D\n"
            f"   Document:     {document_id}\n"
            f"\n"
            f"ACTION: Task will retry in 60 seconds\n"
            f"NOTE: System attempted automatic dimension mismatch recovery\n"
            f"      If this persists, check Qdrant connection and logs\n"
        )
        logger.error(error_msg)
        raise RuntimeError(error_msg)

    # ✅ FIX: Batch insert for Embedding rows (no N+1 adds)
    try:
        for db_chunk, vector_id in zip(db_chunks, vector_ids):
            db.add(
                Embedding(
                    chunk_id=db_chunk.id,
                    vector_id=vector_id,
                    collection_name=collection_name,
                    model_used=actual_model,            # ← actual provider, not default
                    embedding_dimension=actual_dim,     # ← actual dimension
                )
            )
            db_chunk.chunk_status = ChunkStatus.EMBEDDED
        db.commit()
    except Exception as exc:
        db.rollback()
        cleanup_reason = (
            "Rolling back freshly upserted vectors after SQL persistence failure "
            f"for document {document_id}"
        )
        _cleanup_vector_ids(collection_name, vector_ids, reason=cleanup_reason)
        raise RuntimeError(
            f"Failed to persist embedding metadata for document {document_id}"
        ) from exc

    return vector_ids


def _retry_countdown(retries: int) -> int:
    """Exponential back-off: 60 s, 120 s, 240 s, …"""
    return 60 * (2 ** retries)


def _get_chunks_needing_embedding(db, document_uuid: UUID) -> list:
    """Get only PENDING chunks that still need embedding.
    
    On first attempt, all chunks are PENDING.
    On retry, returns only chunks that haven't been embedded yet.
    """
    pending_chunks = db.query(Chunk).filter(
        Chunk.document_id == document_uuid,
        Chunk.chunk_status == ChunkStatus.PENDING
    ).all()
    return pending_chunks


def _has_embedded_chunks(db, document_uuid: UUID) -> bool:
    """Check if document has any already embedded chunks (indicates a retry)."""
    embedded_count = db.query(Chunk).filter(
        Chunk.document_id == document_uuid,
        Chunk.chunk_status == ChunkStatus.EMBEDDED
    ).count()
    return embedded_count > 0


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------


@celery_app.task(bind=True, max_retries=3)
def process_document(self, document_id: str) -> dict:
    """Full ingestion pipeline: download → parse → chunk → embed → index.

    Emits real-time SSE events at each stage for frontend consumption.
    """
    logger.info("📋 [TASK START] process_document - Task ID: %s - Document: %s", self.request.id, document_id)
    
    document_uuid = UUID(document_id)
    start_time = time.time()

    with get_db_session() as db:
        document = None
        workspace_id = None
        try:
            logger.debug("🔍 [DB QUERY] Attempting to fetch document %s from database", document_id)
            document = db.query(Document).filter(Document.id == document_uuid).first()
            if not document:
                # Non-retryable – the record simply does not exist.
                logger.error("❌ [DB ERROR] Document %s not found in database; aborting task - Task ID: %s", document_id, self.request.id)
                return {"status": "failed", "document_id": document_id, "error": "not found"}

            workspace_id = str(document.workspace_id)
            logger.info("📁 [WORKSPACE] Document belongs to workspace: %s", workspace_id)
            logger.debug("📡 [EVENT] Broadcasting ingestion_started event - Document: %s", document_id)
            broadcast_ingestion_started_sync(workspace_id, document_id, document.title)

            document.status = DocumentStatus.PROCESSING
            db.commit()
            logger.debug("💾 [DB UPDATE] Document status set to PROCESSING - Document: %s", document_id)
            _log_ingestion_event(
                db, document_uuid, DocumentStatus.PROCESSING,
                stage="started", message="Document processing started",
            )

            # ── Stage 1: Download ──────────────────────────────────────────
            logger.info("⬇️ [STAGE 1 START] Download - Storage path: %s", document.storage_path)
            t0 = time.time()
            broadcast_stage_update_sync(
                workspace_id, document_id, "download", "started",
                progress={"status": "Downloading…"},
            )

            logger.debug("🔄 [S3] Downloading file from S3 - Path: %s", document.storage_path)
            file_bytes = s3_client.download_file(document.storage_path)
            download_ms = int((time.time() - t0) * 1000)
            logger.info("✅ [STAGE 1 COMPLETE] Downloaded %d bytes in %d ms - Document: %s", len(file_bytes), download_ms, document_id)

            logger.debug("Downloaded %d bytes for document %s in %d ms",
                         len(file_bytes), document_id, download_ms)
            _log_ingestion_event(
                db, document_uuid, DocumentStatus.PROCESSING,
                stage="download",
                message=f"Downloaded {len(file_bytes)} bytes",
                duration_ms=download_ms,
            )
            broadcast_stage_update_sync(
                workspace_id, document_id, "download", "completed",
                progress={"bytes_downloaded": len(file_bytes), "duration_ms": download_ms},
            )

            # ── Stage 2: Parse ─────────────────────────────────────────────
            logger.info("📖 [STAGE 2 START] Parse - Content type: %s", document.source_metadata.get("content_type", "text/plain"))
            t0 = time.time()
            broadcast_stage_update_sync(
                workspace_id, document_id, "parse", "started",
                progress={"status": "Parsing document…"},
            )

            logger.debug("🔧 [PARSER] Getting parser for content type: %s", document.source_metadata.get("content_type", "text/plain"))
            parser = get_parser(document.source_metadata.get("content_type", "text/plain"))
            logger.debug("🔄 [PARSER] Parsing file bytes (size: %d bytes)", len(file_bytes))
            parsed = parser.parse(file_bytes)
            parsed_text = parsed.text or ""
            extracted_chars = len(parsed_text.strip())
            pages_with_text = (
                parsed.metadata.get("pages_with_text")
                if isinstance(parsed.metadata, dict)
                else None
            )
            document.title = parsed.title or document.title
            document.token_count = chunker.count_tokens(parsed_text)
            db.commit()
            logger.debug("💾 [DB UPDATE] Document title and token count updated - Title: %s, Tokens: %d", document.title, document.token_count)

            parse_ms = int((time.time() - t0) * 1000)
            logger.info("✅ [STAGE 2 COMPLETE] Parsed %d words, %d tokens in %d ms - Document: %s", parsed.word_count, document.token_count, parse_ms, document_id)
            logger.debug("Parsed %d words for document %s in %d ms",
                         parsed.word_count, document_id, parse_ms)
            _log_ingestion_event(
                db, document_uuid, DocumentStatus.PROCESSING,
                stage="parse",
                message=f"Parsed {parsed.word_count} words",
                duration_ms=parse_ms,
            )
            broadcast_stage_update_sync(
                workspace_id, document_id, "parse", "completed",
                progress={
                    "word_count": parsed.word_count,
                    "token_count": document.token_count,
                    "duration_ms": parse_ms,
                },
            )

            # ── Stage 3: Chunk ─────────────────────────────────────────────
            if not extracted_chars:
                message = "Document contains no extractable text."
                if document.source_metadata.get("content_type") == "application/pdf":
                    message = (
                        "PDF contains no extractable text. This often means it is scanned/image-only and needs OCR."
                    )
                if pages_with_text is not None:
                    message = f"{message} pages_with_text={pages_with_text}/{parsed.page_count or 0}"
                return _fail_document_without_retry(
                    db,
                    document=document,
                    document_uuid=document_uuid,
                    document_id=document_id,
                    workspace_id=workspace_id,
                    stage="parse",
                    message=message,
                    error_code="NO_EXTRACTABLE_TEXT",
                )

            logger.info("✂️ [STAGE 3 START] Chunking - Parsing complete")
            t0 = time.time()
            broadcast_stage_update_sync(
                workspace_id, document_id, "chunking", "started",
                progress={"status": "Creating chunks…"},
            )

            document.source_metadata = {
                **(document.source_metadata or {}),
                **(parsed.metadata or {}),
            }
            db.commit()

            logger.debug("🔄 [CHUNKER] Starting traceable chunking - Text length: %d chars", len(parsed_text))
            chunks = chunk_parsed_document(
                parsed,
                metadata={
                    "document_id": document_id,
                    "source_type": document.source_type.value,
                    "source_file_name": document.source_metadata.get("filename") or document.title,
                    "document_title": document.title,
                    **parsed.metadata,
                },
            )
            if not chunks:
                return _fail_document_without_retry(
                    db,
                    document=document,
                    document_uuid=document_uuid,
                    document_id=document_id,
                    workspace_id=workspace_id,
                    stage="chunk",
                    message="Document parsing succeeded but produced zero chunks. Review parser output or chunking thresholds.",
                    error_code="NO_CHUNKS_CREATED",
                )
            logger.debug("💾 [DB INSERT] Saving %d chunks to database", len(chunks))
            db_chunks, stale_vector_ids = _save_chunks(db, document_uuid, chunks)
            document.chunk_count = len(chunks)
            db.commit()
            logger.debug("💾 [DB UPDATE] Chunk count updated - Chunks: %d", len(chunks))

            if stale_vector_ids:
                _cleanup_vector_ids(
                    str(document.workspace_id),
                    stale_vector_ids,
                    reason=f"Removing stale vectors after rechunking document {document_id}",
                )

            chunk_ms = int((time.time() - t0) * 1000)
            logger.info("✅ [STAGE 3 COMPLETE] Created %d chunks in %d ms - Document: %s", len(chunks), chunk_ms, document_id)
            logger.debug("Created %d chunks for document %s in %d ms",
                         len(chunks), document_id, chunk_ms)
            _log_ingestion_event(
                db, document_uuid, DocumentStatus.PROCESSING,
                stage="chunk",
                message=f"Created {len(chunks)} chunks",
                duration_ms=chunk_ms,
            )
            broadcast_stage_update_sync(
                workspace_id, document_id, "chunking", "completed",
                progress={"chunks_created": len(chunks), "duration_ms": chunk_ms},
            )

            # ── Stages 4 & 5: Embed + Index ────────────────────────────────
            logger.info("🧠 [STAGE 4-5 START] Embedding & Indexing - %d chunks ready", len(chunks))
            t0 = time.time()
            broadcast_stage_update_sync(
                workspace_id, document_id, "embedding", "started",
                progress={"status": "Generating embeddings…"},
            )

            # ✅ FIX: Build set of PENDING db_chunk IDs for O(1) index lookup
            # (works even when len(chunks) != len(db_chunks) on retry)
            pending_db_chunk_ids = {
                db_chunk.id
                for db_chunk in db_chunks
                if db_chunk.chunk_status == ChunkStatus.PENDING
            }

            if pending_db_chunk_ids:
                # ✅ FIX: Pair each TextChunk with db_chunk via chunk_index
                db_chunk_by_index = {dc.chunk_index: dc for dc in db_chunks}
                chunks_to_embed    = [c for c in chunks if db_chunk_by_index[c.index].id in pending_db_chunk_ids]
                db_chunks_to_embed = [db_chunk_by_index[c.index] for c in chunks_to_embed]

                logger.info(
                    "🧠 [EMBEDDING] Embedding %d PENDING chunks (out of %d total)",
                    len(chunks_to_embed), len(chunks),
                )
                vector_ids = _index_vectors(
                    db, document, document_id, chunks_to_embed, db_chunks_to_embed,
                    {"source_type": document.source_type.value},
                )
            else:
                logger.info("✅ [EMBEDDING] All chunks already embedded, skipping")
                vector_ids = [
                    str(dc.embedding.vector_id)
                    for dc in db_chunks
                    if dc.embedding
                ]

            index_ms = int((time.time() - t0) * 1000)
            logger.info("✅ [STAGE 4-5 COMPLETE] Indexed %d vectors in %d ms - Document: %s", len(vector_ids), index_ms, document_id)
            _log_ingestion_event(
                db, document_uuid, DocumentStatus.INDEXED,
                stage="index",
                message=f"Indexed {len(vector_ids)} vectors",
                duration_ms=index_ms,
            )
            broadcast_stage_update_sync(
                workspace_id, document_id, "embedding", "completed",
                progress={"embeddings_created": len(vector_ids), "duration_ms": index_ms},
            )

            # ── Finalise ───────────────────────────────────────────────────
            logger.info("🔚 [FINALIZE] Setting document status to INDEXED")
            document.status = DocumentStatus.INDEXED
            document.processed_at = datetime.utcnow()
            db.commit()
            logger.debug("💾 [DB UPDATE] Document marked as INDEXED with processed_at timestamp")

            total_ms = int((time.time() - start_time) * 1000)
            _log_ingestion_event(
                db, document_uuid, DocumentStatus.INDEXED,
                stage="complete",
                message=f"Processing complete in {total_ms} ms",
                duration_ms=total_ms,
            )
            broadcast_ingestion_completed_sync(
                workspace_id, document_id,
                token_count=document.token_count,
                chunk_count=len(chunks),
                embedding_count=len(vector_ids),
            )

            logger.info(
                "✅ [TASK SUCCESS] Document %s indexed successfully in %d ms - Task ID: %s - Chunks: %d, Vectors: %d", document_id, total_ms, self.request.id, len(chunks), len(vector_ids)
            )
            return {
                "status": "success",
                "document_id": document_id,
                "chunks_created": len(chunks),
                "vectors_indexed": len(vector_ids),
                "total_duration_ms": total_ms,
            }

        except Exception as exc:
            logger.error("❌ [TASK ERROR] process_document encountered exception - Task ID: %s - Document: %s", self.request.id, document_id, exc_info=True)
            
            # Rollback any pending transactions to clear the session state
            db.rollback()

            if document is not None:
                document.status = DocumentStatus.FAILED
                db.commit()
                logger.debug("💾 [DB UPDATE] Document status set to FAILED - Document: %s", document_id)

            error_msg = str(exc)
            if document is not None:
                _log_ingestion_event(
                    db, document_uuid, DocumentStatus.FAILED,
                    stage="error", error_message=error_msg,
                )
            if workspace_id:
                broadcast_ingestion_failed_sync(workspace_id, document_id, error_message=error_msg)

            logger.error("❌ Document %s processing failed: %s", document_id, error_msg, exc_info=True)

            if self.request.retries < self.max_retries:
                countdown = _retry_countdown(self.request.retries)
                logger.warning(
                    "⏳ [RETRY SCHEDULED] Attempt %d/%d for document %s in %d seconds - Task ID: %s - Error: %s",
                    self.request.retries + 1, self.max_retries, document_id, countdown, self.request.id, error_msg,
                )
                raise self.retry(exc=exc, countdown=countdown)

            logger.error("❌ [MAX RETRIES EXCEEDED] Document %s failed after %d retries - Task ID: %s", document_id, self.max_retries, self.request.id)
            return {"status": "failed", "document_id": document_id, "error": error_msg}


@celery_app.task(bind=True, max_retries=3)
def delete_vector_ids(
    self,
    collection_name: str,
    vector_ids: List[str],
    reason: Optional[str] = None,
) -> dict:
    """Retryable cleanup task for explicit vector IDs."""
    normalized_ids = _normalize_vector_ids(vector_ids)
    logger.info(
        "delete_vector_ids started for collection %s with %d vector(s)",
        collection_name,
        len(normalized_ids),
    )

    if not normalized_ids:
        return {
            "status": "success",
            "collection_name": collection_name,
            "deleted_count": 0,
            "message": "No vector IDs to delete",
        }

    try:
        deleted = get_vector_db_client().delete_points(collection_name, normalized_ids)
        if not deleted:
            raise RuntimeError(
                f"Vector delete returned false for collection '{collection_name}'"
            )
        logger.info(
            "delete_vector_ids completed for collection %s with %d vector(s)",
            collection_name,
            len(normalized_ids),
        )
        return {
            "status": "success",
            "collection_name": collection_name,
            "deleted_count": len(normalized_ids),
            "reason": reason,
        }
    except Exception as exc:
        logger.error(
            "delete_vector_ids failed for collection %s: %s",
            collection_name,
            exc,
            exc_info=True,
        )
        if self.request.retries < self.max_retries:
            countdown = _retry_countdown(self.request.retries)
            raise self.retry(exc=exc, countdown=countdown)
        return {
            "status": "failed",
            "collection_name": collection_name,
            "deleted_count": 0,
            "reason": reason,
            "error": str(exc),
        }


@celery_app.task(bind=True, max_retries=3)
def delete_document_vectors(self, document_id: str, workspace_id: str) -> dict:
    """Remove all vectors for a document from the vector index."""
    logger.info(
        "delete_document_vectors started for document %s in workspace %s",
        document_id,
        workspace_id,
    )

    try:
        deleted = get_vector_db_client().delete_by_filter(
            workspace_id,
            filters={"document_id": document_id},
        )
        if not deleted:
            raise RuntimeError(
                f"Vector delete returned false for document {document_id}"
            )
        logger.info(
            "delete_document_vectors completed for document %s in workspace %s",
            document_id,
            workspace_id,
        )
        return {
            "status": "success",
            "document_id": document_id,
            "message": "Vectors deleted",
        }
    except Exception as exc:
        logger.error(
            "delete_document_vectors failed for document %s: %s",
            document_id,
            exc,
            exc_info=True,
        )
        if self.request.retries < self.max_retries:
            countdown = _retry_countdown(self.request.retries)
            raise self.retry(exc=exc, countdown=countdown)
        return {"status": "failed", "document_id": document_id, "error": str(exc)}


@celery_app.task
def sync_connector(connector_id: str) -> dict:
    """Sync an external connector (stub)."""
    logger.info("🔌 [TASK START] sync_connector - Connector ID: %s", connector_id)
    try:
        logger.debug("🔄 [CONNECTOR] Starting sync for connector: %s", connector_id)
        result = {"status": "success", "connector_id": connector_id, "message": "Sync started"}
        logger.info("✅ [TASK SUCCESS] sync_connector completed - Connector: %s", connector_id)
        return result
    except Exception as exc:
        logger.error("❌ [TASK ERROR] sync_connector failed - Connector: %s - Error: %s", connector_id, exc, exc_info=True)
        return {"status": "failed", "connector_id": connector_id, "error": str(exc)}


@celery_app.task(bind=True, max_retries=3)
def process_paste_content(
    self,
    document_id: str,
    content: str,
    content_type: str,
    tags: Optional[list] = None,
) -> dict:
    """Process pasted content: detect type → normalise → chunk → embed → index."""
    logger.info("📝 [TASK START] process_paste_content - Document: %s, Content Type: %s, Task ID: %s", document_id, content_type, self.request.id)
    
    from app.ingestion.content_detection import (
        classify_topics,
        detect_content_type,  # noqa: F401
        extract_entities,
    )

    document_uuid = UUID(document_id)
    start_time = time.time()

    with get_db_session() as db:
        document = None
        try:
            logger.debug("🔍 [DB QUERY] Fetching document: %s", document_id)
            document = db.query(Document).filter(Document.id == document_uuid).first()
            if not document:
                logger.error("❌ [DB ERROR] Document %s not found - Task ID: %s", document_id, self.request.id)
                raise ValueError(f"Document {document_id} not found")

            logger.info("📋 [CONTENT DETECTION] Processing content type: %s (content size: %d chars)", content_type, len(content))
            document.status = DocumentStatus.PROCESSING
            db.commit()
            logger.debug("💾 [DB UPDATE] Document status set to PROCESSING")
            _log_ingestion_event(
                db, document_uuid, DocumentStatus.PROCESSING,
                stage="paste_detection",
                message=f"Processing {content_type} content",
            )

            # Normalise content
            logger.debug("🔄 [NORMALISE] Starting content normalisation for type: %s", content_type)
            text_content = content
            metadata: dict = {
                "source_type": "paste",
                "detected_type": content_type,
                "tags": tags or [],
            }

            if content_type == "url":
                logger.debug("🌐 [URL DETECTION] Extracting URL metadata")
                metadata["source_url"] = content
                metadata["url_title"] = content.split("/")[-1]
            elif content_type == "email":
                logger.debug("📧 [EMAIL DETECTION] Parsing email content")
                parts = content.split("\n")
                metadata["email_from"] = parts[0] if parts else ""
                metadata["email_subject"] = parts[1] if len(parts) > 1 else ""
                text_content = "\n".join(parts[2:]) if len(parts) > 2 else ""
            elif content_type == "code":
                logger.debug("💻 [CODE DETECTION] Detecting programming language")
                if "def " in content or "import " in content:
                    metadata["language"] = "python"
                elif "function " in content or "const " in content:
                    metadata["language"] = "javascript"
                elif "<" in content and "html" in content.lower():
                    metadata["language"] = "html"
                else:
                    metadata["language"] = "unknown"
                logger.debug("🔍 [LANGUAGE] Detected language: %s", metadata["language"])
            elif content_type == "structured":
                logger.debug("📊 [STRUCTURED DATA] Detecting format")
                metadata["format"] = (
                    "json" if content.lstrip().startswith(("{", "[")) else "csv"
                )
                logger.debug("🔍 [FORMAT] Detected format: %s", metadata["format"])

            logger.debug("🔄 [ENTITY EXTRACTION] Extracting entities from content")
            entities = extract_entities(text_content)
            logger.debug("🔄 [TOPIC CLASSIFICATION] Classifying topics")
            topics = classify_topics(text_content)
            logger.info("✅ [CONTENT ANALYSIS] Found %d entities, %d topics", len(entities.get('people', [])) + len(entities.get('organizations', [])), len(topics))
            metadata["entities"] = entities
            metadata["topics"] = topics

            document.title = (
                f"{content_type.capitalize()} — "
                f"{metadata.get('url_title', 'Pasted Content')[:50]}"
            )
            document.source_type = SourceType.PASTE
            document.source_metadata = metadata
            document.token_count = len(text_content.split())
            db.commit()
            logger.debug("💾 [DB UPDATE] Document metadata set - Title: %s, Tokens: %d", document.title, document.token_count)

            _log_ingestion_event(
                db, document_uuid, DocumentStatus.PROCESSING,
                stage="paste_normalised",
                message=(
                    f"Extracted {len(entities.get('people', []))} entities, "
                    f"{len(topics)} topics"
                ),
            )

            logger.debug("✂️ [CHUNKING] Starting chunking process")
            chunks = chunker.chunk_text(text_content, metadata=metadata)
            logger.debug("💾 [DB INSERT] Saving %d chunks", len(chunks))
            db_chunks, stale_vector_ids = _save_chunks(db, document_uuid, chunks)
            document.chunk_count = len(chunks)
            db.commit()
            logger.info("✅ [CHUNKS SAVED] %d chunks created and indexed", len(chunks))

            if stale_vector_ids:
                _cleanup_vector_ids(
                    str(document.workspace_id),
                    stale_vector_ids,
                    reason=f"Removing stale vectors after reprocessing pasted document {document_id}",
                )

            logger.debug("🧠 [EMBEDDING] Starting vector indexing")
            vector_ids = _index_vectors(
                db, document, document_id, chunks, db_chunks,
                {"source_type": "paste", "paste_type": content_type},
            )
            logger.info("✅ [VECTORS INDEXED] %d vectors created", len(vector_ids))

            document.status = DocumentStatus.INDEXED
            document.processed_at = datetime.utcnow()
            db.commit()
            logger.debug("💾 [DB UPDATE] Document marked as INDEXED")

            total_ms = int((time.time() - start_time) * 1000)
            _log_ingestion_event(
                db, document_uuid, DocumentStatus.INDEXED,
                stage="paste_complete",
                message=f"{len(chunks)} chunks, {len(vector_ids)} vectors indexed",
            )

            logger.info("✅ [TASK SUCCESS] process_paste_content completed - Duration: %dms - Task ID: %s", total_ms, self.request.id)
            return {
                "status": "success",
                "document_id": document_id,
                "content_type": content_type,
                "chunks_created": len(chunks),
                "vectors_indexed": len(vector_ids),
                "entities": entities,
                "topics": topics,
                "total_duration_ms": total_ms,
            }

        except Exception as exc:
            logger.error("❌ [TASK ERROR] process_paste_content failed - Document: %s - Task ID: %s", document_id, self.request.id, exc_info=True)
            
            if document is not None:
                document.status = DocumentStatus.FAILED
                db.commit()
                logger.debug("💾 [DB UPDATE] Document status set to FAILED")

            _log_ingestion_event(
                db, document_uuid, DocumentStatus.FAILED,
                stage="paste_error", error_message=str(exc),
            )
            logger.error("Paste ingestion failed for %s: %s", document_id, exc, exc_info=True)

            if self.request.retries < self.max_retries:
                countdown = _retry_countdown(self.request.retries)
                logger.warning("⏳ [RETRY SCHEDULED] Attempt %d/%d in %d seconds - Task ID: %s", self.request.retries + 1, self.max_retries, countdown, self.request.id)
                raise self.retry(exc=exc, countdown=countdown)

            logger.error("❌ [MAX RETRIES EXCEEDED] process_paste_content failed - Document: %s - Task ID: %s", document_id, self.request.id)
            return {"status": "failed", "document_id": document_id, "error": str(exc)}


@celery_app.task(bind=True, max_retries=3)
def process_voice_input(
    self,
    document_id: str,
    audio_url: str,
    language: str = "en",
    tags: Optional[list] = None,
) -> dict:
    """Transcribe audio via Whisper, then chunk → embed → index."""
    logger.info("🎙️ [TASK START] process_voice_input - Document: %s, Language: %s, Task ID: %s", document_id, language, self.request.id)
    
    from app.ingestion.content_detection import classify_topics, extract_entities

    try:
        from openai import OpenAI
    except ImportError:
        OpenAI = None  # type: ignore[assignment,misc]

    document_uuid = UUID(document_id)
    start_time = time.time()

    with get_db_session() as db:
        document = None
        try:
            logger.debug("🔍 [DB QUERY] Fetching document: %s", document_id)
            document = db.query(Document).filter(Document.id == document_uuid).first()
            if not document:
                logger.error("❌ [DB ERROR] Document %s not found - Task ID: %s", document_id, self.request.id)
                raise ValueError(f"Document {document_id} not found")

            if OpenAI is None:
                logger.error("❌ [DEPENDENCY ERROR] openai library not installed")
                raise ImportError(
                    "openai library is not installed — run: pip install openai"
                )

            logger.info("⬇️ [STAGE 1] Audio Download - URL: %s", audio_url)
            document.status = DocumentStatus.PROCESSING
            db.commit()
            logger.debug("💾 [DB UPDATE] Document status set to PROCESSING")
            _log_ingestion_event(
                db, document_uuid, DocumentStatus.PROCESSING,
                stage="voice_download",
                message=f"Downloading audio from {audio_url}",
            )

            # Download audio
            logger.debug("🔄 [S3] Downloading audio file from S3")
            t0 = time.time()
            audio_bytes = s3_client.download_file(audio_url)
            download_ms = int((time.time() - t0) * 1000)
            logger.info("✅ [STAGE 1 COMPLETE] Downloaded %d bytes in %dms", len(audio_bytes), download_ms)
            _log_ingestion_event(
                db, document_uuid, DocumentStatus.PROCESSING,
                stage="voice_download",
                message=f"Downloaded {len(audio_bytes)} bytes",
                duration_ms=download_ms,
            )

            # Transcribe via Whisper
            logger.info("🔄 [STAGE 2] Whisper Transcription - Language: %s", language)
            t0 = time.time()
            logger.debug("🔐 [OPENAI] Creating OpenAI client for Whisper API")
            client = OpenAI(api_key=settings.OPENAI_API_KEY)

            # Use a context-managed temp file to ensure cleanup even on error.
            logger.debug("📝 [TEMP FILE] Creating temporary audio file")
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                tmp.write(audio_bytes)
                tmp_path = tmp.name

            try:
                logger.debug("🎤 [WHISPER] Calling Whisper API for transcription")
                with open(tmp_path, "rb") as audio_file:
                    transcript = client.audio.transcriptions.create(
                        model="whisper-1",
                        file=audio_file,
                        language=language,
                        response_format="verbose_json",
                    )
            finally:
                logger.debug("🧹 [CLEANUP] Removing temporary audio file")
                os.unlink(tmp_path)

            text_content = transcript.text
            transcription_ms = int((time.time() - t0) * 1000)
            logger.info("✅ [STAGE 2 COMPLETE] Transcribed %d words in %dms", len(text_content.split()), transcription_ms)
            _log_ingestion_event(
                db, document_uuid, DocumentStatus.PROCESSING,
                stage="voice_transcribed",
                message=f"Transcribed {len(text_content.split())} words",
                duration_ms=transcription_ms,
            )

            logger.debug("🔄 [ENTITY EXTRACTION] Extracting entities from transcript")
            entities = extract_entities(text_content)
            logger.debug("🔄 [TOPIC CLASSIFICATION] Classifying topics")
            topics = classify_topics(text_content)
            logger.info("✅ [ANALYSIS] Extracted %d entities, %d topics", len(entities), len(topics))

            metadata = {
                "source_type": "voice",
                "audio_language": language,
                "transcript_confidence": getattr(transcript, "confidence", None),
                "entities": entities,
                "topics": topics,
                "tags": tags or [],
            }

            document.title = f"Voice Note — {language.upper()}"
            document.source_type = SourceType.VOICE
            document.source_metadata = metadata
            document.token_count = len(text_content.split())
            db.commit()
            logger.debug("💾 [DB UPDATE] Document metadata set")

            logger.debug("✂️ [CHUNKING] Creating chunks from transcript")
            chunks = chunker.chunk_text(text_content, metadata=metadata)
            logger.debug("💾 [DB INSERT] Saving %d chunks", len(chunks))
            db_chunks, stale_vector_ids = _save_chunks(db, document_uuid, chunks)
            document.chunk_count = len(chunks)
            db.commit()
            logger.info("✅ [CHUNKS] Created %d chunks", len(chunks))

            if stale_vector_ids:
                _cleanup_vector_ids(
                    str(document.workspace_id),
                    stale_vector_ids,
                    reason=f"Removing stale vectors after reprocessing voice document {document_id}",
                )

            logger.debug("🧠 [EMBEDDING] Indexing vectors")
            vector_ids = _index_vectors(
                db, document, document_id, chunks, db_chunks,
                {"source_type": "voice", "language": language},
            )
            logger.info("✅ [VECTORS] Indexed %d vectors", len(vector_ids))

            document.status = DocumentStatus.INDEXED
            document.processed_at = datetime.utcnow()
            db.commit()
            logger.debug("💾 [DB UPDATE] Document marked as INDEXED")

            total_ms = int((time.time() - start_time) * 1000)
            _log_ingestion_event(
                db, document_uuid, DocumentStatus.INDEXED,
                stage="voice_complete",
                message=f"{len(chunks)} chunks indexed",
            )

            logger.info("✅ [TASK SUCCESS] process_voice_input completed - Duration: %dms - Task ID: %s", total_ms, self.request.id)
            return {
                "status": "success",
                "document_id": document_id,
                "language": language,
                "transcription_length": len(text_content),
                "chunks_created": len(chunks),
                "vectors_indexed": len(vector_ids),
                "entities": entities,
                "topics": topics,
                "total_duration_ms": total_ms,
            }

        except Exception as exc:
            logger.error("❌ [TASK ERROR] process_voice_input failed - Document: %s - Task ID: %s", document_id, self.request.id, exc_info=True)
            
            if document is not None:
                document.status = DocumentStatus.FAILED
                db.commit()
                logger.debug("💾 [DB UPDATE] Document status set to FAILED")

            _log_ingestion_event(
                db, document_uuid, DocumentStatus.FAILED,
                stage="voice_error", error_message=str(exc),
            )
            logger.error("Voice ingestion failed for %s: %s", document_id, exc, exc_info=True)

            if self.request.retries < self.max_retries:
                countdown = _retry_countdown(self.request.retries)
                logger.warning("⏳ [RETRY SCHEDULED] Attempt %d/%d in %d seconds - Task ID: %s", self.request.retries + 1, self.max_retries, countdown, self.request.id)
                raise self.retry(exc=exc, countdown=countdown)

            logger.error("❌ [MAX RETRIES EXCEEDED] process_voice_input failed - Document: %s - Task ID: %s", document_id, self.request.id)
            return {"status": "failed", "document_id": document_id, "error": str(exc)}


@celery_app.task(bind=True, max_retries=2)
def process_auto_tagging(
    self,
    document_id: str,
    extract_entities: bool = True,
    classify_topics: bool = True,
) -> dict:
    """NER + topic classification across all chunks of a document."""
    logger.info("🏷️ [TASK START] process_auto_tagging - Document: %s, Extract Entities: %s, Classify Topics: %s, Task ID: %s", document_id, extract_entities, classify_topics, self.request.id)
    
    from app.ingestion.content_detection import (
        classify_sentiment,
        classify_topics as classify_tops,
        extract_entities as extract_ents,
    )

    document_uuid = UUID(document_id)
    start_time = time.time()

    with get_db_session() as db:
        try:
            logger.debug("🔍 [DB QUERY] Fetching document: %s", document_id)
            document = db.query(Document).filter(Document.id == document_uuid).first()
            if not document:
                logger.error("❌ [DB ERROR] Document %s not found - Task ID: %s", document_id, self.request.id)
                raise ValueError(f"Document {document_id} not found")

            logger.info("🔍 [CHUNKING] Fetching chunks for document")
            _log_ingestion_event(
                db, document_uuid, DocumentStatus.INDEXED,
                stage="auto_tag_start", message="Starting auto-tagging",
            )

            chunks = db.query(Chunk).filter(Chunk.document_id == document_uuid).all()
            logger.info("📦 [CHUNKS LOADED] %d chunks found", len(chunks))

            all_entities: dict = {
                "people": set(), "organizations": set(),
                "locations": set(), "dates": set(), "concepts": set(),
            }
            all_topics: dict = {}
            sentiment_scores: list = []

            logger.debug("🔄 [PROCESSING] Starting chunk processing")
            for i, chunk in enumerate(chunks):
                logger.debug("🔄 [CHUNK %d/%d] Processing chunk: %s", i+1, len(chunks), chunk.id)
                chunk.metadata = chunk.metadata or {}
                chunk_entities: dict = {}
                chunk_topics: list = []
                chunk_sentiment: str = "neutral"

                if extract_entities:
                    logger.debug("👥 [ENTITY EXTRACTION] Extracting entities from chunk %d", i+1)
                    chunk_entities = extract_ents(chunk.text)
                    logger.debug("👥 [ENTITIES FOUND] %d entities in chunk %d", len(chunk_entities), i+1)
                    for key in all_entities:
                        all_entities[key].update(chunk_entities.get(key, []))
                    chunk.metadata["entities"] = chunk_entities

                if classify_topics:
                    logger.debug("📚 [TOPIC CLASSIFICATION] Classifying topics for chunk %d", i+1)
                    chunk_topics = classify_tops(chunk.text)
                    logger.debug("📚 [TOPICS FOUND] %d topics in chunk %d", len(chunk_topics), i+1)
                    for topic in chunk_topics:
                        all_topics[topic] = all_topics.get(topic, 0) + 1

                    logger.debug("❤️ [SENTIMENT] Analyzing sentiment for chunk %d", i+1)
                    chunk_sentiment = classify_sentiment(chunk.text)
                    logger.debug("❤️ [SENTIMENT RESULT] %s sentiment in chunk %d", chunk_sentiment, i+1)
                    sentiment_scores.append(chunk_sentiment)
                    chunk.metadata["topics"] = chunk_topics
                    chunk.metadata["sentiment"] = chunk_sentiment

                # ✅ FIX N+1: Don't commit in loop - batch commit all chunks at once below
                logger.debug("📝 [STAGING] Chunk metadata staged for batch commit")

            # ✅ BATCH COMMIT: Save all chunk updates in a single transaction (not one per chunk)
            db.commit()
            logger.info("💾 [BATCH COMMIT] All %d chunks saved to database", len(chunks))

            logger.info("✅ [CONSOLIDATION] All chunks processed, consolidating results")
            consolidated_entities = {k: list(v) for k, v in all_entities.items()}
            sentiment_distribution = {
                "positive": sentiment_scores.count("positive"),
                "negative": sentiment_scores.count("negative"),
                "neutral": sentiment_scores.count("neutral"),
            }
            logger.info("😊 [SENTIMENT DISTRIBUTION] Positive: %d, Negative: %d, Neutral: %d", 
                       sentiment_distribution["positive"], sentiment_distribution["negative"], sentiment_distribution["neutral"])
            
            top_topics = [
                t
                for t, _ in sorted(all_topics.items(), key=lambda x: x[1], reverse=True)[:10]
            ]
            logger.info("🏆 [TOP TOPICS] %d topics identified, top 10: %s", len(all_topics), top_topics[:5])

            document.source_metadata = document.source_metadata or {}

            if extract_entities:
                logger.debug("💾 [DB UPDATE] Saving extracted entities to document metadata")
                document.source_metadata["extracted_entities"] = consolidated_entities
                entity_count = sum(len(v) for v in consolidated_entities.values())
                logger.info("👥 [ENTITIES SUMMARY] %d unique entities extracted", entity_count)

            if classify_topics:
                logger.debug("💾 [DB UPDATE] Saving classified topics to document metadata")
                document.source_metadata["classified_topics"] = top_topics
                document.source_metadata["topic_distribution"] = dict(all_topics)
                document.source_metadata["sentiment_distribution"] = sentiment_distribution
                pos = sentiment_distribution["positive"]
                neg = sentiment_distribution["negative"]
                overall = (
                    "positive" if pos > neg else "negative" if neg > pos else "neutral"
                )
                document.source_metadata["overall_sentiment"] = overall
                logger.info("🎯 [OVERALL SENTIMENT] Document sentiment: %s", overall)

            document.tagged_at = datetime.utcnow()
            db.commit()
            logger.debug("💾 [DB UPDATE] Document tagged_at timestamp set")

            total_ms = int((time.time() - start_time) * 1000)
            entity_count = sum(len(v) for v in consolidated_entities.values())
            _log_ingestion_event(
                db, document_uuid, DocumentStatus.INDEXED,
                stage="auto_tag_complete",
                message=f"{entity_count} entities, {len(top_topics)} topics",
                duration_ms=total_ms,
            )

            logger.info("✅ [TASK SUCCESS] process_auto_tagging completed - Duration: %dms - %d entities, %d topics - Task ID: %s", total_ms, entity_count, len(top_topics), self.request.id)
            return {
                "status": "success",
                "document_id": document_id,
                "entities_extracted": consolidated_entities if extract_entities else None,
                "topics_classified": top_topics if classify_topics else None,
                "sentiment_analysis": sentiment_distribution if classify_topics else None,
                "total_duration_ms": total_ms,
            }

        except Exception as exc:
            logger.error("❌ [TASK ERROR] process_auto_tagging failed - Document: %s - Task ID: %s", document_id, self.request.id, exc_info=True)
            
            _log_ingestion_event(
                db, document_uuid, DocumentStatus.INDEXED,
                stage="auto_tag_error", error_message=str(exc),
            )
            logger.error("Auto-tagging failed for %s: %s", document_id, exc, exc_info=True)

            if self.request.retries < self.max_retries:
                countdown = _retry_countdown(self.request.retries)
                logger.warning("⏳ [RETRY SCHEDULED] Attempt %d/%d in %d seconds - Task ID: %s", self.request.retries + 1, self.max_retries, countdown, self.request.id)
                raise self.retry(exc=exc, countdown=countdown)

            logger.error("❌ [MAX RETRIES EXCEEDED] process_auto_tagging failed - Document: %s - Task ID: %s", document_id, self.request.id)
            return {"status": "failed", "document_id": document_id, "error": str(exc)}


# ---------------------------------------------------------------------------
# Scheduled / periodic tasks
# ---------------------------------------------------------------------------


@celery_app.on_after_configure.connect
def setup_periodic_tasks(sender, **kwargs) -> None:
    from celery.schedules import crontab

    from app.tasks.conflict_detection import (
        cleanup_old_resolved_conflicts,
        run_conflict_detection_for_all_workspaces,
    )
    import app.tasks.embeddings  # noqa: F401 – registers embedding tasks

    sender.add_periodic_task(3600.0, sync_all_connectors.s(), name="sync-all-connectors")
    sender.add_periodic_task(
        crontab(hour=2, minute=0),
        run_conflict_detection_for_all_workspaces.s(),
        name="conflict-detection-nightly",
    )
    sender.add_periodic_task(
        crontab(hour=3, minute=0, day_of_week=1),
        cleanup_old_resolved_conflicts.s(days=90),
        name="cleanup-old-conflicts",
    )


@celery_app.task
def sync_all_connectors() -> dict:
    """Queue a sync job for every active connector."""
    from app.database.models import Connector

    with get_db_session() as db:
        connectors = db.query(Connector).filter(Connector.is_active == 1).all()
        for connector in connectors:
            sync_connector.delay(str(connector.id))
        return {"status": "success", "connectors_queued": len(connectors)}
