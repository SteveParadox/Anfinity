"""STEP 3 - Retrieve Top K Chunks REST API Endpoints"""
import logging
from typing import List, Dict, Any, Optional
from uuid import UUID
import time

from fastapi import APIRouter, Depends, HTTPException, status, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.database.models import User as DBUser, Workspace, WorkspaceMember, Document
from app.core.auth import get_current_user
from app.services.top_k_retriever import TopKRetriever, get_top_k_retriever
from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/retrieve", tags=["retrieval"])


# Request/Response Models

class RetrievalRequest(BaseModel):
    """Request to retrieve top K chunks."""
    query: str = Field(..., min_length=1, max_length=2000, description="Query text")
    top_k: int = Field(default=10, ge=1, le=20, description="Number of chunks to retrieve (10-15 recommended)")
    similarity_threshold: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Minimum similarity threshold (0.0 = disabled)"
    )
    rerank_by: str = Field(
        default="similarity",
        description="Re-ranking strategy: similarity|diversity|recency"
    )


class RetrievedChunkPayload(BaseModel):
    """Payload for retrieved chunk - STEP 3 specification."""
    chunk_id: str = Field(..., description="Unique chunk identifier")
    document_id: str = Field(..., description="Source document ID")
    similarity: float = Field(..., ge=0.0, le=1.0, description="Similarity score (0-1)")
    text: str = Field(..., description="Full chunk text content")
    source_type: str = Field(..., description="Document source (pdf|upload|slack|notion|gdrive|github|email|web_clip)")
    chunk_index: int = Field(..., ge=0, description="Chunk position in document")
    document_title: str = Field(..., description="Source document title")
    token_count: int = Field(default=0, description="Tokens in chunk")
    context_before: Optional[str] = Field(None, description="Previous chunk snippet for context")
    context_after: Optional[str] = Field(None, description="Next chunk snippet for context")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Custom metadata")


class RetrievalResponse(BaseModel):
    """Response from top-K retrieval endpoint."""
    query: str = Field(..., description="Original query")
    chunks: List[RetrievedChunkPayload] = Field(..., description="Retrieved chunks with metadata")
    total_retrieved: int = Field(..., description="Total chunks retrieved")
    average_similarity: float = Field(..., ge=0.0, le=1.0, description="Average similarity score")
    stats: Dict[str, Any] = Field(..., description="Retrieval statistics")
    retrieval_time_ms: float = Field(..., description="Time taken for retrieval")


class BulkRetrievalRequest(BaseModel):
    """Request to retrieve chunks for multiple queries."""
    queries: List[str] = Field(..., min_items=1, max_items=10, description="List of queries")
    top_k: int = Field(default=10, ge=1, le=20)
    batch_retrieve: bool = Field(default=False, description="Retrieve once for combined query")


class BulkRetrievalResponse(BaseModel):
    """Response with multiple retrieval results."""
    results: Dict[str, RetrievalResponse] = Field(..., description="Results per query")
    total_time_ms: float


# Helper Functions

def _verify_workspace_access(workspace_id: UUID, user: DBUser, db: Session) -> bool:
    """Verify user has access to workspace."""
    workspace = db.query(Workspace).filter(Workspace.id == workspace_id).first()
    if not workspace:
        return False
    
    if workspace.owner_id == user.id:
        return True
    
    member = db.query(WorkspaceMember).filter(
        WorkspaceMember.workspace_id == workspace_id,
        WorkspaceMember.user_id == user.id
    ).first()
    
    return member is not None


# API Endpoints

@router.post("/top-k/{workspace_id}", response_model=RetrievalResponse)
async def retrieve_top_k_chunks(
    workspace_id: str,
    request: RetrievalRequest,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user)
):
    """STEP 3: Retrieve Top K Chunks using semantic similarity.
    
    Pipeline:
    1. Embed query text using same model as document chunks
    2. Search vector database (Qdrant) with workspace filtering
    3. Retrieve top K (10-15) nearest neighbors by similarity
    4. Return chunks with similarity scores and full metadata
    
    Args:
        workspace_id: Workspace ID for filtering results
        request: RetrievalRequest with query and parameters
        db: Database session
        current_user: Current authenticated user
        
    Returns:
        RetrievalResponse with retrieved chunks and metadata
        
    Example Response:
    {
        "query": "What is the document about?",
        "chunks": [
            {
                "chunk_id": "chunk-uuid",
                "document_id": "doc-uuid",
                "similarity": 0.87,
                "text": "Full chunk text...",
                "source_type": "pdf",
                "chunk_index": 2,
                "document_title": "My Document",
                "metadata": {...}
            },
            ...
        ],
        "total_retrieved": 10,
        "average_similarity": 0.82,
        "stats": {
            "unique_documents": 3,
            "high_confidence_chunks": 7,
            "retrieval_time_ms": 234.5
        }
    }
    """
    start_time = time.time()
    
    try:
        # Parse workspace ID
        try:
            ws_uuid = UUID(workspace_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid workspace ID format"
            )
        
        # Verify access
        if not _verify_workspace_access(ws_uuid, current_user, db):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied to this workspace"
            )
        
        logger.info(
            f"Retrieval request from {current_user.email}: "
            f"query='{request.query[:50]}...' top_k={request.top_k}"
        )
        
        # Step 1: Initialize retriever
        retriever = get_top_k_retriever(
            db=db,
            top_k=request.top_k,
            similarity_threshold=request.similarity_threshold
        )
        
        # Step 2: Retrieve chunks with optional re-ranking
        if request.rerank_by in ["diversity", "recency"]:
            result = retriever.retrieve_with_reranking(
                query=request.query,
                workspace_id=ws_uuid,
                top_k=request.top_k,
                rerank_model=request.rerank_by
            )
        else:
            result = retriever.retrieve(
                query=request.query,
                workspace_id=ws_uuid,
                top_k=request.top_k,
                similarity_threshold=request.similarity_threshold
            )
        
        # Step 3: Build response with STEP 3 payload structure
        retrieved_chunks = [
            RetrievedChunkPayload(
                chunk_id=chunk.chunk_id,
                document_id=chunk.document_id,
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
        
        # Step 4: Get statistics
        stats = retriever.get_query_stats(result)
        
        total_time_ms = (time.time() - start_time) * 1000
        
        logger.info(
            f"✓ Retrieved {result.total_retrieved} chunks "
            f"(avg_similarity={result.average_similarity:.4f}, time={total_time_ms:.1f}ms)"
        )
        
        return RetrievalResponse(
            query=request.query,
            chunks=retrieved_chunks,
            total_retrieved=result.total_retrieved,
            average_similarity=result.average_similarity,
            stats=stats,
            retrieval_time_ms=total_time_ms
        )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Retrieval error: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Retrieval failed: {str(e)}"
        )


@router.post("/bulk-retrieve/{workspace_id}")
async def bulk_retrieve_chunks(
    workspace_id: str,
    request: BulkRetrievalRequest,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user)
):
    """Retrieve chunks for multiple queries.
    
    Args:
        workspace_id: Workspace ID
        request: BulkRetrievalRequest with multiple queries
        db: Database session
        current_user: Current user
        
    Returns:
        BulkRetrievalResponse with results per query
    """
    start_time = time.time()
    
    try:
        ws_uuid = UUID(workspace_id)
        
        # Verify access
        if not _verify_workspace_access(ws_uuid, current_user, db):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
        
        retriever = get_top_k_retriever(db=db, top_k=request.top_k)
        results = {}
        
        for query in request.queries:
            result = retriever.retrieve(
                query=query,
                workspace_id=ws_uuid,
                top_k=request.top_k
            )
            
            chunks = [
                RetrievedChunkPayload(
                    chunk_id=chunk.chunk_id,
                    document_id=chunk.document_id,
                    similarity=chunk.similarity,
                    text=chunk.text,
                    source_type=chunk.source_type,
                    chunk_index=chunk.chunk_index,
                    document_title=chunk.document_title
                )
                for chunk in result.chunks
            ]
            
            results[query] = RetrievalResponse(
                query=query,
                chunks=chunks,
                total_retrieved=result.total_retrieved,
                average_similarity=result.average_similarity,
                stats=retriever.get_query_stats(result),
                retrieval_time_ms=result.retrieval_time_ms
            )
        
        total_time_ms = (time.time() - start_time) * 1000
        
        return {
            "results": results,
            "total_time_ms": total_time_ms
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Bulk retrieval error: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.get("/stats/{workspace_id}")
async def get_retrieval_stats(
    workspace_id: str,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user)
):
    """Get retrieval statistics for workspace.
    
    Returns info about indexed documents and chunks.
    
    Args:
        workspace_id: Workspace ID
        db: Database session
        current_user: Current user
        
    Returns:
        Statistics including total documents, chunks, embeddings
    """
    try:
        ws_uuid = UUID(workspace_id)
        
        if not _verify_workspace_access(ws_uuid, current_user, db):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
        
        # Get document counts
        total_docs = db.query(Document).filter(
            Document.workspace_id == ws_uuid
        ).count()
        
        indexed_docs = db.query(Document).filter(
            Document.workspace_id == ws_uuid,
            Document.status == "indexed"
        ).count()
        
        return {
            "workspace_id": workspace_id,
            "total_documents": total_docs,
            "indexed_documents": indexed_docs,
            "indexing_percentage": (indexed_docs / total_docs * 100) if total_docs > 0 else 0,
            "model_info": {
                "provider": settings.EMBEDDING_PROVIDER,
                "batch_size": settings.EMBEDDING_BATCH_SIZE
            }
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Stats error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.post("/search/{workspace_id}")
async def semantic_search(
    workspace_id: str,
    query: str = Query(..., min_length=1, max_length=2000),
    top_k: int = Query(default=10, ge=1, le=20),
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user)
):
    """Quick semantic search endpoint (GET-style POST).
    
    Args:
        workspace_id: Workspace ID
        query: Search query
        top_k: Results to return
        db: Database session
        current_user: Current user
        
    Returns:
        RetrievalResponse
    """
    request = RetrievalRequest(
        query=query,
        top_k=top_k,
        similarity_threshold=0.0
    )
    
    return await retrieve_top_k_chunks(
        workspace_id=workspace_id,
        request=request,
        db=db,
        current_user=current_user
    )
