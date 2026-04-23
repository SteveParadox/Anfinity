"""Schemas for connector API."""
from typing import Optional, List, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field


class ConnectorCreate(BaseModel):
    """Create connector request."""
    workspace_id: str
    connector_type: str
    access_token: str
    refresh_token: Optional[str] = None
    config: Dict[str, Any] = Field(default_factory=dict)


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
    scopes: List[str] = Field(default_factory=list)
    external_account_id: Optional[str] = None
    external_account_metadata: Dict[str, Any] = Field(default_factory=dict)
    sync_status: str = "idle"
    last_sync_at: Optional[datetime] = None
    last_sync_started_at: Optional[datetime] = None
    last_sync_completed_at: Optional[datetime] = None
    last_sync_error: Optional[str] = None
    sync_cursor: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: Optional[datetime] = None
    config: Dict[str, Any] = Field(default_factory=dict)
    
    class Config:
        from_attributes = True


class ConnectorListResponse(BaseModel):
    """Response for a list of connectors."""
    connectors: List[ConnectorResponse]


class ConnectorProviderResponse(BaseModel):
    provider: str
    label: str
    required_scopes: List[str]
    capability_scopes: Dict[str, List[str]] = Field(default_factory=dict)
    capabilities: List[str]


class OAuthAuthorizeResponse(BaseModel):
    authorization_url: str


class ConnectorSyncResponse(BaseModel):
    status: str
    connector_id: str
    provider: Optional[str] = None
    result: Dict[str, Any] = Field(default_factory=dict)
    task_id: Optional[str] = None


class ConnectorSyncItemResponse(BaseModel):
    id: str
    connector_id: str
    workspace_id: str
    provider: str
    external_type: str
    external_id: str
    sync_direction: str
    sync_status: str
    local_note_id: Optional[str] = None
    local_document_id: Optional[str] = None
    source_hash: Optional[str] = None
    external_updated_at: Optional[datetime] = None
    last_error: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    first_seen_at: Optional[datetime] = None
    last_seen_at: Optional[datetime] = None
    last_synced_at: Optional[datetime] = None


class ConnectorSyncItemListResponse(BaseModel):
    items: List[ConnectorSyncItemResponse]


class SlackPostRequest(BaseModel):
    channel_id: Optional[str] = None
    channel: Optional[str] = None
    title: str
    body: Optional[str] = None
    message: Optional[str] = None
    context: List[str] = Field(default_factory=list)
    buttons: List[Dict[str, Any]] = Field(default_factory=list)
    unfurl_links: bool = False
