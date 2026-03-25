"""Slack connector for ingesting messages and documents."""
from typing import Dict, Any, Optional, AsyncGenerator
import httpx
from datetime import datetime, timedelta

from app.connectors.base import BaseConnector, ConnectorType, ConnectorDocument


class SlackConnector(BaseConnector):
    """Connector for Slack workspace messages and documents."""
    
    connector_type = ConnectorType.SLACK
    
    def __init__(self, config: Dict[str, Any]):
        """Initialize Slack connector.
        
        Config should contain:
        - bot_token: Slack bot token
        - channel_ids: List of channel IDs to sync
        - days_back: Number of days to look back (default: 30)
        """
        super().__init__(config)
        self.base_url = "https://slack.com/api"
        self.headers = {
            "Authorization": f"Bearer {config.get('bot_token')}",
        }
    
    def validate_config(self) -> bool:
        """Validate Slack configuration."""
        return "bot_token" in self.config and "channel_ids" in self.config
    
    async def authenticate(self) -> bool:
        """Verify Slack token is valid."""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.base_url}/auth.test",
                    headers=self.headers,
                    timeout=10
                )
                data = response.json()
                return data.get("ok", False)
        except Exception:
            return False
    
    async def list_documents(self) -> AsyncGenerator[ConnectorDocument, None]:
        """List all messages from configured channels."""
        channel_ids = self.config.get("channel_ids", [])
        days_back = self.config.get("days_back", 30)
        
        # Calculate timestamp for cutoff date
        cutoff_timestamp = (datetime.now() - timedelta(days=days_back)).timestamp()
        
        for channel_id in channel_ids:
            async for doc in self._list_channel_messages(channel_id, cutoff_timestamp):
                yield doc
    
    async def _list_channel_messages(
        self,
        channel_id: str,
        oldest: float
    ) -> AsyncGenerator[ConnectorDocument, None]:
        """List messages from a Slack channel."""
        try:
            async with httpx.AsyncClient() as client:
                cursor = None
                
                while True:
                    params = {
                        "channel": channel_id,
                        "oldest": oldest,
                        "limit": 200,
                    }
                    if cursor:
                        params["cursor"] = cursor
                    
                    response = await client.get(
                        f"{self.base_url}/conversations.history",
                        headers=self.headers,
                        params=params,
                        timeout=30
                    )
                    
                    data = response.json()
                    if not data.get("ok"):
                        print(f"Error listing channel messages: {data.get('error')}")
                        break
                    
                    messages = data.get("messages", [])
                    
                    for msg in messages:
                        doc = await self._message_to_document(channel_id, msg)
                        if doc:
                            yield doc
                    
                    # Check for pagination
                    meta = data.get("response_metadata", {})
                    cursor = meta.get("next_cursor")
                    if not cursor:
                        break
        except Exception as e:
            print(f"Error listing channel messages: {e}")
    
    async def get_document(self, document_id: str) -> Optional[ConnectorDocument]:
        """Get a specific message (thread).
        
        Format: channel_id:timestamp
        """
        try:
            parts = document_id.split(":")
            if len(parts) != 2:
                return None
            
            channel_id, timestamp = parts
            
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.base_url}/conversations.replies",
                    headers=self.headers,
                    params={"channel": channel_id, "ts": timestamp},
                    timeout=10
                )
                
                data = response.json()
                if not data.get("ok"):
                    return None
                
                messages = data.get("messages", [])
                if not messages:
                    return None
                
                # Combine thread into single document
                content_parts = []
                for msg in messages:
                    text = await self._extract_message_text(msg)
                    if text:
                        author = msg.get("username") or msg.get("user")
                        content_parts.append(f"**{author}**: {text}")
                
                return ConnectorDocument(
                    id=document_id,
                    title=f"Slack discussion from {timestamp}",
                    content="\n\n".join(content_parts),
                    source_type="slack",
                    source_metadata={
                        "channel_id": channel_id,
                        "thread_ts": timestamp,
                        "workspace": self.config.get("workspace_name"),
                    },
                    url=None,  # Slack doesn't expose direct message URLs easily
                    created_at=messages[0].get("ts"),
                )
        except Exception as e:
            print(f"Error getting message: {e}")
            return None
    
    async def _message_to_document(
        self,
        channel_id: str,
        message: Dict[str, Any]
    ) -> Optional[ConnectorDocument]:
        """Convert Slack message to ConnectorDocument."""
        if "subtype" in message and message["subtype"] not in ["thread_reply"]:
            # Skip certain message subtypes
            return None
        
        timestamp = message.get("ts")
        if not timestamp:
            return None
        
        text = await self._extract_message_text(message)
        if not text:
            return None
        
        # Check if this is a thread
        is_thread = "thread_ts" in message and message.get("thread_ts") == timestamp
        
        doc_id = f"{channel_id}:{timestamp}"
        
        return ConnectorDocument(
            id=doc_id,
            title=text.split("\n")[0][:100],  # First line as title
            content=text,
            source_type="slack",
            source_metadata={
                "channel_id": channel_id,
                "timestamp": timestamp,
                "is_thread": is_thread,
                "reply_count": message.get("reply_count", 0),
                "workspace": self.config.get("workspace_name"),
            },
            url=None,
            created_at=timestamp,
            updated_at=message.get("edited", {}).get("ts"),
        )
    
    async def _extract_message_text(self, message: Dict[str, Any]) -> Optional[str]:
        """Extract text content from Slack message."""
        # Try text first
        if "text" in message:
            text = message["text"]
            
            # Handle thread replies
            if message.get("thread_ts"):
                parent_reply = await self._get_parent_message(
                    message.get("channel"),
                    message.get("thread_ts")
                )
                if parent_reply:
                    text = f"{parent_reply}\n\nReply: {text}"
            
            return text
        
        # Try parsing blocks if text is empty
        if "blocks" in message:
            texts = []
            for block in message["blocks"]:
                if block.get("type") == "section":
                    if "text" in block:
                        texts.append(block["text"].get("text", ""))
            
            return "\n".join(texts) if texts else None
        
        return None
    
    async def _get_parent_message(self, channel_id: str, ts: str) -> Optional[str]:
        """Get parent message text for thread."""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.base_url}/conversations.history",
                    headers=self.headers,
                    params={"channel": channel_id, "latest": ts, "inclusive": True, "limit": 1},
                    timeout=10
                )
                
                data = response.json()
                if data.get("ok") and data.get("messages"):
                    msg = data["messages"][0]
                    return msg.get("text")
        except Exception:
            pass
        
        return None
