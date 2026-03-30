"""RAG Query API routes."""
import time
import logging
from typing import List, Optional, Dict, Any
from uuid import UUID
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
import openai

from app.database.session import get_db
from app.database.models import Query, Answer, Chunk, Document, User as DBUser, Feedback
from app.core.auth import get_current_active_user, get_workspace_context, WorkspaceContext
from app.core.audit import AuditAction, EntityType, AuditLogger
from app.services.embeddings import get_embedding_service
from app.services.vector_db import get_vector_db_client
from app.services.rag_retriever import get_rag_retriever
from app.tasks.embeddings import queue_embedding_task
from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/query", tags=["Query"])

# Initialize OpenAI client with timeout
# LLM operations should timeout after 60 seconds to prevent hanging requests
openai_client = openai.OpenAI(
    api_key=settings.OPENAI_API_KEY,
    timeout=60.0  # 60 second timeout for LLM operations
)


@router.post("/embeddings/process")
async def process_embeddings_for_workspace(
    workspace_id: str,
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """Queue embedding generation for all chunks in a workspace.
    
    Args:
        workspace_id: Workspace UUID
        current_user: Authenticated user
        db: Database session
        
    Returns:
        Task info
    """
    try:
        workspace_uuid = UUID(workspace_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid workspace ID")
    
    # Verify workspace access
    await get_workspace_context(workspace_uuid, current_user, db)
    
    try:
        # Query all chunks in workspace
        result = await db.execute(
            select(Chunk.id, Chunk.text, Document.workspace_id)
            .join(Document, Chunk.document_id == Document.id)
            .where(Document.workspace_id == workspace_uuid)
        )
        
        rows = result.all()
        
        if not rows:
            return {
                "status": "success",
                "workspace_id": workspace_id,
                "chunks_queued": 0,
                "message": "No chunks found to embed"
            }
        
        chunk_ids = [str(row[0]) for row in rows]
        texts = [row[1] for row in rows]
        
        # Queue embedding task
        task_id = queue_embedding_task(
            workspace_id=workspace_id,
            chunk_ids=chunk_ids,
            texts=texts
        )
        
        logger.info(f"Queued embedding task {task_id} for {len(chunk_ids)} chunks in workspace {workspace_id}")
        
        return {
            "status": "queued",
            "workspace_id": workspace_id,
            "chunks_queued": len(chunk_ids),
            "task_id": task_id,
            "message": f"Successfully queued {len(chunk_ids)} chunks for embedding"
        }
    
    except Exception as e:
        logger.error(f"Error queuing embeddings: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to queue embeddings: {str(e)}"
        )


@router.get("/status/{workspace_id}")
async def get_embedding_status(
    workspace_id: str,
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """Get embedding status for workspace.
    
    Args:
        workspace_id: Workspace ID
        current_user: Authenticated user
        db: Database session
        
    Returns:
        Status info
    """
    try:
        workspace_uuid = UUID(workspace_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid workspace ID")
    
    await get_workspace_context(workspace_uuid, current_user, db)
    
    try:
        # Count total chunks
        result = await db.execute(
            select(func.count(Chunk.id))
            .join(Document, Chunk.document_id == Document.id)
            .where(Document.workspace_id == workspace_uuid)
        )
        
        total_chunks = result.scalar() or 0
        
        # TODO: Check vector DB for embedded chunks count
        embedded_chunks = total_chunks  # Placeholder
        
        return {
            "workspace_id": workspace_id,
            "total_chunks": total_chunks,
            "embedded_chunks": embedded_chunks,
            "status": "ready" if total_chunks > 0 else "pending",
            "progress_percent": 100 if total_chunks > 0 else 0
        }
    
    except Exception as e:
        logger.error(f"Error getting embedding status: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


# Schemas
class QueryRequest(BaseModel):
    """Query request schema."""
    workspace_id: UUID
    query: str = Field(..., min_length=1, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=20)
    include_sources: bool = True
    model: str = Field(default="gpt-4o-mini")


class Source(BaseModel):
    """Source chunk schema."""
    chunk_id: str
    document_id: str
    document_title: str
    text: str
    similarity: float


class QueryResponse(BaseModel):
    """Query response schema."""
    query_id: str
    answer: str
    confidence: float
    confidence_factors: Dict[str, float]
    sources: List[Source]
    model_used: str
    tokens_used: int
    response_time_ms: int


class VerificationRequest(BaseModel):
    """Verification request schema."""
    status: str = Field(..., pattern="^(approved|rejected)$")
    comment: Optional[str] = Field(None, max_length=1000)


class VerificationResponse(BaseModel):
    """Verification response schema."""
    answer_id: str
    status: str
    verified_by: str
    comment: Optional[str]
    verified_at: str


class FeedbackRequest(BaseModel):
    """Feedback request schema."""
    rating: int = Field(..., ge=1, le=5)
    comment: Optional[str] = Field(None, max_length=1000)


def calculate_confidence_deprecated(
    similarities: List[float],
    sources: List[Dict[str, Any]],
    answer_text: str
) -> tuple[float, Dict[str, float]]:
    """DEPRECATED: Calculate confidence score for answer.
    
    Note: Use RAGRetriever.retrieve() instead, which returns
    confidence_score already calculated with 3-component formula.
    
    Formula:
    - Base: Average similarity of retrieved chunks
    - Bonus: Number of unique documents (diversity)
    - Penalty: Low similarity chunks
    
    Args:
        similarities: List of similarity scores
        sources: List of source documents
        answer_text: Generated answer text
        
    Returns:
        Tuple of (confidence_score, factors_dict)
    """

    if not similarities:
        return 0.0, {"similarity_avg": 0.0, "document_diversity": 0.0, "source_coverage": 0.0}
    
    # Factor 1: Average similarity (0-1)
    similarity_avg = sum(similarities) / len(similarities)
    
    # Factor 2: Document diversity (0-1)
    unique_docs = len(set(s.get("document_id") for s in sources))
    document_diversity = min(unique_docs / 3, 1.0)  # Max bonus at 3+ docs
    
    # Factor 3: Source coverage (0-1)
    # Penalize if top similarity is too low
    top_similarity = max(similarities) if similarities else 0
    source_coverage = 1.0 if top_similarity > 0.7 else top_similarity
    
    # Combined confidence
    # Weights: similarity 50%, diversity 25%, coverage 25%
    confidence = (
        similarity_avg * 0.5 +
        document_diversity * 0.25 +
        source_coverage * 0.25
    )
    
    # Clamp to 0-1
    confidence = max(0.0, min(1.0, confidence))
    
    factors = {
        "similarity_avg": round(similarity_avg, 3),
        "document_diversity": round(document_diversity, 3),
        "source_coverage": round(source_coverage, 3),
        "chunks_retrieved": len(similarities),
        "unique_documents": unique_docs
    }
    
    return round(confidence, 3), factors


@router.post("", response_model=QueryResponse)
async def query(
    query_request: QueryRequest,
    request: Request,
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """Execute RAG query against workspace documents.
    
    Uses the production RAG pipeline:
    1. Retrieve relevant chunks using vector similarity
    2. Generate answer using LLM with retrieved context
    3. Calculate confidence score
    4. Store results for audit and feedback
    
    Args:
        query_request: Query parameters
        request: FastAPI request object
        current_user: Authenticated user
        db: Database session
        
    Returns:
        Query response with answer and sources
    """
    start_time = time.time()
    step_times = {}
    
    # Verify workspace membership
    auth_start = time.time()
    context = await get_workspace_context(
        query_request.workspace_id, current_user, db
    )
    step_times['auth'] = (time.time() - auth_start) * 1000
    
    # Create query record
    db_start = time.time()
    query_record = Query(
        workspace_id=query_request.workspace_id,
        user_id=current_user.id,
        query_text=query_request.query
    )
    db.add(query_record)
    await db.commit()
    await db.refresh(query_record)
    step_times['db_insert'] = (time.time() - db_start) * 1000
    
    logger.info(f"Query {query_record.id}: '{query_request.query[:50]}...' in workspace {query_request.workspace_id}")
    
    try:
        # Step 1: Retrieve relevant chunks using RAG retriever
        vec_start = time.time()
        retriever = get_rag_retriever(
            similarity_threshold=0.5,
            top_k=query_request.top_k
        )
        
        rag_result = retriever.retrieve(
            query=query_request.query,
            workspace_id=str(query_request.workspace_id)
        )
        step_times['vector_search'] = (time.time() - vec_start) * 1000
        
        if not rag_result.chunks:
            # No relevant documents found
            response_time_ms = int((time.time() - start_time) * 1000)
            logger.info(f"Query {query_record.id}: Total={response_time_ms}ms | No sources found")
            
            answer = Answer(
                query_id=query_record.id,
                workspace_id=query_request.workspace_id,
                answer_text="I couldn't find any relevant information in your documents to answer this question.",
                confidence_score=0.0,
                sources=[],
                model_used=query_request.model,
                tokens_used=0
            )
            db.add(answer)
            await db.commit()
            
            logger.info(f"Query {query_record.id}: No sources found")
            
            return QueryResponse(
                query_id=str(query_record.id),
                answer="I couldn't find any relevant information in your documents to answer this question.",
                confidence=0.0,
                confidence_factors={"similarity_avg": 0.0, "document_diversity": 0.0, "source_coverage": 0.0},
                sources=[],
                model_used=query_request.model,
                tokens_used=0,
                response_time_ms=response_time_ms
            )
        
        # Step 2: Build sources from retrieved chunks
        sources = []
        chunk_texts = []
        similarities = []
        
        for chunk in rag_result.chunks:
            # Get full chunk info from database
            result = await db.execute(
                select(Chunk, Document)
                .join(Document, Chunk.document_id == Document.id)
                .where(Chunk.id == UUID(chunk.chunk_id))
            )
            row = result.first()
            
            if row:
                chunk_obj, document = row
                chunk_texts.append(chunk_obj.text)
                similarities.append(chunk.similarity)
                
                sources.append(Source(
                    chunk_id=str(chunk_obj.id),
                    document_id=str(document.id),
                    document_title=document.title,
                    text=chunk_obj.text[:500] + "..." if len(chunk_obj.text) > 500 else chunk_obj.text,
                    similarity=round(chunk.similarity, 3)
                ))
        
        logger.debug(f"Query {query_record.id}: Retrieved {len(sources)} sources")
        
        # Step 3: Build context for LLM
        context_text = "\n\n".join([
            f"[Document {i+1}]: {text}"
            for i, text in enumerate(chunk_texts)
        ])
        
        # Step 4: Generate answer with LLM
        llm_start = time.time()
        system_prompt = """
        
        You are a precise, document-grounded assistant. Your sole purpose is to answer questions using ONLY the information explicitly stated in the provided documents.

## Core Rules
- **Ground every claim** in the provided documents. Do not use prior knowledge, assumptions, or inferences beyond what the text supports.
- **Cite sources inline** using [Doc N] notation immediately after each claim (e.g., "The policy takes effect in January [Doc 2].").
- **Handle ambiguity explicitly**: If a question is unclear, state your interpretation before answering.
- **Acknowledge gaps honestly**: If the documents lack sufficient information, say exactly that — do not speculate or fill gaps with general knowledge.
- **Resolve conflicts transparently**: If documents contradict each other, surface the conflict and present both perspectives with their respective citations rather than silently favoring one.

## Response Structure
1. Answer the question directly and concisely.
2. Support each key claim with an inline citation.
3. If relevant context is missing, end with: *"Note: The provided documents do not address [specific gap]."*

## Hard Limits
- Never fabricate, infer, or extrapolate facts not present in the documents.
- Never say "based on my knowledge" — your only knowledge source is the provided documents.
- If NO documents are relevant to the question, respond: *"The provided documents do not contain information relevant to this question."*
        
        
        """
        
        user_prompt = f"""Documents:
{context_text}

Question: {query_request.query}

Answer:"""
        
        try:
            llm_response = openai_client.chat.completions.create(
                model=query_request.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.3,
                max_tokens=1000
            )
            
            answer_text = llm_response.choices[0].message.content
            tokens_used = llm_response.usage.total_tokens
            step_times['llm_response'] = (time.time() - llm_start) * 1000
            
            logger.debug(f"Query {query_record.id}: LLM generated answer ({tokens_used} tokens)")
            
        except Exception as e:
            logger.error(f"Query {query_record.id}: LLM error: {str(e)}")
            # Fallback if LLM fails
            answer_text = f"Error generating answer: {str(e)}"
            tokens_used = 0
            step_times['llm_response'] = (time.time() - llm_start) * 1000
        
        # Step 5: Use confidence from RAG retriever
        source_dicts = [{"document_id": s.document_id} for s in sources]
        confidence = rag_result.confidence
        
        factors = {
            "similarity_avg": round(rag_result.avg_similarity, 3),
            "document_diversity": rag_result.unique_documents,
            "source_coverage": round(confidence, 3),
            "chunks_retrieved": len(rag_result.chunks),
            "unique_documents": rag_result.unique_documents
        }
        
        # Step 6: Store answer
        db_store_start = time.time()
        answer = Answer(
            query_id=query_record.id,
            workspace_id=query_request.workspace_id,
            answer_text=answer_text,
            confidence_score=confidence,
            sources=[
                {
                    "chunk_id": s.chunk_id,
                    "document_id": s.document_id,
                    "similarity": s.similarity
                }
                for s in sources
            ],
            model_used=query_request.model,
            tokens_used=tokens_used
        )
        db.add(answer)
        await db.commit()
        await db.refresh(answer)
        step_times['db_store'] = (time.time() - db_store_start) * 1000
        
        logger.info(f"Query {query_record.id}: Answer saved (confidence={confidence:.3f})")
        
        # Log audit
        audit_logger = AuditLogger(db, current_user.id).with_request(request)
        await audit_logger.log(
            action=AuditAction.ANSWER_GENERATED,
            workspace_id=query_request.workspace_id,
            entity_type=EntityType.ANSWER,
            entity_id=answer.id,
            metadata={
                "query_id": str(query_record.id),
                "confidence": confidence,
                "sources_count": len(sources)
            }
        )
        
        # Log detailed timing breakdown
        response_time_ms = int((time.time() - start_time) * 1000)
        timing_msg = f"Query {query_record.id}: Total={response_time_ms}ms | "
        timing_msg += f"Auth={step_times.get('auth', 0):.0f}ms | "
        timing_msg += f"DB_Insert={step_times.get('db_insert', 0):.0f}ms | "
        timing_msg += f"Vector_Search={step_times.get('vector_search', 0):.0f}ms | "
        timing_msg += f"LLM_Response={step_times.get('llm_response', 0):.0f}ms | "
        timing_msg += f"DB_Store={step_times.get('db_store', 0):.0f}ms"
        logger.info(timing_msg)
        
        return QueryResponse(
            query_id=str(query_record.id),
            answer=answer_text,
            confidence=confidence,
            confidence_factors=factors,
            sources=sources if query_request.include_sources else [],
            model_used=query_request.model,
            tokens_used=tokens_used,
            response_time_ms=response_time_ms
        )
    
    except RuntimeError as e:
        # Catch embedding service errors (priority provider failed, Ollama failed, etc.)
        error_msg = str(e)
        if "embedding" in error_msg.lower() or "provider" in error_msg.lower():
            logger.error(f"Query {query_record.id}: Embedding service failure: {error_msg}")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Embedding service is currently unavailable. Please try again later."
            )
        # Other RuntimeErrors pass through to generic handler
        raise
    
    except Exception as e:
        logger.error(f"Query {query_record.id}: Unhandled error: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Query failed: {str(e)}"
        )


# REMOVED: Duplicate feedback endpoints
# These endpoints are now located in /answers.py to maintain single source of truth:
# - POST /answers/{answer_id}/feedback (was: POST /query/{answer_id}/feedback)
# - POST /answers/{answer_id}/verify (was: POST /query/{answer_id}/verify)
# 
# This change ensures all answer-related operations go through the /answers prefix
# and prevents confusion from having the same functionality in multiple places.
