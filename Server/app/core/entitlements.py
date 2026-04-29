"""Entitlement error contract shared by billing-enforced endpoints."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass(slots=True)
class EntitlementErrorMetadata:
    code: str
    message: str
    feature_key: str
    required_plan: Optional[str]
    current_plan: str
    limit: Optional[int]
    usage: Optional[int]
    upgrade_url: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "feature_key": self.feature_key,
            "required_plan": self.required_plan,
            "current_plan": self.current_plan,
            "limit": self.limit,
            "usage": self.usage,
            "upgrade_url": self.upgrade_url,
        }


class EntitlementRequiredError(Exception):
    """Raised when a request exceeds plan limits or lacks a feature entitlement."""

    def __init__(self, metadata: EntitlementErrorMetadata):
        super().__init__(metadata.message)
        self.metadata = metadata
