"""Gmail polling, MIME extraction, and idempotent note capture.

Attachments are intentionally ignored during note capture; their metadata can
be added later without changing message idempotency. Quoted reply trimming is
also left to downstream note cleanup because providers and clients format
reply chains inconsistently.
"""

from __future__ import annotations

import base64
import html
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Mapping, Optional

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Connector
from app.services.integrations.base import BaseIntegrationService
from app.services.integrations.providers import IntegrationProvider
from app.services.integrations.sync_state import create_integration_note, find_sync_item, stable_hash, upsert_sync_item


GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1"


@dataclass(frozen=True)
class GmailCapturedMessage:
    message_id: str
    thread_id: str | None
    title: str
    body: str
    headers: dict[str, str]
    internal_date: datetime | None


class GmailIntegrationService(BaseIntegrationService):
    provider = IntegrationProvider.GOOGLE
    capabilities = ("gmail_sync",)

    async def sync(self, db: AsyncSession) -> Mapping[str, int]:
        return await self.poll_filtered_messages(db)

    async def poll_filtered_messages(self, db: AsyncSession) -> dict[str, int]:
        query = str((self.connector.config or {}).get("gmail_query") or "is:unread")
        max_results = int((self.connector.config or {}).get("gmail_max_results") or 25)
        captured = 0
        mark_read_failed = 0
        skipped = 0

        async with httpx.AsyncClient(timeout=30) as client:
            message_ids = await self._list_message_ids(client, query=query, max_results=max_results)
            for message_id in message_ids:
                if await find_sync_item(
                    db,
                    connector_id=self.connector.id,
                    external_type="gmail_message",
                    external_id=message_id,
                ):
                    skipped += 1
                    continue

                raw_message = await self._get_message(client, message_id)
                captured_message = parse_gmail_message(raw_message)
                if not captured_message.body.strip():
                    skipped += 1
                    continue

                note = await create_integration_note(
                    db,
                    connector=self.connector,
                    title=captured_message.title,
                    content=format_email_note(captured_message),
                    note_type="note",
                    tags=["gmail", "email"],
                    source_url=f"https://mail.google.com/mail/u/0/#inbox/{message_id}",
                )
                sync_item = await upsert_sync_item(
                    db,
                    connector=self.connector,
                    external_type="gmail_message",
                    external_id=message_id,
                    sync_direction="pull",
                    local_note_id=note.id,
                    source_hash=stable_hash({"id": message_id, "body": captured_message.body}),
                    external_updated_at=captured_message.internal_date,
                    metadata={
                        "thread_id": captured_message.thread_id,
                        "headers": captured_message.headers,
                        "marked_read_after_capture": False,
                    },
                )
                await db.flush()

                # Persist local capture before mutating provider state. If the
                # Gmail modify call fails, the sync item still prevents a
                # duplicate note on the next poll while the email remains unread.
                await db.commit()
                try:
                    await self._mark_message_read(client, message_id)
                    await upsert_sync_item(
                        db,
                        connector=self.connector,
                        external_type="gmail_message",
                        external_id=message_id,
                        sync_direction="pull",
                        local_note_id=sync_item.local_note_id,
                        source_hash=sync_item.source_hash,
                        external_updated_at=sync_item.external_updated_at,
                        metadata={
                            "marked_read_after_capture": True,
                            "marked_read_at": datetime.now(timezone.utc).isoformat(),
                        },
                    )
                except Exception as exc:
                    mark_read_failed += 1
                    await upsert_sync_item(
                        db,
                        connector=self.connector,
                        external_type="gmail_message",
                        external_id=message_id,
                        sync_direction="pull",
                        local_note_id=sync_item.local_note_id,
                        source_hash=sync_item.source_hash,
                        external_updated_at=sync_item.external_updated_at,
                        metadata={"marked_read_after_capture": False, "mark_read_error": str(exc)[:500]},
                        status="read_mark_failed",
                    )
                await db.flush()
                captured += 1

        return {"captured": captured, "skipped": skipped, "mark_read_failed": mark_read_failed}

    async def _list_message_ids(self, client: httpx.AsyncClient, *, query: str, max_results: int) -> list[str]:
        response = await client.get(
            f"{GMAIL_API_BASE}/users/me/messages",
            headers=self._headers(),
            params={"q": query, "maxResults": min(max(max_results, 1), 100)},
        )
        data = response.json()
        if response.status_code >= 400:
            raise RuntimeError(f"Gmail message list failed: {data.get('error') or response.text[:300]}")
        return [str(item["id"]) for item in data.get("messages", []) if item.get("id")]

    async def _get_message(self, client: httpx.AsyncClient, message_id: str) -> Mapping[str, Any]:
        response = await client.get(
            f"{GMAIL_API_BASE}/users/me/messages/{message_id}",
            headers=self._headers(),
            params={"format": "full"},
        )
        data = response.json()
        if response.status_code >= 400:
            raise RuntimeError(f"Gmail message fetch failed for {message_id}: {data.get('error') or response.text[:300]}")
        return data

    async def _mark_message_read(self, client: httpx.AsyncClient, message_id: str) -> None:
        response = await client.post(
            f"{GMAIL_API_BASE}/users/me/messages/{message_id}/modify",
            headers={**self._headers(), "Content-Type": "application/json"},
            json={"removeLabelIds": ["UNREAD"]},
        )
        if response.status_code >= 400:
            raise RuntimeError(f"Gmail mark-as-read failed for {message_id}: {response.text[:300]}")

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}"}


def parse_gmail_message(message: Mapping[str, Any]) -> GmailCapturedMessage:
    payload = dict(message.get("payload") or {})
    headers = extract_headers(payload)
    subject = headers.get("subject") or "Captured email"
    internal_date = parse_internal_date(message.get("internalDate"), headers.get("date"))
    body = extract_mime_body(payload)
    return GmailCapturedMessage(
        message_id=str(message.get("id") or ""),
        thread_id=str(message.get("threadId")) if message.get("threadId") else None,
        title=subject,
        body=body,
        headers=headers,
        internal_date=internal_date,
    )


def extract_headers(payload: Mapping[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for header in payload.get("headers", []) or []:
        if isinstance(header, Mapping) and header.get("name"):
            result[str(header["name"]).lower()] = str(header.get("value") or "")
    return result


def extract_mime_body(payload: Mapping[str, Any]) -> str:
    plain_parts: list[str] = []
    html_parts: list[str] = []
    _walk_mime(payload, plain_parts=plain_parts, html_parts=html_parts)
    plain_body = "\n\n".join(part.strip() for part in plain_parts if part.strip()).strip()
    if plain_body:
        return plain_body
    return "\n\n".join(strip_html(part).strip() for part in html_parts if part.strip()).strip()


def _walk_mime(payload: Mapping[str, Any], *, plain_parts: list[str], html_parts: list[str]) -> None:
    mime_type = str(payload.get("mimeType") or "").lower()
    body = payload.get("body") or {}
    if payload.get("filename") or (isinstance(body, Mapping) and body.get("attachmentId")):
        return
    data = body.get("data") if isinstance(body, Mapping) else None

    if data and mime_type == "text/plain":
        plain_parts.append(decode_gmail_body(str(data)))
    elif data and mime_type == "text/html":
        html_parts.append(decode_gmail_body(str(data)))

    for part in payload.get("parts", []) or []:
        if isinstance(part, Mapping):
            _walk_mime(part, plain_parts=plain_parts, html_parts=html_parts)


def decode_gmail_body(data: str) -> str:
    padded = data + ("=" * (-len(data) % 4))
    try:
        decoded = base64.urlsafe_b64decode(padded.encode()).decode("utf-8", errors="replace")
        if decoded.count("\ufffd") > max(1, len(decoded) // 8):
            return ""
        return decoded
    except Exception:
        return ""


def strip_html(value: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", "", value)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p\s*>", "\n\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text)


def parse_internal_date(internal_date: Any, date_header: str | None) -> Optional[datetime]:
    if internal_date is not None:
        try:
            return datetime.fromtimestamp(int(internal_date) / 1000, tz=timezone.utc)
        except (TypeError, ValueError):
            pass
    if date_header:
        try:
            parsed = parsedate_to_datetime(date_header)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except Exception:
            return None
    return None


def format_email_note(message: GmailCapturedMessage) -> str:
    sender = message.headers.get("from", "Unknown sender")
    to = message.headers.get("to", "Unknown recipient")
    date = message.internal_date.isoformat() if message.internal_date else message.headers.get("date", "")
    return "\n".join(
        [
            f"From: {sender}",
            f"To: {to}",
            f"Date: {date}",
            "",
            message.body,
        ]
    ).strip()
