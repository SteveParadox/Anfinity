"""Slack integration service with structured Block Kit message composition."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

import httpx

from app.database.models import Connector
from app.services.integrations.base import BaseIntegrationService, ProviderRateLimitError
from app.services.integrations.providers import IntegrationProvider


SLACK_API_BASE = "https://slack.com/api"


@dataclass(frozen=True)
class SlackActionButton:
    text: str
    url: Optional[str] = None
    value: Optional[str] = None
    action_id: str = "open"
    style: Optional[str] = None


@dataclass(frozen=True)
class SlackMessage:
    channel_id: str
    title: str
    body: str
    context: list[str] = field(default_factory=list)
    buttons: list[SlackActionButton] = field(default_factory=list)
    unfurl_links: bool = False


def build_slack_blocks(message: SlackMessage) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*{_truncate_text(message.title, 250)}*\n{_truncate_text(message.body, 2900)}",
            },
        }
    ]

    if message.context:
        blocks.append(
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": _truncate_text(item, 300)}
                    for item in message.context[:10]
                    if item
                ],
            }
        )

    if message.buttons:
        blocks.append(
            {
                "type": "actions",
                "elements": [
                    _button_to_block(button)
                    for button in message.buttons[:5]
                ],
            }
        )

    return blocks


class SlackIntegrationService(BaseIntegrationService):
    provider = IntegrationProvider.SLACK
    capabilities = ("slack_posting",)

    async def sync(self, db: Any) -> Mapping[str, Any]:
        del db
        return {"status": "noop", "reason": "Slack sync is push-only; use post endpoint"}

    async def post_message(self, message: SlackMessage) -> Mapping[str, Any]:
        if not message.channel_id:
            raise ValueError("Slack channel_id is required")

        payload = {
            "channel": message.channel_id,
            "text": message.title,
            "blocks": build_slack_blocks(message),
            "unfurl_links": message.unfurl_links,
        }

        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                f"{SLACK_API_BASE}/chat.postMessage",
                headers={
                    "Authorization": f"Bearer {self.access_token}",
                    "Content-Type": "application/json; charset=utf-8",
                },
                json=payload,
            )

        data = response.json()
        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            raise ProviderRateLimitError("Slack", retry_after)
        if response.status_code >= 400 or not data.get("ok"):
            raise RuntimeError(f"Slack post failed: {data.get('error') or response.text[:300]}")
        return data


def message_from_payload(payload: Mapping[str, Any], connector: Connector) -> SlackMessage:
    channel_id = str(payload.get("channel_id") or payload.get("channel") or (connector.config or {}).get("default_channel_id") or "")
    buttons = [
        SlackActionButton(
            text=str(item.get("text") or "Open"),
            url=str(item.get("url")) if item.get("url") else None,
            value=str(item.get("value")) if item.get("value") else None,
            action_id=str(item.get("action_id") or "open"),
            style=str(item.get("style")) if item.get("style") in {"primary", "danger"} else None,
        )
        for item in payload.get("buttons", []) or []
        if isinstance(item, Mapping)
    ]
    return SlackMessage(
        channel_id=channel_id,
        title=str(payload.get("title") or "CogniFlow update"),
        body=str(payload.get("body") or payload.get("message") or ""),
        context=[str(item) for item in payload.get("context", []) or [] if str(item)],
        buttons=buttons,
        unfurl_links=bool(payload.get("unfurl_links", False)),
    )


def _button_to_block(button: SlackActionButton) -> dict[str, Any]:
    block: dict[str, Any] = {
        "type": "button",
        "text": {"type": "plain_text", "text": _truncate_text(button.text, 75), "emoji": True},
        "action_id": _truncate_text(button.action_id, 255),
    }
    if button.url:
        block["url"] = button.url
    if button.value:
        block["value"] = _truncate_text(button.value, 2000)
    if button.style:
        block["style"] = button.style
    return block


def _truncate_text(value: str, limit: int) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[: max(limit - 3, 0)] + "..."
