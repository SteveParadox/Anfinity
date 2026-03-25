"""GitHub connector for ingesting issues, PRs, and documentation."""
from typing import Dict, Any, Optional, AsyncGenerator
import httpx

from app.connectors.base import BaseConnector, ConnectorType, ConnectorDocument


class GitHubConnector(BaseConnector):
    """Connector for GitHub issues, pull requests, and documentation."""
    
    connector_type = ConnectorType.GITHUB
    
    def __init__(self, config: Dict[str, Any]):
        """Initialize GitHub connector.
        
        Config should contain:
        - access_token: GitHub personal access token
        - repositories: List of repo slugs (owner/repo)
        - include_issues: Include issues (default: True)
        - include_prs: Include pull requests (default: True)
        - include_docs: Include docs folder (default: True)
        """
        super().__init__(config)
        self.base_url = "https://api.github.com"
        self.headers = {
            "Authorization": f"Bearer {config.get('access_token')}",
            "Accept": "application/vnd.github.v3.raw",
        }
    
    def validate_config(self) -> bool:
        """Validate GitHub configuration."""
        return "access_token" in self.config and "repositories" in self.config
    
    async def authenticate(self) -> bool:
        """Verify GitHub access token is valid."""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.base_url}/user", headers=self.headers)
                return response.status_code == 200
        except httpx.HTTPError:
            return False
        
        
        