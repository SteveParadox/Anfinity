"""Email connector for ingesting emails from IMAP sources."""
from typing import Dict, Any, Optional, AsyncGenerator
import imaplib
import email
from email.mime.text import MIMEText
import asyncio

from app.connectors.base import BaseConnector, ConnectorType, ConnectorDocument


class EmailConnector(BaseConnector):
    """Connector for email sources via IMAP."""
    
    connector_type = ConnectorType.EMAIL
    
    def __init__(self, config: Dict[str, Any]):
        """Initialize Email connector.
        
        Config should contain:
        - imap_server: IMAP server address (e.g., imap.gmail.com)
        - email: Email address
        - password: Email password or app-specific password
        - folders: List of folders to sync (default: INBOX)
        - days_back: Days to look back (default: 30)
        """
        super().__init__(config)
    
    def validate_config(self) -> bool:
        """Validate Email configuration."""
        required = ["imap_server", "email", "password"]
        return all(key in self.config for key in required)
    
    async def authenticate(self) -> bool:
        """Verify email access."""
        try:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(
                None,
                self._authenticate_sync)
        except imaplib.IMAP4.error:
            return False
        
    def _authenticate_sync(self) -> bool:
        """Synchronous method to authenticate with IMAP server."""
        mail = imaplib.IMAP4_SSL(self.config["imap_server"])
        mail.login(self.config["email"], self.config["password"])
        mail.logout()
        return True
    