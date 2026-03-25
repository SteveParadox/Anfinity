"""External data source connectors for knowledge ingestion."""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Dict, Any, Optional, AsyncGenerator
from enum import Enum


class ConnectorType(str, Enum):
    """Supported connector types."""
    NOTION = "notion"
    SLACK = "slack"
    GDRIVE = "gdrive"
    CONFLUENCE = "confluence"
    GITHUB = "github"
    EMAIL = "email"
    UPLOAD = "upload"


@dataclass
class ConnectorDocument:
    """Document from external source."""
    id: str
    title: str
    content: str
    source_type: str
    source_metadata: Dict[str, Any]
    url: Optional[str] = None
    author: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class BaseConnector(ABC):
    """Abstract base class for data source connectors."""
    
    connector_type: ConnectorType
    
    def __init__(self, config: Dict[str, Any]):
        """Initialize connector with configuration.
        
        Args:
            config: Connector-specific configuration (tokens, IDs, etc.)
        """
        self.config = config
    
    @abstractmethod
    async def authenticate(self) -> bool:
        """Verify authentication is valid.
        
        Returns:
            True if authenticated, False otherwise
        """
        pass
    
    @abstractmethod
    async def list_documents(self) -> AsyncGenerator[ConnectorDocument, None]:
        """List all available documents from source.
        
        Yields:
            ConnectorDocument objects
        """
        pass
    
    @abstractmethod
    async def get_document(self, document_id: str) -> ConnectorDocument:
        """Get a specific document.
        
        Args:
            document_id: ID of document to retrieve
            
        Returns:
            ConnectorDocument
        """
        pass
    
    async def sync(self) -> AsyncGenerator[ConnectorDocument, None]:
        """Sync documents from source.
        
        Can be overridden for custom sync logic (incremental, etc.)
        
        Yields:
            ConnectorDocument objects
        """
        async for doc in self.list_documents():
            yield doc
    
    def validate_config(self) -> bool:
        """Validate configuration has all required fields.
        
        Override in subclass to check specific fields.
        
        Returns:
            True if valid, False otherwise
        """
        return True
