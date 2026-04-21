"""STEP 4: Answer Generation API endpoints. STEP 7: Output Structure. STEP 8: Feedback Loop."""

import logging
import time
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query as QueryParam, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.auth import WorkspaceContext, get_current_active_user, get_workspace_context
from app.database.models import Answer, Chunk, Document, Query, User as DBUser
from app.database.session import get_db
from app.ingestion.source_locations import enrich_citation_metadata, source_location_payload
from app.services.answer_generator import GeneratedAnswer, RetrievedChunk, get_answer_generator
from app.services.feedback_handler import get_feedback_handler

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/answers", tags=["Answers"])

NO_EVIDENCE_MESSAGE = "I couldn't find enough reliable information in your documents to answer this question."


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
    status: str = Field(..., pattern="^(verified|rejected)$")
    comment: Optional[str] = Field(None, max_length=1000)


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
    answer_id: str
    feedback_status: str
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


async def _verify_workspace_access(
    workspace_id: UUID,
    current_user: DBUser,
    db: AsyncSession,
) -> WorkspaceContext:
    return await get_workspace_context(workspace_id, current_user, db)


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


@router.post("/{answer_id}/feedback", response_model=AnswerFeedbackResponse)
async def submit_answer_feedback(
    answer_id: UUID,
    request: AnswerFeedbackRequest,
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> AnswerFeedbackResponse:
    answer = (await db.execute(select(Answer).where(Answer.id == answer_id))).scalar_one_or_none()
    if answer is None:
        raise HTTPException(status_code=404, detail="Answer not found")
    await _verify_workspace_access(answer.workspace_id, current_user, db)
    feedback_handler = get_feedback_handler()
    result = await feedback_handler.process_answer_feedback(
        answer_id=answer_id,
        feedback_status=request.status,
        comment=request.comment,
        user_id=current_user.id,
        db=db,
        answer_workspace_id=answer.workspace_id,
        answer_sources=answer.sources,
    )
    return AnswerFeedbackResponse(
        answer_id=result["answer_id"],
        feedback_status=result["feedback_status"],
        chunks_updated=[ChunkWeightUpdate(**chunk) for chunk in result["chunks_updated"]],
        confidence_change=result["confidence_change"],
    )


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
