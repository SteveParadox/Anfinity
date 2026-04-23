"""Durable sync-state helpers shared by provider integrations."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Mapping, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Connector, IntegrationSyncItem, Note


def stable_hash(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


async def find_sync_item(
    db: AsyncSession,
    *,
    connector_id: UUID,
    external_type: str,
    external_id: str,
) -> Optional[IntegrationSyncItem]:
    row = await db.execute(
        select(IntegrationSyncItem).where(
            IntegrationSyncItem.connector_id == connector_id,
            IntegrationSyncItem.external_type == external_type,
            IntegrationSyncItem.external_id == external_id,
        )
    )
    return row.scalar_one_or_none()


async def find_sync_item_for_local_note(
    db: AsyncSession,
    *,
    connector_id: UUID,
    local_note_id: UUID,
    external_type: str | None = None,
) -> Optional[IntegrationSyncItem]:
    statement = select(IntegrationSyncItem).where(
        IntegrationSyncItem.connector_id == connector_id,
        IntegrationSyncItem.local_note_id == local_note_id,
    )
    if external_type:
        statement = statement.where(IntegrationSyncItem.external_type == external_type)
    statement = statement.order_by(IntegrationSyncItem.last_synced_at.desc().nullslast())
    row = await db.execute(statement)
    return row.scalars().first()


async def upsert_sync_item(
    db: AsyncSession,
    *,
    connector: Connector,
    external_type: str,
    external_id: str,
    sync_direction: str,
    local_note_id: UUID | None = None,
    local_document_id: UUID | None = None,
    source_hash: str | None = None,
    external_updated_at: datetime | None = None,
    metadata: Mapping[str, Any] | None = None,
    status: str = "synced",
) -> IntegrationSyncItem:
    now = datetime.now(timezone.utc)
    item = await find_sync_item(
        db,
        connector_id=connector.id,
        external_type=external_type,
        external_id=external_id,
    )
    if item is None:
        item = IntegrationSyncItem(
            connector_id=connector.id,
            workspace_id=connector.workspace_id,
            provider=connector.connector_type,
            external_type=external_type,
            external_id=external_id,
            first_seen_at=now,
        )
        db.add(item)

    item.local_note_id = local_note_id if local_note_id is not None else item.local_note_id
    item.local_document_id = local_document_id if local_document_id is not None else item.local_document_id
    item.external_updated_at = external_updated_at
    item.source_hash = source_hash
    item.sync_direction = sync_direction
    item.sync_status = status
    item.last_error = None if status == "synced" else item.last_error
    item.item_metadata = {**dict(item.item_metadata or {}), **dict(metadata or {})}
    item.last_seen_at = now
    item.last_synced_at = now if status == "synced" else item.last_synced_at
    await db.flush()
    return item


async def mark_sync_item_failed(db: AsyncSession, item: IntegrationSyncItem, error: str) -> None:
    item.sync_status = "failed"
    item.last_error = error[:4000]
    item.last_seen_at = datetime.now(timezone.utc)
    await db.flush()


async def mark_connector_sync_started(db: AsyncSession, connector: Connector) -> None:
    connector.sync_status = "syncing"
    connector.last_sync_started_at = datetime.now(timezone.utc)
    connector.last_sync_error = None
    await db.flush()


async def mark_connector_sync_success(db: AsyncSession, connector: Connector, *, cursor: Mapping[str, Any] | None = None) -> None:
    now = datetime.now(timezone.utc)
    connector.sync_status = "idle"
    connector.last_sync_at = now
    connector.last_sync_completed_at = now
    connector.last_sync_error = None
    if cursor:
        connector.sync_cursor = {**dict(connector.sync_cursor or {}), **dict(cursor)}
    await db.flush()


async def mark_connector_sync_failed(db: AsyncSession, connector: Connector, error: str) -> None:
    connector.sync_status = "failed"
    connector.last_sync_error = error[:4000]
    connector.last_sync_completed_at = datetime.now(timezone.utc)
    await db.flush()


async def mark_connector_reauthorization_required(db: AsyncSession, connector: Connector, error: str) -> None:
    connector.sync_status = "reauth_required"
    connector.last_sync_error = error[:4000]
    connector.last_sync_completed_at = datetime.now(timezone.utc)
    connector.is_active = 0
    await db.flush()


async def create_integration_note(
    db: AsyncSession,
    *,
    connector: Connector,
    title: str,
    content: str,
    note_type: str,
    tags: list[str],
    source_url: str | None = None,
) -> Note:
    note = Note(
        workspace_id=connector.workspace_id,
        user_id=connector.user_id,
        title=title[:500] or "Untitled integration note",
        content=content,
        note_type=note_type,
        tags=list(dict.fromkeys(tags)),
        source_url=source_url,
        word_count=len(content.split()),
        ai_generated=0,
    )
    db.add(note)
    await db.flush()
    return note
