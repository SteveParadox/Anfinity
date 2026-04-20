"""RAG Query API routes."""
import asyncio
import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.audit import AuditAction, AuditLogger, EntityType
from app.core.auth import get_current_active_user, get_workspace_context
from app.database.models import Answer, Chunk, Document, Feedback, Query, User as DBUser
from app.database.session import get_db, log_session_query_metrics
from app.services.answer_generator import RetrievedChunk as AnswerRetrievedChunk
from app.services.answer_generator import get_answer_generator
from app.services.rag_retriever import get_rag_retriever
from app.services.retrieval_relevance import analyze_query_intent, summarize_relevance
from app.services.semantic_search import get_semantic_search_service
from app.tasks.embeddings import queue_embedding_task

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/query", tags=["Query"])

NO_EVIDENCE_MESSAGE = "I couldn't find enough reliable information in your documents to answer this question."


@router.post("/embeddings/process")
async def process_embeddings_for_workspace(
    workspace_id: str,
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Queue embedding generation for all chunks in a workspace."""
    try:
        workspace_uuid = UUID(workspace_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid workspace ID") from exc

    await get_workspace_context(workspace_uuid, current_user, db)

    try:
        result = await db.execute(
            select(Chunk.id, Chunk.text)
            .join(Document, Chunk.document_id == Document.id)
            .where(Document.workspace_id == workspace_uuid)
        )
        rows = result.all()
        if not rows:
            return {
                "status": "success",
                "workspace_id": workspace_id,
                "chunks_queued": 0,
                "message": "No chunks found to embed",
            }

        task_id = queue_embedding_task(
            workspace_id=workspace_id,
            chunk_ids=[str(row[0]) for row in rows],
            texts=[row[1] for row in rows],
        )
        return {
            "status": "queued",
            "workspace_id": workspace_id,
            "chunks_queued": len(rows),
            "task_id": task_id,
            "message": f"Successfully queued {len(rows)} chunks for embedding",
        }
    except Exception as exc:
        logger.error("Error queuing embeddings: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to queue embeddings: {exc}",
        ) from exc


@router.get("/status/{workspace_id}")
async def get_embedding_status(
    workspace_id: str,
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Get embedding status for workspace."""
    try:
        workspace_uuid = UUID(workspace_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid workspace ID") from exc

    await get_workspace_context(workspace_uuid, current_user, db)

    try:
        result = await db.execute(
            select(func.count(Chunk.id))
            .join(Document, Chunk.document_id == Document.id)
            .where(Document.workspace_id == workspace_uuid)
        )
        total_chunks = result.scalar() or 0
        embedded_chunks = total_chunks
        return {
            "workspace_id": workspace_id,
            "total_chunks": total_chunks,
            "embedded_chunks": embedded_chunks,
            "status": "ready" if total_chunks > 0 else "pending",
            "progress_percent": 100 if total_chunks > 0 else 0,
        }
    except Exception as exc:
        logger.error("Error getting embedding status: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


class QueryRequest(BaseModel):
    workspace_id: UUID
    query: str = Field(..., min_length=1, max_length=2000)
    top_k: int = Field(default=10, ge=1, le=20)
    include_sources: bool = True
    model: str = Field(default=settings.OLLAMA_MODEL)


class Source(BaseModel):
    chunk_id: str
    document_id: str
    document_title: str
    source_kind: str
    text: str
    similarity: float


class QueryResponse(BaseModel):
    query_id: str
    answer_id: str
    answer: str
    confidence: float
    confidence_factors: Dict[str, float]
    sources: List[Source]
    model_used: str
    tokens_used: int
    response_time_ms: int


class VerificationRequest(BaseModel):
    status: str = Field(..., pattern="^(approved|rejected)$")
    comment: Optional[str] = Field(None, max_length=1000)


class VerificationResponse(BaseModel):
    answer_id: str
    status: str
    verified_by: str
    comment: Optional[str]
    verified_at: str


class FeedbackRequest(BaseModel):
    rating: int = Field(..., ge=1, le=5)
    comment: Optional[str] = Field(None, max_length=1000)


def _confidence_factors_from_rag(rag_result) -> Dict[str, float]:
    return {
        "similarity_avg": round(float(getattr(rag_result, "avg_similarity", 0.0) or 0.0), 3),
        "document_diversity": float(getattr(rag_result, "unique_documents", 0) or 0),
        "source_coverage": round(float(getattr(rag_result, "confidence", 0.0) or 0.0), 3),
        "chunks_retrieved": float(len(getattr(rag_result, "chunks", []) or [])),
        "unique_documents": float(getattr(rag_result, "unique_documents", 0) or 0),
    }


def _rounded_metric(value: Any) -> float:
    return round(float(value or 0.0), 3)


def _count_source_kinds(chunks: List[AnswerRetrievedChunk]) -> Dict[str, int]:
    counts: Dict[str, int] = {"document": 0, "note": 0}
    for chunk in chunks:
        source_kind = str((chunk.metadata or {}).get("source_kind", "document")).lower()
        if source_kind not in counts:
            counts[source_kind] = 0
        counts[source_kind] += 1
    return counts


def _grounding_failure_confidence(evidence_summary: Dict[str, float]) -> float:
    top_evidence = float(evidence_summary.get("top_evidence_score", 0.0) or 0.0)
    avg_evidence = float(evidence_summary.get("avg_evidence_score", 0.0) or 0.0)
    avg_domain_alignment = float(evidence_summary.get("avg_domain_alignment", 0.0) or 0.0)
    confidence = top_evidence * 0.20 + avg_evidence * 0.10 + avg_domain_alignment * 0.05
    return max(0.0, min(round(confidence, 3), 0.12))


def _build_generation_failure_payload(
    include_sources: bool,
    reliable_evidence: bool,
    evidence_summary: Dict[str, float],
    sources: List[Source],
) -> tuple[str, float, List[Dict[str, Any]]]:
    if reliable_evidence and include_sources and sources:
        return (
            "I found source material that may be relevant, but I couldn't produce a reliable grounded answer from it. "
            "Review the cited sources below.",
            _grounding_failure_confidence(evidence_summary),
            _source_models_to_payload(sources),
        )
    return NO_EVIDENCE_MESSAGE, 0.0, []


def _confidence_factors_from_chunks(
    chunks: List[AnswerRetrievedChunk],
    confidence: float,
    query: str = "",
) -> Dict[str, float]:
    similarities = [float(chunk.similarity or 0.0) for chunk in chunks]
    unique_documents = len({str(chunk.document_id) for chunk in chunks})
    average_similarity = sum(similarities) / len(similarities) if similarities else 0.0
    evidence_summary = summarize_relevance(
        query,
        [
            {
                "text": chunk.text,
                "title": chunk.document_title,
                "metadata": chunk.metadata or {},
                "source_type": chunk.source_type,
            }
            for chunk in chunks
        ],
    )
    return {
        "similarity_avg": round(average_similarity, 3),
        "lexical_support": round(float(evidence_summary["avg_lexical_overlap"]), 3),
        "domain_alignment": round(float(evidence_summary["avg_domain_alignment"]), 3),
        "evidence_quality": round(float(evidence_summary["avg_evidence_score"]), 3),
        "document_diversity": float(unique_documents),
        "source_coverage": round(float(confidence or 0.0), 3),
        "chunks_retrieved": float(len(chunks)),
        "unique_documents": float(unique_documents),
    }


async def _hydrate_sources(
    db: AsyncSession,
    rag_chunks: List[Any],
    include_sources: bool,
) -> tuple[List[Source], List[str]]:
    if not rag_chunks:
        return [], []

    chunk_ids = [UUID(str(chunk.chunk_id)) for chunk in rag_chunks]
    result = await db.execute(
        select(Chunk, Document)
        .join(Document, Chunk.document_id == Document.id)
        .where(Chunk.id.in_(chunk_ids))
    )
    chunk_map = {str(chunk.id): (chunk, doc) for chunk, doc in result.all()}

    sources: List[Source] = []
    llm_chunks: List[str] = []
    for retrieved in rag_chunks:
        key = str(retrieved.chunk_id)
        chunk_row = chunk_map.get(key)
        if not chunk_row:
            continue
        chunk, doc = chunk_row
        text = (chunk.text or "").strip()
        if not text:
            continue
        llm_chunks.append(text[:1200])
        if include_sources:
            preview = text[:400] + ("..." if len(text) > 400 else "")
            sources.append(
                Source(
                    chunk_id=str(chunk.id),
                    document_id=str(doc.id),
                    document_title=doc.title,
                    source_kind="document",
                    text=preview,
                    similarity=round(float(retrieved.similarity), 3),
                )
            )
    return sources, llm_chunks


async def _hydrate_answer_chunks(
    db: AsyncSession,
    rag_chunks: List[Any],
) -> List[AnswerRetrievedChunk]:
    """Hydrate retrieved chunk IDs back to full chunk text and metadata."""
    if not rag_chunks:
        return []

    chunk_ids: List[UUID] = []
    for chunk in rag_chunks:
        try:
            chunk_ids.append(UUID(str(chunk.chunk_id)))
        except (TypeError, ValueError):
            continue

    if not chunk_ids:
        return []

    chunk_map: Dict[str, Any] = {}
    try:
        result = await db.execute(
            select(Chunk, Document)
            .join(Document, Chunk.document_id == Document.id)
            .where(Chunk.id.in_(chunk_ids))
        )
        chunk_map = {str(chunk.id): (chunk, doc) for chunk, doc in result.all()}
    except Exception as exc:
        logger.warning("Falling back to retriever payloads for answer chunks: %s", exc)

    hydrated: List[AnswerRetrievedChunk] = []
    for retrieved in rag_chunks:
        chunk_row = chunk_map.get(str(retrieved.chunk_id))
        if not chunk_row:
            text = (getattr(retrieved, "text", "") or "").strip()
            if not text:
                continue
            hydrated.append(
                AnswerRetrievedChunk(
                    chunk_id=str(retrieved.chunk_id),
                    document_id=str(retrieved.document_id),
                    similarity=float(retrieved.similarity or 0.0),
                    text=text,
                    source_type=str(getattr(retrieved, "source_type", "document")),
                    chunk_index=int(getattr(retrieved, "chunk_index", 0) or 0),
                    document_title=getattr(retrieved, "document_title", "") or "Untitled Document",
                    token_count=int(getattr(retrieved, "token_count", 0) or 0),
                    context_before=getattr(retrieved, "context_before", None),
                    context_after=getattr(retrieved, "context_after", None),
                    metadata={**(getattr(retrieved, "metadata", None) or {}), "source_kind": "document"},
                )
            )
            continue

        chunk, doc = chunk_row
        metadata = {
            **(chunk.chunk_metadata or {}),
            **(getattr(retrieved, "metadata", None) or {}),
            "source_kind": "document",
        }
        if getattr(chunk, "created_at", None):
            metadata.setdefault("created_at", chunk.created_at.isoformat())

        hydrated.append(
            AnswerRetrievedChunk(
                chunk_id=str(chunk.id),
                document_id=str(doc.id),
                similarity=float(retrieved.similarity or 0.0),
                text=(chunk.text or "").strip(),
                source_type=getattr(doc.source_type, "value", str(doc.source_type)),
                chunk_index=int(chunk.chunk_index or 0),
                document_title=doc.title or getattr(retrieved, "document_title", "") or "Untitled Document",
                token_count=int(chunk.token_count or getattr(retrieved, "token_count", 0) or 0),
                context_before=chunk.context_before or getattr(retrieved, "context_before", None),
                context_after=chunk.context_after or getattr(retrieved, "context_after", None),
                metadata=metadata,
            )
        )
    return hydrated


def _dedupe_source_records(records: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    deduped: List[Dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for record in records:
        key = (str(record.get("chunk_id", "")), str(record.get("document_id", "")))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(record)
        if len(deduped) >= limit:
            break
    return deduped


def _merge_answer_chunks(
    primary: List[AnswerRetrievedChunk],
    secondary: List[AnswerRetrievedChunk],
    limit: int,
) -> List[AnswerRetrievedChunk]:
    merged: List[AnswerRetrievedChunk] = []
    seen: set[tuple[str, str]] = set()
    for chunk in sorted([*primary, *secondary], key=lambda item: float(item.similarity or 0.0), reverse=True):
        key = (str(chunk.chunk_id), str(chunk.document_id))
        if key in seen:
            continue
        seen.add(key)
        merged.append(chunk)
        if len(merged) >= limit:
            break
    return merged


def _semantic_results_to_answer_chunks(results: List[Any]) -> tuple[List[AnswerRetrievedChunk], List[Source]]:
    answer_chunks: List[AnswerRetrievedChunk] = []
    sources: List[Source] = []

    for result in results:
        text = (getattr(result, "content", "") or "").strip()
        if not text:
            continue

        similarity = float(
            getattr(result, "final_score", 0.0)
            or getattr(result, "similarity_score", 0.0)
            or 0.0
        )
        preview = (
            getattr(result, "highlight", "")
            or text[:400] + ("..." if len(text) > 400 else "")
        )
        metadata = {
            "semantic_result": True,
            "source_kind": str(getattr(result, "source_kind", "note")),
            "tags": list(getattr(result, "tags", []) or []),
            "created_at": getattr(result, "created_at", None).isoformat()
            if getattr(result, "created_at", None)
            else None,
            "final_score": float(getattr(result, "final_score", 0.0) or 0.0),
        }

        answer_chunks.append(
            AnswerRetrievedChunk(
                chunk_id=str(getattr(result, "chunk_id")),
                document_id=str(getattr(result, "document_id")),
                similarity=similarity,
                text=text,
                source_type=str(getattr(result, "source_type", "note")),
                chunk_index=int(getattr(result, "chunk_index", 0) or 0),
                document_title=str(getattr(result, "document_title", "") or "Untitled"),
                token_count=max(len(text.split()), 0),
                metadata=metadata,
            )
        )
        sources.append(
            Source(
                chunk_id=str(getattr(result, "chunk_id")),
                document_id=str(getattr(result, "document_id")),
                document_title=str(getattr(result, "document_title", "") or "Untitled"),
                source_kind=str(getattr(result, "source_kind", "note")),
                text=preview,
                similarity=round(similarity, 3),
            )
        )

    return answer_chunks, sources


def _source_models_to_payload(sources: List[Source]) -> List[Dict[str, Any]]:
    return [
        {
            "chunk_id": source.chunk_id,
            "document_id": source.document_id,
            "document_title": source.document_title,
            "source_kind": source.source_kind,
            "similarity": source.similarity,
            "text": source.text,
        }
        for source in sources
    ]


def _is_reliable_evidence(query: str, chunks: List[AnswerRetrievedChunk]) -> tuple[bool, Dict[str, float]]:
    intent = analyze_query_intent(query)
    summary = summarize_relevance(
        query,
        [
            {
                "text": chunk.text,
                "title": chunk.document_title,
                "metadata": chunk.metadata or {},
                "source_type": chunk.source_type,
            }
            for chunk in chunks
        ],
    )

    reliable = bool(chunks)
    if summary["off_topic_ratio"] > 0.0:
        reliable = False
    elif intent.is_domain_specific:
        reliable = (
            summary["top_evidence_score"] >= 0.24
            and summary["avg_domain_alignment"] >= 0.14
            and summary["avg_evidence_score"] >= 0.18
        )
    else:
        reliable = summary["top_evidence_score"] >= 0.18 and summary["avg_evidence_score"] >= 0.14

    return reliable, summary


@router.post("", response_model=QueryResponse)
async def query(
    query_request: QueryRequest,
    request: Request,
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
    response: Response = None,
):
    """Execute a grounded RAG query against workspace documents."""
    # Disable caching to ensure every query is treated fresh
    if response:
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    
    start_time = time.time()
    step_times: Dict[str, float] = {}

    await get_workspace_context(query_request.workspace_id, current_user, db)

    query_record = Query(
        workspace_id=query_request.workspace_id,
        user_id=current_user.id,
        query_text=query_request.query,
    )
    db.add(query_record)
    await db.commit()
    await db.refresh(query_record)

    logger.info(f"Query {query_record.id}: Processing query='{query_request.query[:100]}' workspace_id={query_request.workspace_id}")

    try:
        vec_start = time.time()
        retriever = get_rag_retriever(similarity_threshold=0.45, top_k=query_request.top_k)
        rag_result = await asyncio.to_thread(
            retriever.retrieve,
            query=query_request.query,
            workspace_id=str(query_request.workspace_id),
            top_k=query_request.top_k,
        )
        step_times["vector_search"] = (time.time() - vec_start) * 1000

        # Log the similarity scores of retrieved chunks for debugging
        if rag_result.chunks:
            chunk_scores = [
                {"index": i, "similarity": round(float(chunk.similarity or 0.0), 3), "title": chunk.document_title}
                for i, chunk in enumerate(rag_result.chunks[:10])
            ]
            logger.info(f"Query {query_record.id}: Retrieved {len(rag_result.chunks)} chunks, top 10 similarities: {chunk_scores}")
            logger.info(f"Query {query_record.id}: Confidence: {rag_result.confidence}, Avg similarity: {round(sum(float(c.similarity or 0) for c in rag_result.chunks) / len(rag_result.chunks), 3) if rag_result.chunks else 0}")

        confidence = float(rag_result.confidence or 0.0)
        answer_text = NO_EVIDENCE_MESSAGE
        tokens_used = 0
        model_used = query_request.model or settings.OLLAMA_MODEL
        sources, _ = await _hydrate_sources(db, rag_result.chunks, query_request.include_sources)
        evidence_summary: Dict[str, float] = {}
        reliable_evidence = False
        note_search_strategy: Optional[str] = None
        candidate_chunk_count = 0
        candidate_source_kind_counts: Dict[str, int] = {"document": 0, "note": 0}
        answer_generation_status = "not_started"
        answer_generation_error: Optional[str] = None

        answer_chunks = await _hydrate_answer_chunks(db, rag_result.chunks)

        if query_request.top_k > 0:
            semantic_start = time.time()
            try:
                semantic_execution = await get_semantic_search_service().search(
                    workspace_id=query_request.workspace_id,
                    user_id=current_user.id,
                    query=query_request.query,
                    limit=query_request.top_k,
                    db=db,
                    log_execution=False,
                    include_postgresql=True,
                    include_retriever=False,
                )
                note_search_strategy = semantic_execution.strategy
                semantic_chunks, semantic_sources = _semantic_results_to_answer_chunks(semantic_execution.results)
                if semantic_chunks:
                    answer_chunks = _merge_answer_chunks(answer_chunks, semantic_chunks, query_request.top_k)
                    merged_source_payloads = _dedupe_source_records(
                        [
                            *_source_models_to_payload(sources),
                            *_source_models_to_payload(semantic_sources),
                        ],
                        query_request.top_k,
                    )
                    if query_request.include_sources:
                        sources = [Source(**payload) for payload in merged_source_payloads]
                    if semantic_execution.results:
                        top_semantic_scores = [
                            float(
                                getattr(result, "final_score", 0.0)
                                or getattr(result, "similarity_score", 0.0)
                                or 0.0
                            )
                            for result in semantic_execution.results[:3]
                        ]
                        if top_semantic_scores:
                            confidence = max(confidence, sum(top_semantic_scores) / len(top_semantic_scores))
            except Exception as exc:
                logger.warning("Query %s: semantic fallback unavailable: %s", query_record.id, exc)
            finally:
                step_times["semantic_fallback"] = (time.time() - semantic_start) * 1000

        candidate_chunk_count = len(answer_chunks)
        candidate_source_kind_counts = _count_source_kinds(answer_chunks)
        reliable_evidence, evidence_summary = _is_reliable_evidence(query_request.query, answer_chunks)
        if not reliable_evidence:
            answer_chunks = []
            sources = []
            confidence = min(confidence, evidence_summary.get("top_evidence_score", 0.0) * 0.4)

        factors = _confidence_factors_from_chunks(answer_chunks, confidence, query_request.query)

        final_sources = []
        source_kind_by_chunk_id = {
            str(chunk.chunk_id): str((chunk.metadata or {}).get("source_kind", "document"))
            for chunk in answer_chunks
        }
        if answer_chunks:
            answer_generator = get_answer_generator(
                model=query_request.model,
                similarity_threshold=0.45,
                min_unique_documents=1,
                detect_conflicts=True,
                ollama_timeout=int(float(getattr(settings, "OLLAMA_TIMEOUT", 180) or 180)),
            )
            llm_start = time.time()
            try:
                answer_generation_status = "started"
                generated_answer = await answer_generator.generate(
                    query=query_request.query,
                    chunks=answer_chunks,
                    include_citations=query_request.include_sources,
                    citation_style="inline",
                    top_k=query_request.top_k,
                )
                answer_text = generated_answer.answer_text.strip() or NO_EVIDENCE_MESSAGE
                tokens_used = int(generated_answer.tokens_used or 0)
                model_used = generated_answer.model_used or model_used
                confidence = max(0.0, min(float(generated_answer.confidence_score or 0.0) / 100.0, 1.0))

                if query_request.include_sources:
                    if generated_answer.citations:
                        final_sources = [
                            {
                                "chunk_id": citation.chunk_id,
                                "document_id": citation.document_id,
                                "document_title": citation.document_title,
                                "source_kind": source_kind_by_chunk_id.get(str(citation.chunk_id), "document"),
                                "similarity": round(float(citation.similarity), 3),
                                "text": citation.text_snippet,
                            }
                            for citation in generated_answer.citations
                        ]
                    else:
                        final_sources = [
                            {
                                "chunk_id": source.chunk_id,
                                "document_id": source.document_id,
                                "document_title": source.document_title,
                                "source_kind": source.source_kind,
                                "similarity": source.similarity,
                                "text": source.text,
                            }
                            for source in sources
                        ]
                answer_generation_status = "succeeded"
            except Exception as exc:
                logger.error("Query %s: answer generation error: %s", query_record.id, exc, exc_info=True)
                answer_generation_status = "failed"
                answer_generation_error = type(exc).__name__
                answer_text, confidence, final_sources = _build_generation_failure_payload(
                    include_sources=query_request.include_sources,
                    reliable_evidence=reliable_evidence,
                    evidence_summary=evidence_summary,
                    sources=sources,
                )
            finally:
                step_times["llm_response"] = (time.time() - llm_start) * 1000
        else:
            answer_generation_status = "skipped_no_reliable_evidence"

        if answer_text == NO_EVIDENCE_MESSAGE:
            if reliable_evidence and query_request.include_sources and sources:
                answer_text = (
                    "I found relevant source material, but I couldn't produce a confident grounded "
                    "synthesis. Review the cited sources below."
                )
                confidence = min(max(confidence, 0.15), 0.35)
                final_sources = _source_models_to_payload(sources)
            else:
                confidence = 0.0
                final_sources = []

        answer = Answer(
            query_id=query_record.id,
            workspace_id=query_request.workspace_id,
            answer_text=answer_text,
            confidence_score=confidence,
            sources=final_sources,
            model_used=model_used,
            tokens_used=tokens_used,
        )
        db.add(answer)
        await db.commit()
        await db.refresh(answer)

        audit_logger = AuditLogger(db, current_user.id)
        await audit_logger.log(
            action=AuditAction.QUERY_EXECUTED,
            entity_type=EntityType.QUERY,
            entity_id=query_record.id,
            workspace_id=query_request.workspace_id,
            metadata={
                "query": query_request.query[:500],
                "answer_id": str(answer.id),
                "confidence": _rounded_metric(confidence),
                "chunks_retrieved": len(rag_result.chunks),
                "candidate_chunks_considered": candidate_chunk_count,
                "candidate_source_kinds": candidate_source_kind_counts,
                "note_search_strategy": note_search_strategy,
                "reliable_evidence": reliable_evidence,
                "avg_lexical_overlap": _rounded_metric(evidence_summary.get("avg_lexical_overlap")),
                "avg_domain_alignment": _rounded_metric(evidence_summary.get("avg_domain_alignment")),
                "avg_evidence_score": _rounded_metric(evidence_summary.get("avg_evidence_score")),
                "top_evidence_score": _rounded_metric(evidence_summary.get("top_evidence_score")),
                "off_topic_ratio": _rounded_metric(evidence_summary.get("off_topic_ratio")),
                "sources_returned": len(final_sources),
                "answer_generation_status": answer_generation_status,
                "answer_generation_error": answer_generation_error,
                "timings_ms": {k: round(v, 1) for k, v in step_times.items()},
                "client_ip": request.client.host if request.client else None,
            },
        )

        response_time_ms = int((time.time() - start_time) * 1000)
        log_session_query_metrics(db, "query.execute")
        return QueryResponse(
            query_id=str(query_record.id),
            answer_id=str(answer.id),
            answer=answer_text,
            confidence=round(confidence, 3),
            confidence_factors=factors,
            sources=final_sources if query_request.include_sources else [],
            model_used=model_used,
            tokens_used=tokens_used,
            response_time_ms=response_time_ms,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Query execution failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Query failed: {exc}") from exc


@router.post("/{answer_id}/verify", response_model=VerificationResponse)
async def verify_answer(
    answer_id: UUID,
    verification: VerificationRequest,
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    normalized_status = "verified" if verification.status == "approved" else verification.status
    rating = 5 if normalized_status == "verified" else 1
    verified_at = datetime.utcnow()

    result = await db.execute(
        select(Answer.workspace_id).where(Answer.id == answer_id)
    )
    answer_workspace_id = result.scalar_one_or_none()
    if answer_workspace_id is None:
        raise HTTPException(status_code=404, detail="Answer not found")

    await get_workspace_context(answer_workspace_id, current_user, db)

    update_result = await db.execute(
        update(Answer)
        .where(Answer.id == answer_id)
        .values(
            verification_status=normalized_status,
            verified_by=current_user.id,
            verified_at=verified_at,
            verification_comment=verification.comment,
        )
        .returning(Answer.id)
    )
    updated_answer_id = update_result.scalar_one_or_none()
    if updated_answer_id is None:
        raise HTTPException(status_code=404, detail="Answer not found")

    feedback = Feedback(
        answer_id=updated_answer_id,
        workspace_id=answer_workspace_id,
        user_id=current_user.id,
        rating=rating,
        comment=verification.comment,
    )
    db.add(feedback)
    await db.commit()
    log_session_query_metrics(db, "query.verify_answer")

    return VerificationResponse(
        answer_id=str(updated_answer_id),
        status=verification.status,
        verified_by=str(current_user.id),
        comment=verification.comment,
        verified_at=verified_at.isoformat(),
    )
