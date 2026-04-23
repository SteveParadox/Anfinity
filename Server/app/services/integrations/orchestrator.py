"""Integration sync orchestration for API and Celery entry points."""

from __future__ import annotations

from typing import Any, Mapping
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Connector, IntegrationSyncItem
from app.services.integrations.base import BaseIntegrationService, ReauthorizationRequiredError
from app.services.integrations.calendar import CalendarIntegrationService
from app.services.integrations.gmail import GmailIntegrationService
from app.services.integrations.notion import NotionIntegrationService
from app.services.integrations.oauth import get_valid_access_token
from app.services.integrations.providers import IntegrationProvider
from app.services.integrations.slack import SlackIntegrationService, message_from_payload
from app.services.integrations.sync_state import (
    mark_connector_sync_failed,
    mark_connector_sync_started,
    mark_connector_sync_success,
    mark_connector_reauthorization_required,
)


async def load_connector(db: AsyncSession, connector_id: UUID) -> Connector:
    connector = (await db.execute(select(Connector).where(Connector.id == connector_id))).scalar_one_or_none()
    if connector is None:
        raise ValueError("Connector not found")
    return connector


async def sync_connector(db: AsyncSession, connector_id: UUID) -> Mapping[str, Any]:
    connector = await load_connector(db, connector_id)
    if not connector.is_active:
        return {"status": "skipped", "reason": "inactive", "connector_id": str(connector.id)}

    await mark_connector_sync_started(db, connector)
    await db.commit()

    try:
        access_token = await get_valid_access_token(db, connector)
        provider = IntegrationProvider(str(connector.connector_type))
        result: dict[str, Any]
        if provider == IntegrationProvider.NOTION:
            result = dict(await _single_service(NotionIntegrationService(connector, access_token)).sync(db))
        elif provider == IntegrationProvider.GOOGLE:
            enabled = set((connector.config or {}).get("enabled_capabilities") or ["gmail_sync", "calendar_sync"])
            result = {}
            if "gmail_sync" in enabled:
                result["gmail"] = await _single_service(GmailIntegrationService(connector, access_token)).sync(db)
            if "calendar_sync" in enabled:
                result["calendar"] = await _single_service(CalendarIntegrationService(connector, access_token)).sync(db)
            if not result:
                result = {"status": "noop", "reason": "No Google sync capabilities are enabled"}
        elif provider == IntegrationProvider.SLACK:
            result = dict(await _single_service(SlackIntegrationService(connector, access_token)).sync(db))
        else:
            result = {"status": "skipped", "reason": f"Unsupported provider {connector.connector_type}"}

        await mark_connector_sync_success(db, connector)
        await db.commit()
        return {"status": "success", "connector_id": str(connector.id), "provider": connector.connector_type, "result": result}
    except ReauthorizationRequiredError as exc:
        await mark_connector_reauthorization_required(db, connector, str(exc))
        await db.commit()
        return {
            "status": "reauth_required",
            "connector_id": str(connector.id),
            "provider": connector.connector_type,
            "error": str(exc),
        }
    except Exception as exc:
        await mark_connector_sync_failed(db, connector, str(exc))
        await db.commit()
        raise


async def post_slack_message(db: AsyncSession, connector_id: UUID, payload: Mapping[str, Any]) -> Mapping[str, Any]:
    connector = await load_connector(db, connector_id)
    if connector.connector_type != IntegrationProvider.SLACK.value:
        raise ValueError("Connector is not a Slack integration")
    access_token = await get_valid_access_token(db, connector)
    service = SlackIntegrationService(connector, access_token)
    return await service.post_message(message_from_payload(payload, connector))


async def list_connector_sync_items(
    db: AsyncSession,
    *,
    connector_id: UUID,
    limit: int = 50,
) -> list[IntegrationSyncItem]:
    statement = (
        select(IntegrationSyncItem)
        .where(IntegrationSyncItem.connector_id == connector_id)
        .order_by(IntegrationSyncItem.last_seen_at.desc())
        .limit(max(1, min(limit, 200)))
    )
    rows = await db.execute(statement)
    return list(rows.scalars().all())


def _single_service(service: BaseIntegrationService) -> BaseIntegrationService:
    return service
