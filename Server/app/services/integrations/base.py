"""Shared integration service contract.

Provider services keep their API-specific behavior isolated, while the
orchestrator can rely on this small contract for sync-capable integrations.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Mapping

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Connector
from app.services.integrations.providers import IntegrationProvider, ProviderCapability


class IntegrationServiceError(RuntimeError):
    """Base error raised by provider integration services."""


class ProviderRateLimitError(IntegrationServiceError):
    """Raised when a provider asks us to retry later."""

    def __init__(self, provider: str, retry_after: str | None = None) -> None:
        self.provider = provider
        self.retry_after = retry_after
        message = f"{provider} rate limited the request"
        if retry_after:
            message = f"{message}; retry_after={retry_after}"
        super().__init__(message)


class ReauthorizationRequiredError(IntegrationServiceError):
    """Raised when refresh credentials are missing, revoked, or expired."""

    def __init__(self, provider: str, reason: str) -> None:
        self.provider = provider
        self.reason = reason
        super().__init__(f"{provider} reauthorization required: {reason}")


class BaseIntegrationService(ABC):
    """Base class for provider services that operate on one connector."""

    provider: IntegrationProvider
    capabilities: tuple[ProviderCapability, ...] = ()

    def __init__(self, connector: Connector, access_token: str) -> None:
        self.connector = connector
        self.access_token = access_token

    @abstractmethod
    async def sync(self, db: AsyncSession) -> Mapping[str, Any]:
        """Run a provider sync for this connector."""
