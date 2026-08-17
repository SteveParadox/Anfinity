"""Embedding generation API endpoints."""
from typing import Optional, List, Dict, Any
import uuid
import logging

from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.database.models import Document, DocumentStatus, User as DBUser, Workspace, Chunk, Embedding
from app.database.session import get_db
from app.core.auth import get_current_user
from app.ingestion.embedder import Embedder
from app.ingestion.embedding_batch_processor import BatchEmbeddingProcessor
from app.services.vector_db import get_vector_db_client
from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/embeddings", tags=["embeddings"])


class EmbeddingBatchRequest(BaseModel):
    """Request to generate embeddings for document chunks."""
    document_id: str
    batch_size: Optional[int] = settings.EMBEDDING_BATCH_SIZE


class EmbeddingBatchResponse(BaseModel):
    """Response from embedding generation."""
    success: bool
    total_chunks: int
    processed_chunks: int
    failed_chunks: int
    vector_ids: List[str]
    duration_ms: float
    errors: List[str]


class EmbeddingStatusResponse(BaseModel):
    """Status of embeddings for a document."""
    document_id: str
    title: str
    total_chunks: int
    embedded_chunks: int
    embedding_percentage: float
    model_used: str
    model_dimension: int
    created_at: str


class WorkspaceEmbeddingStats(BaseModel):
    """Embedding statistics for workspace."""
    workspace_id: str
    total_documents: int
    indexed_documents: int
    total_chunks: int
    embedded_chunks: int
    embedding_percentage: float
    model_info: Dict[str, Any]


@router.post("/generate/document/{document_id}", response_model=EmbeddingBatchResponse)
async def generate_document_embeddings(
    document_id: str,
    batch_request: Optional[EmbeddingBatchRequest] = None,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user),
    background_tasks: BackgroundTasks = None
):
    """Generate embeddings for all chunks of a document.
    
    This endpoint:
    - Retrieves all pending chunks for the document
    - Generates embeddings using configured provider (OpenAI/Cohere/BGE)
    - Batches embeddings for efficiency (50-100 chunks per batch)
    - Stores embeddings in vector DB (Qdrant) with metadata payload
    - Tracks model version and reproducibility info
    
    Args:
        document_id: ID of document to embed
        batch_request: Optional batch configuration
        db: Database session
        current_user: Current authenticated user
        background_tasks: Background task executor
        
    Returns:
        EmbeddingBatchResponse with generation results
    """
    try:
        # Convert string ID to UUID
        try:
            doc_uuid = uuid.UUID(document_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid document ID format"
            )
        
        # Get document
        document = db.query(Document).filter(
            Document.id == doc_uuid
        ).first()
        
        if not document:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Document {document_id} not found"
            )
        
        # Check user has access to workspace
        workspace = db.query(Workspace).filter(
            Workspace.id == document.workspace_id
        ).first()
        
        if not workspace:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Workspace not found"
            )
        
        # Verify workspace membership
        from sqlalchemy import and_
        from app.database.models import WorkspaceMember
        
        member = db.query(WorkspaceMember).filter(
            and_(
                WorkspaceMember.workspace_id == workspace.id,
                WorkspaceMember.user_id == current_user.id
            )
        ).first()
        
        if not member and workspace.owner_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No access to this workspace"
            )
        
        # Update document status to PROCESSING
        document.status = DocumentStatus.PROCESSING
        db.commit()
        
        # Initialize embedding processor
        embedder = Embedder(provider=settings.EMBEDDING_PROVIDER)
        vector_db = get_vector_db_client(embedding_dim=embedder.dimension)
        
        batch_size = batch_request.batch_size if batch_request else settings.EMBEDDING_BATCH_SIZE
        processor = BatchEmbeddingProcessor(
            db=db,
            embedding_provider=embedder._provider,
            vector_db=vector_db,
            batch_size=batch_size
        )
        
        # Run embedding generation (can be async in background)
        if background_tasks:
            background_tasks.add_task(
                processor.process_document_chunks,
                document.id,
                workspace.id
            )
            
            return EmbeddingBatchResponse(
                success=True,
                total_chunks=0,  # Unknown yet
                processed_chunks=0,
                failed_chunks=0,
                vector_ids=[],
                duration_ms=0,
                errors=["Processing started in background"]
            )
        else:
            # Synchronous processing
            import asyncio
            result = asyncio.run(
                processor.process_document_chunks(
                    document.id,
                    workspace.id
                )
            )
            
            return EmbeddingBatchResponse(
                success=result["success"],
                total_chunks=result["total_chunks"],
                processed_chunks=result["processed_chunks"],
                failed_chunks=result["failed_chunks"],
                vector_ids=result["vector_ids"],
                duration_ms=result["duration_ms"],
                errors=result["errors"]
            )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating embeddings: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate embeddings: {str(e)}"
        )


@router.post("/generate/workspace/{workspace_id}/batch")
async def batch_generate_workspace_embeddings(
    workspace_id: str,
    limit: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user),
    background_tasks: BackgroundTasks = None
):
    """Generate embeddings for all pending documents in a workspace.
    
    This bulk operation:
    - Finds all documents with status=PROCESSING
    - Processes each document in sequence
    - Batches chunks efficiently
    - Tracks overall progress and errors
    
    Args:
        workspace_id: ID of workspace
        limit: Max documents to process
        db: Database session
        current_user: Current user
        background_tasks: Background executor
        
    Returns:
        Job status with total processing info
    """
    try:
        workspace_uuid = uuid.UUID(workspace_id)
        
        # Verify access
        workspace = db.query(Workspace).filter(
            Workspace.id == workspace_uuid
        ).first()
        
        if not workspace:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Workspace not found"
            )
        
        # Check authorization
        from app.database.models import WorkspaceMember
        from sqlalchemy import and_
        
        member = db.query(WorkspaceMember).filter(
            and_(
                WorkspaceMember.workspace_id == workspace.id,
                WorkspaceMember.user_id == current_user.id
            )
        ).first()
        
        if not member and workspace.owner_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No access to this workspace"
            )
        
        # Initialize processor
        embedder = Embedder(provider=settings.EMBEDDING_PROVIDER)
        vector_db = get_vector_db_client(embedding_dim=embedder.dimension)
        processor = BatchEmbeddingProcessor(
            db=db,
            embedding_provider=embedder._provider,
            vector_db=vector_db,
            batch_size=settings.EMBEDDING_BATCH_SIZE
        )
        
        if background_tasks:
            background_tasks.add_task(
                processor.process_pending_chunks,
                workspace.id,
                limit
            )
            
            return {
                "status": "processing",
                "message": "Batch embedding generation started in background"
            }
        else:
            import asyncio
            result = asyncio.run(
                processor.process_pending_chunks(workspace.id, limit)
            )
            return result
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in batch embedding: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.get("/status/document/{document_id}", response_model=EmbeddingStatusResponse)
async def get_document_embedding_status(
    document_id: str,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user)
):
    """Get embedding status for a specific document.
    
    Shows:
    - Total chunks
    - Embedded chunks count
    - Embedding percentage
    - Model used and dimension
    
    Args:
        document_id: Document ID
        db: Database session
        current_user: Current user
        
    Returns:
        EmbeddingStatusResponse with embedding info
    """
    try:
        doc_uuid = uuid.UUID(document_id)
        
        # Get document
        document = db.query(Document).filter(
            Document.id == doc_uuid
        ).first()
        
        if not document:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Document not found"
            )
        
        # Verify access
        workspace = db.query(Workspace).filter(
            Workspace.id == document.workspace_id
        ).first()
        
        if not workspace:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        
        from app.database.models import WorkspaceMember
        from sqlalchemy import and_
        
        member = db.query(WorkspaceMember).filter(
            and_(
                WorkspaceMember.workspace_id == workspace.id,
                WorkspaceMember.user_id == current_user.id
            )
        ).first()
        
        if not member and workspace.owner_id != current_user.id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
        
        # Get chunk and embedding counts
        chunks = db.query(Chunk).filter(
            Chunk.document_id == doc_uuid
        ).all()
        
        total_chunks = len(chunks)
        
        embedded_chunks = 0
        model_info = {"model_name": "unknown", "dimension": 1536}
        
        if chunks:
            embeddings = db.query(Embedding).filter(
                Embedding.chunk_id.in_([c.id for c in chunks])
            ).all()
            
            embedded_chunks = len(embeddings)
            
            if embeddings:
                first_embedding = embeddings[0]
                model_info = {
                    "model_name": first_embedding.model_used,
                    "dimension": first_embedding.embedding_dimension
                }
        
        percentage = (embedded_chunks / total_chunks * 100) if total_chunks > 0 else 0
        
        return EmbeddingStatusResponse(
            document_id=str(document.id),
            title=document.title,
            total_chunks=total_chunks,
            embedded_chunks=embedded_chunks,
            embedding_percentage=percentage,
            model_used=model_info["model_name"],
            model_dimension=model_info["dimension"],
            created_at=document.created_at.isoformat()
        )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting embedding status: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.get("/stats/workspace/{workspace_id}", response_model=WorkspaceEmbeddingStats)
async def get_workspace_embedding_stats(
    workspace_id: str,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user)
):
    """Get workspace-wide embedding statistics.
    
    Shows:
    - Total documents and indexed documents
    - Total chunks and embedded chunks
    - Overall embedding percentage
    - Model information used
    
    Args:
        workspace_id: Workspace ID
        db: Database session
        current_user: Current user
        
    Returns:
        WorkspaceEmbeddingStats with aggregate stats
    """
    try:
        ws_uuid = uuid.UUID(workspace_id)
        
        # Verify access
        workspace = db.query(Workspace).filter(
            Workspace.id == ws_uuid
        ).first()
        
        if not workspace:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        
        from app.database.models import WorkspaceMember
        from sqlalchemy import and_
        
        member = db.query(WorkspaceMember).filter(
            and_(
                WorkspaceMember.workspace_id == workspace.id,
                WorkspaceMember.user_id == current_user.id
            )
        ).first()
        
        if not member and workspace.owner_id != current_user.id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
        
        # Get document stats
        documents = db.query(Document).filter(
            Document.workspace_id == ws_uuid
        ).all()
        
        total_documents = len(documents)
        indexed_documents = len([d for d in documents if d.status == DocumentStatus.INDEXED])
        
        # Get chunk and embedding stats
        all_chunks = db.query(Chunk).filter(
            Chunk.document_id.in_([d.id for d in documents])
        ).all()
        
        total_chunks = len(all_chunks)
        
        embedded_chunks = 0
        model_info = embedder.model_info if settings.EMBEDDING_PROVIDER else {}
        
        if all_chunks:
            embeddings = db.query(Embedding).filter(
                Embedding.chunk_id.in_([c.id for c in all_chunks])
            ).all()
            embedded_chunks = len(embeddings)
            
            if embeddings:
                first_embedding = embeddings[0]
                model_info = {
                    "model_name": first_embedding.model_used,
                    "dimension": first_embedding.embedding_dimension,
                    "provider": "configured"
                }
        
        embedding_percentage = (embedded_chunks / total_chunks * 100) if total_chunks > 0 else 0
        
        return WorkspaceEmbeddingStats(
            workspace_id=str(workspace.id),
            total_documents=total_documents,
            indexed_documents=indexed_documents,
            total_chunks=total_chunks,
            embedded_chunks=embedded_chunks,
            embedding_percentage=embedding_percentage,
            model_info=model_info
        )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting embedding stats: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )
