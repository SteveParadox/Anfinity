"""Google Calendar sync with conservative semantic note matching."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Optional

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Connector, Note
from app.services.integrations.base import BaseIntegrationService
from app.services.integrations.providers import IntegrationProvider
from app.services.integrations.sync_state import create_integration_note, find_sync_item, stable_hash, upsert_sync_item
from app.services.semantic_search import SemanticSearchResult, get_semantic_search_service


CALENDAR_API_BASE = "https://www.googleapis.com/calendar/v3"
DEFAULT_CALENDAR_MATCH_THRESHOLD = 0.82


@dataclass(frozen=True)
class CalendarMeeting:
    event_id: str
    calendar_id: str
    title: str
    description: str
    start: str
    end: str
    attendees: list[str]
    location: str | None
    html_link: str | None
    updated_at: datetime | None


class CalendarIntegrationService(BaseIntegrationService):
    provider = IntegrationProvider.GOOGLE
    capabilities = ("calendar_sync",)

    async def sync(self, db: AsyncSession) -> Mapping[str, int]:
        return await self.sync_events(db)

    async def sync_events(self, db: AsyncSession) -> dict[str, int]:
        config = self.connector.config or {}
        calendar_id = str(config.get("calendar_id") or "primary")
        days_ahead = int(config.get("calendar_days_ahead") or 30)
        threshold = max(DEFAULT_CALENDAR_MATCH_THRESHOLD, min(float(config.get("calendar_match_threshold") or DEFAULT_CALENDAR_MATCH_THRESHOLD), 0.98))
        linked = 0
        created = 0
        skipped = 0

        async with httpx.AsyncClient(timeout=30) as client:
            events = await self._list_events(client, calendar_id=calendar_id, days_ahead=days_ahead)

        for raw_event in events:
            meeting = parse_calendar_event(raw_event, calendar_id=calendar_id)
            existing = await find_sync_item(
                db,
                connector_id=self.connector.id,
                external_type="calendar_event",
                external_id=meeting.event_id,
            )
            event_hash = stable_hash(raw_event)
            if existing and existing.source_hash == event_hash:
                skipped += 1
                continue
            if existing and existing.local_note_id:
                note = (
                    await db.execute(
                        select(Note).where(
                            Note.id == existing.local_note_id,
                            Note.workspace_id == self.connector.workspace_id,
                        )
                    )
                ).scalar_one_or_none()
                if note is not None:
                    note.tags = list(dict.fromkeys([*(note.tags or []), "meeting", "calendar"]))
                    await upsert_sync_item(
                        db,
                        connector=self.connector,
                        external_type="calendar_event",
                        external_id=meeting.event_id,
                        sync_direction=existing.sync_direction,
                        local_note_id=note.id,
                        source_hash=event_hash,
                        external_updated_at=meeting.updated_at,
                        metadata={"meeting": meeting_payload(meeting), "updated_existing_mapping": True},
                    )
                    linked += 1
                    continue

            match = await find_best_note_match(
                db,
                connector=self.connector,
                meeting=meeting,
                threshold=threshold,
            )
            if match is not None:
                note = (
                    await db.execute(
                        select(Note).where(
                            Note.id == match.document_id,
                            Note.workspace_id == self.connector.workspace_id,
                        )
                    )
                ).scalar_one_or_none()
                if note is not None:
                    note.tags = list(dict.fromkeys([*(note.tags or []), "meeting"]))
                    await upsert_sync_item(
                        db,
                        connector=self.connector,
                        external_type="calendar_event",
                        external_id=meeting.event_id,
                        sync_direction="linked",
                        local_note_id=note.id,
                        source_hash=event_hash,
                        external_updated_at=meeting.updated_at,
                        metadata={"meeting": meeting_payload(meeting), "match_score": match.final_score, "matched_note_id": str(note.id)},
                    )
                    linked += 1
                    continue

            note = await create_integration_note(
                db,
                connector=self.connector,
                title=f"Meeting: {meeting.title}",
                content=build_meeting_note_content(meeting),
                note_type="note",
                tags=["meeting", "calendar"],
                source_url=meeting.html_link,
            )
            await upsert_sync_item(
                db,
                connector=self.connector,
                external_type="calendar_event",
                external_id=meeting.event_id,
                sync_direction="pull",
                local_note_id=note.id,
                source_hash=event_hash,
                external_updated_at=meeting.updated_at,
                metadata={"meeting": meeting_payload(meeting), "created_meeting_note": True},
            )
            created += 1

        return {"linked": linked, "created": created, "skipped": skipped}

    async def _list_events(self, client: httpx.AsyncClient, *, calendar_id: str, days_ahead: int) -> list[Mapping[str, Any]]:
        now = datetime.now(timezone.utc)
        params = {
            "singleEvents": "true",
            "orderBy": "startTime",
            "timeMin": now.isoformat().replace("+00:00", "Z"),
            "timeMax": (now + timedelta(days=max(1, min(days_ahead, 365)))).isoformat().replace("+00:00", "Z"),
            "maxResults": 100,
        }
        events: list[Mapping[str, Any]] = []
        page_token: str | None = None
        while True:
            request_params = dict(params)
            if page_token:
                request_params["pageToken"] = page_token
            response = await client.get(
                f"{CALENDAR_API_BASE}/calendars/{calendar_id}/events",
                headers={"Authorization": f"Bearer {self.access_token}"},
                params=request_params,
            )
            data = response.json()
            if response.status_code >= 400:
                raise RuntimeError(f"Calendar event list failed: {data.get('error') or response.text[:300]}")
            events.extend(event for event in data.get("items", []) if event.get("id") and event.get("status") != "cancelled")
            page_token = data.get("nextPageToken")
            if not page_token:
                return events


async def find_best_note_match(
    db: AsyncSession,
    *,
    connector: Connector,
    meeting: CalendarMeeting,
    threshold: float,
) -> Optional[SemanticSearchResult]:
    query = " ".join(part for part in [meeting.title, meeting.description, " ".join(meeting.attendees)] if part).strip()
    if len(query) < 8:
        return None

    service = get_semantic_search_service()
    execution = await service.search(
        workspace_id=connector.workspace_id,
        user_id=connector.user_id,
        query=query,
        limit=5,
        filters={"source_type": "note"},
        db=db,
        log_execution=False,
        include_postgresql=True,
        include_retriever=True,
    )
    for result in execution.results:
        if calendar_match_is_confident(meeting, result, threshold=threshold):
            return result
    return None


def calendar_match_is_confident(meeting: CalendarMeeting, result: SemanticSearchResult, *, threshold: float) -> bool:
    if result.source_kind != "note" or result.final_score < threshold:
        return False
    meeting_terms = _important_terms(" ".join([meeting.title, meeting.description]))
    result_terms = _important_terms(" ".join([result.document_title, result.content[:1000]]))
    if not meeting_terms or not result_terms:
        return result.final_score >= min(0.94, threshold + 0.1)
    overlap = len(meeting_terms & result_terms) / max(len(meeting_terms), 1)
    return overlap >= 0.28 or result.final_score >= min(0.94, threshold + 0.1)


def parse_calendar_event(event: Mapping[str, Any], *, calendar_id: str) -> CalendarMeeting:
    return CalendarMeeting(
        event_id=str(event["id"]),
        calendar_id=calendar_id,
        title=str(event.get("summary") or "Untitled meeting"),
        description=str(event.get("description") or ""),
        start=_event_time(event.get("start") or {}),
        end=_event_time(event.get("end") or {}),
        attendees=[str(item.get("email")) for item in event.get("attendees", []) or [] if isinstance(item, Mapping) and item.get("email")],
        location=str(event.get("location")) if event.get("location") else None,
        html_link=str(event.get("htmlLink")) if event.get("htmlLink") else None,
        updated_at=parse_calendar_time(event.get("updated")),
    )


def build_meeting_note_content(meeting: CalendarMeeting) -> str:
    attendees = "\n".join(f"- {email}" for email in meeting.attendees) or "- Not available"
    return "\n".join(
        [
            "## Meeting Metadata",
            f"- Calendar: {meeting.calendar_id}",
            f"- Event ID: {meeting.event_id}",
            f"- Start: {meeting.start}",
            f"- End: {meeting.end}",
            f"- Location: {meeting.location or 'Not provided'}",
            f"- Link: {meeting.html_link or 'Not provided'}",
            "",
            "## Attendees",
            attendees,
            "",
            "## Agenda / Description",
            meeting.description or "No description provided.",
            "",
            "## Notes",
            "- ",
            "",
            "## Action Items",
            "- ",
        ]
    )


def meeting_payload(meeting: CalendarMeeting) -> dict[str, Any]:
    return {
        "event_id": meeting.event_id,
        "calendar_id": meeting.calendar_id,
        "title": meeting.title,
        "description": meeting.description,
        "start": meeting.start,
        "end": meeting.end,
        "attendees": meeting.attendees,
        "location": meeting.location,
        "html_link": meeting.html_link,
    }


def parse_calendar_time(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _event_time(value: Mapping[str, Any]) -> str:
    return str(value.get("dateTime") or value.get("date") or "")


def _important_terms(value: str) -> set[str]:
    stopwords = {"the", "and", "for", "with", "from", "into", "this", "that", "meeting", "review", "sync"}
    return {
        token
        for token in re.findall(r"[a-z0-9]{4,}", value.lower())
        if token not in stopwords
    }
