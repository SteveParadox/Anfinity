"""Note schemas."""
from datetime import datetime
from typing import Optional, List, Dict, Any
from uuid import UUID

from pydantic import BaseModel, Field


class NoteCreate(BaseModel):
    """Note creation schema."""
    title: str
    content: str
    workspace_id: Optional[UUID] = None
    note_type: str = Field(default="note", description="Type of note: note, web-clip, document, voice, ai-generated")
    tags: List[str] = Field(default_factory=list)
    source_url: Optional[str] = None


class NoteUpdate(BaseModel):
    """Note update schema."""
    title: Optional[str] = None
    content: Optional[str] = None
    note_type: Optional[str] = Field(default=None, description="Type of note: note, web-clip, document, voice, ai-generated")
    tags: Optional[List[str]] = None
    connections: Optional[List[UUID]] = None


class NoteResponse(BaseModel):
    """Note response schema."""
    id: UUID
    workspace_id: Optional[UUID]
    user_id: UUID
    title: str
    content: str
    summary: Optional[str] = None
    note_type: str
    tags: List[str]
    connections: List[UUID]
    ai_generated: bool
    confidence_score: Optional[float] = None
    source_url: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True


class NoteListResponse(BaseModel):
    """Note list response schema."""
    items: List[NoteResponse]
    total: int
    page: int
    page_size: int
