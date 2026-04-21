"""
PostgreSQL hybrid search service - calls the hybrid_search SQL function.

This service provides an alternative to Qdrant-based search, using PostgreSQL's
native capabilities (pgvector + full-text search).
"""

import logging
import time
from typing import List, Dict, Any, Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Note  # noqa: F401 — kept for downstream imports
from app.services.embeddings import get_embedding_service

logger = logging.getLogger(__name__)


class EmbeddingValidationError(ValueError):
    """Raised when an embedding vector fails the sanity check."""


class PostgreSQLHybridSearchService:
    """PostgreSQL-based hybrid search service.

    Combines:
    * Vector similarity (pgvector cosine distance)
    * Full-text search (tsvector / tsquery)
    * Interaction / engagement scoring

    All three paths delegate ordering to the SQL functions; Python only
    applies post-fetch filtering and serialisation.
    """

    # Scoring weights — must sum to 1.0
    SIMILARITY_WEIGHT: float = 0.60
    USAGE_WEIGHT: float = 0.15
    RECENCY_WEIGHT: float = 0.25

    def __init__(self, embedding_service=None) -> None:
        self.embedding_service = embedding_service or get_embedding_service()
        self._hybrid_search_available: bool = True
        self._hybrid_search_disable_reason: Optional[str] = None
        self._hybrid_search_retry_after: float = 0.0

    # ------------------------------------------------------------------
    # Embedding validation (defence-in-depth after the service hardening)
    # ------------------------------------------------------------------

    def _validate_embedding(self, vec: List[float], context: str = "") -> None:
        """Raise EmbeddingValidationError if *vec* looks like a mock/constant vector.

        Args:
            vec: Candidate embedding.
            context: Optional label used in the error message (e.g. the query).

        Raises:
            EmbeddingValidationError: On any sanity-check failure.
        """
        expected_dim = self.embedding_service.dimension
        tag = f" [{context[:60]}]" if context else ""

        if not isinstance(vec, list):
            raise EmbeddingValidationError(
                f"Embedding{tag} is not a list (got {type(vec).__name__})."
            )
        if len(vec) != expected_dim:
            raise EmbeddingValidationError(
                f"Embedding{tag} has wrong dimension "
                f"(expected {expected_dim}, got {len(vec)})."
            )
        # Constant / near-constant vectors → mock data leaked through
        if len(set(vec)) <= 10:
            raise EmbeddingValidationError(
                f"Embedding{tag} looks like a mock/constant vector "
                f"(only {len(set(vec))} distinct values)."
            )

    @staticmethod
    def _embedding_to_pg(vec: List[float]) -> str:
        """Serialise a float list to the PostgreSQL vector literal format."""
        return f"[{','.join(map(str, vec))}]"

    @staticmethod
    def _is_pgvector_unavailable(exc: Exception) -> bool:
        message = str(exc).lower()
        return (
            'type "vector" does not exist' in message
            or "function hybrid_search" in message
            or "function vector_search_only" in message
            or "embedding_vector does not exist" in message
            or "query_embedding_vector" in message
            or "content_tsv" in message
            or "note_interactions" in message
            or "undefinedcolumnerror" in message
        )

    # ------------------------------------------------------------------
    # Public search methods
    # ------------------------------------------------------------------

    async def hybrid_search(
        self,
        db: AsyncSession,
        query: str,
        workspace_id: UUID,
        limit: int = 10,
        similarity_threshold: float = 0.6,
    ) -> List[Dict[str, Any]]:
        """Perform hybrid search using the ``hybrid_search`` SQL function.

        Args:
            db: Async database session.
            query: Search query text.
            workspace_id: Workspace to search within.
            limit: Maximum results to return.
            similarity_threshold: Minimum vector similarity (0–1).  Results
                with ``embedding_similarity`` below this value are discarded
                in Python *after* the SQL call so that low-quality semantic
                matches never reach the caller even if the SQL function lacks
                an explicit threshold guard.

        Returns:
            List of result dicts ranked by ``final_score`` descending.

        Raises:
            EmbeddingValidationError: If the query embedding fails validation.
        """
        query = query.strip()
        if not query:
            logger.warning("hybrid_search called with empty query — returning []")
            return []

        if not self._hybrid_search_available and time.monotonic() < self._hybrid_search_retry_after:
            raise RuntimeError(self._hybrid_search_disable_reason or "PostgreSQL hybrid search is disabled")

        self._hybrid_search_available = True
        self._hybrid_search_disable_reason = None

        # 1. Generate and validate query embedding
        log_tag = query[:50]
        logger.info("Embedding query: %s", log_tag)
        query_embedding = self.embedding_service.embed_query(query)
        self._validate_embedding(query_embedding, context=log_tag)
        logger.info("Generated %dD query embedding", len(query_embedding))

        # 2. Build SQL args
        dim = self.embedding_service.dimension
        embedding_str = self._embedding_to_pg(query_embedding)

        # 3. Execute — ordering is delegated entirely to the SQL function;
        #    no redundant ORDER BY here (avoids double-sort + pagination bugs).
        sql = text(f"""
            SELECT
                note_id,
                title,
                content,
                note_type,
                workspace_id,
                user_id,
                created_at,
                updated_at,
                embedding_similarity,
                text_score,
                interaction_score,
                final_score,
                COALESCE(highlight, SUBSTRING(content, 1, 150)) AS highlight
            FROM hybrid_search(
                :query_text,
                CAST(:query_embedding AS vector({dim})),
                :workspace_id,
                :limit,
                :sim_weight,
                :rec_weight,
                :usage_weight
            );
        """)

        logger.debug("Executing hybrid_search SQL (limit=%d, threshold=%.2f)", limit, similarity_threshold)
        try:
            result = await db.execute(sql, {
                "query_text": query,
                "query_embedding": embedding_str,
                "workspace_id": workspace_id,   # SQLAlchemy handles UUID natively
                "limit": limit,
                "sim_weight": self.SIMILARITY_WEIGHT,
                "rec_weight": self.RECENCY_WEIGHT,
                "usage_weight": self.USAGE_WEIGHT,
            })
            rows = result.fetchall()
        except Exception as exc:
            if self._is_pgvector_unavailable(exc):
                self._hybrid_search_available = False
                self._hybrid_search_retry_after = time.monotonic() + 30.0
                self._hybrid_search_disable_reason = (
                    "PostgreSQL hybrid search is unavailable because pgvector schema support is missing "
                    "(for example notes.embedding_vector, notes.content_tsv, note_interactions, "
                    "search_queries.query_embedding_vector, or the hybrid_search SQL function)"
                )
            raise
        logger.info("Retrieved %d rows from hybrid_search", len(rows))

        # 4. Serialise rows
        results: List[Dict[str, Any]] = []
        for row in rows:
            results.append({
                "note_id": str(row[0]),
                "title": row[1],
                "content": row[2],
                "note_type": row[3],
                "workspace_id": str(row[4]),
                "user_id": str(row[5]),
                "created_at": row[6].isoformat() if row[6] else None,
                "updated_at": row[7].isoformat() if row[7] else None,
                "embedding_similarity": float(row[8]) if row[8] is not None else 0.0,
                "text_score": float(row[9]) if row[9] is not None else 0.0,
                "interaction_score": float(row[10]) if row[10] is not None else 0.0,
                "final_score": float(row[11]) if row[11] is not None else 0.0,
                "highlight": row[12] or "",
            })

        # 5. Enforce similarity_threshold in Python — catches cases where the
        #    SQL function lacks its own threshold guard (belt-and-suspenders).
        before = len(results)
        results = [r for r in results if r["embedding_similarity"] >= similarity_threshold]
        dropped = before - len(results)
        if dropped:
            logger.debug(
                "Dropped %d result(s) below similarity_threshold=%.2f",
                dropped,
                similarity_threshold,
            )

        return results

    async def vector_search_only(
        self,
        db: AsyncSession,
        query: str,
        workspace_id: UUID,
        limit: int = 10,
        similarity_threshold: float = 0.6,
    ) -> List[Dict[str, Any]]:
        """Pure vector (semantic) similarity search.

        Args:
            db: Async database session.
            query: Search query text.
            workspace_id: Workspace to search within.
            limit: Maximum results.
            similarity_threshold: Minimum similarity score (0–1).

        Returns:
            List of result dicts sorted by ``similarity`` descending.

        Raises:
            EmbeddingValidationError: If the query embedding fails validation.
        """
        query = query.strip()
        if not query:
            logger.warning("vector_search_only called with empty query — returning []")
            return []

        log_tag = query[:50]
        query_embedding = self.embedding_service.embed_query(query)
        self._validate_embedding(query_embedding, context=log_tag)

        dim = self.embedding_service.dimension
        embedding_str = self._embedding_to_pg(query_embedding)

        sql = text(f"""
            SELECT note_id, title, content, similarity
            FROM vector_search_only(
                :embedding::vector({dim}),
                :workspace_id,
                :limit,
                :threshold
            );
        """)

        result = await db.execute(sql, {
            "embedding": embedding_str,
            "workspace_id": workspace_id,
            "limit": limit,
            "threshold": similarity_threshold,
        })

        rows = result.fetchall()
        logger.info("vector_search_only: %d results", len(rows))

        return [
            {
                "note_id": str(row[0]),
                "title": row[1],
                "content": row[2],
                "similarity": float(row[3]),
            }
            for row in rows
        ]

    async def text_search_only(
        self,
        db: AsyncSession,
        query: str,
        workspace_id: UUID,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Full-text (keyword) search only.

        Args:
            db: Async database session.
            query: Search query text.
            workspace_id: Workspace to search within.
            limit: Maximum results.

        Returns:
            List of result dicts sorted by ``text_rank`` descending.
        """
        query = query.strip()
        if not query:
            logger.warning("text_search_only called with empty query — returning []")
            return []

        sql = text("""
            SELECT note_id, title, content, text_rank
            FROM text_search_only(
                :query,
                :workspace_id,
                :limit
            );
        """)

        result = await db.execute(sql, {
            "query": query,
            "workspace_id": workspace_id,
            "limit": limit,
        })

        rows = result.fetchall()
        logger.info("text_search_only: %d results", len(rows))

        return [
            {
                "note_id": str(row[0]),
                "title": row[1],
                "content": row[2],
                "text_rank": float(row[3]),
            }
            for row in rows
        ]

    async def get_interaction_stats(
        self,
        db: AsyncSession,
        workspace_id: UUID,
    ) -> List[Dict[str, Any]]:
        """Retrieve per-note interaction statistics.

        Args:
            db: Async database session.
            workspace_id: Workspace to query.

        Returns:
            List of interaction-stat dicts ordered by ``interaction_count`` desc.
        """
        sql = text("""
            SELECT note_id, interaction_count, last_interaction, interaction_types
            FROM get_interaction_stats(:workspace_id)
            ORDER BY interaction_count DESC;
        """)

        result = await db.execute(sql, {"workspace_id": workspace_id})
        rows = result.fetchall()
        logger.info("get_interaction_stats: %d notes", len(rows))

        return [
            {
                "note_id": str(row[0]),
                "interaction_count": int(row[1]),
                "last_interaction": row[2].isoformat() if row[2] else None,
                "interaction_types": list(row[3]) if row[3] else [],
            }
            for row in rows
        ]


# ---------------------------------------------------------------------------
# Singleton factory
# ---------------------------------------------------------------------------

_service: Optional[PostgreSQLHybridSearchService] = None


def get_postgresql_search_service() -> PostgreSQLHybridSearchService:
    """Return (or lazily create) the global search-service singleton."""
    global _service
    if _service is None:
        _service = PostgreSQLHybridSearchService()
    return _service
