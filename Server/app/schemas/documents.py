"""Document schemas."""
from datetime import datetime
from typing import Optional, List, Dict, Any
from uuid import UUID

from pydantic import BaseModel, Field


class DocumentCreate(BaseModel):
    """Document creation schema."""
    title: Optional[str] = None
    source_type: str = "upload"
    source_metadata: Dict[str, Any] = Field(default_factory=dict)


class ChunkResponse(BaseModel):
    """Chunk response schema."""
    id: UUID
    chunk_index: int
    text: str
    token_count: int
    context_before: Optional[str] = None
    context_after: Optional[str] = None
    metadata: Dict[str, Any]
    created_at: datetime
    
    class Config:
        from_attributes = True


class DocumentResponse(BaseModel):
    """Document response schema."""
    id: UUID
    workspace_id: UUID
    title: str
    source_type: str
    source_metadata: Dict[str, Any]
    storage_url: Optional[str] = None
    status: str
    token_count: int
    chunk_count: int
    processed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    chunks: Optional[List[ChunkResponse]] = None
    
    class Config:
        from_attributes = True


class DocumentListResponse(BaseModel):
    """Document list response schema."""
    items: List[DocumentResponse]
    total: int
    page: int
    page_size: int
