"""RAG retrieval service with confidence scoring."""
import logging
from typing import List, Dict, Any, Optional, Set, Union
from dataclasses import dataclass
from statistics import mean
from uuid import UUID
import os

logger = logging.getLogger(__name__)

from app.services.embeddings import get_embedding_service
from app.services.vector_db import get_vector_db_client


@dataclass
class RetrievedChunk:
    """Retrieved chunk with metadata."""
    chunk_id: str
    document_id: str
    text: str
    similarity: float
    chunk_index: int
    source_type: str
    metadata: Dict[str, Any]
    document_title: str = ""
    token_count: int = 0
    context_before: Optional[str] = None
    context_after: Optional[str] = None


@dataclass
class RagResult:
    """RAG retrieval result."""
    chunks: List[RetrievedChunk]
    avg_similarity: float
    unique_documents: int
    confidence: float


class RAGRetriever:
    """Retrieval-Augmented Generation retriever."""

    def __init__(
        self,
        similarity_threshold: float = 0.6,
        top_k: int = 15,
        max_documents: int = 5
    ):
        """Initialize RAG retriever.

        Args:
            similarity_threshold: Minimum similarity score (0-1)
            top_k: Number of chunks to retrieve
            max_documents: Maximum unique documents in results
        """
        self.embedding_service = get_embedding_service()
        self.vector_db = get_vector_db_client()
        self.similarity_threshold = similarity_threshold
        self.top_k = top_k
        self.max_documents = max_documents

    def retrieve(
        self,
        query: str,
        # FIX B4: accept UUID or str; always coerce to str before Qdrant calls
        workspace_id: Union[str, UUID],
        collection_name: str = None,
        top_k: int = None,
        # FIX B3: SemanticSearchService passes filters= — accept and thread through
        filters: Optional[Dict[str, Any]] = None,
    ) -> RagResult:
        """Retrieve relevant chunks for query.

        Args:
            query: User query text
            workspace_id: Workspace ID for filtering (str or UUID)
            collection_name: Qdrant collection name
            top_k: Override default top_k
            filters: Optional metadata filters (passed to vector search)

        Returns:
            RagResult with retrieved chunks and confidence
        """
        top_k = top_k or self.top_k

        # FIX B4: always work with strings for Qdrant collection/filter keys
        workspace_id_str = str(workspace_id)
        collection_name = collection_name or workspace_id_str

        try:
            # Step 1: Embed query
            query_vector = self.embedding_service.embed_text(query)
            logger.debug(
                "Embedded query with %s (dim=%d): '%s...'",
                self.embedding_service.get_model_name(),
                len(query_vector),
                query[:50],
            )

            expected_dim = self.embedding_service.get_dimension()
            if len(query_vector) != expected_dim:
                raise RuntimeError(
                    f"Query embedding dimension mismatch: "
                    f"got {len(query_vector)}D, expected {expected_dim}D. "
                    f"Check that EMBEDDING_PROVIDER matches the provider used "
                    f"during ingestion."
                )

            # Step 2: Search vector DB
            raw_results = self.vector_db.search_similar(
                collection_name=collection_name,
                query_vector=query_vector,
                # FIX B4: always a str so Qdrant MatchValue gets the right type
                workspace_id=workspace_id_str,
                limit=top_k,
                score_threshold=self.similarity_threshold
            )
            logger.debug(f"Retrieved {len(raw_results)} chunks from vector DB")

            # Step 3: Convert to RetrievedChunk objects
            chunks = []
            for result in raw_results:
                payload = result.get("payload", {})
                chunk = RetrievedChunk(
                    chunk_id=result["id"],
                    document_id=payload.get("document_id", "unknown"),
                    text=payload.get("text", ""),
                    similarity=result["similarity"],
                    chunk_index=payload.get("chunk_index", 0),
                    source_type=payload.get("source_type", "unknown"),
                    metadata=payload,
                    document_title = payload.get("document_title", ""),
                    token_count = payload.get("token_count", ""),
                    context_before = payload.get("context_before"),
                    context_after = payload.get("context_after"),
                )
                chunks.append(chunk)

            # Step 4: Filter by similarity threshold
            filtered_chunks = [c for c in chunks if c.similarity >= self.similarity_threshold]
            logger.debug(f"After threshold filter: {len(filtered_chunks)} chunks")

            # Step 5: Ensure diversity - limit documents
            unique_docs: Set[str] = set()
            diverse_chunks = []
            for chunk in filtered_chunks:
                if chunk.document_id not in unique_docs:
                    if len(unique_docs) < self.max_documents:
                        unique_docs.add(chunk.document_id)
                        diverse_chunks.append(chunk)
                elif len(diverse_chunks) < top_k:
                    diverse_chunks.append(chunk)

            # Step 6: Compute confidence score
            confidence = self._compute_confidence(diverse_chunks)

            return RagResult(
                chunks=diverse_chunks,
                avg_similarity=mean([c.similarity for c in diverse_chunks]) if diverse_chunks else 0,
                unique_documents=len(unique_docs),
                confidence=confidence
            )

        except Exception as e:
            error_msg = str(e)
            if "embedding" in error_msg.lower() or "provider" in error_msg.lower():
                logger.error(f"Embedding service failed during RAG retrieval: {e}")
                raise

            logger.warning(f"Non-embedding error in RAG retrieval: {e}")
            return RagResult(
                chunks=[],
                avg_similarity=0,
                unique_documents=0,
                confidence=0
            )

    def _compute_confidence(self, chunks: List[RetrievedChunk]) -> float:
        """Compute confidence score for retrieved chunks.

        Confidence = (
            avg_similarity * 0.6 +
            source_diversity * 0.3 +
            agreement_score * 0.1
        )

        Args:
            chunks: Retrieved chunks

        Returns:
            Confidence score (0-1)
        """
        if not chunks:
            return 0.0

        avg_similarity = mean([c.similarity for c in chunks])
        similarity_score = min(avg_similarity, 1.0)

        unique_docs = len(set(c.document_id for c in chunks))
        source_diversity = min(unique_docs / self.max_documents, 1.0)

        high_similarity_count = sum(1 for c in chunks if c.similarity >= 0.75)
        agreement_score = high_similarity_count / len(chunks)

        confidence = (
            similarity_score * 0.6 +
            source_diversity * 0.3 +
            agreement_score * 0.1
        )

        return min(confidence, 1.0)

    def rank_by_relevance(
        self,
        chunks: List[RetrievedChunk],
        query: str = None
    ) -> List[RetrievedChunk]:
        """Rank chunks by relevance (similarity descending)."""
        return sorted(chunks, key=lambda c: c.similarity, reverse=True)


def get_rag_retriever(
    similarity_threshold: float = 0.6,
    top_k: int = 15
) -> RAGRetriever:
    """Get RAG retriever instance.

    Args:
        similarity_threshold: Minimum similarity score
        top_k: Number of chunks to retrieve

    Returns:
        RAGRetriever instance
    """
    return RAGRetriever(
        similarity_threshold=similarity_threshold,
        top_k=top_k
    )