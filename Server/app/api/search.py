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
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.auth import WorkspaceContext, get_current_active_user, get_workspace_context
from app.core.permissions import ensure_workspace_permission
from app.database.models import User as DBUser
from app.database.models import WorkspaceSection
from app.database.session import get_db, log_session_query_metrics
from app.services.semantic_search import SemanticSearchResult, get_semantic_search_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/search", tags=["search"])
_redis_client: Optional[redis.Redis] = None


async def get_redis() -> Optional[redis.Redis]:
    global _redis_client
    if _redis_client is None:
        try:
            client = redis.from_url(settings.REDIS_URL, decode_responses=True)
            await client.ping()
            _redis_client = client
        except Exception as exc:
            logger.warning("Redis unavailable, search caching disabled: %s", exc)
            return None
    try:
        await _redis_client.ping()
    except Exception:
        _redis_client = None
        return None
    return _redis_client


class SearchFilter(BaseModel):
    tags: Optional[List[str]] = Field(default=None)
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    source_type: Optional[str] = None


class SemanticSearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    limit: int = Field(default=10, ge=1, le=50)
    filters: Optional[SearchFilter] = None


class SemanticSearchResultPayload(BaseModel):
    model_config = ConfigDict(protected_namespaces=())
    chunk_id: str
    document_id: str
    document_title: str
    content: str
    source_kind: str
    tags: List[str] = Field(default_factory=list)
    source_type: str
    chunk_index: int
    token_count: int = 0
    context_before: Optional[str] = None
    context_after: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: str
    interaction_count: int = 0
    similarity_score: float = Field(..., ge=0, le=1)
    recency_score: float = Field(..., ge=0, le=1)
    usage_score: float = Field(..., ge=0, le=1)
    final_score: float = Field(..., ge=0, le=1)
    highlight: str


class SemanticSearchResponse(BaseModel):
    query: str
    results: List[SemanticSearchResultPayload]
    total: int
    took_ms: int
    cached: bool = False
    search_log_id: Optional[str] = None


class TrendingEntry(BaseModel):
    query: str
    search_count: int
    unique_users: int


class TrendingResponse(BaseModel):
    trending: List[TrendingEntry]
    period: str
    workspace_id: str


class ClickLogResponse(BaseModel):
    status: str
    clicked_count: int


def _build_cache_key(user_id: UUID, workspace_id: UUID, query: str, filters: Dict[str, Any], limit: int) -> str:
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
    return f"search:v2:{hashlib.sha256(payload.encode()).hexdigest()}"


async def _run_semantic_search(
    query: str,
    limit: int,
    filters: Dict[str, Any],
    current_user: DBUser,
    workspace_ctx: WorkspaceContext,
    db: AsyncSession,
) -> SemanticSearchResponse:
    start_time = time.time()
    rc = await get_redis()
    cache_key = _build_cache_key(current_user.id, workspace_ctx.workspace_id, query, filters, limit)

    if rc is not None:
        try:
            raw = await rc.get(cache_key)
            if raw:
                data = json.loads(raw)
                service = get_semantic_search_service()
                cached_results = [SemanticSearchResult.from_dict(item) for item in data.get("results", [])]
                search_log_id = await service.log_search_execution(
                    db=db,
                    user_id=current_user.id,
                    workspace_id=workspace_ctx.workspace_id,
                    query=query,
                    results=cached_results,
                    search_duration_ms=0,
                )
                data["cached"] = True
                data["search_log_id"] = search_log_id
                log_session_query_metrics(db, "search.semantic.cached")
                return SemanticSearchResponse(**data)
        except Exception as exc:
            logger.warning("Search cache read error: %s", exc)

    try:
        service = get_semantic_search_service()
        execution = await service.search(
            workspace_id=workspace_ctx.workspace_id,
            user_id=current_user.id,
            query=query,
            limit=limit,
            filters=filters or None,
            db=db,
        )
    except Exception as exc:
        logger.error("Semantic search failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Search failed: {exc}") from exc

    payloads = [SemanticSearchResultPayload(**result.to_dict()) for result in execution.results]
    response_data = {
        "query": query,
        "results": [payload.model_dump() for payload in payloads],
        "total": len(payloads),
        "took_ms": int((time.time() - start_time) * 1000),
        "cached": False,
        "search_log_id": execution.search_log_id,
    }

    if rc is not None:
        try:
            cacheable_response = dict(response_data)
            cacheable_response.pop("search_log_id", None)
            await rc.setex(cache_key, 900, json.dumps(cacheable_response))
        except Exception as exc:
            logger.warning("Search cache write error: %s", exc)

    log_session_query_metrics(db, "search.semantic")
    return SemanticSearchResponse(**response_data)


@router.get("/semantic", response_model=SemanticSearchResponse)
async def semantic_search(
    request: Request,
    q: str = QueryParam(..., min_length=1, max_length=2000),
    limit: int = QueryParam(10, ge=1, le=50),
    tags: Optional[str] = QueryParam(None),
    date_from: Optional[str] = QueryParam(None),
    date_to: Optional[str] = QueryParam(None),
    source_type: Optional[str] = QueryParam(None),
    current_user: Annotated[DBUser, Depends(get_current_active_user)] = None,
    workspace_ctx: Annotated[WorkspaceContext, Depends(get_workspace_context)] = None,
    db: Annotated[AsyncSession, Depends(get_db)] = None,
) -> SemanticSearchResponse:
    await ensure_workspace_permission(
        workspace_id=workspace_ctx.workspace_id,
        user=current_user,
        db=db,
        section=WorkspaceSection.SEARCH,
        action="create",
        context=workspace_ctx,
    )
    filters: Dict[str, Any] = {}
    if tags:
        filters["tags"] = [item.strip() for item in tags.split(",") if item.strip()]
    if date_from:
        filters["date_from"] = date_from
    if date_to:
        filters["date_to"] = date_to
    if source_type:
        filters["source_type"] = source_type
    return await _run_semantic_search(q, limit, filters, current_user, workspace_ctx, db)


@router.post("/semantic", response_model=SemanticSearchResponse)
async def semantic_search_post(
    payload: SemanticSearchRequest,
    current_user: Annotated[DBUser, Depends(get_current_active_user)] = None,
    workspace_ctx: Annotated[WorkspaceContext, Depends(get_workspace_context)] = None,
    db: Annotated[AsyncSession, Depends(get_db)] = None,
) -> SemanticSearchResponse:
    await ensure_workspace_permission(
        workspace_id=workspace_ctx.workspace_id,
        user=current_user,
        db=db,
        section=WorkspaceSection.SEARCH,
        action="create",
        context=workspace_ctx,
    )
    filters = payload.filters.model_dump(exclude_none=True) if payload.filters else {}
    return await _run_semantic_search(payload.query, payload.limit, filters, current_user, workspace_ctx, db)


@router.get("/trending", response_model=TrendingResponse)
async def trending_searches(
    workspace_ctx: Annotated[WorkspaceContext, Depends(get_workspace_context)] = None,
    current_user: Annotated[DBUser, Depends(get_current_active_user)] = None,
    db: Annotated[AsyncSession, Depends(get_db)] = None,
    limit: int = QueryParam(10, ge=1, le=50),
) -> TrendingResponse:
    await ensure_workspace_permission(
        workspace_id=workspace_ctx.workspace_id,
        user=current_user,
        db=db,
        section=WorkspaceSection.SEARCH,
        action="view",
        context=workspace_ctx,
    )
    try:
        stmt = text(
            """
            SELECT query_text, COUNT(*) AS search_count, COUNT(DISTINCT user_id) AS unique_users
            FROM search_logs
            WHERE workspace_id = :workspace_id
              AND created_at > NOW() - INTERVAL '7 days'
            GROUP BY query_text
            ORDER BY search_count DESC
            LIMIT :limit
            """
        )
        rows = (await db.execute(stmt, {"workspace_id": str(workspace_ctx.workspace_id), "limit": limit})).fetchall()
    except Exception as exc:
        logger.error("Failed to fetch trending searches: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch trending searches") from exc

    return TrendingResponse(
        trending=[TrendingEntry(query=row[0], search_count=row[1], unique_users=row[2]) for row in rows],
        period="7 days",
        workspace_id=str(workspace_ctx.workspace_id),
    )


@router.post("/log-click", response_model=ClickLogResponse)
async def log_search_click(
    search_log_id: UUID,
    chunk_id: UUID,
    workspace_ctx: Annotated[WorkspaceContext, Depends(get_workspace_context)] = None,
    current_user: Annotated[DBUser, Depends(get_current_active_user)] = None,
    db: Annotated[AsyncSession, Depends(get_db)] = None,
) -> ClickLogResponse:
    await ensure_workspace_permission(
        workspace_id=workspace_ctx.workspace_id,
        user=current_user,
        db=db,
        section=WorkspaceSection.SEARCH,
        action="create",
        context=workspace_ctx,
    )

    try:
        chunk_id_str = str(chunk_id)
        chunk_json = json.dumps([chunk_id_str])

        result = await db.execute(
            text(
                """
                WITH target AS (
                    SELECT
                        id,
                        COALESCE(clicked_chunk_ids, '[]'::jsonb) AS clicked_chunk_ids,
                        COALESCE(clicked_chunk_ids, '[]'::jsonb) @> CAST(:chunk_json AS jsonb) AS already_clicked
                    FROM search_logs
                    WHERE id = :search_log_id
                      AND workspace_id = :workspace_id
                    FOR UPDATE
                ),
                updated AS (
                    UPDATE search_logs AS search_logs
                    SET clicked_chunk_ids = CASE
                            WHEN target.already_clicked
                                THEN target.clicked_chunk_ids
                            ELSE target.clicked_chunk_ids || CAST(:chunk_json AS jsonb)
                        END,
                        clicked_count = CASE
                            WHEN target.already_clicked
                                THEN jsonb_array_length(target.clicked_chunk_ids)
                            ELSE jsonb_array_length(target.clicked_chunk_ids || CAST(:chunk_json AS jsonb))
                        END,
                        updated_at = NOW()
                    FROM target
                    WHERE search_logs.id = target.id
                    RETURNING
                        search_logs.clicked_count AS clicked_count,
                        NOT target.already_clicked AS click_added
                )
                SELECT clicked_count, click_added
                FROM updated
                """
            ),
            {
                "search_log_id": search_log_id,
                "workspace_id": workspace_ctx.workspace_id,
                "chunk_json": chunk_json,
            },
        )
        row = result.first()
        if row is None:
            raise HTTPException(status_code=404, detail="Search log not found")

        clicked_count = int(row[0] or 0)
        click_added = bool(row[1])

        if click_added:
            await db.execute(
                text(
                    """
                    INSERT INTO note_interactions (note_id, user_id, workspace_id, interaction_type, context)
                    SELECT
                        notes.id,
                        :user_id,
                        :workspace_id,
                        :interaction_type,
                        CAST(:context AS jsonb)
                    FROM notes
                    WHERE notes.id = :note_id
                      AND notes.workspace_id = :workspace_id
                    """
                ),
                {
                    "note_id": chunk_id,
                    "user_id": current_user.id,
                    "workspace_id": workspace_ctx.workspace_id,
                    "interaction_type": "search_click",
                    "context": json.dumps({"search_log_id": str(search_log_id)}),
                },
            )

        await db.commit()
        log_session_query_metrics(db, "search.log_click")
        return ClickLogResponse(status="logged", clicked_count=clicked_count)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Error logging search click: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to log search click") from exc
