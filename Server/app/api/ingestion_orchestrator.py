"""Ingestion orchestrator that coordinates document fetching and processing."""
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta
import asyncio
import uuid

from sqlalchemy.orm import Session
from app.database.models import (
    Document, IngestionLog, Connector, Chunk, Embedding, User, Workspace
)
from app.ingestion.chunker import Chunker
from app.ingestion.embedder import EmbeddingProvider
from app.ingestion.vector_index import VectorIndex
from app.connectors.base import BaseConnector, ConnectorType
from app.connectors.notion import NotionConnector
from app.connectors.slack import SlackConnector
from app.connectors.gdrive import GoogleDriveConnector
from app.connectors.confluence import ConfluenceConnector
from app.connectors.github import GitHubConnector
from app.connectors.email import EmailConnector


class IngestionOrchestrator:
    """Orchestrates document ingestion from multiple sources."""
    
    def __init__(self, db: Session, embedding_provider: EmbeddingProvider):
        """Initialize orchestrator."""
        self.db = db
        self.embedding_provider = embedding_provider
        self.chunker = Chunker()
        self.vector_manager = VectorIndex()
        self.connector_classes = {
            ConnectorType.NOTION: NotionConnector,
            ConnectorType.SLACK: SlackConnector,
            ConnectorType.GDRIVE: GoogleDriveConnector,
            ConnectorType.CONFLUENCE: ConfluenceConnector,
            ConnectorType.GITHUB: GitHubConnector,
            ConnectorType.EMAIL: EmailConnector,
        }