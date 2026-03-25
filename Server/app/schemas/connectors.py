"""Schemas for connector API."""
from typing import Optional, List, Dict, Any
from datetime import datetime
from pydantic import BaseModel


class ConnectorCreate(BaseModel):
    """Create connector request."""
    connector_type: str
    access_token: str
    refresh_token: Optional[str] = None
    config: Optional[Dict[str, Any]] = None


class ConnectorUpdate(BaseModel):
    """Update connector request."""
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
    config: Optional[Dict[str, Any]] = None
    is_active: Optional[bool] = None


class ConnectorResponse(BaseModel):
    """Connector response."""
    id: str
    workspace_id: str
    user_id: str
    connector_type: str
    is_active: bool
    last_sync_at: Optional[datetime] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    config: Dict[str, Any] = {}
    
    class Config:
        from_attributes = True


class ConnectorListResponse(BaseModel):
    """Response for a list of connectors."""
    connectors: List[ConnectorResponse]