"""Confluence connector for ingesting pages and spaces."""
from typing import Dict, Any, Optional, AsyncGenerator
import httpx
import base64

from app.connectors.base import BaseConnector, ConnectorType, ConnectorDocument


class ConfluenceConnector(BaseConnector):
    """Connector for Confluence spaces and pages."""
    
    connector_type = ConnectorType.CONFLUENCE
    
    def __init__(self, config: Dict[str, Any]):
        """Initialize Confluence connector.
        
        Config should contain:
        - domain: Confluence domain (e.g., company.atlassian.net)
        - username: Email or username
        - api_token: Confluence API token
        - space_keys: List of space keys to sync
        """
        super().__init__(config)
        self.domain = config.get("domain")
        self.base_url = f"https://{self.domain}/wiki/rest/api"
        
        # Create basic auth header
        credentials = f"{config.get('username')}:{config.get('api_token')}"
        encoded = base64.b64encode(credentials.encode()).decode()
        self.headers = {
            "Authorization": f"Basic {encoded}",
            "Content-Type": "application/json"
        }
    
    def validate_config(self) -> bool:
        """Validate Confluence configuration."""
        required = ["domain", "username", "api_token", "space_keys"]
        return all(key in self.config for key in required)
    
    async def authenticate(self) -> bool:
        """Verify Confluence access."""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.base_url}/user/current",
                    headers=self.headers,
                    timeout=10
                )
                return response.status_code == 200
        except Exception:
            return False
    
    async def list_documents(self) -> AsyncGenerator[ConnectorDocument, None]:
        """List all pages from configured spaces."""
        space_keys = self.config.get("space_keys", [])
        
        for space_key in space_keys:
            async for doc in self._list_space_pages(space_key):
                yield doc
    
    async def _list_space_pages(self, space_key: str) -> AsyncGenerator[ConnectorDocument, None]:
        """List all pages in a Confluence space."""
        try:
            async with httpx.AsyncClient() as client:
                start = 0
                limit = 50
                
                while True:
                    params = {
                        "spaceKey": space_key,
                        "limit": limit,
                        "start": start,
                        "expand": "history.createdBy"
                    }
                    
                    response = await client.get(
                        f"{self.base_url}/content",
                        headers=self.headers,
                        params=params,
                        timeout=30
                    )
                    
                    data = response.json()
                    results = data.get("results", [])
                    
                    if not results:
                        break
                    
                    for page in results:
                        doc = await self.get_document(page["id"])
                        if doc:
                            yield doc
                    
                    # Check for pagination
                    if start + limit >= data.get("size", 0):
                        break
                    
                    start += limit
        except Exception as e:
            print(f"Error listing space pages: {e}")
    
    async def get_document(self, document_id: str) -> Optional[ConnectorDocument]:
        """Get a specific Confluence page."""
        try:
            async with httpx.AsyncClient() as client:
                params = {
                    "expand": "body.storage,history.createdBy,space"
                }
                
                response = await client.get(
                    f"{self.base_url}/content/{document_id}",
                    headers=self.headers,
                    params=params,
                    timeout=10
                )
                response.raise_for_status()
                page_data = response.json()
                
                # Extract content
                content = self._extract_page_content(page_data)
                
                return ConnectorDocument(
                    id=document_id,
                    title=page_data.get("title", "Untitled"),
                    content=content,
                    source_type="confluence",
                    source_metadata={
                        "confluence_id": document_id,
                        "space_key": page_data.get("space", {}).get("key"),
                        "type": page_data.get("type"),
                    },
                    url=page_data.get("_links", {}).get("webui"),
                    author=page_data.get("history", {}).get("createdBy", {}).get("username"),
                    created_at=page_data.get("history", {}).get("createdDate"),
                    updated_at=page_data.get("history", {}).get("lastUpdatedDate"),
                )
        except Exception as e:
            print(f"Error getting document {document_id}: {e}")
            return None
    
    def _extract_page_content(self, page_data: dict) -> str:
        """Extract text content from Confluence page."""
        # Try to get storage format (HTML-like)
        body = page_data.get("body", {})
        storage = body.get("storage", {})
        content = storage.get("value", "")
        
        if content:
            # Simple HTML stripping - would be better with proper parser
            import re
            
            # Remove HTML tags
            content = re.sub(r'<[^>]+>', '', content)
            
            # Decode HTML entities
            import html
            content = html.unescape(content)
            
            return content.strip()
        
        return ""
