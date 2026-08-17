import json
import logging
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import SearchLog

logger = logging.getLogger(__name__)

_SEARCH_LOGS_EXTENDED_CACHE_KEY = "search_logs_supports_extended_feedback_context"
_SEARCH_FEEDBACK_TABLE_CACHE_KEY = "search_feedback_table_available"


def _db_info(db: AsyncSession) -> Dict[str, Any]:
    info = getattr(db, "info", None)
    if info is None:
        info = {}
        setattr(db, "info", info)
    return info


async def search_logs_support_extended_context(db: AsyncSession) -> bool:
    info = _db_info(db)
    cached = info.get(_SEARCH_LOGS_EXTENDED_CACHE_KEY)
    if cached is not None:
        return bool(cached)

    try:
        rows = await db.execute(
            text(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'search_logs'
                  AND column_name IN ('result_snapshot', 'retrieval_metadata')
                """
            )
        )
        available = {str(row[0]) for row in rows.fetchall()}
        supported = {"result_snapshot", "retrieval_metadata"}.issubset(available)
    except Exception as exc:
        logger.warning("Unable to inspect search_logs schema; assuming legacy shape: %s", exc)
        supported = False

    info[_SEARCH_LOGS_EXTENDED_CACHE_KEY] = supported
    return supported


async def search_feedback_table_available(db: AsyncSession) -> bool:
    info = _db_info(db)
    cached = info.get(_SEARCH_FEEDBACK_TABLE_CACHE_KEY)
    if cached is not None:
        return bool(cached)

    try:
        row = (
            await db.execute(
                text(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM information_schema.tables
                        WHERE table_schema = current_schema()
                          AND table_name = 'search_feedback'
                    )
                    """
                )
            )
        ).first()
        available = bool(row[0]) if row else False
    except Exception as exc:
        logger.warning("Unable to inspect search_feedback schema availability: %s", exc)
        available = False

    info[_SEARCH_FEEDBACK_TABLE_CACHE_KEY] = available
    return available


async def load_search_log(
    db: AsyncSession,
    *,
    search_log_id: UUID,
    workspace_id: UUID,
) -> Optional[Any]:
    if await search_logs_support_extended_context(db):
        return (
            await db.execute(
                select(SearchLog).where(
                    SearchLog.id == search_log_id,
                    SearchLog.workspace_id == workspace_id,
                )
            )
        ).scalar_one_or_none()

    row = (
        await db.execute(
            text(
                """
                SELECT
                    id,
                    workspace_id,
                    user_id,
                    query_text,
                    result_chunk_ids,
                    result_count,
                    clicked_count,
                    clicked_chunk_ids,
                    search_duration_ms,
                    created_at,
                    updated_at
                FROM search_logs
                WHERE id = :search_log_id
                  AND workspace_id = :workspace_id
                """
            ),
            {
                "search_log_id": search_log_id,
                "workspace_id": workspace_id,
            },
        )
    ).first()
    if row is None:
        return None

    mapping = getattr(row, "_mapping", None)
    data = dict(mapping) if mapping is not None else {}
    data.setdefault("result_snapshot", [])
    data.setdefault("retrieval_metadata", {})
    return SimpleNamespace(**data)


async def insert_search_log(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    query_text: str,
    result_chunk_ids: List[str],
    result_count: int,
    result_snapshot: Optional[List[Dict[str, Any]]] = None,
    clicked_count: int = 0,
    clicked_chunk_ids: Optional[List[str]] = None,
    search_duration_ms: Optional[int] = None,
    retrieval_metadata: Optional[Dict[str, Any]] = None,
    created_at: Optional[datetime] = None,
) -> Any:
    created_at = created_at or datetime.now(timezone.utc)
    search_log_id = uuid4()
    normalized_result_chunk_ids = [str(item).strip() for item in (result_chunk_ids or []) if str(item).strip()]
    normalized_clicked_chunk_ids = [str(item).strip() for item in (clicked_chunk_ids or []) if str(item).strip()]
    normalized_result_snapshot = list(result_snapshot or [])
    normalized_retrieval_metadata = dict(retrieval_metadata or {})

    if await search_logs_support_extended_context(db):
        search_log = SearchLog(
            id=search_log_id,
            workspace_id=workspace_id,
            user_id=user_id,
            query_text=query_text,
            result_chunk_ids=normalized_result_chunk_ids,
            result_count=result_count,
            result_snapshot=normalized_result_snapshot,
            clicked_count=clicked_count,
            clicked_chunk_ids=normalized_clicked_chunk_ids,
            search_duration_ms=search_duration_ms,
            retrieval_metadata=normalized_retrieval_metadata,
            created_at=created_at,
        )
        db.add(search_log)
        await db.flush()
        return search_log

    await db.execute(
        text(
            """
            INSERT INTO search_logs (
                id,
                workspace_id,
                user_id,
                query_text,
                result_chunk_ids,
                result_count,
                clicked_count,
                clicked_chunk_ids,
                search_duration_ms,
                created_at,
                updated_at
            )
            VALUES (
                :id,
                :workspace_id,
                :user_id,
                :query_text,
                CAST(:result_chunk_ids AS jsonb),
                :result_count,
                :clicked_count,
                CAST(:clicked_chunk_ids AS jsonb),
                :search_duration_ms,
                :created_at,
                NULL
            )
            """
        ),
        {
            "id": search_log_id,
            "workspace_id": workspace_id,
            "user_id": user_id,
            "query_text": query_text,
            "result_chunk_ids": json.dumps(normalized_result_chunk_ids),
            "result_count": result_count,
            "clicked_count": clicked_count,
            "clicked_chunk_ids": json.dumps(normalized_clicked_chunk_ids),
            "search_duration_ms": search_duration_ms,
            "created_at": created_at,
        },
    )

    return SimpleNamespace(
        id=search_log_id,
        workspace_id=workspace_id,
        user_id=user_id,
        query_text=query_text,
        result_chunk_ids=normalized_result_chunk_ids,
        result_count=result_count,
        result_snapshot=normalized_result_snapshot,
        clicked_count=clicked_count,
        clicked_chunk_ids=normalized_clicked_chunk_ids,
        search_duration_ms=search_duration_ms,
        retrieval_metadata=normalized_retrieval_metadata,
        created_at=created_at,
        updated_at=None,
    )
