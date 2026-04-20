"""Batch embedding processor for efficient vectorization of document chunks."""
from collections import defaultdict
from typing import List, Dict, Any, Optional
from datetime import datetime
from uuid import UUID, uuid4
import logging
from dataclasses import dataclass, asdict

from sqlalchemy import select, and_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Chunk, ChunkStatus, Embedding, Document, DocumentStatus
from app.ingestion.embedder import EmbeddingProvider
from app.services.vector_db import VectorDBClient
from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class ChunkEmbeddingPayload:
    """Payload for storing with embedding in vector DB."""
    chunk_id: str
    document_id: str
    workspace_id: str
    source_type: str
    chunk_index: int
    created_at: str
    document_title: str
    chunk_text: str
    token_count: int
    context_before: Optional[str] = None
    context_after: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        data = asdict(self)
        if self.metadata is None:
            data.pop("metadata")
        return data


class BatchEmbeddingProcessor:
    """Process chunks in batches to generate and store embeddings."""
    
    def __init__(
        self,
        db: AsyncSession,
        embedding_provider: EmbeddingProvider,
        vector_db: VectorDBClient,
        batch_size: int = settings.EMBEDDING_BATCH_SIZE
    ):
        """Initialize batch processor.
        
        Args:
            db: Database session
            embedding_provider: Embedding provider (OpenAI, Cohere, BGE, etc.)
            vector_db: Vector database client (Qdrant)
            batch_size: Number of chunks to process per batch (50-100 recommended)
        """
        self.db = db
        self.embedding_provider = embedding_provider
        self.vector_db = vector_db
        self.batch_size = batch_size
        self.model_version = self._get_model_info()

    @staticmethod
    def _build_vector_id(chunk: Chunk) -> str:
        """Use a deterministic vector ID so re-embeds update instead of duplicating."""
        return str(chunk.id)
    
    def _get_model_info(self) -> Dict[str, Any]:
        """Get model information for reproducibility tracking.
        
        Returns:
            Dict with model name, version, dimension
        """
        return {
            "provider": self.embedding_provider.__class__.__name__,
            "model_name": self.embedding_provider.model_name,
            "model_dimension": self.embedding_provider.dimension,
            "timestamp": datetime.utcnow().isoformat(),
        }
    
    async def process_document_chunks(
        self,
        document_id: UUID,
        workspace_id: UUID,
    ) -> Dict[str, Any]:
        """Process all chunks for a document and generate embeddings.
        
        Args:
            document_id: ID of document
            workspace_id: ID of workspace
            
        Returns:
            Dict with processing results:
                {
                    "success": bool,
                    "total_chunks": int,
                    "processed_chunks": int,
                    "failed_chunks": int,
                    "vector_ids": List[str],
                    "duration_ms": float,
                    "errors": List[str]
                }
        """
        try:
            doc_res = await self.db.execute(select(Document).where(Document.id == document_id))
            document = doc_res.scalars().first()

            if not document:
                return {
                    "success": False,
                    "total_chunks": 0,
                    "processed_chunks": 0,
                    "failed_chunks": 0,
                    "vector_ids": [],
                    "errors": [f"Document {document_id} not found"],
                    "duration_ms": 0.0,
                }

            chunks_res = await self.db.execute(
                select(Chunk)
                .outerjoin(Embedding, Embedding.chunk_id == Chunk.id)
                .where(
                    Chunk.document_id == document_id,
                    Embedding.id.is_(None),
                )
                .order_by(Chunk.chunk_index)
            )
            chunks = chunks_res.scalars().all()
            return await self._process_loaded_document_chunks(
                document=document,
                workspace_id=workspace_id,
                chunks=chunks,
            )
            
        except Exception as e:
            logger.error(f"Error processing document chunks: {e}", exc_info=True)
            try:
                doc_res = await self.db.execute(select(Document).where(Document.id == document_id))
                document = doc_res.scalars().first()
                if document:
                    document.status = DocumentStatus.FAILED
                    await self.db.commit()
            except Exception:
                pass
            return {
                "success": False,
                "total_chunks": 0,
                "processed_chunks": 0,
                "failed_chunks": 0,
                "vector_ids": [],
                "errors": [str(e)],
                "duration_ms": 0.0,
            }

    async def _process_loaded_document_chunks(
        self,
        *,
        document: Document,
        workspace_id: UUID,
        chunks: List[Chunk],
    ) -> Dict[str, Any]:
        """Process a document when the caller already has the pending chunks."""
        import time

        start_time = time.time()
        result = {
            "success": True,
            "total_chunks": len(chunks),
            "processed_chunks": 0,
            "failed_chunks": 0,
            "vector_ids": [],
            "errors": [],
        }

        try:
            if not chunks:
                logger.info("No pending chunks left to embed for document %s", document.id)
                document.status = DocumentStatus.INDEXED
                document.processed_at = datetime.utcnow()
                try:
                    await self.db.commit()
                except Exception:
                    await self.db.rollback()
                return {**result, "duration_ms": (time.time() - start_time) * 1000}

            collection_name = str(workspace_id)

            for batch_start in range(0, len(chunks), self.batch_size):
                batch_end = min(batch_start + self.batch_size, len(chunks))
                batch_chunks = chunks[batch_start:batch_end]

                logger.info(
                    "Processing chunk batch %d (%d-%d) for document %s",
                    batch_start // self.batch_size + 1,
                    batch_start,
                    batch_end,
                    document.id,
                )

                batch_result = await self._process_batch(
                    batch_chunks,
                    document,
                    workspace_id,
                    collection_name,
                )

                result["processed_chunks"] += batch_result["processed"]
                result["failed_chunks"] += batch_result["failed"]
                result["vector_ids"].extend(batch_result["vector_ids"])
                result["errors"].extend(batch_result["errors"])

            document.status = (
                DocumentStatus.INDEXED
                if result["failed_chunks"] == 0
                else DocumentStatus.FAILED
            )
            document.processed_at = datetime.utcnow()
            try:
                await self.db.commit()
            except Exception:
                await self.db.rollback()

            logger.info(
                "Successfully processed %d/%d chunks for document %s",
                result["processed_chunks"],
                result["total_chunks"],
                document.id,
            )
        except Exception as e:
            logger.error(f"Error processing document chunks: {e}", exc_info=True)
            result["success"] = False
            result["errors"].append(str(e))
            try:
                document.status = DocumentStatus.FAILED
                await self.db.commit()
            except Exception:
                await self.db.rollback()

        result["duration_ms"] = (time.time() - start_time) * 1000
        return result
    
    async def _process_batch(
        self,
        batch_chunks: List[Chunk],
        document: Document,
        workspace_id: UUID,
        collection_name: str
    ) -> Dict[str, Any]:
        """Process a single batch of chunks.
        
        Args:
            batch_chunks: List of chunk objects to process
            document: Parent document
            workspace_id: Workspace ID
            collection_name: Vector DB collection name
            
        Returns:
            Batch processing result with vector IDs and error tracking
        """
        batch_result = {
            "processed": 0,
            "failed": 0,
            "vector_ids": [],
            "errors": []
        }
        
        try:
            # Extract texts from chunks
            chunk_texts = [chunk.text for chunk in batch_chunks]
            
            # Generate embeddings via provider
            logger.debug(f"Generating embeddings for {len(chunk_texts)} chunks")
            embeddings = self.embedding_provider.embed(chunk_texts)
            
            if len(embeddings) != len(batch_chunks):
                error = f"Embedding count mismatch: got {len(embeddings)}, expected {len(batch_chunks)}"
                logger.error(error)
                batch_result["errors"].append(error)
                batch_result["failed"] = len(batch_chunks)
                return batch_result
            
            # FIX: Capture ACTUAL model info AFTER embeddings generated (reflects fallback if it happened)
            actual_model_info = {
                "provider": self.embedding_provider.__class__.__name__,
                "model_name": self.embedding_provider.model_name,
                "model_dimension": self.embedding_provider.dimension,
                "timestamp": datetime.utcnow().isoformat(),
            }
            logger.info(
                f"📝 [EMBEDDING METADATA] Used: {actual_model_info['model_name']} "
                f"({actual_model_info['model_dimension']}D) from {actual_model_info['provider']}"
            )
            
            self.vector_db.create_collection(
                collection_name,
                embedding_dim=actual_model_info["model_dimension"],
            )

            # Prepare vector points for Qdrant
            vector_points = []
            embedding_records = []
            
            for chunk, embedding in zip(batch_chunks, embeddings):
                try:
                    # Create vector ID (Qdrant expects hashable identifier)
                    vector_id = self._build_vector_id(chunk)
                    
                    # Build payload
                    payload = ChunkEmbeddingPayload(
                        chunk_id=str(chunk.id),
                        document_id=str(document.id),
                        workspace_id=str(workspace_id),
                        source_type=document.source_type.value,
                        chunk_index=chunk.chunk_index,
                        created_at=(
                            chunk.created_at.isoformat()
                            if getattr(chunk, "created_at", None)
                            else datetime.utcnow().isoformat()
                        ),
                        document_title=document.title,
                        chunk_text=chunk.text,
                        token_count=chunk.token_count,
                        context_before=chunk.context_before,
                        context_after=chunk.context_after,
                        metadata={
                            **(chunk.chunk_metadata or {}),
                            "interaction_count": 0,
                        }
                    )
                    
                    # Create point for vector DB
                    point = {
                        "id": vector_id,
                        "vector": embedding,
                        "payload": payload.to_dict()
                    }
                    vector_points.append(point)
                    
                    # FIX: Use ACTUAL model info (reflects provider that really generated this embedding)
                    embedding_record = {
                        "chunk_id": chunk.id,
                        "vector_id": vector_id,
                        "collection_name": collection_name,
                        "model_used": actual_model_info["model_name"],
                        "embedding_dimension": actual_model_info["model_dimension"],
                        "provider_used": actual_model_info["provider"],
                        "vector": embedding  # Store for reference (could be in separate vector store)
                    }
                    embedding_records.append(embedding_record)
                    
                    batch_result["vector_ids"].append(vector_id)
                    batch_result["processed"] += 1
                    
                except Exception as chunk_error:
                    logger.error(f"Error processing chunk {chunk.id}: {chunk_error}")
                    batch_result["failed"] += 1
                    batch_result["errors"].append(f"Chunk {chunk.id}: {str(chunk_error)}")
            
            # Upsert vectors to Qdrant in one batch
            if vector_points:
                logger.debug(f"Upserting {len(vector_points)} vectors to {collection_name}")
                success = self.vector_db.upsert_vectors(
                    collection_name,
                    vector_points
                )
                
                if not success:
                    error = "Failed to upsert vectors to Qdrant"
                    logger.error(error)
                    batch_result["errors"].append(error)
                    # Mark batch as failed
                    batch_result["processed"] = 0
                    batch_result["failed"] = len(batch_chunks)
                    batch_result["vector_ids"] = []
                    return batch_result
                
                # Save embedding metadata to PostgreSQL
                await self._save_embedding_metadata(embedding_records)
                await self._mark_chunks_embedded([chunk.id for chunk in batch_chunks])
            
        except Exception as e:
            logger.error(f"Error processing batch: {e}", exc_info=True)
            batch_result["failed"] = len(batch_chunks)
            batch_result["errors"].append(str(e))
        
        return batch_result
    
    async def _save_embedding_metadata(self, embedding_records: List[Dict[str, Any]]):
        """Stage embedding metadata in PostgreSQL without per-batch commits."""
        if not embedding_records:
            return

        try:
            values = [
                {
                    "id": uuid4(),
                    "chunk_id": record["chunk_id"],
                    "vector_id": record["vector_id"],
                    "collection_name": record["collection_name"],
                    "model_used": record["model_used"],
                    "embedding_dimension": record["embedding_dimension"],
                }
                for record in embedding_records
            ]
            stmt = pg_insert(Embedding).values(values)
            stmt = stmt.on_conflict_do_nothing(index_elements=["chunk_id"])
            await self.db.execute(stmt)
            await self.db.flush()
            logger.debug("Staged %d embedding metadata records", len(embedding_records))
        except Exception as e:
            logger.error(f"Error staging embedding metadata: {e}")
            raise

    async def _mark_chunks_embedded(self, chunk_ids: List[UUID]) -> None:
        """Mark successfully embedded chunks in a single bulk update."""
        if not chunk_ids:
            return

        await self.db.execute(
            update(Chunk)
            .where(Chunk.id.in_(chunk_ids))
            .values(
                chunk_status=ChunkStatus.EMBEDDED,
                updated_at=datetime.utcnow(),
            )
        )
        await self.db.flush()
    
    async def process_pending_chunks(
        self,
        workspace_id: UUID,
        limit: Optional[int] = None
    ) -> Dict[str, Any]:
        """Process all pending chunks for a workspace.
        
        Args:
            workspace_id: Workspace ID
            limit: Max documents to process (None = all)
            
        Returns:
            Overall processing result
        """
        result = {
            "success": True,
            "total_documents": 0,
            "processed_documents": 0,
            "failed_documents": 0,
            "total_chunks": 0,
            "total_processed_chunks": 0,
            "total_failed_chunks": 0,
            "errors": []
        }
        
        try:
            # Find all documents in PROCESSING status
            stmt = select(Document).where(
                and_(Document.workspace_id == workspace_id, Document.status == DocumentStatus.PROCESSING)
            )

            if limit:
                stmt = stmt.limit(limit)

            docs_res = await self.db.execute(stmt)
            documents = docs_res.scalars().all()
            result["total_documents"] = len(documents)

            if not documents:
                return result

            document_ids = [document.id for document in documents]
            chunks_res = await self.db.execute(
                select(Chunk)
                .outerjoin(Embedding, Embedding.chunk_id == Chunk.id)
                .where(
                    Chunk.document_id.in_(document_ids),
                    Embedding.id.is_(None),
                )
                .order_by(Chunk.document_id, Chunk.chunk_index)
            )
            chunks_by_document: Dict[UUID, List[Chunk]] = defaultdict(list)
            for chunk in chunks_res.scalars().all():
                chunks_by_document[chunk.document_id].append(chunk)

            for document in documents:
                logger.info(f"Processing document {document.id}: {document.title}")

                doc_result = await self._process_loaded_document_chunks(
                    document=document,
                    workspace_id=workspace_id,
                    chunks=chunks_by_document.get(document.id, []),
                )

                if doc_result["success"]:
                    result["processed_documents"] += 1
                else:
                    result["failed_documents"] += 1

                result["total_chunks"] += doc_result["total_chunks"]
                result["total_processed_chunks"] += doc_result["processed_chunks"]
                result["total_failed_chunks"] += doc_result["failed_chunks"]
                result["errors"].extend(doc_result["errors"])

            logger.info(
                f"Batch processing complete: {result['total_processed_chunks']} chunks "
                f"from {result['processed_documents']} documents"
            )
            
        except Exception as e:
            logger.error(f"Error in batch processing: {e}", exc_info=True)
            result["success"] = False
            result["errors"].append(str(e))
        
        return result
