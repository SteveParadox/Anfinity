"""Shared OAuth flow and token handling for all integrations."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Optional
from urllib.parse import urlencode
from uuid import UUID

import httpx
from fastapi import HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.encryption import token_encryptor
from app.database.models import Connector
from app.services.integrations.base import ReauthorizationRequiredError
from app.services.integrations.providers import IntegrationProvider, OAuthProviderDefinition, ProviderCapability, get_provider_definition


STATE_TTL_SECONDS = 10 * 60


@dataclass(frozen=True)
class OAuthState:
    provider: IntegrationProvider
    workspace_id: UUID
    user_id: UUID
    nonce: str
    issued_at: int
    capabilities: tuple[ProviderCapability, ...] = ()


def build_redirect_uri(request: Request, provider: IntegrationProvider) -> str:
    base = (settings.INTEGRATIONS_OAUTH_REDIRECT_BASE_URL or str(request.base_url).rstrip("/")).rstrip("/")
    return f"{base}/connectors/oauth/{provider.value}/callback"


def build_authorization_url(
    *,
    provider: str,
    workspace_id: UUID,
    user_id: UUID,
    request: Request,
    capabilities: tuple[ProviderCapability, ...] | None = None,
) -> str:
    definition = get_provider_definition(provider)
    client_id = definition.client_id()
    if not client_id or not definition.client_secret():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"{definition.label} OAuth is not configured")

    redirect_uri = build_redirect_uri(request, definition.provider)
    selected_capabilities = validate_capabilities(definition, capabilities)
    requested_scopes = definition.scopes_for_capabilities(selected_capabilities)
    params: dict[str, str] = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": encode_oauth_state(
            OAuthState(
                provider=definition.provider,
                workspace_id=workspace_id,
                user_id=user_id,
                nonce=secrets.token_urlsafe(16),
                issued_at=int(datetime.now(timezone.utc).timestamp()),
                capabilities=selected_capabilities,
            )
        ),
    }
    if requested_scopes:
        params["scope"] = " ".join(requested_scopes)
    params.update(definition.auth_params)
    if "response_type" not in params:
        params["response_type"] = "code"
    return f"{definition.auth_url}?{urlencode(params)}"


async def handle_oauth_callback(
    db: AsyncSession,
    *,
    provider: str,
    code: str,
    state_token: str,
    request: Request,
) -> Connector:
    definition = get_provider_definition(provider)
    state = decode_oauth_state(state_token)
    if state.provider != definition.provider:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="OAuth state provider mismatch")

    redirect_uri = build_redirect_uri(request, definition.provider)
    token_payload = await exchange_code_for_token(definition, code=code, redirect_uri=redirect_uri)
    required_scopes = definition.scopes_for_capabilities(state.capabilities)
    granted_scopes = parse_scopes(token_payload.get("scope"), fallback=required_scopes)
    missing_scopes = set(required_scopes) - set(granted_scopes)
    if missing_scopes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Provider did not grant required scopes: {', '.join(sorted(missing_scopes))}",
        )

    access_token = str(token_payload.get("access_token") or "")
    if not access_token:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Provider token response did not include an access token")

    external_account = await fetch_external_account_metadata(definition, access_token, token_payload)
    connector = await upsert_oauth_connector(
        db,
        workspace_id=state.workspace_id,
        user_id=state.user_id,
        provider=definition.provider.value,
        access_token=access_token,
        refresh_token=token_payload.get("refresh_token"),
        expires_in=token_payload.get("expires_in"),
        scopes=granted_scopes,
        external_account=external_account,
        config=default_provider_config(definition.provider, external_account, capabilities=state.capabilities),
    )
    await db.commit()
    await db.refresh(connector)
    return connector


async def exchange_code_for_token(definition: OAuthProviderDefinition, *, code: str, redirect_uri: str) -> Mapping[str, Any]:
    client_id = definition.client_id()
    client_secret = definition.client_secret()
    if not client_id or not client_secret:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"{definition.label} OAuth is not configured")

    async with httpx.AsyncClient(timeout=20) as client:
        if definition.token_auth_method == "basic_json":
            auth = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
            response = await client.post(
                definition.token_url,
                headers={"Authorization": f"Basic {auth}", "Content-Type": "application/json"},
                json={"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri},
            )
        else:
            response = await client.post(
                definition.token_url,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
            )

    data = _safe_response_json(response)
    if response.status_code >= 400 or data.get("ok") is False:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"{definition.label} OAuth token exchange failed: {data.get('error') or response.text[:300]}",
        )
    return data


async def refresh_connector_token(connector: Connector) -> str:
    provider = IntegrationProvider(str(connector.connector_type))
    definition = get_provider_definition(provider)
    refresh_token = token_encryptor.decrypt(connector.refresh_token or "")
    if not refresh_token:
        if _connector_token_expired(connector):
            raise ReauthorizationRequiredError(definition.label, "missing refresh token for expired access token")
        return token_encryptor.decrypt(connector.access_token or "")

    if provider == IntegrationProvider.SLACK or provider == IntegrationProvider.NOTION:
        return token_encryptor.decrypt(connector.access_token or "")

    client_id = definition.client_id()
    client_secret = definition.client_secret()
    if not client_id or not client_secret:
        raise RuntimeError(f"{definition.label} OAuth is not configured")

    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            definition.token_url,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
                "client_secret": client_secret,
            },
        )
    data = _safe_response_json(response)
    if response.status_code >= 400:
        error = str(data.get("error") or "")
        if error in {"invalid_grant", "invalid_client", "unauthorized_client"}:
            raise ReauthorizationRequiredError(definition.label, error)
        raise RuntimeError(f"{definition.label} token refresh failed: {error or response.text[:300]}")

    access_token = str(data.get("access_token") or "")
    if not access_token:
        raise RuntimeError(f"{definition.label} token refresh did not return an access token")

    connector.access_token = token_encryptor.encrypt(access_token)
    if data.get("refresh_token"):
        connector.refresh_token = token_encryptor.encrypt(str(data["refresh_token"]))
    if data.get("expires_in"):
        connector.token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(data["expires_in"]))
    return access_token


async def get_valid_access_token(db: AsyncSession, connector: Connector) -> str:
    expires_at = connector.token_expires_at
    if expires_at and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at and expires_at <= datetime.now(timezone.utc) + timedelta(minutes=5):
        token = await refresh_connector_token(connector)
        await db.flush()
        return token
    return token_encryptor.decrypt(connector.access_token or "")


async def upsert_oauth_connector(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    provider: str,
    access_token: str,
    refresh_token: Any,
    expires_in: Any,
    scopes: list[str],
    external_account: Mapping[str, Any],
    config: Mapping[str, Any],
) -> Connector:
    row = await db.execute(
        select(Connector).where(Connector.workspace_id == workspace_id, Connector.connector_type == provider)
    )
    connector = row.scalar_one_or_none()
    if connector is None:
        connector = Connector(workspace_id=workspace_id, user_id=user_id, connector_type=provider)
        db.add(connector)

    connector.user_id = user_id
    connector.access_token = token_encryptor.encrypt(access_token)
    if refresh_token:
        connector.refresh_token = token_encryptor.encrypt(str(refresh_token))
    if expires_in:
        connector.token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))
    connector.scopes = scopes
    connector.external_account_id = str(external_account.get("id") or external_account.get("team_id") or external_account.get("email") or "")
    connector.external_account_metadata = dict(external_account)
    connector.config = {**dict(connector.config or {}), **dict(config)}
    connector.sync_status = "idle"
    connector.last_sync_error = None
    connector.is_active = 1
    await db.flush()
    return connector


async def fetch_external_account_metadata(
    definition: OAuthProviderDefinition,
    access_token: str,
    token_payload: Mapping[str, Any],
) -> dict[str, Any]:
    if definition.provider == IntegrationProvider.SLACK:
        team = dict(token_payload.get("team") or {})
        authed_user = dict(token_payload.get("authed_user") or {})
        return {"id": team.get("id"), "name": team.get("name"), "team": team, "authed_user": authed_user}

    if definition.provider == IntegrationProvider.NOTION:
        owner = token_payload.get("owner") or {}
        workspace = token_payload.get("workspace_name") or token_payload.get("workspace_id")
        return {"id": token_payload.get("workspace_id"), "name": workspace, "owner": owner}

    if not definition.userinfo_url:
        return {}

    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(definition.userinfo_url, headers={"Authorization": f"Bearer {access_token}"})
    if response.status_code >= 400:
        return {}
    data = response.json()
    return {"id": data.get("sub"), "email": data.get("email"), "name": data.get("name"), **data}


def default_provider_config(
    provider: IntegrationProvider,
    external_account: Mapping[str, Any],
    *,
    capabilities: tuple[ProviderCapability, ...] = (),
) -> dict[str, Any]:
    if provider == IntegrationProvider.GOOGLE:
        config: dict[str, Any] = {"enabled_capabilities": list(capabilities or ("gmail_sync", "calendar_sync"))}
        if not capabilities or "gmail_sync" in capabilities:
            config["gmail_query"] = "is:unread"
        if not capabilities or "calendar_sync" in capabilities:
            config.update(
                {
                    "calendar_id": "primary",
                    "calendar_days_ahead": 30,
                    "calendar_match_threshold": 0.82,
                }
            )
        return config
    if provider == IntegrationProvider.SLACK:
        return {"default_channel_id": None, "team_name": external_account.get("name")}
    if provider == IntegrationProvider.NOTION:
        return {"database_id": None, "workspace_name": external_account.get("name")}
    return {}


def parse_scopes(value: Any, *, fallback: tuple[str, ...]) -> list[str]:
    if isinstance(value, str):
        return [item for item in value.replace(",", " ").split() if item]
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    return list(fallback)


def validate_capabilities(
    definition: OAuthProviderDefinition,
    capabilities: tuple[ProviderCapability, ...] | None,
) -> tuple[ProviderCapability, ...]:
    if not capabilities:
        return definition.capabilities
    allowed = set(definition.capabilities)
    invalid = [capability for capability in capabilities if capability not in allowed]
    if invalid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported capabilities for {definition.label}: {', '.join(invalid)}",
        )
    return tuple(dict.fromkeys(capabilities))


def encode_oauth_state(state: OAuthState) -> str:
    payload = {
        "provider": state.provider.value,
        "workspace_id": str(state.workspace_id),
        "user_id": str(state.user_id),
        "nonce": state.nonce,
        "iat": state.issued_at,
        "capabilities": list(state.capabilities),
    }
    payload_b64 = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
    signature = _sign_state_payload(payload_b64)
    return f"{payload_b64}.{signature}"


def decode_oauth_state(token: str) -> OAuthState:
    try:
        payload_b64, signature = token.split(".", 1)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid OAuth state") from exc

    if not hmac.compare_digest(_sign_state_payload(payload_b64), signature):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid OAuth state signature")

    padded = payload_b64 + ("=" * (-len(payload_b64) % 4))
    try:
        payload = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
        issued_at = int(payload["iat"])
        now = int(datetime.now(timezone.utc).timestamp())
        if now - issued_at > STATE_TTL_SECONDS:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="OAuth state expired")
        return OAuthState(
            provider=IntegrationProvider(str(payload["provider"])),
            workspace_id=UUID(str(payload["workspace_id"])),
            user_id=UUID(str(payload["user_id"])),
            nonce=str(payload["nonce"]),
            issued_at=issued_at,
            capabilities=tuple(payload.get("capabilities") or ()),
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid OAuth state payload") from exc


def _sign_state_payload(payload_b64: str) -> str:
    secret = settings.JWT_SECRET or settings.ENCRYPTION_KEY or "dev-oauth-state"
    digest = hmac.new(secret.encode(), payload_b64.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def _connector_token_expired(connector: Connector) -> bool:
    expires_at = connector.token_expires_at
    if expires_at and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return bool(expires_at and expires_at <= datetime.now(timezone.utc) + timedelta(minutes=5))


def _safe_response_json(response: httpx.Response) -> Mapping[str, Any]:
    try:
        data = response.json()
        return data if isinstance(data, Mapping) else {}
    except Exception:
        return {}
