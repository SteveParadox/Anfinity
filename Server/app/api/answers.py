"""STEP 4: Answer Generation API endpoints. STEP 7: Output Structure. STEP 8: Feedback Loop."""

from datetime import datetime, timezone
import logging
import time
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query as QueryParam
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.auth import WorkspaceContext, get_current_active_user, get_workspace_context
from app.core.audit import AuditAction, EntityType, AuditEventPayload, stage_audit_event
from app.database.models import Answer, Chunk, Document, Query, SearchFeedback, SearchLog, User as DBUser
from app.database.session import get_db
from app.ingestion.source_locations import enrich_citation_metadata, source_location_payload
from app.services.answer_generator import GeneratedAnswer, RetrievedChunk, get_answer_generator
from app.services.feedback_handler import get_feedback_handler
from app.services.search_log_storage import (
    insert_search_log,
    load_search_log,
    search_feedback_table_available,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/answers", tags=["Answers"])

NO_EVIDENCE_MESSAGE = "I couldn't find enough reliable information in your documents to answer this question."
ALLOWED_FEEDBACK_TYPES = {
    "correct",
    "partially_correct",
    "irrelevant",
    "missing_expected_result",
    "wrong_result",
    "bad_highlight",
    "low_quality_answer",
    "hallucinated_or_unsupported",
    "other",
}
ALLOWED_REASON_CODES = {
    "result_unrelated",
    "expected_note_missing",
    "highlight_wrong",
    "wrong_source_used",
    "answer_unsupported",
    "similarity_misleading",
    "result_outdated",
    "duplicate_result",
    "other",
}
ALLOWED_TARGET_KINDS = {"answer", "result"}
FEEDBACK_TYPES_REQUIRING_REASON = {
    "partially_correct",
    "irrelevant",
    "missing_expected_result",
    "wrong_result",
    "bad_highlight",
    "low_quality_answer",
    "hallucinated_or_unsupported",
}
MAX_FEEDBACK_RESULT_IDS = 100
MAX_FEEDBACK_SNAPSHOT_ITEMS = 100
MAX_FEEDBACK_ID_LENGTH = 255
MAX_FEEDBACK_SNAPSHOT_TEXT = 500
RESULT_SNAPSHOT_ALLOWED_KEYS = {
    "rank",
    "chunk_id",
    "document_id",
    "source_kind",
    "source_type",
    "chunk_index",
    "similarity_score",
    "final_score",
    "confidence",
    "confidence_score",
    "highlights",
    "matched_chunks",
}


class CitationPayload(BaseModel):
    chunk_id: str
    document_id: str
    document_title: str
    chunk_index: int
    similarity: float
    text_snippet: str
    citation_label: Optional[str] = None
    source_location: Dict[str, Any] = Field(default_factory=dict)


class QualityCheckInfo(BaseModel):
    high_quality_chunks: int
    low_quality_chunks: int
    has_conflicts: bool
    conflict_count: int
    diversity_score: float
    unique_documents: int
    issues_found: int


class AnswerGenerationRequest(BaseModel):
    workspace_id: UUID
    query: str = Field(..., min_length=1, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=20)
    similarity_threshold: float = Field(default=0.45, ge=0.0, le=1.0)
    include_citations: bool = True
    citation_style: str = Field(default="inline", pattern="^(inline|footnote)$")
    model: Optional[str] = None
    min_unique_documents: int = Field(default=1, ge=1, le=10)
    detect_conflicts: bool = True


class AnswerGenerationResponse(BaseModel):
    answer_id: str
    query: str
    answer_text: str
    citations: List[CitationPayload]
    confidence_score: float
    model_used: str
    tokens_used: int
    generation_time_ms: float
    average_similarity: float
    unique_documents: int
    chunks_retrieved: int
    quality_check: Optional[QualityCheckInfo] = None
    metadata: Dict[str, Any]
    cross_doc_agreement_score: Optional[float] = None
    top_k_used: Optional[int] = None


class QueryHistoryItem(BaseModel):
    query_id: str
    query_text: str
    answer_id: str
    answer_text: str
    confidence_score: float
    created_at: str
    model_used: str


class QueryHistoryResponse(BaseModel):
    workspace_id: str
    total_queries: int
    queries: List[QueryHistoryItem]


class SourceReference(BaseModel):
    document_id: str
    chunk_index: int
    similarity: float


class Step7AnswerResponse(BaseModel):
    answer: str
    confidence: float
    sources: List[SourceReference]


class AnswerFeedbackRequest(BaseModel):
    answer_id: UUID
    feedback_type: Optional[str] = Field(default=None, max_length=64)
    status: Optional[str] = Field(default=None, pattern="^(verified|rejected)$")
    target_kind: str = Field(default="answer", pattern="^(answer|result)$")
    target_result_id: Optional[str] = Field(default=None, max_length=255)
    search_log_id: Optional[UUID] = None
    query_id: Optional[UUID] = None
    query_text: Optional[str] = Field(default=None, min_length=1, max_length=2000)
    rating_value: Optional[int] = Field(default=None, ge=-1, le=1)
    reason_code: Optional[str] = Field(default=None, max_length=64)
    comment: Optional[str] = Field(None, max_length=1000)
    result_ids: List[str] = Field(default_factory=list, max_length=MAX_FEEDBACK_RESULT_IDS)
    result_snapshot: List[Dict[str, Any]] = Field(default_factory=list, max_length=MAX_FEEDBACK_SNAPSHOT_ITEMS)
    answer_snapshot: Dict[str, Any] = Field(default_factory=dict)
    retrieval_diagnostics: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("feedback_type", "target_kind", "target_result_id", "query_text", "reason_code", "comment", mode="before")
    @classmethod
    def _strip_optional_text(cls, value: Any) -> Any:
        if value is None:
            return value
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("result_ids", mode="before")
    @classmethod
    def _normalize_result_ids(cls, value: Any) -> List[str]:
        return _normalize_result_ids(value or [])


class ChunkWeightUpdate(BaseModel):
    chunk_id: str
    document_id: str
    old_weight: float
    new_weight: float
    accuracy: float
    positive_count: int
    negative_count: int
    total_uses: int


class AnswerFeedbackResponse(BaseModel):
    feedback_id: str
    answer_id: str
    feedback_type: str
    target_kind: str
    target_result_id: Optional[str]
    reason_code: Optional[str]
    comment: Optional[str]
    updated_existing: bool
    feedback_status: str
    search_log_id: Optional[str]
    context_key: str
    scope_key: str
    chunks_updated: List[ChunkWeightUpdate]
    confidence_change: float


class ChunkCredibilityScore(BaseModel):
    chunk_id: str
    document_id: str
    credibility_score: float
    accuracy_rate: float
    positive_feedback: int
    negative_feedback: int
    total_uses: int
    updated_at: Optional[str]


class ModelEvaluationMetrics(BaseModel):
    total_feedback: int
    approved_count: int
    rejected_count: int
    approval_rate: float
    rejection_rate: float
    average_rating: float


class CurrentAnswerFeedbackResponse(BaseModel):
    exists: bool
    feedback_id: Optional[str] = None
    feedback_type: Optional[str] = None
    target_kind: Optional[str] = None
    target_result_id: Optional[str] = None
    reason_code: Optional[str] = None
    comment: Optional[str] = None
    rating_value: Optional[int] = None
    search_log_id: Optional[str] = None
    context_key: Optional[str] = None
    scope_key: Optional[str] = None


class CurrentAnswerFeedbackItem(BaseModel):
    feedback_id: str
    feedback_type: str
    target_kind: str
    target_result_id: Optional[str] = None
    reason_code: Optional[str] = None
    comment: Optional[str] = None
    rating_value: Optional[int] = None
    search_log_id: Optional[str] = None
    context_key: str
    scope_key: str


class FeedbackReviewItem(BaseModel):
    feedback_id: str
    feedback_type: str
    target_kind: str
    target_result_id: Optional[str]
    reason_code: Optional[str]
    query_text: str
    answer_id: Optional[str]
    search_log_id: Optional[str]
    user_id: str
    created_at: Optional[str]


async def _verify_workspace_access(
    workspace_id: UUID,
    current_user: DBUser,
    db: AsyncSession,
) -> WorkspaceContext:
    return await get_workspace_context(workspace_id, current_user, db)


async def _has_feedback_storage(db: AsyncSession) -> bool:
    return await search_feedback_table_available(db)


def _normalize_feedback_type(request: AnswerFeedbackRequest) -> str:
    feedback_type = (request.feedback_type or "").strip().lower()
    if not feedback_type and request.status:
        feedback_type = "correct" if request.status == "verified" else "wrong_result"
    if feedback_type not in ALLOWED_FEEDBACK_TYPES:
        raise HTTPException(status_code=422, detail="Invalid feedback_type")
    return feedback_type


def _normalize_feedback_id(value: Any) -> str:
    return str(value or "").strip()[:MAX_FEEDBACK_ID_LENGTH]


def _normalize_result_ids(values: Any) -> List[str]:
    normalized: List[str] = []
    seen: set[str] = set()
    for raw_value in list(values or [])[:MAX_FEEDBACK_RESULT_IDS]:
        value = _normalize_feedback_id(raw_value)
        if not value or value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    return normalized


def _compact_snapshot_value(value: Any, *, depth: int = 0) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:MAX_FEEDBACK_SNAPSHOT_TEXT]
    if depth >= 2:
        return str(value)[:MAX_FEEDBACK_SNAPSHOT_TEXT]
    if isinstance(value, list):
        return [_compact_snapshot_value(item, depth=depth + 1) for item in value[:8]]
    if isinstance(value, dict):
        return {
            str(key)[:64]: _compact_snapshot_value(item, depth=depth + 1)
            for key, item in list(value.items())[:16]
        }
    return str(value)[:MAX_FEEDBACK_SNAPSHOT_TEXT]


def _normalize_result_snapshot(snapshot: Any) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for raw_item in list(snapshot or [])[:MAX_FEEDBACK_SNAPSHOT_ITEMS]:
        if not isinstance(raw_item, dict):
            continue
        item: Dict[str, Any] = {}
        for key in RESULT_SNAPSHOT_ALLOWED_KEYS:
            if key not in raw_item:
                continue
            value = raw_item.get(key)
            if key in {"chunk_id", "document_id", "source_kind", "source_type", "confidence"}:
                item[key] = _normalize_feedback_id(value)
            elif key in {"rank", "chunk_index"}:
                try:
                    item[key] = int(value)
                except (TypeError, ValueError):
                    continue
            elif key in {"similarity_score", "final_score", "confidence_score"}:
                try:
                    item[key] = max(0.0, min(float(value), 1.0))
                except (TypeError, ValueError):
                    continue
            else:
                item[key] = _compact_snapshot_value(value)
        if _normalize_feedback_id(item.get("chunk_id")):
            normalized.append(item)
    return normalized


def _snapshot_result_ids(result_snapshot: List[Dict[str, Any]]) -> set[str]:
    return {
        _normalize_feedback_id(item.get("chunk_id"))
        for item in result_snapshot
        if _normalize_feedback_id(item.get("chunk_id"))
    }


def _snapshot_item_for_result(result_snapshot: List[Dict[str, Any]], target_result_id: str) -> Optional[Dict[str, Any]]:
    for item in result_snapshot:
        if _normalize_feedback_id(item.get("chunk_id")) == target_result_id:
            return item
    return None


def _source_pairs_for_result_feedback(
    *,
    target_result_id: Optional[str],
    result_snapshot: List[Dict[str, Any]],
) -> List[Dict[str, str]]:
    normalized_target = _normalize_feedback_id(target_result_id)
    if not normalized_target:
        return []
    item = _snapshot_item_for_result(result_snapshot, normalized_target)
    if not item:
        return []
    document_id = _normalize_feedback_id(item.get("document_id"))
    if not document_id:
        return []
    return [{"chunk_id": normalized_target, "document_id": document_id}]


def _ensure_feedback_reason(feedback_type: str, reason_code: Optional[str]) -> None:
    if feedback_type in FEEDBACK_TYPES_REQUIRING_REASON and not reason_code:
        raise HTTPException(status_code=422, detail="reason_code is required for this feedback_type")


def _feedback_context_key(search_log_id: Optional[UUID], query_id: Optional[UUID], answer_id: UUID) -> str:
    if search_log_id:
        return f"search_log:{search_log_id}"
    if query_id:
        return f"query:{query_id}"
    return f"answer:{answer_id}"


def _feedback_scope_key(target_kind: str, answer_id: UUID, target_result_id: Optional[str]) -> str:
    if target_kind == "result":
        if not target_result_id:
            raise HTTPException(status_code=422, detail="target_result_id is required for result feedback")
        return f"result:{target_result_id}"
    return f"answer:{answer_id}"


async def _ensure_feedback_search_log(
    *,
    db: AsyncSession,
    answer: Answer,
    current_user: DBUser,
    query_id: UUID,
    query_text: str,
    result_ids: List[str],
    result_snapshot: List[Dict[str, Any]],
    retrieval_diagnostics: Dict[str, Any],
) -> SearchLog:
    normalized_result_ids = _normalize_result_ids(result_ids)
    normalized_result_snapshot = _normalize_result_snapshot(result_snapshot)
    if not normalized_result_ids and result_snapshot:
        normalized_result_ids = _normalize_result_ids(item.get("chunk_id") for item in normalized_result_snapshot)

    search_log = await insert_search_log(
        db,
        workspace_id=answer.workspace_id,
        user_id=current_user.id,
        query_text=query_text,
        result_chunk_ids=normalized_result_ids,
        result_count=len(normalized_result_snapshot) if normalized_result_snapshot else len(normalized_result_ids),
        result_snapshot=normalized_result_snapshot,
        clicked_count=0,
        clicked_chunk_ids=[],
        search_duration_ms=None,
        retrieval_metadata={
            **(retrieval_diagnostics or {}),
            "reconstructed_from_feedback": True,
            "reconstructed_reason": "missing_search_log_id_on_feedback_submit",
            "answer_id": str(answer.id),
            "query_id": str(query_id),
        },
        created_at=datetime.now(timezone.utc),
    )
    return search_log


def _signal_to_verification_status(signal: int) -> str:
    if signal > 0:
        return "verified"
    if signal < 0:
        return "rejected"
    return "pending"


async def _get_retrieved_chunks(
    workspace_id: UUID,
    query: str,
    top_k: int,
    similarity_threshold: float,
    db: AsyncSession,
) -> List[RetrievedChunk]:
    try:
        from app.services.top_k_retriever import get_top_k_retriever

        retriever = get_top_k_retriever(db=db, top_k=top_k, similarity_threshold=similarity_threshold)
        result = retriever.retrieve(
            query=query,
            workspace_id=workspace_id,
            top_k=top_k,
            similarity_threshold=similarity_threshold,
        )
        raw_chunks = [
            RetrievedChunk(
                chunk_id=str(chunk.chunk_id),
                document_id=str(chunk.document_id),
                similarity=float(chunk.similarity),
                text=chunk.text,
                source_type=chunk.source_type,
                chunk_index=int(chunk.chunk_index),
                document_title=chunk.document_title,
                token_count=int(getattr(chunk, "token_count", 0) or 0),
                context_before=getattr(chunk, "context_before", None),
                context_after=getattr(chunk, "context_after", None),
                metadata=getattr(chunk, "metadata", None) or {},
            )
            for chunk in result.chunks
        ]
        return await _hydrate_retrieved_chunks(db, raw_chunks)
    except Exception as exc:
        logger.error("Error retrieving chunks: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to retrieve chunks: {exc}") from exc


async def _hydrate_retrieved_chunks(
    db: AsyncSession,
    chunks: List[RetrievedChunk],
) -> List[RetrievedChunk]:
    """Replace sparse vector payload data with authoritative chunk rows."""
    if not chunks:
        return []

    chunk_ids: List[UUID] = []
    for chunk in chunks:
        try:
            chunk_ids.append(UUID(str(chunk.chunk_id)))
        except (TypeError, ValueError):
            continue

    if not chunk_ids:
        return chunks

    try:
        rows = await db.execute(
            select(Chunk, Document)
            .join(Document, Chunk.document_id == Document.id)
            .where(Chunk.id.in_(chunk_ids))
        )
    except Exception as exc:
        logger.warning("Falling back to retriever payloads for answer sources: %s", exc)
        return chunks

    hydrated_by_id = {
        str(chunk_row.id): (chunk_row, document_row)
        for chunk_row, document_row in rows.all()
    }

    hydrated: List[RetrievedChunk] = []
    for chunk in chunks:
        row = hydrated_by_id.get(str(chunk.chunk_id))
        if row is None:
            hydrated.append(chunk)
            continue

        chunk_row, document_row = row
        merged_metadata = {
            **(chunk_row.chunk_metadata or {}),
            **(chunk.metadata or {}),
        }
        if getattr(chunk_row, "created_at", None):
            merged_metadata.setdefault("created_at", chunk_row.created_at.isoformat())
        merged_metadata = enrich_citation_metadata(
            merged_metadata,
            document_title=document_row.title or chunk.document_title,
            source_type=getattr(document_row.source_type, "value", str(document_row.source_type)),
        )

        hydrated.append(
            RetrievedChunk(
                chunk_id=str(chunk_row.id),
                document_id=str(document_row.id),
                similarity=float(chunk.similarity),
                text=chunk_row.text or chunk.text,
                source_type=getattr(document_row.source_type, "value", str(document_row.source_type)),
                chunk_index=int(chunk_row.chunk_index),
                document_title=document_row.title or chunk.document_title,
                token_count=int(chunk_row.token_count or chunk.token_count or 0),
                context_before=chunk_row.context_before or chunk.context_before,
                context_after=chunk_row.context_after or chunk.context_after,
                metadata=merged_metadata,
            )
        )

    return hydrated


@router.post("/generate", response_model=AnswerGenerationResponse)
async def generate_answer(
    request: AnswerGenerationRequest,
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> AnswerGenerationResponse:
    start_time = time.time()
    await _verify_workspace_access(request.workspace_id, current_user, db)

    query_record = Query(workspace_id=request.workspace_id, user_id=current_user.id, query_text=request.query)
    db.add(query_record)
    await db.commit()
    await db.refresh(query_record)

    chunks = await _get_retrieved_chunks(
        workspace_id=request.workspace_id,
        query=request.query,
        top_k=request.top_k,
        similarity_threshold=request.similarity_threshold,
        db=db,
    )

    if not chunks:
        answer = Answer(
            query_id=query_record.id,
            workspace_id=request.workspace_id,
            answer_text=NO_EVIDENCE_MESSAGE,
            confidence_score=0.0,
            sources=[],
            model_used=request.model or settings.OLLAMA_MODEL,
            tokens_used=0,
        )
        db.add(answer)
        await db.commit()
        await db.refresh(answer)
        return AnswerGenerationResponse(
            answer_id=str(answer.id),
            query=request.query,
            answer_text=answer.answer_text,
            citations=[],
            confidence_score=0.0,
            model_used=answer.model_used,
            tokens_used=0,
            generation_time_ms=int((time.time() - start_time) * 1000),
            average_similarity=0.0,
            unique_documents=0,
            chunks_retrieved=0,
            metadata={"status": "no_reliable_match"},
            top_k_used=request.top_k,
        )

    try:
        generator = get_answer_generator(
            model=request.model,
            similarity_threshold=request.similarity_threshold,
            min_unique_documents=request.min_unique_documents,
            detect_conflicts=request.detect_conflicts,
        )
        generated_answer: GeneratedAnswer = await generator.generate(
            query=request.query,
            chunks=chunks,
            include_citations=request.include_citations,
            citation_style=request.citation_style,
            top_k=request.top_k,
        )
    except Exception as exc:
        logger.error("Answer generation failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to generate answer: {exc}") from exc

    citations = [
        CitationPayload(
            chunk_id=str(c.chunk_id),
            document_id=str(c.document_id),
            document_title=c.document_title,
            chunk_index=int(c.chunk_index),
            similarity=float(c.similarity),
            text_snippet=c.text_snippet,
            citation_label=getattr(c, "citation_label", None),
            source_location=getattr(c, "source_location", None) or {},
        )
        for c in generated_answer.citations
    ]

    filtered_chunks = list(getattr(generated_answer.quality_check, "filtered_chunks", []) or chunks)
    quality_check_info = None
    if generated_answer.quality_check:
        quality_check_info = QualityCheckInfo(
            high_quality_chunks=int(generated_answer.quality_check.high_quality_chunks),
            low_quality_chunks=int(generated_answer.quality_check.low_quality_chunks),
            has_conflicts=bool(generated_answer.quality_check.has_conflicts),
            conflict_count=len(generated_answer.quality_check.conflict_details),
            diversity_score=float(generated_answer.quality_check.diversity_score),
            unique_documents=len({str(c.document_id) for c in filtered_chunks}),
            issues_found=len(generated_answer.quality_check.quality_issues),
        )

    sources_for_audit = [
        {
            "chunk_id": str(chunk.chunk_id),
            "document_id": str(chunk.document_id),
            "document_title": chunk.document_title,
            "chunk_index": int(chunk.chunk_index),
            "similarity": round(float(chunk.similarity), 3),
            "source_kind": str((chunk.metadata or {}).get("source_kind", "document")),
            "source_type": chunk.source_type,
            "citation_label": (chunk.metadata or {}).get("citation_label"),
            "source_location": source_location_payload(chunk.metadata or {}, document_title=chunk.document_title),
        }
        for chunk in filtered_chunks
    ]

    answer = Answer(
        query_id=query_record.id,
        workspace_id=request.workspace_id,
        answer_text=generated_answer.answer_text,
        confidence_score=float(generated_answer.confidence_score),
        sources=sources_for_audit,
        model_used=generated_answer.model_used or request.model or settings.OLLAMA_MODEL,
        tokens_used=int(generated_answer.tokens_used or 0),
    )
    db.add(answer)
    await db.commit()
    await db.refresh(answer)

    return AnswerGenerationResponse(
        answer_id=str(answer.id),
        query=request.query,
        answer_text=generated_answer.answer_text,
        citations=citations,
        confidence_score=float(generated_answer.confidence_score),
        model_used=answer.model_used,
        tokens_used=answer.tokens_used,
        generation_time_ms=int((time.time() - start_time) * 1000),
        average_similarity=float(generated_answer.average_similarity),
        unique_documents=int(generated_answer.unique_documents),
        chunks_retrieved=len(filtered_chunks),
        quality_check=quality_check_info,
        metadata=generated_answer.metadata or {},
        cross_doc_agreement_score=generated_answer.cross_doc_agreement_score,
        top_k_used=generated_answer.top_k,
    )


@router.get("/{workspace_id}/history", response_model=QueryHistoryResponse)
async def get_query_history(
    workspace_id: UUID,
    limit: int = QueryParam(default=10, ge=1, le=100),
    offset: int = QueryParam(default=0, ge=0),
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> QueryHistoryResponse:
    await _verify_workspace_access(workspace_id, current_user, db)
    total_queries = (await db.execute(select(func.count(Query.id)).where(Query.workspace_id == workspace_id))).scalar() or 0
    rows = (
        await db.execute(
            select(Query, Answer)
            .outerjoin(Answer, Query.id == Answer.query_id)
            .where(Query.workspace_id == workspace_id)
            .order_by(Query.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
    ).all()

    items = [
        QueryHistoryItem(
            query_id=str(query_record.id),
            query_text=query_record.query_text,
            answer_id=str(answer.id) if answer else "",
            answer_text=(answer.answer_text[:200] if answer else "No answer"),
            confidence_score=float(answer.confidence_score) if answer else 0.0,
            created_at=query_record.created_at.isoformat() if query_record.created_at else "",
            model_used=(answer.model_used if answer else "N/A"),
        )
        for query_record, answer in rows
    ]
    return QueryHistoryResponse(workspace_id=str(workspace_id), total_queries=int(total_queries), queries=items)


@router.get("/{answer_id}", response_model=Dict[str, Any])
async def get_answer_details(
    answer_id: UUID,
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    row = (await db.execute(select(Answer, Query).join(Query, Answer.query_id == Query.id).where(Answer.id == answer_id))).first()
    if not row:
        raise HTTPException(status_code=404, detail="Answer not found")
    answer, query = row
    await _verify_workspace_access(answer.workspace_id, current_user, db)
    return {
        "answer_id": str(answer.id),
        "query": query.query_text,
        "answer_text": answer.answer_text,
        "confidence_score": answer.confidence_score,
        "model_used": answer.model_used,
        "tokens_used": answer.tokens_used,
        "sources": answer.sources,
        "created_at": answer.created_at.isoformat() if answer.created_at else "",
        "workspace_id": str(answer.workspace_id),
    }


@router.get("/{answer_id}/step7", response_model=Step7AnswerResponse)
async def get_answer_step7_format(
    answer_id: UUID,
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> Step7AnswerResponse:
    row = (await db.execute(select(Answer, Query).join(Query, Answer.query_id == Query.id).where(Answer.id == answer_id))).first()
    if not row:
        raise HTTPException(status_code=404, detail="Answer not found")
    answer, _query = row
    await _verify_workspace_access(answer.workspace_id, current_user, db)
    return Step7AnswerResponse(
        answer=answer.answer_text,
        confidence=float(answer.confidence_score),
        sources=[
            SourceReference(
                document_id=source.get("document_id", ""),
                chunk_index=int(source.get("chunk_index", 0)),
                similarity=float(source.get("similarity", 0.0)),
            )
            for source in (answer.sources or [])
        ],
    )


@router.get("/{answer_id}/feedback", response_model=CurrentAnswerFeedbackResponse)
async def get_current_answer_feedback(
    answer_id: UUID,
    search_log_id: Optional[UUID] = QueryParam(default=None),
    query_id: Optional[UUID] = QueryParam(default=None),
    target_kind: str = QueryParam(default="answer", pattern="^(answer|result)$"),
    target_result_id: Optional[str] = QueryParam(default=None),
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> CurrentAnswerFeedbackResponse:
    answer = (await db.execute(select(Answer).where(Answer.id == answer_id))).scalar_one_or_none()
    if answer is None:
        raise HTTPException(status_code=404, detail="Answer not found")
    await _verify_workspace_access(answer.workspace_id, current_user, db)
    if not await _has_feedback_storage(db):
        return CurrentAnswerFeedbackResponse(exists=False)

    normalized_target_kind = (target_kind or "answer").strip().lower()
    if normalized_target_kind not in ALLOWED_TARGET_KINDS:
        raise HTTPException(status_code=422, detail="Invalid target_kind")

    normalized_target_result_id = _normalize_feedback_id(target_result_id)
    context_key = _feedback_context_key(search_log_id, query_id, answer_id)
    scope_key = _feedback_scope_key(normalized_target_kind, answer_id, normalized_target_result_id)

    existing = (
        await db.execute(
            select(SearchFeedback).where(
                SearchFeedback.workspace_id == answer.workspace_id,
                SearchFeedback.user_id == current_user.id,
                SearchFeedback.context_key == context_key,
                SearchFeedback.scope_key == scope_key,
            )
        )
    ).scalar_one_or_none()

    if existing is None:
        return CurrentAnswerFeedbackResponse(exists=False)

    return CurrentAnswerFeedbackResponse(
        exists=True,
        feedback_id=str(existing.id),
        feedback_type=existing.feedback_type,
        target_kind=existing.target_kind,
        target_result_id=existing.target_result_id,
        reason_code=existing.reason_code,
        comment=existing.comment,
        rating_value=existing.rating_value,
        search_log_id=str(existing.search_log_id) if existing.search_log_id else None,
        context_key=existing.context_key,
        scope_key=existing.scope_key,
    )


@router.get("/{answer_id}/feedback-list", response_model=List[CurrentAnswerFeedbackItem])
async def list_current_answer_feedback(
    answer_id: UUID,
    search_log_id: Optional[UUID] = QueryParam(default=None),
    query_id: Optional[UUID] = QueryParam(default=None),
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> List[CurrentAnswerFeedbackItem]:
    answer = (await db.execute(select(Answer).where(Answer.id == answer_id))).scalar_one_or_none()
    if answer is None:
        raise HTTPException(status_code=404, detail="Answer not found")
    await _verify_workspace_access(answer.workspace_id, current_user, db)
    if not await _has_feedback_storage(db):
        return []

    feedback_query = select(SearchFeedback).where(
        SearchFeedback.workspace_id == answer.workspace_id,
        SearchFeedback.user_id == current_user.id,
        SearchFeedback.answer_id == answer_id,
    )
    if search_log_id is not None:
        feedback_query = feedback_query.where(SearchFeedback.search_log_id == search_log_id)
    elif query_id is not None:
        feedback_query = feedback_query.where(SearchFeedback.query_id == query_id)

    rows = (await db.execute(feedback_query.order_by(SearchFeedback.created_at.desc()))).scalars().all()
    return [
        CurrentAnswerFeedbackItem(
            feedback_id=str(item.id),
            feedback_type=item.feedback_type,
            target_kind=item.target_kind,
            target_result_id=item.target_result_id,
            reason_code=item.reason_code,
            comment=item.comment,
            rating_value=item.rating_value,
            search_log_id=str(item.search_log_id) if item.search_log_id else None,
            context_key=item.context_key,
            scope_key=item.scope_key,
        )
        for item in rows
    ]


@router.post("/{answer_id}/feedback", response_model=AnswerFeedbackResponse)
async def submit_answer_feedback(
    answer_id: UUID,
    request: AnswerFeedbackRequest,
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> AnswerFeedbackResponse:
    row = (
        await db.execute(
            select(Answer, Query)
            .join(Query, Answer.query_id == Query.id)
            .where(Answer.id == answer_id)
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Answer not found")
    answer, query_record = row
    await _verify_workspace_access(answer.workspace_id, current_user, db)
    if not await _has_feedback_storage(db):
        raise HTTPException(
            status_code=503,
            detail="Feedback storage schema is unavailable. Run the latest database migration and retry.",
        )

    if request.answer_id != answer_id:
        raise HTTPException(status_code=422, detail="answer_id payload does not match path parameter")

    feedback_type = _normalize_feedback_type(request)
    normalized_target_kind = str(request.target_kind or "answer").strip().lower()
    if normalized_target_kind not in ALLOWED_TARGET_KINDS:
        raise HTTPException(status_code=422, detail="Invalid target_kind")

    reason_code = str(request.reason_code or "").strip().lower() or None
    if reason_code and reason_code not in ALLOWED_REASON_CODES:
        raise HTTPException(status_code=422, detail="Invalid reason_code")
    _ensure_feedback_reason(feedback_type, reason_code)

    effective_query_id = request.query_id or query_record.id
    target_result_id = _normalize_feedback_id(request.target_result_id)

    search_log: Optional[SearchLog] = None
    if request.search_log_id:
        search_log = await load_search_log(
            db,
            search_log_id=request.search_log_id,
            workspace_id=answer.workspace_id,
        )
        if search_log is None:
            raise HTTPException(status_code=404, detail="Search session not found or no longer accessible")
        if str(getattr(search_log, "user_id", current_user.id)) != str(current_user.id):
            raise HTTPException(status_code=404, detail="Search session not found or no longer accessible")

    if normalized_target_kind == "result" and not target_result_id:
        raise HTTPException(status_code=422, detail="target_result_id is required for result feedback")

    query_text = (
        (request.query_text or "").strip()
        or (search_log.query_text if search_log is not None else "")
        or (query_record.query_text or "").strip()
    )
    if not query_text:
        raise HTTPException(status_code=422, detail="query_text is required to persist feedback context")

    result_ids = _normalize_result_ids(request.result_ids or [])
    if not result_ids and search_log is not None:
        result_ids = _normalize_result_ids(search_log.result_chunk_ids or [])

    result_snapshot = _normalize_result_snapshot(request.result_snapshot or [])
    if not result_snapshot and search_log is not None:
        result_snapshot = _normalize_result_snapshot(search_log.result_snapshot or [])

    if search_log is None:
        search_log = await _ensure_feedback_search_log(
            db=db,
            answer=answer,
            current_user=current_user,
            query_id=effective_query_id,
            query_text=query_text,
            result_ids=result_ids,
            result_snapshot=result_snapshot,
            retrieval_diagnostics=request.retrieval_diagnostics or {},
        )

    if search_log and normalized_target_kind == "result":
        log_result_ids = set(_normalize_result_ids(search_log.result_chunk_ids or []))
        snapshot_result_ids = _snapshot_result_ids(result_snapshot)
        valid_result_ids = log_result_ids or snapshot_result_ids or set(result_ids)
        if valid_result_ids and target_result_id not in valid_result_ids:
            raise HTTPException(status_code=422, detail="target_result_id not present in referenced search session")

    if search_log and request.result_ids:
        log_result_ids = set(_normalize_result_ids(search_log.result_chunk_ids or []))
        snapshot_result_ids = _snapshot_result_ids(result_snapshot)
        submitted_ids = set(result_ids)
        known_result_ids = log_result_ids or snapshot_result_ids
        if known_result_ids and not submitted_ids.issubset(known_result_ids):
            raise HTTPException(status_code=422, detail="result_ids include items not found in referenced search session")

    effective_search_log_id = search_log.id if search_log is not None else request.search_log_id
    context_key = _feedback_context_key(effective_search_log_id, effective_query_id, answer_id)
    scope_key = _feedback_scope_key(normalized_target_kind, answer_id, target_result_id)
    feedback_handler = get_feedback_handler()

    context_key_candidates = [context_key]
    legacy_query_context_key = _feedback_context_key(None, effective_query_id, answer_id)
    if legacy_query_context_key not in context_key_candidates:
        context_key_candidates.append(legacy_query_context_key)

    existing = (
        await db.execute(
            select(SearchFeedback).where(
                SearchFeedback.workspace_id == answer.workspace_id,
                SearchFeedback.user_id == current_user.id,
                SearchFeedback.context_key.in_(context_key_candidates),
                SearchFeedback.scope_key == scope_key,
            )
        )
    ).scalar_one_or_none()

    previous_signal = (
        feedback_handler.feedback_signal_from_type(existing.feedback_type, existing.rating_value)
        if existing is not None
        else 0
    )
    new_signal = feedback_handler.feedback_signal_from_type(feedback_type, request.rating_value)

    chunks_updated: List[Dict[str, Any]] = []
    if normalized_target_kind == "answer":
        feedback_sources = answer.sources or []
    else:
        feedback_sources = _source_pairs_for_result_feedback(
            target_result_id=target_result_id,
            result_snapshot=result_snapshot,
        )

    if feedback_sources:
        chunks_updated = await feedback_handler.apply_feedback_delta(
            workspace_id=answer.workspace_id,
            answer_sources=feedback_sources,
            previous_signal=previous_signal,
            new_signal=new_signal,
            db=db,
        )

    verification_status = _signal_to_verification_status(new_signal)
    if normalized_target_kind == "answer":
        if verification_status == "pending":
            await db.execute(
                update(Answer)
                .where(Answer.id == answer_id)
                .values(
                    verification_status=verification_status,
                    verified_by=None,
                    verified_at=None,
                    verification_comment=request.comment,
                )
            )
        else:
            await db.execute(
                update(Answer)
                .where(Answer.id == answer_id)
                .values(
                    verification_status=verification_status,
                    verified_by=current_user.id,
                    verified_at=datetime.now(timezone.utc),
                    verification_comment=request.comment,
                )
            )

    base_retrieval_metadata = dict(search_log.retrieval_metadata or {}) if search_log is not None else {}
    retrieval_diagnostics = {**base_retrieval_metadata, **(request.retrieval_diagnostics or {})}
    embedding_provider = str(base_retrieval_metadata.get("embedding_provider") or "") or None
    embedding_model = str(base_retrieval_metadata.get("embedding_model") or "") or None

    if existing is None:
        feedback_row = SearchFeedback(
            workspace_id=answer.workspace_id,
            user_id=current_user.id,
            search_log_id=effective_search_log_id,
            query_id=effective_query_id,
            answer_id=answer_id,
            context_key=context_key,
            scope_key=scope_key,
            target_kind=normalized_target_kind,
            target_result_id=target_result_id or None,
            feedback_type=feedback_type,
            rating_value=request.rating_value,
            reason_code=reason_code,
            comment=request.comment,
            query_text=query_text,
            query_embedding_provider=embedding_provider,
            query_embedding_model=embedding_model,
            result_ids=result_ids,
            result_snapshot=result_snapshot,
            answer_snapshot=request.answer_snapshot or {},
            retrieval_diagnostics=retrieval_diagnostics,
            metadata_json=request.metadata or {},
        )
        db.add(feedback_row)
        updated_existing = False
    else:
        existing.search_log_id = effective_search_log_id or existing.search_log_id
        existing.query_id = effective_query_id or existing.query_id
        existing.answer_id = answer_id
        existing.context_key = context_key
        existing.scope_key = scope_key
        existing.target_kind = normalized_target_kind
        existing.target_result_id = target_result_id or None
        existing.feedback_type = feedback_type
        existing.rating_value = request.rating_value
        existing.reason_code = reason_code
        existing.comment = request.comment
        existing.query_text = query_text
        existing.query_embedding_provider = embedding_provider or existing.query_embedding_provider
        existing.query_embedding_model = embedding_model or existing.query_embedding_model
        existing.result_ids = result_ids or existing.result_ids or []
        existing.result_snapshot = result_snapshot or existing.result_snapshot or []
        existing.answer_snapshot = request.answer_snapshot or existing.answer_snapshot or {}
        existing.retrieval_diagnostics = retrieval_diagnostics or existing.retrieval_diagnostics or {}
        existing.metadata_json = request.metadata or existing.metadata_json or {}
        feedback_row = existing
        updated_existing = True

    weight_changes = [
        chunk["new_weight"] / chunk["old_weight"]
        for chunk in chunks_updated
        if float(chunk.get("old_weight") or 0.0) > 0.0
    ]
    confidence_change = round(((sum(weight_changes) / len(weight_changes)) - 1.0) * 10, 2) if weight_changes else 0.0

    stage_audit_event(
        db,
        AuditEventPayload(
            action_type=AuditAction.FEEDBACK_SUBMITTED,
            entity_type=EntityType.ANSWER if normalized_target_kind == "answer" else EntityType.CHUNK,
            entity_id=str(answer_id if normalized_target_kind == "answer" else target_result_id),
            actor_user_id=current_user.id,
            workspace_id=answer.workspace_id,
            metadata_json={
                "feedback_type": feedback_type,
                "target_kind": normalized_target_kind,
                "target_result_id": target_result_id or None,
                "reason_code": reason_code,
                "search_log_id": str(effective_search_log_id) if effective_search_log_id else None,
                "query_id": str(effective_query_id) if effective_query_id else None,
                "updated_existing": updated_existing,
                "signal": new_signal,
                "context_key": context_key,
                "scope_key": scope_key,
            },
        ),
    )

    await db.flush()
    await db.commit()

    return AnswerFeedbackResponse(
        feedback_id=str(feedback_row.id),
        answer_id=str(answer_id),
        feedback_type=feedback_type,
        target_kind=normalized_target_kind,
        target_result_id=target_result_id or None,
        reason_code=reason_code,
        comment=request.comment,
        updated_existing=updated_existing,
        feedback_status=verification_status if normalized_target_kind == "answer" else "recorded",
        search_log_id=str(effective_search_log_id) if effective_search_log_id else None,
        context_key=context_key,
        scope_key=scope_key,
        chunks_updated=[ChunkWeightUpdate(**chunk) for chunk in chunks_updated],
        confidence_change=confidence_change,
    )


@router.get("/{workspace_id}/feedback-review", response_model=List[FeedbackReviewItem])
async def get_feedback_review(
    workspace_id: UUID,
    negative_only: bool = QueryParam(default=False),
    limit: int = QueryParam(default=50, ge=1, le=500),
    offset: int = QueryParam(default=0, ge=0),
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> List[FeedbackReviewItem]:
    await _verify_workspace_access(workspace_id, current_user, db)
    if not await _has_feedback_storage(db):
        return []

    query_stmt = (
        select(SearchFeedback)
        .where(SearchFeedback.workspace_id == workspace_id)
        .order_by(SearchFeedback.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    if negative_only:
        query_stmt = query_stmt.where(
            SearchFeedback.feedback_type.in_(
                [
                    "irrelevant",
                    "missing_expected_result",
                    "wrong_result",
                    "bad_highlight",
                    "low_quality_answer",
                    "hallucinated_or_unsupported",
                ]
            )
        )

    rows = (await db.execute(query_stmt)).scalars().all()
    return [
        FeedbackReviewItem(
            feedback_id=str(item.id),
            feedback_type=item.feedback_type,
            target_kind=item.target_kind,
            target_result_id=item.target_result_id,
            reason_code=item.reason_code,
            query_text=item.query_text,
            answer_id=str(item.answer_id) if item.answer_id else None,
            search_log_id=str(item.search_log_id) if item.search_log_id else None,
            user_id=str(item.user_id),
            created_at=item.created_at.isoformat() if item.created_at else None,
        )
        for item in rows
    ]


@router.get("/{workspace_id}/credibility", response_model=List[ChunkCredibilityScore])
async def get_chunk_credibility_scores(
    workspace_id: UUID,
    limit: int = QueryParam(default=50, ge=1, le=500),
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> List[ChunkCredibilityScore]:
    await _verify_workspace_access(workspace_id, current_user, db)
    scores = await get_feedback_handler().get_chunk_credibility_scores(workspace_id=workspace_id, db=db, limit=limit)
    return [ChunkCredibilityScore(**score) for score in scores]


@router.get("/{workspace_id}/evaluation-metrics", response_model=ModelEvaluationMetrics)
async def get_model_evaluation_metrics(
    workspace_id: UUID,
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> ModelEvaluationMetrics:
    await _verify_workspace_access(workspace_id, current_user, db)
    metrics = await get_feedback_handler().get_model_evaluation_metrics(workspace_id=workspace_id, db=db)
    return ModelEvaluationMetrics(**metrics)
