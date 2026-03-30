"""Semantic search API endpoints."""
from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Annotated, Any, Dict, List, Optional
from uuid import UUID

import redis.asyncio as redis
from fastapi import APIRouter, Depends, HTTPException, Query as QueryParam, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.auth import WorkspaceContext, get_current_active_user, get_workspace_context
from app.database.models import User as DBUser
from app.database.session import get_db
from app.services.semantic_search import SemanticSearchService, get_semantic_search_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/search", tags=["search"])

# ---------------------------------------------------------------------------
# Redis helper — lazy singleton with reconnect guard
# ---------------------------------------------------------------------------

_redis_client: Optional[redis.Redis] = None


async def get_redis() -> Optional[redis.Redis]:
    """Return a shared Redis client, or *None* if Redis is unavailable.

    Using *None* as the sentinel lets every caller degrade gracefully
    (skip cache) instead of crashing the request.
    """
    global _redis_client
    if _redis_client is None:
        try:
            client = redis.from_url(settings.REDIS_URL, decode_responses=True)
            # Verify the connection is actually alive before storing it.
            await client.ping()
            _redis_client = client
        except Exception as exc:
            logger.warning("Redis unavailable — search caching disabled: %s", exc)
            return None

    # Cheap liveness check so a Redis restart doesn't leave us with a
    # permanently broken singleton.
    try:
        await _redis_client.ping()
    except Exception:
        _redis_client = None
        logger.warning("Redis connection lost — resetting client.")
        return None

    return _redis_client


# ---------------------------------------------------------------------------
# Pydantic request / response models
#
# NOTE — Pydantic v2 raises UserWarning for fields whose names start with
# "model_" because that prefix is reserved for BaseModel internals.
# Suppress this per-model with:
#
#     model_config = ConfigDict(protected_namespaces=())
#
# If you see the same warning from *app.database.models* (e.g. on
# `model_used`, `model_dimension`, `model_info`), add the same config dict
# to those SQLAlchemy-mapped Pydantic schemas.
# ---------------------------------------------------------------------------


class SearchFilter(BaseModel):
    """Advanced search filters."""

    tags: Optional[List[str]] = Field(None, description="Filter by tags")
    date_from: Optional[str] = Field(None, description="ISO date lower bound")
    date_to: Optional[str] = Field(None, description="ISO date upper bound")
    source_type: Optional[str] = Field(
        None,
        description="pdf | upload | slack | notion | gdrive | github | email | web_clip",
    )


class SemanticSearchRequest(BaseModel):
    """Body model for POST-style callers (kept for symmetry; GET is primary)."""

    query: str = Field(..., min_length=1, max_length=2000)
    limit: int = Field(default=10, ge=1, le=50)
    filters: Optional[SearchFilter] = None


class SemanticSearchResultPayload(BaseModel):
    """Individual search result payload."""

    # Fields that start with "model_" require the namespace override.
    model_config = ConfigDict(protected_namespaces=())

    chunk_id: str
    document_id: str
    document_title: str
    content: str
    source_type: str
    chunk_index: int
    created_at: str
    interaction_count: int = 0
    similarity_score: float = Field(..., ge=0, le=1)
    recency_score: float = Field(..., ge=0, le=1)
    usage_score: float = Field(..., ge=0, le=1)
    final_score: float = Field(..., ge=0, le=1)
    highlight: str


class SemanticSearchResponse(BaseModel):
    """Semantic search response envelope."""

    query: str
    results: List[SemanticSearchResultPayload]
    total: int
    took_ms: int
    cached: bool = False


class TrendingEntry(BaseModel):
    """Single trending query entry."""

    query: str
    search_count: int
    unique_users: int


class TrendingResponse(BaseModel):
    """Trending searches response."""

    trending: List[TrendingEntry]
    period: str
    workspace_id: str


class ClickLogResponse(BaseModel):
    """Response for the log-click endpoint."""

    status: str
    clicked_count: int


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/semantic",
    response_model=SemanticSearchResponse,
    summary="Semantic search with hybrid scoring",
    description=(
        "Search documents using semantic similarity. "
        "Composite score = 60 % similarity + 25 % recency + 15 % usage."
    ),
)
async def semantic_search(
    request: Request,
    q: str = QueryParam(..., min_length=1, max_length=2000, description="Search query"),
    limit: int = QueryParam(10, ge=1, le=50, description="Max results to return"),
    tags: Optional[str] = QueryParam(None, description="Comma-separated tags"),
    date_from: Optional[str] = QueryParam(None, description="ISO date lower bound"),
    date_to: Optional[str] = QueryParam(None, description="ISO date upper bound"),
    source_type: Optional[str] = QueryParam(None, description="Source type filter"),
    # FIX: Annotated[..., Depends(...)] tells FastAPI this is a *dependency*,
    # not a response field.  Without this, FastAPI tries to validate DBUser
    # (a SQLAlchemy model) as a Pydantic schema and crashes at startup.
    current_user: Annotated[DBUser, Depends(get_current_active_user)] = None,
    workspace_ctx: Annotated[WorkspaceContext, Depends(get_workspace_context)] = None,
    db: Annotated[AsyncSession, Depends(get_db)] = None,
) -> SemanticSearchResponse:
    """Perform semantic search with hybrid composite scoring.

    **Scoring formula**
    - `final_score = 0.60 × similarity + 0.25 × recency + 0.15 × usage`
    - *recency*: exponential decay with 4-week half-life
    - *usage*: log-normalised interaction count

    **Examples**

    ```
    GET /search/semantic?q=machine+learning&limit=10
    GET /search/semantic?q=deep+learning&date_from=2024-01-01&source_type=pdf
    ```
    """
    start_time = time.time()
    workspace_id = workspace_ctx.workspace_id
    user_id = current_user.id

    # Build filter dict — only include keys that were actually provided.
    filters: Dict[str, Any] = {}
    if tags:
        filters["tags"] = [t.strip() for t in tags.split(",") if t.strip()]
    if date_from:
        filters["date_from"] = date_from
    if date_to:
        filters["date_to"] = date_to
    if source_type:
        filters["source_type"] = source_type

    # ── Cache lookup ──────────────────────────────────────────────────────
    cache_key = _build_cache_key(user_id, workspace_id, q, filters, limit)
    rc = await get_redis()

    if rc is not None:
        try:
            raw = await rc.get(cache_key)
            if raw:
                logger.info("Cache hit: query=%r workspace=%s", q, workspace_id)
                cached_data = json.loads(raw)
                cached_data["cached"] = True
                return SemanticSearchResponse(**cached_data)
        except Exception as cache_err:
            logger.warning("Cache read error (degrading gracefully): %s", cache_err)

    # ── Semantic search ───────────────────────────────────────────────────
    try:
        search_service: SemanticSearchService = get_semantic_search_service(db)
        results = await search_service.search(
            workspace_id=workspace_id,
            user_id=user_id,
            query=q,
            limit=limit,
            filters=filters or None,
            db=db,
        )
    except Exception as exc:
        took_ms = int((time.time() - start_time) * 1000)
        logger.error(
            "Semantic search failed: workspace=%s query=%r took_ms=%d",
            workspace_id, q, took_ms,
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail=f"Search failed: {exc}") from exc

    took_ms = int((time.time() - start_time) * 1000)

    # Validate each result dict through the Pydantic schema so any upstream
    # shape mismatch surfaces here with a clear error rather than silently
    # producing a malformed response.
    result_payloads = [
        SemanticSearchResultPayload(**r.to_dict()) for r in results
    ]

    response_data: Dict[str, Any] = {
        "query": q,
        "results": [r.model_dump() for r in result_payloads],
        "total": len(result_payloads),
        "took_ms": took_ms,
        "cached": False,
    }

    # ── Cache write ───────────────────────────────────────────────────────
    if rc is not None:
        try:
            await rc.setex(cache_key, 900, json.dumps(response_data))
        except Exception as cache_err:
            logger.warning("Cache write error (result still returned): %s", cache_err)

    logger.info(
        "Semantic search: workspace=%s user=%s query=%r results=%d took_ms=%d",
        workspace_id, user_id, q, len(result_payloads), took_ms,
    )

    return SemanticSearchResponse(**response_data)


@router.get(
    "/trending",
    response_model=TrendingResponse,
    summary="Trending searches in workspace",
    description="Most frequently searched queries in the past 7 days.",
)
async def trending_searches(
    workspace_ctx: Annotated[WorkspaceContext, Depends(get_workspace_context)] = None,
    current_user: Annotated[DBUser, Depends(get_current_active_user)] = None,
    db: Annotated[AsyncSession, Depends(get_db)] = None,
    limit: int = QueryParam(10, ge=1, le=50),
) -> TrendingResponse:
    """Return the most searched queries in this workspace over the last 7 days.

    **Note:** Requires a `search_logs` table with columns
    `query_text`, `workspace_id`, `user_id`, `created_at`.
    The underlying SQL uses PostgreSQL's `INTERVAL` syntax.
    """
    workspace_id = workspace_ctx.workspace_id

    try:
        # Raw SQL is intentionally kept here for performance; parameters are
        # bound — no injection risk.  INTERVAL is PostgreSQL-specific.
        stmt = text("""
            SELECT
                query_text,
                COUNT(*)                AS search_count,
                COUNT(DISTINCT user_id) AS unique_users
            FROM search_logs
            WHERE workspace_id = :workspace_id
              AND created_at > NOW() - INTERVAL '7 days'
            GROUP BY query_text
            ORDER BY search_count DESC
            LIMIT :limit
        """)

        result = await db.execute(
            stmt, {"workspace_id": str(workspace_id), "limit": limit}
        )
        rows = result.fetchall()

    except Exception as exc:
        logger.error(
            "Failed to fetch trending searches: workspace=%s", workspace_id, exc_info=True
        )
        raise HTTPException(status_code=500, detail="Failed to fetch trending searches") from exc

    trending = [
        TrendingEntry(query=row[0], search_count=row[1], unique_users=row[2])
        for row in rows
    ]

    return TrendingResponse(
        trending=trending,
        period="7 days",
        workspace_id=str(workspace_id),
    )


@router.post(
    "/log-click",
    response_model=ClickLogResponse,
    summary="Log a search result click",
    description="Track when a user clicks a search result for analytics.",
)
async def log_search_click(
    search_log_id: UUID,
    chunk_id: UUID,
    workspace_ctx: Annotated[WorkspaceContext, Depends(get_workspace_context)] = None,
    current_user: Annotated[DBUser, Depends(get_current_active_user)] = None,
    db: Annotated[AsyncSession, Depends(get_db)] = None,
) -> ClickLogResponse:
    """Record that a user clicked a specific chunk in a search result set."""
    from app.database.models import SearchLog

    try:
        stmt = select(SearchLog).where(
            SearchLog.id == search_log_id,
            SearchLog.workspace_id == workspace_ctx.workspace_id,
        )
        result = await db.execute(stmt)
        search_log = result.scalars().first()

        if search_log is None:
            raise HTTPException(status_code=404, detail="Search log not found")

        search_log.clicked_count += 1
        chunk_id_str = str(chunk_id)
        if chunk_id_str not in (search_log.clicked_chunk_ids or []):
            search_log.clicked_chunk_ids = (search_log.clicked_chunk_ids or []) + [chunk_id_str]

        db.add(search_log)
        await db.commit()

        logger.info(
            "Search click logged: log=%s chunk=%s user=%s",
            search_log_id, chunk_id, current_user.id,
        )

        return ClickLogResponse(status="logged", clicked_count=search_log.clicked_count)

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Error logging search click: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to log search click") from exc


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_cache_key(
    user_id: UUID,
    workspace_id: UUID,
    query: str,
    filters: Dict[str, Any],
    limit: int,
) -> str:
    """Return a short, deterministic Redis key for the given search parameters.

    The entire payload is hashed so the key length is always fixed regardless
    of query length or filter complexity, and special characters in the query
    cannot affect key structure.
    """
    payload = json.dumps(
        {
            "user_id": str(user_id),
            "workspace_id": str(workspace_id),
            "query": query,
            "filters": filters,
            "limit": limit,
        },
        sort_keys=True,
        default=str,
    )
    digest = hashlib.sha256(payload.encode()).hexdigest()
    return f"search:v1:{digest}"