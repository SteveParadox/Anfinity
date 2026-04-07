"""RAG Query API routes."""
import asyncio
import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.audit import AuditAction, AuditLogger, EntityType
from app.core.auth import get_current_active_user, get_workspace_context
from app.database.models import Answer, Chunk, Document, Feedback, Query, User as DBUser
from app.database.session import get_db
from app.services.llm_service import get_llm_service
from app.services.rag_retriever import get_rag_retriever
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
    text: str
    similarity: float


class QueryResponse(BaseModel):
    query_id: str
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
                    text=preview,
                    similarity=round(float(retrieved.similarity), 3),
                )
            )
    return sources, llm_chunks


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
        retriever = get_rag_retriever(similarity_threshold=0.5, top_k=query_request.top_k)
        rag_result = retriever.retrieve(
            query=query_request.query,
            workspace_id=str(query_request.workspace_id),
            top_k=query_request.top_k,
        )
        step_times["vector_search"] = (time.time() - vec_start) * 1000

        confidence = float(rag_result.confidence or 0.0)
        factors = _confidence_factors_from_rag(rag_result)
        answer_text = NO_EVIDENCE_MESSAGE
        tokens_used = 0
        model_used = query_request.model or settings.OLLAMA_MODEL
        sources, llm_chunks = await _hydrate_sources(db, rag_result.chunks, query_request.include_sources)

        # FIX: Minimum confidence cutoff - if confidence too low, return NO_ANSWER state with empty sources
        MIN_CONFIDENCE_THRESHOLD = 0.35  # 35% minimum to trust retrieval
        retrieval_is_reliable = bool(rag_result.chunks) and confidence >= MIN_CONFIDENCE_THRESHOLD and llm_chunks
        if retrieval_is_reliable:
            llm_service = get_llm_service()
            llm_start = time.time()
            try:
                llm_response = await asyncio.wait_for(
                    asyncio.to_thread(
                        llm_service.generate_answer,
                        query_request.query,
                        llm_chunks[:4],
                        settings.LLM_TEMPERATURE,
                        min(settings.LLM_MAX_TOKENS, 700),
                        True,
                    ),
                    timeout=float(getattr(settings, "OLLAMA_TIMEOUT", 180) or 180),
                )
                answer_text = llm_response.answer.strip() or NO_EVIDENCE_MESSAGE
                tokens_used = int(llm_response.tokens_used or 0)
                model_used = llm_response.model or model_used
            except asyncio.TimeoutError:
                logger.warning("Query %s: LLM timed out", query_record.id)
                answer_text = "I found relevant documents, but the response generator timed out before it could finish."
                confidence = min(confidence, 0.2)
            except Exception as exc:
                logger.error("Query %s: LLM error: %s", query_record.id, exc, exc_info=True)
                answer_text = "I found relevant documents, but I couldn't generate a grounded answer from them."
                confidence = min(confidence, 0.2)
            finally:
                step_times["llm_response"] = (time.time() - llm_start) * 1000
        else:
            confidence = 0.0 if not rag_result.chunks else min(confidence, 0.25)

        # FIX: NO ANSWER state - if confidence too low, return no sources
        # This prevents showing unreliable chunks that might contain incorrect info
        final_sources = []
        if confidence >= MIN_CONFIDENCE_THRESHOLD and retrieval_is_reliable:
            final_sources = [
                {
                    "chunk_id": source.chunk_id,
                    "document_id": source.document_id,
                    "document_title": source.document_title,
                    "similarity": source.similarity,
                }
                for source in sources
            ]
        else:
            # Low confidence: return empty sources and reset answer to generic message
            confidence = 0.0
            answer_text = NO_EVIDENCE_MESSAGE

        # RULE 1: Kill confidence when no answer
        # If we're returning NO_EVIDENCE_MESSAGE, ALWAYS force confidence to 0.0 and sources to []
        # No negotiation. No debate.
        if answer_text == NO_EVIDENCE_MESSAGE:
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
                "confidence": round(confidence, 3),
                "chunks_retrieved": len(rag_result.chunks),
                "timings_ms": {k: round(v, 1) for k, v in step_times.items()},
                "client_ip": request.client.host if request.client else None,
            },
        )

        response_time_ms = int((time.time() - start_time) * 1000)
        return QueryResponse(
            query_id=str(query_record.id),
            answer=answer_text,
            confidence=round(confidence, 3),
            confidence_factors=factors,
            sources=sources if query_request.include_sources else [],
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
    result = await db.execute(select(Answer).where(Answer.id == answer_id))
    answer = result.scalar_one_or_none()
    if answer is None:
        raise HTTPException(status_code=404, detail="Answer not found")

    await get_workspace_context(answer.workspace_id, current_user, db)

    feedback = Feedback(
        answer_id=answer.id,
        user_id=current_user.id,
        status=verification.status,
        comment=verification.comment,
        verified_at=datetime.utcnow(),
    )
    db.add(feedback)
    await db.commit()

    return VerificationResponse(
        answer_id=str(answer.id),
        status=verification.status,
        verified_by=str(current_user.id),
        comment=verification.comment,
        verified_at=feedback.verified_at.isoformat(),
    )
