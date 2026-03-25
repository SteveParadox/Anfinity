"""STEP 4: Answer Generation API endpoints. STEP 7: Output Structure. STEP 8: Feedback Loop."""

import time
import logging
from typing import List, Optional, Dict, Any
from uuid import UUID
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status, Query as QueryParam
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.database.session import get_db
from app.database.models import Query, Answer, User as DBUser, WorkspaceMember, Workspace
from app.core.auth import get_current_active_user
from app.services.answer_generator import (
    AnswerGenerator,
    GeneratedAnswer,
    RetrievedChunk,
    Citation,
    get_answer_generator
)
from app.services.top_k_retriever import TopKRetriever
from app.services.vector_db import get_vector_db_client
from app.ingestion.embedder import Embedder
from app.services.feedback_handler import get_feedback_handler

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/answers", tags=["Answers"])


# ===== Request/Response Models =====

class CitationPayload(BaseModel):
    """Citation reference in answer."""
    chunk_id: str
    document_id: str
    document_title: str
    chunk_index: int
    similarity: float
    text_snippet: str


class QualityCheckInfo(BaseModel):
    """Quality check results for retrieved chunks."""
    high_quality_chunks: int
    low_quality_chunks: int
    has_conflicts: bool
    conflict_count: int
    diversity_score: float
    unique_documents: int
    issues_found: int


class AnswerGenerationRequest(BaseModel):
    """Request to generate answer from query."""
    workspace_id: UUID
    query: str = Field(..., min_length=1, max_length=2000)
    top_k: int = Field(default=10, ge=1, le=20)
    similarity_threshold: float = Field(default=0.5, ge=0.0, le=1.0, description="Filter chunks below this similarity")
    include_citations: bool = True
    citation_style: str = Field(default="inline", pattern="^(inline|footnote)$")
    model: str = Field(default="gpt-4o-mini")
    min_unique_documents: int = Field(default=1, ge=1, le=10, description="Minimum unique source documents required")
    detect_conflicts: bool = Field(default=True, description="Detect contradictory chunks and mark low confidence")


class AnswerGenerationResponse(BaseModel):
    """Response with generated answer."""
    answer_id: str
    query: str
    answer_text: str
    citations: List[CitationPayload]
    confidence_score: float  # 0-100% percentage (STEP 5)
    model_used: str
    tokens_used: int
    generation_time_ms: float
    average_similarity: float
    unique_documents: int
    chunks_retrieved: int
    quality_check: Optional[QualityCheckInfo] = None
    metadata: Dict[str, Any]
    cross_doc_agreement_score: Optional[float] = None  # 0-1 from STEP 5
    top_k_used: Optional[int] = None  # K value used for normalization


class QueryHistoryItem(BaseModel):
    """Single query history item."""
    query_id: str
    query_text: str
    answer_id: str
    answer_text: str
    confidence_score: float
    created_at: str
    model_used: str


class QueryHistoryResponse(BaseModel):
    """Query history response."""
    workspace_id: str
    total_queries: int
    queries: List[QueryHistoryItem]


# ===== STEP 7: Output Structure Models =====

class SourceReference(BaseModel):
    """STEP 7: Simplified source reference in output structure."""
    document_id: str
    chunk_index: int
    similarity: float


class Step7AnswerResponse(BaseModel):
    """STEP 7: Standard JSON output structure."""
    answer: str
    confidence: float
    sources: List[SourceReference]


# ===== STEP 8: Feedback Loop Models =====

class AnswerFeedbackRequest(BaseModel):
    """STEP 8: Feedback on answer correctness."""
    answer_id: UUID
    status: str = Field(..., pattern="^(verified|rejected)$", description="Feedback status: verified or rejected")
    comment: Optional[str] = Field(None, max_length=1000)


class ChunkWeightUpdate(BaseModel):
    """STEP 8: Chunk weight update from feedback."""
    chunk_id: str
    document_id: str
    old_weight: float
    new_weight: float
    accuracy: float
    positive_count: int
    negative_count: int
    total_uses: int


class AnswerFeedbackResponse(BaseModel):
    """STEP 8: Response after processing feedback."""
    answer_id: str
    feedback_status: str
    chunks_updated: List[ChunkWeightUpdate]
    confidence_change: float


class ChunkCredibilityScore(BaseModel):
    """STEP 8: Chunk credibility score."""
    chunk_id: str
    document_id: str
    credibility_score: float
    accuracy_rate: float
    positive_feedback: int
    negative_feedback: int
    total_uses: int
    updated_at: Optional[str]


class ModelEvaluationMetrics(BaseModel):
    """STEP 8: Model evaluation metrics from feedback."""
    total_feedback: int
    approved_count: int
    rejected_count: int
    approval_rate: float
    rejection_rate: float
    average_rating: float


# ===== Helper Functions =====

async def _verify_workspace_access(
    workspace_id: UUID,
    current_user: DBUser,
    db: AsyncSession
) -> Workspace:
    """
    Verify user has access to workspace.
    
    Args:
        workspace_id: Workspace ID
        current_user: Current user
        db: Database session
        
    Returns:
        Workspace object if access granted
        
    Raises:
        HTTPException: If workspace not found or no access
    """
    # Check workspace exists
    result = await db.execute(
        select(Workspace).where(Workspace.id == workspace_id)
    )
    workspace = result.scalar_one_or_none()
    
    if not workspace:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workspace not found"
        )
    
    # Check membership
    result = await db.execute(
        select(WorkspaceMember).where(
            (WorkspaceMember.workspace_id == workspace_id) &
            (WorkspaceMember.user_id == current_user.id)
        )
    )
    membership = result.scalar_one_or_none()
    
    if not membership:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No access to workspace"
        )
    
    return workspace


async def _get_retrieved_chunks(
    workspace_id: UUID,
    query: str,
    top_k: int,
    similarity_threshold: float,
    db: AsyncSession
) -> List[RetrievedChunk]:
    """
    Retrieve chunks using STEP 3 retriever.
    
    Args:
        workspace_id: Workspace ID
        query: Query text
        top_k: Number of chunks to retrieve
        similarity_threshold: Minimum similarity
        db: Database session
        
    Returns:
        List of retrieved chunks
    """
    try:
        # Get embedder
        embedder = get_embedding_provider()
        
        # Get vector DB client
        vector_db = get_vector_db_client()
        
        # Create retriever
        retriever = TopKRetriever(
            embedder=embedder,
            vector_db=vector_db,
            similarity_threshold=similarity_threshold,
            top_k=top_k
        )
        
        # Retrieve chunks
        result = retriever.retrieve(
            query=query,
            workspace_id=str(workspace_id),
            top_k=top_k,
            similarity_threshold=similarity_threshold
        )
        
        # Convert to RetrievedChunk format
        chunks = [
            RetrievedChunk(
                chunk_id=str(chunk.chunk_id),
                document_id=str(chunk.document_id),
                similarity=chunk.similarity,
                text=chunk.text,
                source_type=chunk.source_type,
                chunk_index=chunk.chunk_index,
                document_title=chunk.document_title,
                token_count=chunk.token_count,
                context_before=chunk.context_before,
                context_after=chunk.context_after,
                metadata=chunk.metadata or {}
            )
            for chunk in result.chunks
        ]
        
        logger.info(f"Retrieved {len(chunks)} chunks for query '{query[:50]}...'")
        return chunks
        
    except Exception as e:
        logger.error(f"Error retrieving chunks: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve chunks: {str(e)}"
        )


# ===== API Endpoints =====

@router.post("/generate", response_model=AnswerGenerationResponse)
async def generate_answer(
    request: AnswerGenerationRequest,
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
) -> AnswerGenerationResponse:
    """
    Generate answer from query using STEP 2 & STEP 3 pipeline.
    
    Pipeline:
    1. Verify workspace access
    2. Retrieve relevant chunks (STEP 3)
    3. Generate answer with LLM (STEP 4)
    4. Store query and answer records
    5. Return answer with citations
    
    Args:
        request: Answer generation request
        current_user: Authenticated user
        db: Database session
        
    Returns:
        AnswerGenerationResponse with answer, citations, and metadata
    """
    start_time = time.time()
    
    # Verify workspace access
    workspace = await _verify_workspace_access(
        request.workspace_id,
        current_user,
        db
    )
    
    # Create query record
    query_record = Query(
        workspace_id=request.workspace_id,
        user_id=current_user.id,
        query_text=request.query
    )
    db.add(query_record)
    await db.commit()
    await db.refresh(query_record)
    
    logger.info(
        f"Answer generation for query {query_record.id}: "
        f"'{request.query[:50]}...' in workspace {request.workspace_id}"
    )
    
    try:
        # Step 1: Retrieve chunks
        chunks = await _get_retrieved_chunks(
            workspace_id=request.workspace_id,
            query=request.query,
            top_k=request.top_k,
            similarity_threshold=request.similarity_threshold,
            db=db
        )
        
        if not chunks:
            # No chunks found
            response_time_ms = int((time.time() - start_time) * 1000)
            
            answer = Answer(
                query_id=query_record.id,
                workspace_id=request.workspace_id,
                answer_text="I couldn't find relevant information in your documents to answer this question.",
                confidence_score=0.0,
                sources=[],
                model_used=request.model,
                tokens_used=0
            )
            db.add(answer)
            await db.commit()
            await db.refresh(answer)
            
            logger.info(f"Query {query_record.id}: No chunks retrieved")
            
            return AnswerGenerationResponse(
                answer_id=str(answer.id),
                query=request.query,
                answer_text=answer.answer_text,
                citations=[],
                confidence_score=0.0,
                model_used=request.model,
                tokens_used=0,
                generation_time_ms=response_time_ms,
                average_similarity=0.0,
                unique_documents=0,
                chunks_retrieved=0,
                metadata={}
            )
        
        # Step 2: Generate answer with LLM
        generator = get_answer_generator(
            model=request.model,
            similarity_threshold=request.similarity_threshold,
            min_unique_documents=request.min_unique_documents,
            detect_conflicts=request.detect_conflicts
        )
        
        generated_answer: GeneratedAnswer = generator.generate(
            query=request.query,
            chunks=chunks,
            include_citations=request.include_citations,
            citation_style=request.citation_style,
            top_k=request.top_k
        )
        
        logger.info(
            f"Generated answer for query {query_record.id} "
            f"({generated_answer.tokens_used} tokens, "
            f"{len(generated_answer.citations)} citations, "
            f"confidence: {generated_answer.confidence_score})"
        )
        
        # Step 3: Convert citations to payload format
        citation_payloads = [
            CitationPayload(
                chunk_id=c.chunk_id,
                document_id=c.document_id,
                document_title=c.document_title,
                chunk_index=c.chunk_index,
                similarity=c.similarity,
                text_snippet=c.text_snippet
            )
            for c in generated_answer.citations
        ]
        
        # Step 3b: Build quality check info
        quality_check_info = None
        if generated_answer.quality_check:
            quality_check_info = QualityCheckInfo(
                high_quality_chunks=generated_answer.quality_check.high_quality_chunks,
                low_quality_chunks=generated_answer.quality_check.low_quality_chunks,
                has_conflicts=generated_answer.quality_check.has_conflicts,
                conflict_count=len(generated_answer.quality_check.conflict_details),
                diversity_score=generated_answer.quality_check.diversity_score,
                unique_documents=len(set(c.document_id for c in generated_answer.quality_check.filtered_chunks)),
                issues_found=len(generated_answer.quality_check.quality_issues)
            )
        
        # Step 4: Store answer record (STEP 6 — Audit Trail)
        # Build source list with document_id + chunk_index for auditing
        sources_for_audit = [
            {
                "chunk_id": str(c.chunk_id),
                "document_id": str(c.document_id),
                "document_title": c.document_title,
                "chunk_index": c.chunk_index,
                "similarity": round(c.similarity, 3),
                "source_type": c.source_type
            }
            for c in generated_answer.quality_check.filtered_chunks
            if generated_answer.quality_check
        ]
        
        answer = Answer(
            query_id=query_record.id,
            workspace_id=request.workspace_id,
            answer_text=generated_answer.answer_text,
            confidence_score=generated_answer.confidence_score,
            sources=sources_for_audit,  # STEP 6: Full source information for audit
            model_used=generated_answer.model_used,
            tokens_used=generated_answer.tokens_used
        )
        db.add(answer)
        await db.commit()
        await db.refresh(answer)
        
        logger.info(
            f"STEP 6: Stored answer {answer.id} for query {query_record.id} "
            f"with {len(sources_for_audit)} sources, confidence: {generated_answer.confidence_score}%"
        )
        
        response_time_ms = int((time.time() - start_time) * 1000)
        
        # Step 5: Return response
        return AnswerGenerationResponse(
            answer_id=str(answer.id),
            query=request.query,
            answer_text=generated_answer.answer_text,
            citations=citation_payloads,
            confidence_score=generated_answer.confidence_score,
            model_used=generated_answer.model_used,
            tokens_used=generated_answer.tokens_used,
            generation_time_ms=response_time_ms,
            average_similarity=generated_answer.average_similarity,
            unique_documents=generated_answer.unique_documents,
            chunks_retrieved=len(generated_answer.quality_check.filtered_chunks) if generated_answer.quality_check else len(chunks),
            quality_check=quality_check_info,
            metadata=generated_answer.metadata,
            cross_doc_agreement_score=generated_answer.cross_doc_agreement_score,
            top_k_used=generated_answer.top_k
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating answer for query {query_record.id}: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate answer: {str(e)}"
        )


@router.get("/{workspace_id}/history", response_model=QueryHistoryResponse)
async def get_query_history(
    workspace_id: UUID,
    limit: int = QueryParam(default=10, ge=1, le=100),
    offset: int = QueryParam(default=0, ge=0),
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
) -> QueryHistoryResponse:
    """
    Get query history for workspace.
    
    Args:
        workspace_id: Workspace ID
        limit: Number of results to return
        offset: Number of results to skip
        current_user: Authenticated user
        db: Database session
        
    Returns:
        QueryHistoryResponse with query history items
    """
    # Verify workspace access
    await _verify_workspace_access(workspace_id, current_user, db)
    
    try:
        # Get total query count
        result = await db.execute(
            select(func.count(Query.id))
            .where(Query.workspace_id == workspace_id)
        )
        total_queries = result.scalar() or 0
        
        # Get query history
        result = await db.execute(
            select(Query, Answer)
            .outerjoin(Answer, Query.id == Answer.query_id)
            .where(Query.workspace_id == workspace_id)
            .order_by(Query.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        
        rows = result.all()
        
        items = []
        for query_record, answer in rows:
            item = QueryHistoryItem(
                query_id=str(query_record.id),
                query_text=query_record.query_text,
                answer_id=str(answer.id) if answer else "",
                answer_text=answer.answer_text[:200] if answer else "No answer",
                confidence_score=answer.confidence_score if answer else 0.0,
                created_at=query_record.created_at.isoformat() if query_record.created_at else "",
                model_used=answer.model_used if answer else "N/A"
            )
            items.append(item)
        
        logger.info(f"Retrieved {len(items)} query history items for workspace {workspace_id}")
        
        return QueryHistoryResponse(
            workspace_id=str(workspace_id),
            total_queries=total_queries,
            queries=items
        )
        
    except Exception as e:
        logger.error(f"Error retrieving query history: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve query history: {str(e)}"
        )


@router.get("/{answer_id}", response_model=Dict[str, Any])
async def get_answer_details(
    answer_id: UUID,
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """
    Get detailed information about a specific answer.
    
    Args:
        answer_id: Answer ID
        current_user: Authenticated user
        db: Database session
        
    Returns:
        Answer details with metadata
    """
    try:
        # Get answer
        result = await db.execute(
            select(Answer, Query)
            .join(Query, Answer.query_id == Query.id)
            .where(Answer.id == answer_id)
        )
        
        row = result.first()
        if not row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Answer not found"
            )
        
        answer, query = row
        
        # Verify workspace access
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
            "workspace_id": str(answer.workspace_id)
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving answer details: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve answer: {str(e)}"
        )


# ===== STEP 7: Output Structure Endpoints =====

@router.get("/{answer_id}/step7", response_model=Step7AnswerResponse)
async def get_answer_step7_format(
    answer_id: UUID,
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
) -> Step7AnswerResponse:
    """
    STEP 7: Get answer in standard JSON output structure.
    
    Simplified format with:
    - answer: Plain text response
    - confidence: 0-100 percentage
    - sources: List of {document_id, chunk_index, similarity}
    
    Args:
        answer_id: Answer ID
        current_user: Authenticated user
        db: Database session
        
    Returns:
        Answer in STEP 7 standard output format
    """
    try:
        # Get answer
        result = await db.execute(
            select(Answer, Query)
            .join(Query, Answer.query_id == Query.id)
            .where(Answer.id == answer_id)
        )
        
        row = result.first()
        if not row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Answer not found"
            )
        
        answer, query = row
        
        # Verify workspace access
        await _verify_workspace_access(answer.workspace_id, current_user, db)
        
        # Build sources in STEP 7 format
        sources = []
        if answer.sources:
            for source in answer.sources:
                sources.append(
                    SourceReference(
                        document_id=source.get("document_id", ""),
                        chunk_index=int(source.get("chunk_index", 0)),
                        similarity=float(source.get("similarity", 0.0))
                    )
                )
        
        logger.info(
            f"STEP 7: Retrieved answer {answer_id} in standard output format "
            f"with {len(sources)} sources"
        )
        
        return Step7AnswerResponse(
            answer=answer.answer_text,
            confidence=answer.confidence_score,
            sources=sources
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving answer in STEP 7 format: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve answer: {str(e)}"
        )


# ===== STEP 8: Feedback Loop Endpoints =====

@router.post("/{answer_id}/feedback", response_model=AnswerFeedbackResponse)
async def submit_answer_feedback(
    answer_id: UUID,
    request: AnswerFeedbackRequest,
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
) -> AnswerFeedbackResponse:
    """
    STEP 8: Submit feedback on answer correctness.
    
    Feedback Loop:
    1. User marks answer as verified (correct) or rejected (incorrect)
    2. Extract source chunks from answer
    3. Update chunk credibility weights
    4. Recalibrate confidence for future answers
    5. Log for model evaluation
    
    Args:
        answer_id: Answer to provide feedback on
        request: Feedback request with status and optional comment
        current_user: Authenticated user
        db: Database session
        
    Returns:
        Feedback processing result with chunk weight updates
    """
    try:
        # Get answer to verify workspace access
        result = await db.execute(
            select(Answer).where(Answer.id == answer_id)
        )
        answer = result.scalar_one_or_none()
        
        if not answer:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Answer not found"
            )
        
        # Verify workspace access
        await _verify_workspace_access(answer.workspace_id, current_user, db)
        
        # Process feedback
        feedback_handler = get_feedback_handler()
        result = await feedback_handler.process_answer_feedback(
            answer_id=answer_id,
            feedback_status=request.status,
            comment=request.comment,
            user_id=current_user.id,
            db=db
        )
        
        logger.info(
            f"STEP 8: Feedback processed for answer {answer_id}: "
            f"status={request.status}, chunks_updated={len(result['chunks_updated'])}"
        )
        
        return AnswerFeedbackResponse(
            answer_id=result["answer_id"],
            feedback_status=result["feedback_status"],
            chunks_updated=[
                ChunkWeightUpdate(**chunk)
                for chunk in result["chunks_updated"]
            ],
            confidence_change=result["confidence_change"]
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error submitting feedback for answer {answer_id}: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to submit feedback: {str(e)}"
        )


@router.get("/{workspace_id}/credibility", response_model=List[ChunkCredibilityScore])
async def get_chunk_credibility_scores(
    workspace_id: UUID,
    limit: int = QueryParam(default=50, ge=1, le=500),
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
) -> List[ChunkCredibilityScore]:
    """
    STEP 8: Get chunks ranked by credibility score.
    
    Credibility score reflects feedback history:
    - Positive feedback → increases score (up to 2.0 multiplier)
    - Negative feedback → decreases score (down to 0.1 multiplier)
    - Accuracy rate: % of times used correctly
    
    Args:
        workspace_id: Workspace ID
        limit: Number of results to return
        current_user: Authenticated user
        db: Database session
        
    Returns:
        List of chunks with credibility scores
    """
    try:
        # Verify workspace access
        await _verify_workspace_access(workspace_id, current_user, db)
        
        # Get credibility scores
        feedback_handler = get_feedback_handler()
        scores = await feedback_handler.get_chunk_credibility_scores(
            workspace_id=workspace_id,
            db=db,
            limit=limit
        )
        
        logger.info(
            f"STEP 8: Retrieved {len(scores)} chunk credibility scores "
            f"for workspace {workspace_id}"
        )
        
        return [
            ChunkCredibilityScore(**score)
            for score in scores
        ]
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving credibility scores: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve credibility scores: {str(e)}"
        )


@router.get("/{workspace_id}/evaluation-metrics", response_model=ModelEvaluationMetrics)
async def get_model_evaluation_metrics(
    workspace_id: UUID,
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
) -> ModelEvaluationMetrics:
    """
    STEP 8: Get model evaluation metrics from user feedback.
    
    Tracks:
    - Total answers evaluated by users
    - Approval rate: % of answers marked as verified
    - Rejection rate: % of answers marked as rejected
    - Average rating: 1-5 star average (4-5 = approved)
    
    Args:
        workspace_id: Workspace ID
        current_user: Authenticated user
        db: Database session
        
    Returns:
        Model evaluation metrics
    """
    try:
        # Verify workspace access
        await _verify_workspace_access(workspace_id, current_user, db)
        
        # Get evaluation metrics
        feedback_handler = get_feedback_handler()
        metrics = await feedback_handler.get_model_evaluation_metrics(
            workspace_id=workspace_id,
            db=db
        )
        
        logger.info(
            f"STEP 8: Retrieved model evaluation metrics for workspace {workspace_id}: "
            f"approval_rate={metrics['approval_rate']:.1%}, "
            f"avg_rating={metrics['average_rating']:.2f}/5.0"
        )
        
        return ModelEvaluationMetrics(**metrics)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving evaluation metrics: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve evaluation metrics: {str(e)}"
        )
