"""Workspace schemas."""
from datetime import datetime
from typing import Optional, List, Dict, Any
from uuid import UUID

from pydantic import BaseModel, Field


class WorkspaceCreate(BaseModel):
    """Workspace creation schema."""
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None


class WorkspaceUpdate(BaseModel):
    """Workspace update schema."""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    settings: Optional[Dict[str, Any]] = None


class WorkspaceMemberResponse(BaseModel):
    """Workspace member response schema."""
    user_id: UUID
    role: str
    joined_at: datetime
    user_name: Optional[str] = None
    user_email: Optional[str] = None
    
    class Config:
        from_attributes = True


class WorkspaceResponse(BaseModel):
    """Workspace response schema."""
    id: UUID
    name: str
    description: Optional[str]
    owner_id: UUID
    settings: Dict[str, Any]
    created_at: datetime
    updated_at: Optional[datetime]
    member_count: Optional[int] = None
    document_count: Optional[int] = None
    members: Optional[List[WorkspaceMemberResponse]] = None
    
    class Config:
        from_attributes = True


class WorkspaceListResponse(BaseModel):
    """Workspace list response schema."""
    items: List[WorkspaceResponse]
    total: int
