"""RAG retrieval service with confidence scoring."""
import logging
from typing import List, Dict, Any, Optional, Set
from dataclasses import dataclass
from statistics import mean
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
        workspace_id: str,
        collection_name: str = None,
        top_k: int = None
    ) -> RagResult:
        """Retrieve relevant chunks for query.
        
        Args:
            query: User query text
            workspace_id: Workspace ID for filtering
            collection_name: Qdrant collection name
            top_k: Override default top_k
            
        Returns:
            RagResult with retrieved chunks and confidence
        """
        top_k = top_k or self.top_k
        collection_name = collection_name or workspace_id
        
        try:
            # Step 1: Embed query
            query_vector = self.embedding_service.embed_text(query)
            logger.debug(f"Embedded query: {query[:50]}...")
            
            # Step 2: Search vector DB
            raw_results = self.vector_db.search_similar(
                collection_name=collection_name,
                query_vector=query_vector,
                workspace_id=workspace_id,
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
                    metadata=payload
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
                    # Allow additional chunks from same document if below top_k
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
            logger.error(f"Error in RAG retrieval: {e}")
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
        
        # Component 1: Average similarity (60% weight)
        avg_similarity = mean([c.similarity for c in chunks])
        similarity_score = min(avg_similarity, 1.0)  # Cap at 1.0
        
        # Component 2: Source diversity (30% weight)
        unique_docs = len(set(c.document_id for c in chunks))
        source_diversity = min(unique_docs / self.max_documents, 1.0)
        
        # Component 3: Agreement score (10% weight)
        # Simple proxy: all chunks have high similarity (low conflict)
        high_similarity_count = sum(1 for c in chunks if c.similarity >= 0.75)
        agreement_score = high_similarity_count / len(chunks)
        
        # Compute weighted confidence
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
        """Rank chunks by relevance.
        
        Args:
            chunks: Chunks to rank
            query: Optional query for re-ranking
            
        Returns:
            Ranked chunks (sorted by similarity descending)
        """
        # Sort by similarity score
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
