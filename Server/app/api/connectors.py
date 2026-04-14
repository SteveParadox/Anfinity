"""Connector management API endpoints."""
from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_active_user, get_workspace_context
from app.core.encryption import token_encryptor
from app.database.models import Connector, User as DBUser, WorkspaceRole
from app.database.session import get_db
from app.schemas.connectors import ConnectorCreate, ConnectorListResponse, ConnectorResponse, ConnectorUpdate

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
        last_sync_at=connector.last_sync_at,
        created_at=connector.created_at,
        updated_at=connector.updated_at,
        config=_sanitize_config(connector.config),
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
