"""STEP 3 - Retrieve Top K Chunks REST API Endpoints."""
import logging
import time
from typing import Any, Dict, List, Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import settings
from app.core.auth import get_current_user
from app.database.models import Document, User as DBUser, Workspace, WorkspaceMember
from app.database.session import get_db
from app.ingestion.source_locations import source_location_payload
from app.services.top_k_retriever import get_top_k_retriever

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/retrieve", tags=["retrieval"])


class RetrievalRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    top_k: int = Field(default=4, ge=1, le=20)
    similarity_threshold: float = Field(default=0.45, ge=0.0, le=1.0)
    rerank_by: Literal["similarity", "diversity", "recency"] = "similarity"


class RetrievedChunkPayload(BaseModel):
    chunk_id: str
    document_id: str
    similarity: float = Field(..., ge=0.0, le=1.0)
    text: str
    source_kind: str
    source_type: str
    chunk_index: int = Field(..., ge=0)
    document_title: str
    token_count: int = 0
    context_before: Optional[str] = None
    context_after: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    citation_label: Optional[str] = None
    source_location: Dict[str, Any] = Field(default_factory=dict)


class RetrievalResponse(BaseModel):
    query: str
    chunks: List[RetrievedChunkPayload]
    total_retrieved: int
    average_similarity: float = Field(..., ge=0.0, le=1.0)
    stats: Dict[str, Any]
    retrieval_time_ms: float


class BulkRetrievalRequest(BaseModel):
    queries: List[str] = Field(..., min_length=1, max_length=10)
    top_k: int = Field(default=4, ge=1, le=20)


class BulkRetrievalResponse(BaseModel):
    results: Dict[str, RetrievalResponse]
    total_time_ms: float


def _verify_workspace_access(workspace_id: UUID, user: DBUser, db: Session) -> bool:
    workspace = db.query(Workspace).filter(Workspace.id == workspace_id).first()
    if not workspace:
        return False
    if workspace.owner_id == user.id:
        return True
    return (
        db.query(WorkspaceMember)
        .filter(WorkspaceMember.workspace_id == workspace_id, WorkspaceMember.user_id == user.id)
        .first()
        is not None
    )


def _build_stats(result: Any) -> Dict[str, Any]:
    chunks = list(getattr(result, "chunks", []) or [])
    unique_documents = len({str(chunk.document_id) for chunk in chunks})
    high_confidence = sum(1 for chunk in chunks if float(chunk.similarity or 0.0) >= 0.75)
    return {
        "unique_documents": unique_documents,
        "high_confidence_chunks": high_confidence,
        "retrieval_time_ms": round(float(getattr(result, "retrieval_time_ms", 0.0) or 0.0), 2),
        "query_embedding_dim": int(getattr(result, "query_embedding_dim", 0) or 0),
    }


def _as_payloads(result: Any) -> List[RetrievedChunkPayload]:
    return [
        RetrievedChunkPayload(
            chunk_id=str(chunk.chunk_id),
            document_id=str(chunk.document_id),
            similarity=float(chunk.similarity),
            text=chunk.text,
            source_kind="document",
            source_type=chunk.source_type,
            chunk_index=int(chunk.chunk_index),
            document_title=chunk.document_title,
            token_count=int(getattr(chunk, "token_count", 0) or 0),
            context_before=getattr(chunk, "context_before", None),
            context_after=getattr(chunk, "context_after", None),
            metadata=getattr(chunk, "metadata", None) or {},
            citation_label=((getattr(chunk, "metadata", None) or {}).get("citation_label")),
            source_location=source_location_payload(
                (getattr(chunk, "metadata", None) or {}),
                document_title=getattr(chunk, "document_title", ""),
            ),
        )
        for chunk in getattr(result, "chunks", [])
    ]


@router.post("/top-k/{workspace_id}", response_model=RetrievalResponse)
async def retrieve_top_k_chunks(
    workspace_id: str,
    request: RetrievalRequest,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user),
):
    start_time = time.time()
    try:
        ws_uuid = UUID(workspace_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid workspace ID format") from exc

    if not _verify_workspace_access(ws_uuid, current_user, db):
        raise HTTPException(status_code=403, detail="Access denied to this workspace")

    try:
        retriever = get_top_k_retriever(
            db=db,
            top_k=request.top_k,
            similarity_threshold=request.similarity_threshold,
        )
        if request.rerank_by != "similarity" and hasattr(retriever, "retrieve_with_reranking"):
            result = retriever.retrieve_with_reranking(
                query=request.query,
                workspace_id=ws_uuid,
                top_k=request.top_k,
                rerank_model=request.rerank_by,
            )
        else:
            result = retriever.retrieve(
                query=request.query,
                workspace_id=ws_uuid,
                top_k=request.top_k,
                similarity_threshold=request.similarity_threshold,
            )

        payloads = _as_payloads(result)
        took_ms = (time.time() - start_time) * 1000
        return RetrievalResponse(
            query=request.query,
            chunks=payloads,
            total_retrieved=len(payloads),
            average_similarity=round(float(getattr(result, "average_similarity", 0.0) or 0.0), 4),
            stats=_build_stats(result),
            retrieval_time_ms=round(took_ms, 2),
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Retrieval error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Retrieval failed: {exc}") from exc


@router.post("/bulk-retrieve/{workspace_id}", response_model=BulkRetrievalResponse)
async def bulk_retrieve_chunks(
    workspace_id: str,
    request: BulkRetrievalRequest,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user),
):
    start_time = time.time()
    ws_uuid = UUID(workspace_id)
    if not _verify_workspace_access(ws_uuid, current_user, db):
        raise HTTPException(status_code=403, detail="Access denied to this workspace")

    retriever = get_top_k_retriever(db=db, top_k=request.top_k, similarity_threshold=0.45)
    results: Dict[str, RetrievalResponse] = {}
    for query in request.queries:
        result = retriever.retrieve(query=query, workspace_id=ws_uuid, top_k=request.top_k)
        payloads = _as_payloads(result)
        results[query] = RetrievalResponse(
            query=query,
            chunks=payloads,
            total_retrieved=len(payloads),
            average_similarity=round(float(getattr(result, "average_similarity", 0.0) or 0.0), 4),
            stats=_build_stats(result),
            retrieval_time_ms=round(float(getattr(result, "retrieval_time_ms", 0.0) or 0.0), 2),
        )

    return BulkRetrievalResponse(results=results, total_time_ms=round((time.time() - start_time) * 1000, 2))


@router.get("/stats/{workspace_id}")
async def get_retrieval_stats(
    workspace_id: str,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user),
):
    ws_uuid = UUID(workspace_id)
    if not _verify_workspace_access(ws_uuid, current_user, db):
        raise HTTPException(status_code=403, detail="Access denied to this workspace")

    total_docs = db.query(Document).filter(Document.workspace_id == ws_uuid).count()
    indexed_docs = db.query(Document).filter(Document.workspace_id == ws_uuid, Document.status == "indexed").count()
    return {
        "workspace_id": workspace_id,
        "total_documents": total_docs,
        "indexed_documents": indexed_docs,
        "indexing_percentage": (indexed_docs / total_docs * 100) if total_docs else 0,
        "model_info": {
            "provider": settings.EMBEDDING_PROVIDER,
            "batch_size": settings.EMBEDDING_BATCH_SIZE,
        },
    }


@router.post("/search/{workspace_id}", response_model=RetrievalResponse)
async def semantic_search(
    workspace_id: str,
    query: str = Query(..., min_length=1, max_length=2000),
    top_k: int = Query(default=4, ge=1, le=20),
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user),
):
    return await retrieve_top_k_chunks(
        workspace_id=workspace_id,
        request=RetrievalRequest(query=query, top_k=top_k, similarity_threshold=0.45),
        db=db,
        current_user=current_user,
    )
