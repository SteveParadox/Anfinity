"""Google Drive connector for ingesting documents."""
from typing import Dict, Any, Optional, AsyncGenerator
import httpx
from datetime import datetime

from app.connectors.base import BaseConnector, ConnectorType, ConnectorDocument


class GoogleDriveConnector(BaseConnector):
    """Connector for Google Drive documents and files."""
    
    connector_type = ConnectorType.GDRIVE
    
    def __init__(self, config: Dict[str, Any]):
        """Initialize Google Drive connector.
        
        Config should contain:
        - access_token: OAuth 2.0 access token
        - folder_ids: List of folder IDs to sync (optional)
        - file_types: List of MIME types to include
        """
        super().__init__(config)
        self.base_url = "https://www.googleapis.com/drive/v3"
        self.headers = {
            "Authorization": f"Bearer {config.get('access_token')}",
        }
    
    def validate_config(self) -> bool:
        """Validate Google Drive configuration."""
        return "access_token" in self.config
    
    async def authenticate(self) -> bool:
        """Verify Google Drive access token is valid."""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.base_url}/about?fields=user",
                    headers=self.headers,
                    timeout=10
                )
                return response.status_code == 200
        except Exception:
            return False
    
    async def list_documents(self) -> AsyncGenerator[ConnectorDocument, None]:
        """List all documents from Google Drive."""
        folder_ids = self.config.get("folder_ids", ["root"])
        file_types = self.config.get("file_types", [
            "application/vnd.google-apps.document",
            "application/pdf",
            "text/plain",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ])
        
        # Build MIME type filter
        mime_filter = " or ".join([f'mimeType="{mime}"' for mime in file_types])
        
        for folder_id in folder_ids:
            async for doc in self._list_folder_files(folder_id, mime_filter):
                yield doc
    
    async def _list_folder_files(
        self,
        folder_id: str,
        mime_filter: str
    ) -> AsyncGenerator[ConnectorDocument, None]:
        """List files in a Google Drive folder."""
        try:
            async with httpx.AsyncClient() as client:
                page_token = None
                
                while True:
                    query = f"'{folder_id}' in parents and ({mime_filter}) and trashed=false"
                    
                    params = {
                        "q": query,
                        "spaces": "drive",
                        "fields": "files(id,name,mimeType,createdTime,modifiedTime,webViewLink)",
                        "pageSize": 100,
                    }
                    if page_token:
                        params["pageToken"] = page_token
                    
                    response = await client.get(
                        f"{self.base_url}/files",
                        headers=self.headers,
                        params=params,
                        timeout=30
                    )
                    
                    data = response.json()
                    
                    for file in data.get("files", []):
                        doc = await self.get_document(file["id"])
                        if doc:
                            yield doc
                    
                    page_token = data.get("nextPageToken")
                    if not page_token:
                        break
        except Exception as e:
            print(f"Error listing folder files: {e}")
    
    async def get_document(self, document_id: str) -> Optional[ConnectorDocument]:
        """Get a specific Google Drive document."""
        try:
            async with httpx.AsyncClient() as client:
                # Get file metadata
                response = await client.get(
                    f"{self.base_url}/files/{document_id}",
                    headers=self.headers,
                    params={"fields": "id,name,mimeType,createdTime,modifiedTime,webViewLink"},
                    timeout=10
                )
                response.raise_for_status()
                file_data = response.json()
                
                # Get file content
                content = await self._get_file_content(document_id, file_data["mimeType"])
                
                if not content:
                    return None
                
                return ConnectorDocument(
                    id=document_id,
                    title=file_data.get("name", "Untitled"),
                    content=content,
                    source_type="gdrive",
                    source_metadata={
                        "drive_id": document_id,
                        "mime_type": file_data.get("mimeType"),
                    },
                    url=file_data.get("webViewLink"),
                    created_at=file_data.get("createdTime"),
                    updated_at=file_data.get("modifiedTime"),
                )
        except Exception as e:
            print(f"Error getting document {document_id}: {e}")
            return None
    
    async def _get_file_content(
        self,
        file_id: str,
        mime_type: str
    ) -> Optional[str]:
        """Get content from Google Drive file."""
        try:
            async with httpx.AsyncClient() as client:
                if mime_type == "application/vnd.google-apps.document":
                    # Export Google Doc as plain text
                    response = await client.get(
                        f"{self.base_url}/files/{file_id}/export",
                        headers=self.headers,
                        params={"mimeType": "text/plain"},
                        timeout=30
                    )
                elif mime_type in ["application/pdf", "text/plain"]:
                    # Download as-is
                    response = await client.get(
                        f"{self.base_url}/files/{file_id}?alt=media",
                        headers=self.headers,
                        timeout=30
                    )
                elif mime_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
                    # Word doc - would need parsing library
                    return "[Word document - requires docx parser]"
                else:
                    return None
                
                response.raise_for_status()
                
                # Try to decode as text
                try:
                    return response.text
                except:
                    return f"[Binary content: {mime_type}]"
        except Exception as e:
            print(f"Error getting file content: {e}")
            return None
