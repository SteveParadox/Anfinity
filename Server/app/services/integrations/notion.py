"""Notion two-way sync with CogniFlowID tracking and block chunking."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Optional
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Connector, IntegrationSyncItem, Note
from app.services.integrations.base import BaseIntegrationService
from app.services.integrations.providers import IntegrationProvider
from app.services.integrations.sync_state import (
    create_integration_note,
    find_sync_item,
    find_sync_item_for_local_note,
    stable_hash,
    upsert_sync_item,
)


NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
COGNIFLOW_ID_PROPERTY = "CogniFlowID"
NOTION_TEXT_LIMIT = 2000


class NotionIntegrationService(BaseIntegrationService):
    provider = IntegrationProvider.NOTION
    capabilities = ("notion_sync",)

    async def sync(self, db: AsyncSession) -> dict[str, int]:
        database_id = str((self.connector.config or {}).get("database_id") or "")
        if not database_id:
            return {"pulled": 0, "pushed": 0, "updated": 0, "skipped": 0}
        pulled_result = await self.pull_database(db, database_id=database_id)
        pushed_result = await self.push_eligible_notes(db, database_id=database_id)
        return {
            "pulled": pulled_result.get("pulled", 0),
            "updated": pulled_result.get("updated", 0),
            "pushed": pushed_result.get("pushed", 0),
            "skipped": pulled_result.get("skipped", 0) + pushed_result.get("skipped", 0),
        }

    async def push_note_to_notion(self, db: AsyncSession, *, note_id: UUID, database_id: str) -> Mapping[str, Any]:
        note = (await db.execute(select(Note).where(Note.id == note_id, Note.workspace_id == self.connector.workspace_id))).scalar_one_or_none()
        if note is None:
            raise ValueError("Note not found in connector workspace")

        existing_item = (
            await find_sync_item_for_local_note(
                db,
                connector_id=self.connector.id,
                external_type="notion_page",
                local_note_id=note.id,
            )
            or await find_sync_item(
                db,
                connector_id=self.connector.id,
                external_type="notion_page",
                external_id=str(note.id),
            )
        )
        return await self._push_note(db, note=note, database_id=database_id, existing_item=existing_item)

    async def _push_note(
        self,
        db: AsyncSession,
        *,
        note: Note,
        database_id: str,
        existing_item: IntegrationSyncItem | None,
    ) -> Mapping[str, Any]:
        blocks = content_to_notion_blocks(note.content)
        async with httpx.AsyncClient(timeout=30) as client:
            page_id = str((existing_item.item_metadata or {}).get("notion_page_id") or existing_item.external_id) if existing_item else ""
            if page_id:
                await self._replace_page_children(client, page_id=page_id, blocks=blocks)
                await self._update_page_properties(client, page_id=page_id, title=note.title, cogniflow_id=str(note.id))
                page = {"id": page_id, "url": (existing_item.item_metadata or {}).get("url")}
            else:
                page = await self._create_page(client, database_id=database_id, note=note, blocks=blocks)
                page_id = str(page["id"])

        await upsert_sync_item(
            db,
            connector=self.connector,
            external_type="notion_page",
            external_id=page_id,
            sync_direction="push",
            local_note_id=note.id,
            source_hash=stable_hash({"pushed_cogniflow_id": str(note.id), "title": note.title, "content": note.content}),
            metadata={
                "notion_page_id": page_id,
                "url": page.get("url"),
                "cogniflow_id": str(note.id),
                "local_hash": stable_hash({"title": note.title, "content": note.content}),
            },
        )
        return page

    async def push_eligible_notes(self, db: AsyncSession, *, database_id: str) -> dict[str, int]:
        config = self.connector.config or {}
        push_all = bool(config.get("notion_push_all", False))
        push_tag = str(config.get("notion_push_tag") or "notion-sync").strip()
        limit = max(1, min(int(config.get("notion_push_limit") or 50), 200))

        if not push_all and not push_tag:
            return {"pushed": 0, "skipped": 0}

        rows = await db.execute(
            select(Note)
            .where(Note.workspace_id == self.connector.workspace_id)
            .order_by(Note.updated_at.desc().nullslast(), Note.created_at.desc())
            .limit(limit)
        )
        notes = list(rows.scalars().all())
        note_ids = [note.id for note in notes]
        existing_by_note_id: dict[UUID, IntegrationSyncItem] = {}
        if note_ids:
            existing_rows = await db.execute(
                select(IntegrationSyncItem).where(
                    IntegrationSyncItem.connector_id == self.connector.id,
                    IntegrationSyncItem.external_type == "notion_page",
                    IntegrationSyncItem.local_note_id.in_(note_ids),
                )
            )
            existing_by_note_id = {
                item.local_note_id: item
                for item in existing_rows.scalars().all()
                if item.local_note_id is not None
            }
        pushed = 0
        skipped = 0
        for note in notes:
            tags = set(str(tag) for tag in (note.tags or []))
            if not push_all and push_tag not in tags:
                skipped += 1
                continue

            existing_item = existing_by_note_id.get(note.id)
            current_hash = stable_hash({"title": note.title, "content": note.content})
            if existing_item and (existing_item.item_metadata or {}).get("local_hash") == current_hash:
                skipped += 1
                continue
            if _origin_is_notion_pull(existing_item) and (existing_item.item_metadata or {}).get("local_hash") == current_hash:
                skipped += 1
                continue

            await self._push_note(db, note=note, database_id=database_id, existing_item=existing_item)
            pushed += 1

        return {"pushed": pushed, "skipped": skipped}

    async def pull_database(self, db: AsyncSession, *, database_id: str) -> dict[str, int]:
        pulled = 0
        updated = 0
        skipped = 0
        async with httpx.AsyncClient(timeout=30) as client:
            for page in await self._query_database(client, database_id=database_id):
                page_id = str(page.get("id") or "")
                if not page_id:
                    continue
                page_hash = stable_hash(page)
                existing = await find_sync_item(
                    db,
                    connector_id=self.connector.id,
                    external_type="notion_page",
                    external_id=page_id,
                )
                if existing and existing.source_hash == page_hash:
                    skipped += 1
                    continue

                cogniflow_id = extract_cogniflow_id(page)
                if cogniflow_id:
                    note = await self._load_note_by_cogniflow_id(db, cogniflow_id)
                    if note is not None:
                        content = await self._fetch_page_content(client, page_id)
                        incoming_hash = stable_hash({"title": extract_page_title(page), "content": content})
                        local_hash = stable_hash({"title": note.title, "content": note.content})
                        if incoming_hash != local_hash:
                            note.title = extract_page_title(page)
                            note.content = content or note.content
                            note.word_count = len((note.content or "").split())
                            if "notion" not in (note.tags or []):
                                note.tags = list(dict.fromkeys([*(note.tags or []), "notion"]))
                            updated += 1
                        await upsert_sync_item(
                            db,
                            connector=self.connector,
                            external_type="notion_page",
                            external_id=page_id,
                            sync_direction="push" if incoming_hash == local_hash else "pull",
                            local_note_id=note.id,
                            source_hash=page_hash,
                            external_updated_at=parse_notion_time(page.get("last_edited_time")),
                            metadata={
                                "notion_page_id": page_id,
                                "url": page.get("url"),
                                "cogniflow_id": cogniflow_id,
                                "source": "cogniflow",
                                "last_sync_origin": "cogniflow" if incoming_hash == local_hash else "notion",
                                "local_hash": stable_hash({"title": note.title, "content": note.content}),
                            },
                        )
                        if incoming_hash == local_hash:
                            skipped += 1
                        continue

                content = await self._fetch_page_content(client, page_id)
                note = await create_integration_note(
                    db,
                    connector=self.connector,
                    title=extract_page_title(page),
                    content=content or "Imported Notion page with no text content.",
                    note_type="note",
                    tags=["notion"],
                    source_url=str(page.get("url") or ""),
                )
                await upsert_sync_item(
                    db,
                    connector=self.connector,
                    external_type="notion_page",
                    external_id=page_id,
                    sync_direction="pull",
                    local_note_id=note.id,
                    source_hash=page_hash,
                    external_updated_at=parse_notion_time(page.get("last_edited_time")),
                    metadata={
                        "notion_page_id": page_id,
                        "url": page.get("url"),
                        "cogniflow_id": str(note.id),
                        "source": "notion",
                        "last_sync_origin": "notion",
                        "local_hash": stable_hash({"title": note.title, "content": note.content}),
                    },
                )
                await self._set_cogniflow_id_if_possible(client, page_id=page_id, cogniflow_id=str(note.id))
                pulled += 1

        return {"pulled": pulled, "updated": updated, "pushed": 0, "skipped": skipped}

    async def _create_page(self, client: httpx.AsyncClient, *, database_id: str, note: Note, blocks: list[dict[str, Any]]) -> Mapping[str, Any]:
        payload = {
            "parent": {"database_id": database_id},
            "properties": build_page_properties(note.title, str(note.id)),
            "children": blocks[:100],
        }
        response = await client.post(f"{NOTION_API_BASE}/pages", headers=self._headers(), json=payload)
        data = response.json()
        if response.status_code >= 400:
            raise RuntimeError(f"Notion page create failed: {data.get('message') or response.text[:300]}")
        page_id = str(data["id"])
        if len(blocks) > 100:
            await self._append_blocks(client, page_id=page_id, blocks=blocks[100:])
        return data

    async def _replace_page_children(self, client: httpx.AsyncClient, *, page_id: str, blocks: list[dict[str, Any]]) -> None:
        existing = await self._list_block_children(client, page_id)
        for block in existing:
            block_id = block.get("id")
            if block_id:
                await client.delete(f"{NOTION_API_BASE}/blocks/{block_id}", headers=self._headers())
        await self._append_blocks(client, page_id=page_id, blocks=blocks)

    async def _update_page_properties(self, client: httpx.AsyncClient, *, page_id: str, title: str, cogniflow_id: str) -> None:
        response = await client.patch(
            f"{NOTION_API_BASE}/pages/{page_id}",
            headers=self._headers(),
            json={"properties": build_page_properties(title, cogniflow_id)},
        )
        if response.status_code >= 400:
            raise RuntimeError(f"Notion page property update failed: {response.text[:300]}")

    async def _append_blocks(self, client: httpx.AsyncClient, *, page_id: str, blocks: list[dict[str, Any]]) -> None:
        for start in range(0, len(blocks), 100):
            response = await client.patch(
                f"{NOTION_API_BASE}/blocks/{page_id}/children",
                headers=self._headers(),
                json={"children": blocks[start : start + 100]},
            )
            if response.status_code >= 400:
                raise RuntimeError(f"Notion append blocks failed: {response.text[:300]}")

    async def _query_database(self, client: httpx.AsyncClient, *, database_id: str) -> list[Mapping[str, Any]]:
        results: list[Mapping[str, Any]] = []
        start_cursor: Optional[str] = None
        while True:
            payload: dict[str, Any] = {"page_size": 100}
            if start_cursor:
                payload["start_cursor"] = start_cursor
            response = await client.post(f"{NOTION_API_BASE}/databases/{database_id}/query", headers=self._headers(), json=payload)
            data = response.json()
            if response.status_code >= 400:
                raise RuntimeError(f"Notion database query failed: {data.get('message') or response.text[:300]}")
            results.extend(data.get("results", []))
            if not data.get("has_more"):
                return results
            start_cursor = data.get("next_cursor")

    async def _fetch_page_content(self, client: httpx.AsyncClient, page_id: str) -> str:
        blocks = await self._list_block_children(client, page_id)
        return notion_blocks_to_markdown(blocks)

    async def _list_block_children(self, client: httpx.AsyncClient, page_id: str) -> list[Mapping[str, Any]]:
        response = await client.get(f"{NOTION_API_BASE}/blocks/{page_id}/children", headers=self._headers(), params={"page_size": 100})
        data = response.json()
        if response.status_code >= 400:
            raise RuntimeError(f"Notion block fetch failed: {data.get('message') or response.text[:300]}")
        return data.get("results", [])

    async def _set_cogniflow_id_if_possible(self, client: httpx.AsyncClient, *, page_id: str, cogniflow_id: str) -> None:
        response = await client.patch(
            f"{NOTION_API_BASE}/pages/{page_id}",
            headers=self._headers(),
            json={"properties": {COGNIFLOW_ID_PROPERTY: {"rich_text": [{"text": {"content": cogniflow_id}}]}}},
        )
        if response.status_code >= 400:
            return

    async def _load_note_by_cogniflow_id(self, db: AsyncSession, cogniflow_id: str) -> Optional[Note]:
        try:
            note_id = UUID(cogniflow_id)
        except ValueError:
            return None
        return (await db.execute(select(Note).where(Note.id == note_id, Note.workspace_id == self.connector.workspace_id))).scalar_one_or_none()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        }


def split_text_for_notion(text: str, limit: int = NOTION_TEXT_LIMIT) -> list[str]:
    value = str(text or "")
    if not value:
        return [""]
    return [value[index : index + limit] for index in range(0, len(value), limit)]


def content_to_notion_blocks(content: str) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for paragraph in str(content or "").split("\n\n"):
        chunks = split_text_for_notion(paragraph)
        for chunk in chunks:
            blocks.append(
                {
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {"rich_text": [{"type": "text", "text": {"content": chunk}}]},
                }
            )
    return blocks or content_to_notion_blocks(" ")


def build_page_properties(title: str, cogniflow_id: str) -> dict[str, Any]:
    return {
        "Name": {"title": [{"text": {"content": title[:2000] or "Untitled"}}]},
        COGNIFLOW_ID_PROPERTY: {"rich_text": [{"text": {"content": cogniflow_id}}]},
    }


def extract_cogniflow_id(page: Mapping[str, Any]) -> Optional[str]:
    prop = (page.get("properties") or {}).get(COGNIFLOW_ID_PROPERTY) if isinstance(page.get("properties"), Mapping) else None
    if not isinstance(prop, Mapping):
        return None
    rich_text = prop.get("rich_text") or []
    if not rich_text:
        return None
    first = rich_text[0] if isinstance(rich_text[0], Mapping) else {}
    raw_value = str((first.get("plain_text") or ((first.get("text") or {}).get("content") if isinstance(first.get("text"), Mapping) else "")) or "").strip()
    return normalize_cogniflow_id(raw_value)


def normalize_cogniflow_id(value: str) -> Optional[str]:
    try:
        return str(UUID(str(value).strip()))
    except (TypeError, ValueError):
        return None


def extract_page_title(page: Mapping[str, Any]) -> str:
    properties = page.get("properties") or {}
    if not isinstance(properties, Mapping):
        return "Untitled Notion page"
    for value in properties.values():
        if isinstance(value, Mapping) and value.get("type") == "title":
            title_parts = value.get("title") or []
            return "".join(str(part.get("plain_text") or "") for part in title_parts if isinstance(part, Mapping)) or "Untitled Notion page"
    return "Untitled Notion page"


def notion_blocks_to_markdown(blocks: list[Mapping[str, Any]]) -> str:
    lines: list[str] = []
    for block in blocks:
        block_type = str(block.get("type") or "")
        data = block.get(block_type) if isinstance(block.get(block_type), Mapping) else {}
        text = _rich_text_to_plain(data.get("rich_text") or []) if isinstance(data, Mapping) else ""
        if not text:
            continue
        if block_type == "heading_1":
            lines.append(f"# {text}")
        elif block_type == "heading_2":
            lines.append(f"## {text}")
        elif block_type == "heading_3":
            lines.append(f"### {text}")
        elif block_type == "bulleted_list_item":
            lines.append(f"- {text}")
        elif block_type == "numbered_list_item":
            lines.append(f"1. {text}")
        elif block_type == "quote":
            lines.append(f"> {text}")
        else:
            lines.append(text)
    return "\n\n".join(lines)


def _rich_text_to_plain(rich_text: list[Any]) -> str:
    return "".join(str(item.get("plain_text") or "") for item in rich_text if isinstance(item, Mapping))


def parse_notion_time(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _origin_is_notion_pull(item: IntegrationSyncItem | None) -> bool:
    if item is None:
        return False
    metadata = item.item_metadata or {}
    return metadata.get("source") == "notion" or metadata.get("last_sync_origin") == "notion"
