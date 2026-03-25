"""User schemas."""
from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class UserCreate(BaseModel):
    """User creation schema."""
    email: EmailStr
    password: str = Field(..., min_length=8)
    full_name: Optional[str] = None


class UserLogin(BaseModel):
    """User login schema."""
    email: EmailStr
    password: str


class UserResponse(BaseModel):
    """User response schema."""
    id: UUID
    email: str
    full_name: Optional[str]
    is_active: bool
    created_at: datetime
    
    class Config:
        from_attributes = True
