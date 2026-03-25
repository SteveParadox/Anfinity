"""Notion connector for ingesting pages and databases."""
from typing import Dict, Any, Optional, AsyncGenerator
import httpx
import json
from datetime import datetime

from app.connectors.base import BaseConnector, ConnectorType, ConnectorDocument


class NotionConnector(BaseConnector):
    """Connector for Notion workspace pages and databases."""
    
    connector_type = ConnectorType.NOTION
    
    def __init__(self, config: Dict[str, Any]):
        """Initialize Notion connector.
        
        Config should contain:
        - integration_token: Notion integration token
        - database_ids: List of database IDs to sync (optional)
        - page_ids: List of page IDs to sync (optional)
        """
        super().__init__(config)
        self.base_url = "https://api.notion.com/v1"
        self.headers = {
            "Authorization": f"Bearer {config.get('integration_token')}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json"
        }
    
    def validate_config(self) -> bool:
        """Validate Notion configuration."""
        return "integration_token" in self.config
    
    async def authenticate(self) -> bool:
        """Verify Notion access token is valid."""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.base_url}/me",
                    headers=self.headers,
                    timeout=10
                )
                return response.status_code == 200
        except Exception:
            return False
    
    async def list_documents(self) -> AsyncGenerator[ConnectorDocument, None]:
        """List all accessible Notion pages and database items."""
        # List databases
        database_ids = self.config.get("database_ids", [])
        for db_id in database_ids:
            async for doc in self._list_database_items(db_id):
                yield doc
        
        # List pages
        page_ids = self.config.get("page_ids", [])
        for page_id in page_ids:
            doc = await self.get_document(page_id)
            if doc:
                yield doc
    
    async def _list_database_items(self, database_id: str) -> AsyncGenerator[ConnectorDocument, None]:
        """List all items in a Notion database."""
        try:
            async with httpx.AsyncClient() as client:
                start_cursor = None
                
                while True:
                    payload = {}
                    if start_cursor:
                        payload["start_cursor"] = start_cursor
                    
                    response = await client.post(
                        f"{self.base_url}/databases/{database_id}/query",
                        headers=self.headers,
                        json=payload,
                        timeout=30
                    )
                    response.raise_for_status()
                    
                    results = response.json().get("results", [])
                    
                    for item in results:
                        doc = await self._page_to_document(item)
                        if doc:
                            yield doc
                    
                    # Check for pagination
                    if response.json().get("has_more"):
                        start_cursor = response.json().get("next_cursor")
                    else:
                        break
        except Exception as e:
            print(f"Error listing database items: {e}")
    
    async def get_document(self, document_id: str) -> Optional[ConnectorDocument]:
        """Get a specific Notion page."""
        try:
            async with httpx.AsyncClient() as client:
                # Get page metadata
                page_response = await client.get(
                    f"{self.base_url}/pages/{document_id}",
                    headers=self.headers,
                    timeout=10
                )
                page_response.raise_for_status()
                page_data = page_response.json()
                
                # Get page content
                blocks_response = await client.get(
                    f"{self.base_url}/blocks/{document_id}/children",
                    headers=self.headers,
                    timeout=30
                )
                blocks_response.raise_for_status()
                blocks_data = blocks_response.json()
                
                # Extract content
                content = await self._extract_page_content(blocks_data.get("results", []))
                title = self._extract_page_title(page_data)
                
                return ConnectorDocument(
                    id=document_id,
                    title=title,
                    content=content,
                    source_type="notion",
                    source_metadata={
                        "notion_id": document_id,
                        "properties": page_data.get("properties", {}),
                        "workspace_name": self.config.get("workspace_name"),
                    },
                    url=page_data.get("url"),
                    created_at=page_data.get("created_time"),
                    updated_at=page_data.get("last_edited_time"),
                )
        except Exception as e:
            print(f"Error getting document {document_id}: {e}")
            return None
    
    async def _extract_page_content(self, blocks: list) -> str:
        """Extract text content from Notion blocks."""
        content_parts = []
        
        for block in blocks:
            block_type = block.get("type")
            block_data = block.get(block_type, {})
            
            # Extract text based on block type
            if block_type == "paragraph":
                text = self._extract_rich_text(block_data.get("rich_text", []))
                if text:
                    content_parts.append(text)
            
            elif block_type == "heading_1":
                text = self._extract_rich_text(block_data.get("rich_text", []))
                if text:
                    content_parts.append(f"# {text}")
            
            elif block_type == "heading_2":
                text = self._extract_rich_text(block_data.get("rich_text", []))
                if text:
                    content_parts.append(f"## {text}")
            
            elif block_type == "heading_3":
                text = self._extract_rich_text(block_data.get("rich_text", []))
                if text:
                    content_parts.append(f"### {text}")
            
            elif block_type == "bulleted_list_item":
                text = self._extract_rich_text(block_data.get("rich_text", []))
                if text:
                    content_parts.append(f"- {text}")
            
            elif block_type == "numbered_list_item":
                text = self._extract_rich_text(block_data.get("rich_text", []))
                if text:
                    content_parts.append(f"1. {text}")
            
            elif block_type == "table":
                # Extract table content (simplified)
                content_parts.append("[Table]")
            
            elif block_type == "code":
                code = self._extract_rich_text(block_data.get("rich_text", []))
                language = block_data.get("language", "")
                if code:
                    content_parts.append(f"```{language}\n{code}\n```")
            
            elif block_type == "quote":
                text = self._extract_rich_text(block_data.get("rich_text", []))
                if text:
                    content_parts.append(f"> {text}")
            
            # Handle nested blocks
            if block.get("has_children"):
                # Would need recursive call here
                pass
        
        return "\n\n".join(content_parts)
    
    def _extract_rich_text(self, rich_text_array: list) -> str:
        """Extract text from Notion rich text objects."""
        texts = []
        for text_obj in rich_text_array:
            if text_obj.get("type") == "text":
                texts.append(text_obj["text"]["content"])
            elif text_obj.get("type") == "equation":
                texts.append(text_obj["equation"]["expression"])
            elif text_obj.get("type") == "mention":
                texts.append(text_obj["mention"].get("name", ""))
        
        return "".join(texts)
    
    def _extract_page_title(self, page_data: dict) -> str:
        """Extract title from Notion page properties."""
        properties = page_data.get("properties", {})
        
        # Most common: "Name" or "Title" property with type "title"
        for prop_name, prop_value in properties.items():
            if prop_value.get("type") == "title":
                rich_text = prop_value.get("title", [])
                return self._extract_rich_text(rich_text) or "Untitled"
        
        return "Untitled"
    
    async def _page_to_document(self, page_data: dict) -> Optional[ConnectorDocument]:
        """Convert Notion page to ConnectorDocument."""
        page_id = page_data.get("id")
        if not page_id:
            return None
        
        return await self.get_document(page_id)
