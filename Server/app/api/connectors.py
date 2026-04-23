"""Connector management API endpoints."""
from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.auth import get_current_active_user, get_workspace_context
from app.core.encryption import token_encryptor
from app.database.models import Connector, User as DBUser, WorkspaceRole
from app.database.session import get_db
from app.schemas.connectors import (
    ConnectorCreate,
    ConnectorListResponse,
    ConnectorProviderResponse,
    ConnectorResponse,
    ConnectorSyncItemListResponse,
    ConnectorSyncItemResponse,
    ConnectorSyncResponse,
    ConnectorUpdate,
    OAuthAuthorizeResponse,
    SlackPostRequest,
)
from app.services.integrations.oauth import build_authorization_url, handle_oauth_callback
from app.services.integrations.orchestrator import list_connector_sync_items, post_slack_message, sync_connector
from app.services.integrations.providers import ProviderCapability, get_provider_definition, provider_registry_payload

router = APIRouter(prefix="/connectors", tags=["connectors"])

SENSITIVE_CONFIG_KEYS = {
    "access_token",
    "refresh_token",
    "token",
    "api_token",
    "password",
    "secret",
    "client_secret",
}


def _sanitize_config(config: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(config or {})
    for key in list(payload.keys()):
        if key.lower() in SENSITIVE_CONFIG_KEYS:
            payload.pop(key, None)
    return payload


def _to_response(connector: Connector) -> ConnectorResponse:
    return ConnectorResponse(
        id=str(connector.id),
        workspace_id=str(connector.workspace_id),
        user_id=str(connector.user_id),
        connector_type=connector.connector_type,
        is_active=bool(connector.is_active),
        scopes=list(connector.scopes or []),
        external_account_id=connector.external_account_id,
        external_account_metadata=dict(connector.external_account_metadata or {}),
        sync_status=connector.sync_status or "idle",
        last_sync_at=connector.last_sync_at,
        last_sync_started_at=connector.last_sync_started_at,
        last_sync_completed_at=connector.last_sync_completed_at,
        last_sync_error=connector.last_sync_error,
        sync_cursor=dict(connector.sync_cursor or {}),
        created_at=connector.created_at,
        updated_at=connector.updated_at,
        config=_sanitize_config(connector.config),
    )


def _sync_item_to_response(item) -> ConnectorSyncItemResponse:
    return ConnectorSyncItemResponse(
        id=str(item.id),
        connector_id=str(item.connector_id),
        workspace_id=str(item.workspace_id),
        provider=item.provider,
        external_type=item.external_type,
        external_id=item.external_id,
        sync_direction=item.sync_direction,
        sync_status=item.sync_status,
        local_note_id=str(item.local_note_id) if item.local_note_id else None,
        local_document_id=str(item.local_document_id) if item.local_document_id else None,
        source_hash=item.source_hash,
        external_updated_at=item.external_updated_at,
        last_error=item.last_error,
        metadata=dict(item.item_metadata or {}),
        first_seen_at=item.first_seen_at,
        last_seen_at=item.last_seen_at,
        last_synced_at=item.last_synced_at,
    )


async def _get_connector_for_workspace(db: AsyncSession, connector_id: UUID, workspace_id: UUID) -> Connector:
    result = await db.execute(
        select(Connector).where(
            Connector.id == connector_id,
            Connector.workspace_id == workspace_id,
        )
    )
    connector = result.scalar_one_or_none()
    if connector is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Connector not found",
        )
    return connector


@router.get("/providers", response_model=list[ConnectorProviderResponse])
async def list_integration_providers() -> list[ConnectorProviderResponse]:
    """List supported OAuth providers and capabilities."""
    return [ConnectorProviderResponse(**payload) for payload in provider_registry_payload()]


@router.get("/oauth/{provider}/authorize", response_model=OAuthAuthorizeResponse)
async def start_oauth_authorization(
    provider: str,
    request: Request,
    workspace_id: UUID = Query(...),
    capabilities: list[ProviderCapability] | None = Query(None),
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> OAuthAuthorizeResponse:
    """Build a shared OAuth authorization URL for any supported provider."""
    context = await get_workspace_context(workspace_id, current_user, db)
    context.require_role(WorkspaceRole.ADMIN)
    authorization_url = build_authorization_url(
        provider=provider,
        workspace_id=workspace_id,
        user_id=current_user.id,
        request=request,
        capabilities=tuple(capabilities or ()),
    )
    return OAuthAuthorizeResponse(authorization_url=authorization_url)


@router.get("/oauth/{provider}/callback")
async def complete_oauth_authorization(
    provider: str,
    request: Request,
    code: str = Query(...),
    state: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """Shared OAuth callback used by Slack, Notion, and Google providers."""
    connector = await handle_oauth_callback(db, provider=provider, code=code, state_token=state, request=request)
    frontend_url = settings.FRONTEND_URL.rstrip("/")
    return RedirectResponse(
        f"{frontend_url}/integrations?provider={connector.connector_type}&workspace_id={connector.workspace_id}&connected=true",
        status_code=status.HTTP_302_FOUND,
    )


@router.post("", response_model=ConnectorResponse, status_code=status.HTTP_201_CREATED)
async def create_connector(
    connector_data: ConnectorCreate,
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> ConnectorResponse:
    """Create a connector with encrypted credential storage."""
    try:
        workspace_id = UUID(str(connector_data.workspace_id))
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid workspace_id",
        ) from exc

    context = await get_workspace_context(workspace_id, current_user, db)
    context.require_role(WorkspaceRole.ADMIN)

    existing_result = await db.execute(
        select(Connector).where(
            Connector.workspace_id == workspace_id,
            Connector.connector_type == connector_data.connector_type,
        )
    )
    if existing_result.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Connector already exists for this workspace",
        )

    connector = Connector(
        user_id=current_user.id,
        workspace_id=workspace_id,
        connector_type=connector_data.connector_type,
        access_token=token_encryptor.encrypt(connector_data.access_token),
        refresh_token=token_encryptor.encrypt(connector_data.refresh_token) if connector_data.refresh_token else None,
        config=_sanitize_config(connector_data.config),
        scopes=[],
        external_account_metadata={},
        sync_status="idle",
        sync_cursor={},
        is_active=1,
    )
    db.add(connector)
    await db.commit()
    await db.refresh(connector)
    return _to_response(connector)


@router.get("/workspace/{workspace_id}", response_model=ConnectorListResponse)
async def list_connectors(
    workspace_id: UUID,
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> ConnectorListResponse:
    """List connectors for a workspace without exposing tokens."""
    await get_workspace_context(workspace_id, current_user, db)
    result = await db.execute(
        select(Connector)
        .where(Connector.workspace_id == workspace_id)
        .order_by(Connector.created_at.desc())
    )
    return ConnectorListResponse(connectors=[_to_response(connector) for connector in result.scalars().all()])


@router.get("/{connector_id}", response_model=ConnectorResponse)
async def get_connector(
    connector_id: UUID,
    workspace_id: UUID,
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> ConnectorResponse:
    """Fetch a single connector without exposing credentials."""
    await get_workspace_context(workspace_id, current_user, db)
    connector = await _get_connector_for_workspace(db, connector_id, workspace_id)
    return _to_response(connector)


@router.post("/{connector_id}/sync", response_model=ConnectorSyncResponse)
async def trigger_connector_sync(
    connector_id: UUID,
    background_tasks: BackgroundTasks,
    workspace_id: UUID,
    run_inline: bool = Query(False),
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> ConnectorSyncResponse:
    """Queue a connector sync, or run inline when explicitly requested."""
    context = await get_workspace_context(workspace_id, current_user, db)
    context.require_role(WorkspaceRole.ADMIN)
    connector = await _get_connector_for_workspace(db, connector_id, workspace_id)
    del background_tasks
    if not run_inline:
        try:
            from app.tasks.worker import sync_connector as sync_connector_task

            task = sync_connector_task.delay(str(connector.id))
        except Exception as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"Failed to queue connector sync: {exc}") from exc
        return ConnectorSyncResponse(
            status="queued",
            connector_id=str(connector.id),
            provider=connector.connector_type,
            task_id=str(task.id),
            result={},
        )

    try:
        result = await sync_connector(db, connector.id)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Connector sync failed: {exc}") from exc
    return ConnectorSyncResponse(
        status=str(result.get("status") or "success"),
        connector_id=str(connector.id),
        provider=connector.connector_type,
        result=dict(result.get("result") or {}),
    )


@router.get("/{connector_id}/sync-items", response_model=ConnectorSyncItemListResponse)
async def list_connector_items(
    connector_id: UUID,
    workspace_id: UUID,
    limit: int = Query(50, ge=1, le=200),
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> ConnectorSyncItemListResponse:
    """List recent provider item mappings for operator-facing sync visibility."""
    await get_workspace_context(workspace_id, current_user, db)
    connector = await _get_connector_for_workspace(db, connector_id, workspace_id)
    items = await list_connector_sync_items(db, connector_id=connector.id, limit=limit)
    return ConnectorSyncItemListResponse(items=[_sync_item_to_response(item) for item in items])


@router.post("/{connector_id}/slack/post")
async def post_to_slack(
    connector_id: UUID,
    payload: SlackPostRequest,
    workspace_id: UUID,
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Post a Block Kit message through a workspace Slack connector."""
    context = await get_workspace_context(workspace_id, current_user, db)
    context.require_role(WorkspaceRole.MEMBER)
    connector = await _get_connector_for_workspace(db, connector_id, workspace_id)
    try:
        return await post_slack_message(db, connector.id, payload.model_dump(exclude_none=True))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Slack post failed: {exc}") from exc


@router.patch("/{connector_id}", response_model=ConnectorResponse)
async def update_connector(
    connector_id: UUID,
    workspace_id: UUID,
    connector_data: ConnectorUpdate,
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> ConnectorResponse:
    """Update connector configuration with encrypted token handling."""
    context = await get_workspace_context(workspace_id, current_user, db)
    context.require_role(WorkspaceRole.ADMIN)
    connector = await _get_connector_for_workspace(db, connector_id, workspace_id)

    if connector_data.access_token is not None:
        connector.access_token = token_encryptor.encrypt(connector_data.access_token)
    if connector_data.refresh_token is not None:
        connector.refresh_token = token_encryptor.encrypt(connector_data.refresh_token)
    if connector_data.config is not None:
        connector.config = _sanitize_config(connector_data.config)
    if connector_data.is_active is not None:
        connector.is_active = 1 if connector_data.is_active else 0

    await db.commit()
    await db.refresh(connector)
    return _to_response(connector)


@router.delete("/{connector_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_connector(
    connector_id: UUID,
    workspace_id: UUID,
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Delete a connector."""
    context = await get_workspace_context(workspace_id, current_user, db)
    context.require_role(WorkspaceRole.ADMIN)
    connector = await _get_connector_for_workspace(db, connector_id, workspace_id)
    await db.delete(connector)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
