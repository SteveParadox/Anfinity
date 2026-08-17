"""Typed provider registry for shared OAuth integrations."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Literal, Mapping, Optional

from app.config import settings


class IntegrationProvider(str, Enum):
    SLACK = "slack"
    NOTION = "notion"
    GOOGLE = "google"


ProviderCapability = Literal["slack_posting", "notion_sync", "gmail_sync", "calendar_sync"]
TokenAuthMethod = Literal["client_secret_post", "basic_json"]


@dataclass(frozen=True)
class OAuthProviderDefinition:
    provider: IntegrationProvider
    label: str
    auth_url: str
    token_url: str
    client_id_setting: str
    client_secret_setting: str
    scopes: tuple[str, ...]
    capabilities: tuple[ProviderCapability, ...]
    capability_scopes: Mapping[ProviderCapability, tuple[str, ...]] = field(default_factory=dict)
    token_auth_method: TokenAuthMethod = "client_secret_post"
    auth_params: Dict[str, str] = field(default_factory=dict)
    userinfo_url: Optional[str] = None

    def client_id(self) -> Optional[str]:
        return getattr(settings, self.client_id_setting, None)

    def client_secret(self) -> Optional[str]:
        return getattr(settings, self.client_secret_setting, None)

    def scopes_for_capabilities(self, capabilities: tuple[ProviderCapability, ...] | None = None) -> tuple[str, ...]:
        if not capabilities or not self.capability_scopes:
            return self.scopes
        resolved: list[str] = []
        for capability in capabilities:
            for scope in self.capability_scopes.get(capability, ()):
                if scope not in resolved:
                    resolved.append(scope)
        return tuple(resolved)


PROVIDER_DEFINITIONS: dict[IntegrationProvider, OAuthProviderDefinition] = {
    IntegrationProvider.SLACK: OAuthProviderDefinition(
        provider=IntegrationProvider.SLACK,
        label="Slack",
        auth_url="https://slack.com/oauth/v2/authorize",
        token_url="https://slack.com/api/oauth.v2.access",
        client_id_setting="SLACK_CLIENT_ID",
        client_secret_setting="SLACK_CLIENT_SECRET",
        scopes=("chat:write", "channels:read", "groups:read"),
        capabilities=("slack_posting",),
        capability_scopes={
            "slack_posting": ("chat:write", "channels:read", "groups:read"),
        },
        userinfo_url="https://slack.com/api/auth.test",
    ),
    IntegrationProvider.NOTION: OAuthProviderDefinition(
        provider=IntegrationProvider.NOTION,
        label="Notion",
        auth_url="https://api.notion.com/v1/oauth/authorize",
        token_url="https://api.notion.com/v1/oauth/token",
        client_id_setting="NOTION_CLIENT_ID",
        client_secret_setting="NOTION_CLIENT_SECRET",
        scopes=(),
        capabilities=("notion_sync",),
        capability_scopes={"notion_sync": ()},
        token_auth_method="basic_json",
        auth_params={"owner": "user", "response_type": "code"},
    ),
    IntegrationProvider.GOOGLE: OAuthProviderDefinition(
        provider=IntegrationProvider.GOOGLE,
        label="Google",
        auth_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        client_id_setting="GOOGLE_CLIENT_ID",
        client_secret_setting="GOOGLE_CLIENT_SECRET",
        scopes=(
            "openid",
            "email",
            "https://www.googleapis.com/auth/gmail.modify",
            "https://www.googleapis.com/auth/calendar.readonly",
        ),
        capabilities=("gmail_sync", "calendar_sync"),
        capability_scopes={
            "gmail_sync": ("openid", "email", "https://www.googleapis.com/auth/gmail.modify"),
            "calendar_sync": ("openid", "email", "https://www.googleapis.com/auth/calendar.readonly"),
        },
        auth_params={"access_type": "offline", "prompt": "consent"},
        userinfo_url="https://openidconnect.googleapis.com/v1/userinfo",
    ),
}


def get_provider_definition(provider: str | IntegrationProvider) -> OAuthProviderDefinition:
    try:
        normalized = provider if isinstance(provider, IntegrationProvider) else IntegrationProvider(str(provider))
    except ValueError as exc:
        raise ValueError(f"Unsupported integration provider: {provider}") from exc
    return PROVIDER_DEFINITIONS[normalized]


def provider_registry_payload() -> list[dict[str, object]]:
    return [
        {
            "provider": definition.provider.value,
            "label": definition.label,
            "required_scopes": list(definition.scopes),
            "capability_scopes": {
                capability: list(scopes)
                for capability, scopes in definition.capability_scopes.items()
            },
            "capabilities": list(definition.capabilities),
        }
        for definition in PROVIDER_DEFINITIONS.values()
    ]
