"""Semantic search service with hybrid scoring (vector + text + recency + usage)."""
import asyncio
import logging
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
from uuid import UUID
import math

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import SearchLog
from app.services.rag_retriever import get_rag_retriever, RAGRetriever, RetrievedChunk

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
    RECENCY_WEIGHT = 0.25      # 25% recency
    USAGE_WEIGHT = 0.15        # 15% personal usage

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
            # Step 1: Retrieve top chunks with vector similarity.
            # FIX B1: retrieve() is synchronous — do NOT await it.
            # FIX B1a: pass filters so RAGRetriever can thread them through.
            rag_result = await asyncio.to_thread(
                self.retriever.retrieve,
                query=query,
                workspace_id=workspace_id,
                top_k=min(limit * 2, 60),
                filters=filters or {},
            )

            # FIX B2: RagResult is a dataclass (always truthy); check .chunks.
            if not rag_result.chunks:
                logger.info(f"No results found for query: {query}")
                return []

            # Step 2: Enrich results with metadata and calculate composite scores.
            # FIX B2: pass rag_result.chunks (List[RetrievedChunk]), not the RagResult object.
            enriched_results = await self._enrich_results(
                rag_result.chunks, user_id, workspace_id, query, db
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
        # FIX B2: typed as List[RetrievedChunk]; uses attribute access, not .get()
        chunks: List[RetrievedChunk],
        user_id: UUID,
        workspace_id: UUID,
        query: str,
        db: Optional[AsyncSession],
    ) -> List[SemanticSearchResult]:
        """Enrich raw RetrievedChunk objects with extracted highlights.

        Args:
            chunks: List of RetrievedChunk objects from RAGRetriever
            user_id: User UUID
            workspace_id: Workspace UUID
            query: Original query for highlighting
            db: Database session

        Returns:
            List of enriched SemanticSearchResult objects
        """
        enriched = []

        for chunk in chunks:
            try:
                text = chunk.text
                highlight = self._extract_highlight(text, query)

                # FIX minor: clamp score and actually use the clamped value
                similarity_score = max(0.0, min(chunk.similarity, 1.0))

                # Pull optional fields from the Qdrant payload metadata dict
                metadata = chunk.metadata or {}

                # FIX: Convert ISO string created_at to datetime if needed
                created_at = metadata.get("created_at", datetime.utcnow())
                if isinstance(created_at, str):
                    try:
                        created_at = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                    except (ValueError, AttributeError):
                        created_at = datetime.utcnow()

                result_obj = SemanticSearchResult(
                    chunk_id=chunk.chunk_id,
                    document_id=chunk.document_id,
                    document_title=chunk.document_title or metadata.get("document_title", ""),
                    content=text,
                    source_type=chunk.source_type,
                    chunk_index=chunk.chunk_index,
                    created_at=created_at,
                    interaction_count=metadata.get("interaction_count", 0),
                    # FIX minor: use the clamped value, not the raw chunk attribute
                    similarity_score=similarity_score,
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
            # Recency score: exponential decay, half-life = 4 weeks
            age_days = (now - result.created_at).days
            decay_constant = math.log(2) / self.RECENCY_HALF_LIFE_DAYS
            result.recency_score = math.exp(-decay_constant * age_days)

            # Usage score: logarithmic normalization (max ≈ log10(100) / log10(100) = 1)
            result.usage_score = min(
                math.log1p(result.interaction_count) / math.log1p(100),
                1.0
            )

            # Penalise recency score for low-similarity results
            if result.similarity_score < 0.5:
                result.recency_score *= 0.3

            # Composite final score
            result.final_score = (
                (self.SIMILARITY_WEIGHT * result.similarity_score) +
                (self.RECENCY_WEIGHT * result.recency_score) +
                (self.USAGE_WEIGHT * result.usage_score)
            )

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
        if not content:
            return ""

        query_terms = [
            term.lower()
            for term in query.split()
            if len(term) > 3
        ]

        if not query_terms:
            return content[:200] + "..." if len(content) > 200 else content

        content_lower = content.lower()
        earliest_pos = len(content)
        earliest_match = None

        for term in query_terms:
            pos = content_lower.find(term)
            if pos != -1 and pos < earliest_pos:
                earliest_pos = pos
                earliest_match = pos

        if earliest_match is None:
            return content[:200] + "..." if len(content) > 200 else content

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
        """Log search query for analytics and learning."""
        try:
            search_log = SearchLog(
                user_id=user_id,
                workspace_id=workspace_id,
                query_text=query,
                result_chunk_ids=[str(r.chunk_id) for r in results],
                result_count=len(results),
                clicked_count=0,
                search_duration_ms=None,
                created_at=datetime.utcnow(),
            )

            db.add(search_log)
            await db.flush()

            logger.debug(f"Logged search query: {query} in workspace={workspace_id}")

        except Exception as e:
            logger.warning(f"Error logging search query: {e}")


# FIX B3: was `async def get_semantic_search_service(db: AsyncSession)` which:
#   (a) passed db to get_rag_retriever() which takes no db arg
#   (b) awaited two sync factory functions
#   (c) was declared async for no reason — both factories are sync
#
# db is no longer a parameter here; callers that need a db session for logging
# pass it directly to .search().
def get_semantic_search_service(db: Optional[AsyncSession] = None) -> SemanticSearchService:
    """Create and return a SemanticSearchService instance.

    Both underlying factories (get_rag_retriever, get_embedding_service) are
    synchronous — no await needed.  Pass a db session to .search() if you want
    query logging.

    Returns:
        Configured SemanticSearchService
    """
    retriever = get_rag_retriever()
    embedding_service = get_embedding_service()
    return SemanticSearchService(retriever, embedding_service)
