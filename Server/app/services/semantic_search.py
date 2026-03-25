"""Semantic search service with hybrid scoring (vector + text + recency + usage)."""
import logging
import json
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
from uuid import UUID
import math

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.database.models import Chunk, Document, Embedding, SearchQuery, SearchLog
from app.services.rag_retriever import get_rag_retriever, RAGRetriever
from app.services.embeddings import get_embedding_service
from app.config import settings

logger = logging.getLogger(__name__)


class SemanticSearchResult:
    """Semantic search result with composite scoring."""
    
    def __init__(
        self,
        chunk_id: UUID,
        document_id: UUID,
        document_title: str,
        content: str,
        source_type: str,
        chunk_index: int,
        created_at: datetime,
        interaction_count: int,
        similarity_score: float,
        recency_score: float = 0.0,
        usage_score: float = 0.0,
        final_score: float = 0.0,
        highlight: str = "",
    ):
        """Initialize semantic search result."""
        self.chunk_id = chunk_id
        self.document_id = document_id
        self.document_title = document_title
        self.content = content
        self.source_type = source_type
        self.chunk_index = chunk_index
        self.created_at = created_at
        self.interaction_count = interaction_count
        self.similarity_score = similarity_score
        self.recency_score = recency_score
        self.usage_score = usage_score
        self.final_score = final_score
        self.highlight = highlight

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "chunk_id": str(self.chunk_id),
            "document_id": str(self.document_id),
            "document_title": self.document_title,
            "content": self.content,
            "source_type": self.source_type,
            "chunk_index": self.chunk_index,
            "created_at": self.created_at.isoformat(),
            "interaction_count": self.interaction_count,
            "similarity_score": round(self.similarity_score, 4),
            "recency_score": round(self.recency_score, 4),
            "usage_score": round(self.usage_score, 4),
            "final_score": round(self.final_score, 4),
            "highlight": self.highlight,
        }


class SemanticSearchService:
    """Semantic search service with hybrid scoring."""
    
    # Composite scoring weights
    SIMILARITY_WEIGHT = 0.60   # 60% semantic similarity
    RECENCY_WEIGHT = 0.25     # 25% recency
    USAGE_WEIGHT = 0.15       # 15% personal usage
    
    # Recency decay: half-life = 4 weeks
    RECENCY_HALF_LIFE_DAYS = 28
    
    def __init__(self, rag_retriever: RAGRetriever, embedding_service):
        """Initialize semantic search service."""
        self.retriever = rag_retriever
        self.embedding_service = embedding_service

    async def search(
        self,
        workspace_id: UUID,
        user_id: UUID,
        query: str,
        limit: int = 10,
        filters: Optional[Dict[str, Any]] = None,
        db: Optional[AsyncSession] = None,
    ) -> List[SemanticSearchResult]:
        """Perform semantic search with hybrid scoring.
        
        Args:
            workspace_id: Workspace UUID
            user_id: User UUID
            query: Search query text
            limit: Maximum number of results to return
            filters: Optional filters (tags, date_from, date_to, source_type)
            db: Database session for logging
            
        Returns:
            List of semantic search results, ranked by composite score
        """
        logger.info(f"Starting semantic search: workspace={workspace_id}, query={query}")
        
        try:
            # Step 1: Retrieve top chunks with vector similarity
            # Fetch 3x limit to allow for re-ranking
            raw_results = await self.retriever.retrieve(
                query=query,
                workspace_id=workspace_id,
                top_k=min(limit * 3, 100),
                filters=filters or {},
            )
            
            if not raw_results:
                logger.info(f"No results found for query: {query}")
                return []
            
            # Step 2: Enrich results with metadata and calculate composite scores
            enriched_results = await self._enrich_results(
                raw_results, user_id, workspace_id, query, db
            )
            
            # Step 3: Re-rank by composite score
            ranked_results = self._rerank(enriched_results, user_id)
            
            # Step 4: Log search query for analytics
            if db:
                await self._log_search_query(
                    db, user_id, workspace_id, query, ranked_results[:limit]
                )
            
            return ranked_results[:limit]
            
        except Exception as e:
            logger.error(f"Semantic search error: {e}", exc_info=True)
            raise

    async def _enrich_results(
        self,
        raw_results: List[Dict[str, Any]],
        user_id: UUID,
        workspace_id: UUID,
        query: str,
        db: Optional[AsyncSession],
    ) -> List[SemanticSearchResult]:
        """Enrich raw results with metadata and extracted highlights.
        
        Args:
            raw_results: Raw vector search results
            user_id: User UUID
            workspace_id: Workspace UUID
            query: Original query for highlighting
            db: Database session
            
        Returns:
            List of enriched semantic search results
        """
        enriched = []
        
        for result in raw_results:
            try:
                # Extract highlight from content
                highlight = self._extract_highlight(result.get("text", ""), query)
                
                result_obj = SemanticSearchResult(
                    chunk_id=result.get("chunk_id"),
                    document_id=result.get("document_id"),
                    document_title=result.get("document_title", ""),
                    content=result.get("text", ""),
                    source_type=result.get("source_type", "unknown"),
                    chunk_index=result.get("chunk_index", 0),
                    created_at=result.get("created_at", datetime.utcnow()),
                    interaction_count=result.get("interaction_count", 0),
                    similarity_score=result.get("score", 0.0),
                    highlight=highlight,
                )
                enriched.append(result_obj)
                
            except Exception as e:
                logger.warning(f"Error enriching result: {e}")
                continue
        
        return enriched

    def _rerank(
        self,
        results: List[SemanticSearchResult],
        user_id: UUID,
    ) -> List[SemanticSearchResult]:
        """Re-rank results using composite scoring formula.
        
        Composite Score = 60% similarity + 25% recency + 15% usage
        
        Args:
            results: Enriched search results
            user_id: User UUID
            
        Returns:
            Re-ranked results by composite final score
        """
        now = datetime.utcnow()
        
        for result in results:
            # Calculate recency score: exponential decay with half-life = 4 weeks
            age_days = (now - result.created_at).days
            decay_constant = math.log(2) / self.RECENCY_HALF_LIFE_DAYS
            result.recency_score = math.exp(-decay_constant * age_days)
            
            # Calculate usage score: logarithmic normalization
            # max log10(100) ≈ 2, so divide by 2 to normalize to 0-1
            result.usage_score = min(
                math.log10(result.interaction_count + 1) / 3.0, 1.0
            )
            
            # Calculate final composite score
            result.final_score = (
                (self.SIMILARITY_WEIGHT * result.similarity_score) +
                (self.RECENCY_WEIGHT * result.recency_score) +
                (self.USAGE_WEIGHT * result.usage_score)
            )
        
        # Sort by final score (descending)
        results.sort(key=lambda r: r.final_score, reverse=True)
        
        logger.debug(f"Ranked {len(results)} results for user={user_id}")
        
        return results

    def _extract_highlight(self, content: str, query: str) -> str:
        """Extract highlighted snippet from content containing query terms.
        
        Args:
            content: Full content text
            query: Query text to search for
            
        Returns:
            Highlighted snippet with ellipsis
        """
        if not content or len(content) == 0:
            return ""
        
        # Get query terms (words longer than 3 characters)
        query_terms = [
            term.lower()
            for term in query.split()
            if len(term) > 3
        ]
        
        if not query_terms:
            return content[:200] + "..." if len(content) > 200 else content
        
        # Find first occurrence of any query term
        content_lower = content.lower()
        earliest_match = None
        earliest_pos = len(content)
        
        for term in query_terms:
            pos = content_lower.find(term)
            if pos != -1 and pos < earliest_pos:
                earliest_pos = pos
                earliest_match = pos
        
        if earliest_match is None:
            # No match found, return preview
            return content[:200] + "..." if len(content) > 200 else content
        
        # Extract context around match
        start = max(0, earliest_match - 80)
        end = min(len(content), earliest_match + 200)
        
        highlight = ""
        if start > 0:
            highlight += "..."
        
        highlight += content[start:end]
        
        if end < len(content):
            highlight += "..."
        
        return highlight

    async def _log_search_query(
        self,
        db: AsyncSession,
        user_id: UUID,
        workspace_id: UUID,
        query: str,
        results: List[SemanticSearchResult],
    ) -> None:
        """Log search query for analytics and learning.
        
        Args:
            db: Database session
            user_id: User UUID
            workspace_id: Workspace UUID
            query: Query text
            results: Search results
        """
        try:
            # Create search log entry
            search_log = SearchLog(
                user_id=user_id,
                workspace_id=workspace_id,
                query_text=query,
                result_chunk_ids=[str(r.chunk_id) for r in results],
                result_count=len(results),
                clicked_count=0,  # Will be updated when user clicks results
                created_at=datetime.utcnow(),
            )
            
            db.add(search_log)
            await db.flush()
            
            logger.debug(f"Logged search query: {query} in workspace={workspace_id}")
            
        except Exception as e:
            logger.warning(f"Error logging search query: {e}")
            # Don't fail the search if logging fails


async def get_semantic_search_service(
    db: AsyncSession,
) -> SemanticSearchService:
    """Get semantic search service instance.
    
    Args:
        db: Database session
        
    Returns:
        Configured semantic search service
    """
    retriever = await get_rag_retriever(db)
    embedding_service = await get_embedding_service()
    
    return SemanticSearchService(retriever, embedding_service)
