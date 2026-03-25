"""Connector management API endpoints."""
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database.models import Connector, User as DBUser, Workspace
from app.database.session import get_db
from app.core.auth import get_current_user
from app.schemas.connectors import (
    ConnectorCreate, ConnectorUpdate, ConnectorResponse, ConnectorListResponse
)
from app.services.ingestion_orchestrator import IngestionOrchestrator
from app.ingestion.embedder import EmbeddingProvider

router = APIRouter(prefix="/connectors", tags=["connectors"])


@router.post("", response_model=ConnectorResponse)
async def create_connector(
    workspace_id: str,
    connector_data: ConnectorCreate,
    current_user: DBUser = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> ConnectorResponse:
    """Create a new connector for a workspace.
    
    Supports:
    - notion: Notion API token
    - slack: Slack OAuth token
    - gdrive: Google Drive OAuth token
    - confluence: Confluence URL + email + API token
    - github: GitHub OAuth token
    - email: IMAP server + email + password
    """
    # Verify workspace access
    workspace = db.query(Workspace).filter(
        Workspace.id == workspace_id,
        Workspace.owner_id == current_user.id).first()
    if not workspace:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workspace not found"
        )
    # Create connector
    connector = Connector( 
        workspace_id=workspace_id,
        type=connector_data.type,   
        name=connector_data.name,
        config=connector_data.config.dict()
    )
    db.add(connector)
    db.commit()
    db.refresh(connector)