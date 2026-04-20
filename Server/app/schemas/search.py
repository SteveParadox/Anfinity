"""Search schemas."""
from datetime import datetime
from typing import Optional, List, Dict, Any
from uuid import UUID

from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    """Search request schema."""
    query: str = Field(..., min_length=1)
    limit: int = Field(default=10, ge=1, le=100)
    score_threshold: Optional[float] = Field(None, ge=0, le=1)
    filters: Dict[str, Any] = Field(default_factory=dict)


class SearchResult(BaseModel):
    """Search result schema."""
    chunk_id: UUID
    document_id: UUID
    workspace_id: UUID
    score: float
    text: str
    text_preview: str
    chunk_index: int
    source_kind: str
    source_type: str
    token_count: int = 0
    context_before: Optional[str] = None
    context_after: Optional[str] = None
    metadata: Dict[str, Any]
    created_at: str


class SearchResponse(BaseModel):
    """Search response schema."""
    query: str
    results: List[SearchResult]
    total: int
    took_ms: int
