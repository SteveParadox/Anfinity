"""Celery tasks for embedding and indexing chunks."""
import asyncio
import logging
import os
from typing import Any, Dict, List, Optional
from uuid import UUID
from datetime import datetime

logger = logging.getLogger(__name__)

# Import the central Celery app
from app.celery_app import celery_app

# ---------------------------------------------------------------------------
# FIX 8: Safe asyncio.run wrapper.
#
# asyncio.run() raises RuntimeError if called from inside an already-running
# event loop (common in Jupyter, some test frameworks, and certain Celery
# configurations).  This helper detects that situation and falls back to
# creating a fresh loop on a background thread so the coroutine always runs.
# ---------------------------------------------------------------------------
def _run_async(coro):
    """Run *coro* safely regardless of the current event-loop state."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # We're inside a running loop (e.g. gevent/eventlet Celery pool).
        # Spin up a dedicated thread with its own loop.
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result()
    else:
        return asyncio.run(coro)


# ---------------------------------------------------------------------------
# FIX 6 & 7 & 9: Use AsyncSessionLocal (async session) instead of the sync
# SessionLocal.  The BatchEmbeddingProcessor expects an async session; mixing
# a sync session with async DB calls corrupts the connection.
# ---------------------------------------------------------------------------
async def generate_embeddings_for_document(
    document_id: str,
    workspace_id: str,
    batch_size: Optional[int] = None,
) -> Dict[str, Any]:
    """Generate embeddings for all chunks of a document.

    Steps:
    1. Retrieve all chunks for the document.
    2. Generate embeddings via the configured provider (batched).
    3. Store embeddings in Qdrant with full metadata payload.
    4. Update document status to INDEXED.

    Args:
        document_id:  Document UUID as string.
        workspace_id: Workspace UUID as string.
        batch_size:   Override default batch size from settings.

    Returns:
        Dict with processing stats and status.
    """
    # Lazy imports — keep module-level import cost low
    from app.database.session import AsyncSessionLocal  # FIX 6: async session
    from app.ingestion.embedder import Embedder
    from app.ingestion.embedding_batch_processor import BatchEmbeddingProcessor
    from app.services.vector_db import get_vector_db_client
    from app.config import settings

    logger.info("Starting embedding generation for document %s", document_id)

    # FIX 9: use async context manager — guarantees connection cleanup even
    # if the processor raises partway through.
    async with AsyncSessionLocal() as db:
        try:
            embedder = Embedder(provider=settings.EMBEDDING_PROVIDER)
            
            # ── FIX: capture actual model and dimension for accurate collection creation ───
            actual_model = embedder.model_name
            actual_dim = embedder.dimension
            logger.info(
                "🧠 [EMBEDDING CONFIG] Model: %s, Dimension: %dD",
                actual_model, actual_dim,
            )
            
            vector_db = get_vector_db_client()
            # Pass explicit dimension so collection is created with correct size
            vector_db.create_collection(workspace_id, embedding_dim=actual_dim)

            batch_sz = batch_size or settings.EMBEDDING_BATCH_SIZE
            processor = BatchEmbeddingProcessor(
                db=db,
                embedding_provider=embedder._provider,
                vector_db=vector_db,
                batch_size=batch_sz,
            )

            doc_uuid = UUID(document_id)
            ws_uuid = UUID(workspace_id)

            result = await processor.process_document_chunks(doc_uuid, ws_uuid)

            logger.info(
                "✓ Embedding complete: %d/%d chunks processed for document %s",
                result["processed_chunks"],
                result["total_chunks"],
                document_id,
            )

            return {
                "status": "success" if result["success"] else "partial",
                "document_id": document_id,
                "workspace_id": workspace_id,
                "total_chunks": result["total_chunks"],
                "processed_chunks": result["processed_chunks"],
                "failed_chunks": result["failed_chunks"],
                "duration_ms": result["duration_ms"],
                "errors": result["errors"],
            }

        except Exception as exc:
            logger.error("Error generating embeddings for document %s: %s", document_id, exc, exc_info=True)
            return {
                "status": "error",
                "document_id": document_id,
                "workspace_id": workspace_id,
                "error": str(exc),
            }


async def generate_embeddings_for_workspace(
    workspace_id: str,
    limit: Optional[int] = None,
    batch_size: Optional[int] = None,
) -> Dict[str, Any]:
    """Generate embeddings for all pending documents in a workspace.

    Args:
        workspace_id: Workspace UUID as string.
        limit:        Max documents to process in this run.
        batch_size:   Chunk batch size override.

    Returns:
        Dict with aggregate stats.
    """
    from app.database.session import AsyncSessionLocal  # FIX 7: async session
    from app.ingestion.embedder import Embedder
    from app.ingestion.embedding_batch_processor import BatchEmbeddingProcessor
    from app.services.vector_db import get_vector_db_client
    from app.config import settings

    logger.info("Starting workspace batch embedding for %s", workspace_id)

    async with AsyncSessionLocal() as db:
        try:
            embedder = Embedder(provider=settings.EMBEDDING_PROVIDER)
            
            # ── FIX: capture actual model and dimension for accurate collection creation ───
            actual_model = embedder.model_name
            actual_dim = embedder.dimension
            logger.info(
                "🧠 [EMBEDDING CONFIG] Workspace batch - Model: %s, Dimension: %dD",
                actual_model, actual_dim,
            )
            
            vector_db = get_vector_db_client()
            # Pass explicit dimension so collection is created with correct size
            vector_db.create_collection(workspace_id, embedding_dim=actual_dim)

            batch_sz = batch_size or settings.EMBEDDING_BATCH_SIZE
            processor = BatchEmbeddingProcessor(
                db=db,
                embedding_provider=embedder._provider,
                vector_db=vector_db,
                batch_size=batch_sz,
            )

            ws_uuid = UUID(workspace_id)
            result = await processor.process_pending_chunks(ws_uuid, limit)

            logger.info(
                "✓ Workspace batch complete: %d/%d chunks from %d documents for workspace %s",
                result["total_processed_chunks"],
                result["total_chunks"],
                result["processed_documents"],
                workspace_id,
            )

            return {
                "status": "success" if result["success"] else "partial",
                "workspace_id": workspace_id,
                **result,
            }

        except Exception as exc:
            logger.error("Error in workspace batch embedding for %s: %s", workspace_id, exc, exc_info=True)
            return {
                "status": "error",
                "workspace_id": workspace_id,
                "error": str(exc),
            }


# ---------------------------------------------------------------------------
# Legacy sync task — kept for backward compatibility
# ---------------------------------------------------------------------------
def embed_chunks_task(
    workspace_id: str,
    chunk_ids: List[str],
    texts: List[str],
    batch_size: int = 50,
) -> Dict[str, Any]:
    """Embed chunks synchronously using the legacy embedding service."""
    from app.services.embeddings import get_embedding_service
    from app.services.vector_db import get_vector_db_client

    embedding_service = get_embedding_service()
    vector_db = get_vector_db_client()

    logger.info(
        "Starting legacy embedding task for workspace %s: %d chunks",
        workspace_id,
        len(chunk_ids),
    )

    try:
        # ── FIX: Probe for actual embedding dimension (may differ from default) ────────
        # Some services may return different dims in different scenarios.
        probe_embedding = embedding_service.embed_batch(["test"])
        actual_dim = len(probe_embedding[0]) if probe_embedding else 768
        logger.info(
            "🧠 [LEGACY EMBEDDING] Live dimension: %dD (probed from embedding service)",
            actual_dim,
        )
        
        # ── FIX: pass explicit dimension so collection is created correctly ───────────
        vector_db.create_collection(str(workspace_id), embedding_dim=actual_dim)

        total_embedded = 0
        for i in range(0, len(texts), batch_size):
            batch_texts = texts[i : i + batch_size]
            batch_ids = chunk_ids[i : i + batch_size]

            logger.debug(
                "Processing batch %d: %d chunks", i // batch_size + 1, len(batch_texts)
            )

            embeddings = embedding_service.embed_batch(batch_texts)

            if not embeddings or len(embeddings) != len(batch_ids):
                logger.error(
                    "Embedding mismatch: got %d embeddings for %d chunks — skipping batch",
                    len(embeddings) if embeddings else 0,
                    len(batch_ids),
                )
                continue

            points = [
                {
                    "id": chunk_id,
                    "vector": embedding,
                    "payload": {
                        "chunk_id": chunk_id,
                        "text": text[:1000],
                        "workspace_id": workspace_id,
                        "created_at": datetime.utcnow().isoformat(),
                    },
                }
                for chunk_id, text, embedding in zip(batch_ids, batch_texts, embeddings)
            ]

            if vector_db.upsert_vectors(str(workspace_id), points):
                total_embedded += len(batch_ids)
            else:
                logger.error("Failed to upsert batch %d", i // batch_size)

        logger.info(
            "✓ Embedded %d/%d chunks for workspace %s",
            total_embedded,
            len(chunk_ids),
            workspace_id,
        )

        return {
            "status": "success",
            "workspace_id": workspace_id,
            "chunks_embedded": total_embedded,
            "total_chunks": len(chunk_ids),
        }

    except Exception as exc:
        logger.error("Error embedding chunks for workspace %s: %s", workspace_id, exc, exc_info=True)
        return {"status": "error", "workspace_id": workspace_id, "error": str(exc)}


# ---------------------------------------------------------------------------
# Celery task wrappers
# FIX 8: Use _run_async() everywhere instead of bare asyncio.run()
# ---------------------------------------------------------------------------

@celery_app.task(bind=True, name="generate_embeddings_document")
def generate_embeddings_document_celery(
    self,
    document_id: str,
    workspace_id: str,
    batch_size: Optional[int] = None,
) -> Dict[str, Any]:
    """Celery task: generate embeddings for a single document."""
    logger.info("🧠 [TASK START] generate_embeddings_document_celery - Document: %s, Workspace: %s, Batch Size: %s, Task ID: %s", document_id, workspace_id, batch_size, self.request.id)
    
    try:
        logger.debug("🔄 [ASYNC EXECUTION] Starting async embedding generation")
        result = _run_async(
            generate_embeddings_for_document(document_id, workspace_id, batch_size)
        )
        logger.info("✅ [TASK SUCCESS] generate_embeddings_document_celery completed - Task ID: %s - Result: %s", self.request.id, result.get("status"))
        return result
    except Exception as exc:
        logger.error("❌ [TASK ERROR] generate_embeddings_document_celery failed - Document: %s, Workspace: %s - Task ID: %s", document_id, workspace_id, self.request.id, exc_info=True)
        return {"status": "error", "document_id": document_id, "error": str(exc)}

@celery_app.task(bind=True, name="generate_embeddings_workspace")
def generate_embeddings_workspace_celery(
    self,
    workspace_id: str,
    limit: Optional[int] = None,
    batch_size: Optional[int] = None,
) -> Dict[str, Any]:
    """Celery task: generate embeddings for all pending documents in a workspace."""
    logger.info("🧠 [TASK START] generate_embeddings_workspace_celery - Workspace: %s, Limit: %s, Batch Size: %s, Task ID: %s", workspace_id, limit, batch_size, self.request.id)
    
    try:
        logger.debug("🔄 [ASYNC EXECUTION] Starting async workspace embedding generation for %d documents", limit or -1)
        result = _run_async(
            generate_embeddings_for_workspace(workspace_id, limit, batch_size)
        )
        logger.info("✅ [TASK SUCCESS] generate_embeddings_workspace_celery completed - Workspace: %s - Task ID: %s - Result: %s", workspace_id, self.request.id, result.get("status"))
        return result
    except Exception as exc:
        logger.error("❌ [TASK ERROR] generate_embeddings_workspace_celery failed - Workspace: %s - Task ID: %s", workspace_id, self.request.id, exc_info=True)
        return {"status": "error", "workspace_id": workspace_id, "error": str(exc)}

@celery_app.task(bind=True, name="embed_chunks")
def embed_chunks_celery(
    self,
    workspace_id: str,
    chunk_ids: List[str],
    texts: List[str],
    batch_size: int = 50,
) -> Dict[str, Any]:
    """Celery task: legacy chunk embedding."""
    logger.info("🧠 [TASK START] embed_chunks_celery - Workspace: %s, Chunks: %d, Batch Size: %d, Task ID: %s", workspace_id, len(chunk_ids), batch_size, self.request.id)
    
    try:
        logger.debug("📦 [CHUNK PROCESSING] Processing %d chunks in batches of %d", len(chunk_ids), batch_size)
        result = embed_chunks_task(workspace_id, chunk_ids, texts, batch_size)
        logger.info("✅ [TASK SUCCESS] embed_chunks_celery completed - Workspace: %s, Chunks: %d - Task ID: %s - Result: %s", workspace_id, len(chunk_ids), self.request.id, result.get("status"))
        return result
    except Exception as exc:
        logger.error("❌ [TASK ERROR] embed_chunks_celery failed - Workspace: %s, Chunks: %d - Task ID: %s", workspace_id, len(chunk_ids), self.request.id, exc_info=True)
        return {"status": "error", "workspace_id": workspace_id, "error": str(exc)}


# ---------------------------------------------------------------------------
# Public queue helpers — callers should use these, not the task wrappers.
# ---------------------------------------------------------------------------

def queue_embedding_task(
    workspace_id: str,
    chunk_ids: List[str],
    texts: List[str],
    batch_size: int = 50,
) -> str:
    """Queue a legacy chunk-embedding job."""
    task = embed_chunks_celery.delay(workspace_id, chunk_ids, texts, batch_size)
    logger.debug("Queued embedding task: %s", task.id)
    return str(task.id)


def queue_document_embeddings(
    document_id: str,
    workspace_id: str,
    batch_size: Optional[int] = None,
) -> str:
    """Queue embedding generation for a document."""
    task = generate_embeddings_document_celery.delay(
        document_id, workspace_id, batch_size
    )
    logger.info("Queued document embedding task: %s", task.id)
    return str(task.id)


def queue_workspace_embeddings(
    workspace_id: str,
    limit: Optional[int] = None,
    batch_size: Optional[int] = None,
) -> str:
    """Queue batch embedding generation for a workspace."""
    task = generate_embeddings_workspace_celery.delay(
        workspace_id, limit, batch_size
    )
    logger.info("Queued workspace embedding task: %s", task.id)
    return str(task.id)