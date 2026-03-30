"""Top-K Chunk Retrieval Service for RAG Pipeline - STEP 3"""
import logging
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session

# FIX B5: removed `from app.ingestion.embedder import Embedder` — that class
# has its own provider initialisation path that can diverge from the
# EmbeddingService singleton (different model, different dimension), which
# would silently corrupt Qdrant searches against the wrong-sized collection.
# Use the shared singleton instead so the embed model is always identical to
# what wrote the vectors.
from app.services.embeddings import get_embedding_service
from app.services.vector_db import get_vector_db_client
from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class RetrievedChunk:
    """Chunk retrieved with similarity score and metadata."""
    chunk_id: str
    document_id: str
    similarity: float
    text: str
    source_type: str
    chunk_index: int
    document_title: str
    token_count: int
    context_before: Optional[str] = None
    context_after: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to response dictionary."""
        return {
            "chunk_id": self.chunk_id,
            "document_id": self.document_id,
            "similarity": round(self.similarity, 4),
            "text": self.text,
            "source_type": self.source_type,
            "chunk_index": self.chunk_index,
            "document_title": self.document_title,
            "token_count": self.token_count,
            "context_before": self.context_before,
            "context_after": self.context_after,
            "metadata": self.metadata or {}
        }


@dataclass
class RetrievalResult:
    """Result from top-K chunk retrieval."""
    chunks: List[RetrievedChunk]
    total_retrieved: int
    average_similarity: float
    query_embedding_dim: int
    retrieval_time_ms: float
    workspace_id: str


class TopKRetriever:
    """Top-K chunk retriever using semantic similarity.

    STEP 3 Implementation:
    1. Embed query using the shared EmbeddingService singleton (same model as chunks)
    2. Search vector DB with workspace filtering
    3. Retrieve top K (10-15) nearest neighbors
    4. Return with similarity scores and metadata
    """

    def __init__(
        self,
        db: Session,
        top_k: int = 10,
        similarity_threshold: float = 0.0
    ):
        """Initialize retriever.

        Args:
            db: Database session
            top_k: Number of top chunks to retrieve (10-15 recommended)
            similarity_threshold: Minimum similarity threshold (0.0 = disabled)
        """
        self.db = db
        self.top_k = min(top_k, 20)
        self.similarity_threshold = similarity_threshold

        # FIX B5: use the shared EmbeddingService singleton.
        # This guarantees the query embedding uses the exact same model and
        # dimension as the embeddings that were written to Qdrant by the
        # ingestion pipeline.
        self.embedding_service = get_embedding_service()

        # Pass the actual active dimension so VectorDBClient constructs
        # (or validates) the collection with the right size.
        self.vector_db = get_vector_db_client(
            embedding_dim=self.embedding_service.get_dimension()
        )

        logger.info(
            "TopKRetriever initialized: top_k=%d, provider=%s, dim=%d",
            self.top_k,
            settings.EMBEDDING_PROVIDER,
            self.embedding_service.get_dimension(),
        )

    def retrieve(
        self,
        query: str,
        workspace_id: UUID,
        top_k: Optional[int] = None,
        similarity_threshold: Optional[float] = None
    ) -> RetrievalResult:
        """Retrieve top K chunks most similar to query.

        STEP 3 Pipeline:
        1. Embed query text via shared EmbeddingService
        2. Search Qdrant with workspace filter
        3. Get top K results with similarity scores
        4. Construct result payloads with metadata

        Args:
            query: User query text
            workspace_id: Workspace ID for filtering
            top_k: Override default top_k
            similarity_threshold: Override default threshold

        Returns:
            RetrievalResult with retrieved chunks and metadata
        """
        import time
        start_time = time.time()

        top_k = top_k or self.top_k
        threshold = (
            similarity_threshold
            if similarity_threshold is not None
            else self.similarity_threshold
        )
        workspace_id_str = str(workspace_id)

        logger.info(f"Retrieving top {top_k} chunks for query: '{query[:50]}...'")

        try:
            # Step 1: Embed query using the shared singleton.
            # FIX B5: was `self.embedder._provider.embed([query])[0]` which
            # bypassed all EmbeddingService validation and could produce a
            # vector of a different dimension if the Embedder was initialised
            # with a different provider config.
            logger.debug(
                "Embedding query with %s (dim=%d)...",
                settings.EMBEDDING_PROVIDER,
                self.embedding_service.get_dimension(),
            )
            query_embedding = self.embedding_service.embed_text(query)
            # embed_text raises RuntimeError on failure; no silent empty-vector
            # path exists, so we only need to guard against an unexpected None.
            if not query_embedding:
                logger.error("embed_text returned empty vector unexpectedly")
                return self._empty_result(workspace_id_str, start_time)

            embedding_dim = len(query_embedding)
            logger.debug(f"Query embedding generated: {embedding_dim} dimensions")

            # Step 2: Search vector DB with workspace filtering
            collection_name = workspace_id_str

            logger.debug(
                "Searching Qdrant collection '%s' with threshold %s...",
                collection_name,
                threshold,
            )

            vector_results = self.vector_db.search_similar(
                collection_name=collection_name,
                query_vector=query_embedding,
                workspace_id=workspace_id_str,
                limit=top_k,
                score_threshold=threshold
            )

            logger.info(f"Vector DB returned {len(vector_results)} results")

            # Step 3: Convert to RetrievedChunk objects with full metadata
            retrieved_chunks = []

            for i, result in enumerate(vector_results):
                try:
                    payload = result.get("payload", {})
                    similarity = result.get("similarity", 0.0)

                    chunk = RetrievedChunk(
                        chunk_id=payload.get("chunk_id", result.get("id", "")),
                        document_id=payload.get("document_id", ""),
                        similarity=similarity,
                        text=payload.get("chunk_text", ""),
                        source_type=payload.get("source_type", "unknown"),
                        chunk_index=payload.get("chunk_index", 0),
                        document_title=payload.get("document_title", "Unknown"),
                        token_count=payload.get("token_count", 0),
                        context_before=payload.get("context_before"),
                        context_after=payload.get("context_after"),
                        metadata=payload.get("metadata")
                    )

                    retrieved_chunks.append(chunk)
                    logger.debug(
                        "  [%d] chunk_id=%s similarity=%.4f doc=%s",
                        i + 1, chunk.chunk_id, chunk.similarity, chunk.document_title,
                    )

                except Exception as e:
                    logger.error(f"Error processing result {i}: {e}")
                    continue

            avg_similarity = (
                sum(c.similarity for c in retrieved_chunks) / len(retrieved_chunks)
                if retrieved_chunks else 0.0
            )

            retrieval_time_ms = (time.time() - start_time) * 1000

            logger.info(
                "✓ Retrieved %d chunks (avg_similarity=%.4f, time=%.1fms)",
                len(retrieved_chunks), avg_similarity, retrieval_time_ms,
            )

            return RetrievalResult(
                chunks=retrieved_chunks,
                total_retrieved=len(retrieved_chunks),
                average_similarity=avg_similarity,
                query_embedding_dim=embedding_dim,
                retrieval_time_ms=retrieval_time_ms,
                workspace_id=workspace_id_str
            )

        except Exception as e:
            logger.error(f"Error during retrieval: {e}", exc_info=True)
            return self._empty_result(workspace_id_str, start_time)

    def _empty_result(self, workspace_id_str: str, start_time: float) -> RetrievalResult:
        """Return a zero-result RetrievalResult."""
        import time
        return RetrievalResult(
            chunks=[],
            total_retrieved=0,
            average_similarity=0.0,
            query_embedding_dim=0,
            retrieval_time_ms=(time.time() - start_time) * 1000,
            workspace_id=workspace_id_str
        )

    def retrieve_with_reranking(
        self,
        query: str,
        workspace_id: UUID,
        top_k: Optional[int] = None,
        rerank_model: str = "similarity"
    ) -> RetrievalResult:
        """Retrieve top K chunks with optional re-ranking.

        Args:
            query: User query
            workspace_id: Workspace ID
            top_k: Number of chunks
            rerank_model: Reranker to use ("similarity", "diversity", "recency")

        Returns:
            RetrievalResult with re-ranked chunks
        """
        initial_top_k = (top_k or self.top_k) * 2

        result = self.retrieve(
            query=query,
            workspace_id=workspace_id,
            top_k=initial_top_k
        )

        if not result.chunks:
            return result

        if rerank_model == "diversity":
            result.chunks = self._rerank_by_diversity(result.chunks)
        elif rerank_model == "recency":
            result.chunks = self._rerank_by_recency(result.chunks)

        result.chunks = result.chunks[:top_k or self.top_k]
        result.total_retrieved = len(result.chunks)

        return result

    def _rerank_by_diversity(
        self,
        chunks: List[RetrievedChunk]
    ) -> List[RetrievedChunk]:
        """Re-rank chunks to maximise document diversity."""
        if not chunks:
            return chunks

        sorted_chunks = sorted(chunks, key=lambda c: c.similarity, reverse=True)

        reranked = []
        seen_docs = set()

        for chunk in sorted_chunks:
            if chunk.document_id not in seen_docs:
                reranked.append(chunk)
                seen_docs.add(chunk.document_id)

        for chunk in sorted_chunks:
            if len(reranked) < len(chunks) and chunk not in reranked:
                reranked.append(chunk)

        return reranked

    def _rerank_by_recency(
        self,
        chunks: List[RetrievedChunk]
    ) -> List[RetrievedChunk]:
        """Re-rank chunks by recency (similarity fallback until timestamps in metadata)."""
        return sorted(chunks, key=lambda c: c.similarity, reverse=True)

    def get_query_stats(
        self,
        result: RetrievalResult
    ) -> Dict[str, Any]:
        """Get statistics about retrieval result."""
        unique_docs = len(set(c.document_id for c in result.chunks))
        high_sim_count = sum(1 for c in result.chunks if c.similarity >= 0.75)

        return {
            "total_chunks_retrieved": result.total_retrieved,
            "average_similarity": round(result.average_similarity, 4),
            "unique_documents": unique_docs,
            "high_confidence_chunks": high_sim_count,
            "query_embedding_dimension": result.query_embedding_dim,
            "retrieval_time_ms": round(result.retrieval_time_ms, 2)
        }


def get_top_k_retriever(
    db: Session,
    top_k: int = 10,
    similarity_threshold: float = 0.0
) -> TopKRetriever:
    """Factory function to create TopKRetriever instance."""
    return TopKRetriever(
        db=db,
        top_k=top_k,
        similarity_threshold=similarity_threshold
    )