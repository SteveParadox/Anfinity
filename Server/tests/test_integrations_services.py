from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.services.integrations.base import ReauthorizationRequiredError
from app.services.integrations.calendar import CalendarMeeting, build_meeting_note_content, calendar_match_is_confident, find_best_note_match, meeting_payload
from app.services.integrations.gmail import extract_mime_body, format_email_note, parse_gmail_message
from app.services.integrations.notion import (
    COGNIFLOW_ID_PROPERTY,
    build_page_properties,
    content_to_notion_blocks,
    extract_cogniflow_id,
    normalize_cogniflow_id,
    split_text_for_notion,
)
from app.services.integrations.oauth import OAuthState, decode_oauth_state, encode_oauth_state, refresh_connector_token
from app.services.integrations.providers import IntegrationProvider, get_provider_definition, provider_registry_payload
from app.services.integrations.slack import SlackActionButton, SlackMessage, build_slack_blocks, message_from_payload
from app.services.semantic_search import SemanticSearchExecution, SemanticSearchResult


def _gmail_data(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")


def test_provider_registry_exposes_shared_oauth_providers() -> None:
    providers = {item["provider"]: item for item in provider_registry_payload()}
    assert providers["slack"]["capabilities"] == ["slack_posting"]
    assert providers["notion"]["capabilities"] == ["notion_sync"]
    assert set(providers["google"]["capabilities"]) == {"gmail_sync", "calendar_sync"}
    assert "https://www.googleapis.com/auth/gmail.modify" in providers["google"]["required_scopes"]
    assert "profile" not in providers["google"]["required_scopes"]


def test_google_scopes_can_be_narrowed_by_capability() -> None:
    definition = get_provider_definition("google")
    gmail_scopes = definition.scopes_for_capabilities(("gmail_sync",))
    calendar_scopes = definition.scopes_for_capabilities(("calendar_sync",))
    assert "https://www.googleapis.com/auth/gmail.modify" in gmail_scopes
    assert "https://www.googleapis.com/auth/calendar.readonly" not in gmail_scopes
    assert "https://www.googleapis.com/auth/calendar.readonly" in calendar_scopes
    assert "https://www.googleapis.com/auth/gmail.modify" not in calendar_scopes


def test_oauth_state_is_signed_and_tamper_protected() -> None:
    state = OAuthState(
        provider=IntegrationProvider.GOOGLE,
        workspace_id=uuid4(),
        user_id=uuid4(),
        nonce="nonce",
        issued_at=int(datetime.now(timezone.utc).timestamp()),
        capabilities=("gmail_sync",),
    )
    token = encode_oauth_state(state)
    decoded = decode_oauth_state(token)
    assert decoded.provider == IntegrationProvider.GOOGLE
    assert decoded.workspace_id == state.workspace_id
    assert decoded.capabilities == ("gmail_sync",)
    with pytest.raises(HTTPException):
        decode_oauth_state(token[:-1] + ("A" if token[-1] != "A" else "B"))


def test_slack_block_kit_payload_includes_context_and_actions() -> None:
    blocks = build_slack_blocks(
        SlackMessage(
            channel_id="C123",
            title="New note captured",
            body="Launch plan was synced.",
            context=["Workspace: Product"],
            buttons=[SlackActionButton(text="Open note", url="https://app.example.com/notes/1", action_id="open_note")],
        )
    )
    assert blocks[0]["type"] == "section"
    assert blocks[1]["type"] == "context"
    assert blocks[2]["type"] == "actions"
    assert blocks[2]["elements"][0]["url"] == "https://app.example.com/notes/1"


def test_slack_message_payload_uses_workspace_default_channel() -> None:
    connector = SimpleNamespace(config={"default_channel_id": "CDEFAULT"})
    message = message_from_payload(
        {
            "title": "Decision ready",
            "body": "Review the note.",
            "buttons": [{"text": "Open note", "value": "note-1", "action_id": "open_note"}],
        },
        connector,
    )
    assert message.channel_id == "CDEFAULT"
    assert message.buttons[0].value == "note-1"


def test_notion_cogniflow_id_and_2000_character_chunking() -> None:
    note_id = str(uuid4())
    chunks = split_text_for_notion("x" * 4500)
    assert [len(chunk) for chunk in chunks] == [2000, 2000, 500]

    properties = build_page_properties("Decision log", note_id)
    assert COGNIFLOW_ID_PROPERTY in properties
    assert properties[COGNIFLOW_ID_PROPERTY]["rich_text"][0]["text"]["content"] == note_id

    page = {
        "properties": {
            COGNIFLOW_ID_PROPERTY: {
                "rich_text": [{"plain_text": note_id, "text": {"content": note_id}}]
            }
        }
    }
    assert extract_cogniflow_id(page) == note_id
    assert normalize_cogniflow_id("not-a-uuid") is None

    blocks = content_to_notion_blocks("A" * 4501)
    assert len(blocks) == 3
    assert all(
        len(block["paragraph"]["rich_text"][0]["text"]["content"]) <= 2000
        for block in blocks
    )


def test_gmail_mime_extraction_prefers_plain_text_and_falls_back_to_html() -> None:
    payload = {
        "mimeType": "multipart/alternative",
        "parts": [
            {"mimeType": "text/html", "body": {"data": _gmail_data("<p>Hello <b>HTML</b></p>")}},
            {"mimeType": "text/plain", "body": {"data": _gmail_data("Hello plain")}},
        ],
    }
    assert extract_mime_body(payload) == "Hello plain"

    html_only = {"mimeType": "text/html", "body": {"data": _gmail_data("<p>Hello<br>HTML</p>")}}
    assert extract_mime_body(html_only) == "Hello\nHTML"


def test_gmail_message_parser_extracts_headers_and_body() -> None:
    message = {
        "id": "msg-1",
        "threadId": "thread-1",
        "internalDate": "1776902400000",
        "payload": {
            "headers": [
                {"name": "Subject", "value": "Launch update"},
                {"name": "From", "value": "ada@example.com"},
            ],
            "mimeType": "text/plain",
            "body": {"data": _gmail_data("Ship it")},
        },
    }
    parsed = parse_gmail_message(message)
    assert parsed.title == "Launch update"
    assert parsed.headers["from"] == "ada@example.com"
    assert parsed.body == "Ship it"
    assert "From: ada@example.com" in format_email_note(parsed)


def test_gmail_mime_extraction_handles_nested_multipart() -> None:
    payload = {
        "mimeType": "multipart/mixed",
        "parts": [
            {
                "mimeType": "multipart/alternative",
                "parts": [
                    {"mimeType": "text/html", "body": {"data": _gmail_data("<p>Nested HTML</p>")}},
                    {"mimeType": "text/plain", "body": {"data": _gmail_data("Nested plain")}},
                ],
            }
        ],
    }
    assert extract_mime_body(payload) == "Nested plain"


def test_gmail_mime_extraction_skips_attachments_and_bad_data() -> None:
    payload = {
        "mimeType": "multipart/mixed",
        "parts": [
            {"mimeType": "text/plain", "filename": "notes.txt", "body": {"data": _gmail_data("attachment text")}},
            {"mimeType": "text/plain", "body": {"data": "not-valid-base64"}},
            {"mimeType": "text/html", "body": {"data": _gmail_data("<p>Safe body</p>")}},
        ],
    }
    assert extract_mime_body(payload) == "Safe body"


def test_calendar_meeting_note_template_preserves_metadata() -> None:
    meeting = CalendarMeeting(
        event_id="event-1",
        calendar_id="primary",
        title="Roadmap review",
        description="Discuss Q2 launch",
        start="2026-04-24T10:00:00Z",
        end="2026-04-24T11:00:00Z",
        attendees=["ada@example.com"],
        location="Meet",
        html_link="https://calendar.example.com/event-1",
        updated_at=None,
    )
    content = build_meeting_note_content(meeting)
    assert "## Meeting Metadata" in content
    assert "event-1" in content
    assert "ada@example.com" in content
    assert meeting_payload(meeting)["title"] == "Roadmap review"


@pytest.mark.asyncio
async def test_calendar_semantic_matching_respects_threshold(monkeypatch) -> None:
    meeting = CalendarMeeting(
        event_id="event-1",
        calendar_id="primary",
        title="Roadmap review",
        description="Discuss Q2 launch",
        start="2026-04-24T10:00:00Z",
        end="2026-04-24T11:00:00Z",
        attendees=["ada@example.com"],
        location=None,
        html_link=None,
        updated_at=None,
    )
    result = SemanticSearchResult(
        chunk_id=uuid4(),
        document_id=uuid4(),
        document_title="Unrelated roadmap note",
        content="Roadmap ideas",
        source_kind="note",
        source_type="note",
        chunk_index=0,
        created_at=datetime.now(timezone.utc),
        interaction_count=0,
        similarity_score=0.5,
        final_score=0.73,
    )

    class FakeSearchService:
        async def search(self, **kwargs):
            assert kwargs["filters"] == {"source_type": "note"}
            return SemanticSearchExecution(results=[result])

    monkeypatch.setattr(
        "app.services.integrations.calendar.get_semantic_search_service",
        lambda: FakeSearchService(),
    )
    connector = SimpleNamespace(workspace_id=uuid4(), user_id=uuid4())
    assert await find_best_note_match(None, connector=connector, meeting=meeting, threshold=0.74) is None


def test_calendar_matching_requires_score_and_overlap() -> None:
    meeting = CalendarMeeting(
        event_id="event-1",
        calendar_id="primary",
        title="Payments rollout planning",
        description="Discuss Stripe subscriptions and billing migration",
        start="2026-04-24T10:00:00Z",
        end="2026-04-24T11:00:00Z",
        attendees=[],
        location=None,
        html_link=None,
        updated_at=None,
    )
    unrelated = SemanticSearchResult(
        chunk_id=uuid4(),
        document_id=uuid4(),
        document_title="Roadmap review",
        content="General product planning",
        source_kind="note",
        source_type="note",
        chunk_index=0,
        created_at=datetime.now(timezone.utc),
        interaction_count=0,
        similarity_score=0.86,
        final_score=0.86,
    )
    related = SemanticSearchResult(
        chunk_id=uuid4(),
        document_id=uuid4(),
        document_title="Stripe billing migration",
        content="Payments rollout, subscriptions, and invoices",
        source_kind="note",
        source_type="note",
        chunk_index=0,
        created_at=datetime.now(timezone.utc),
        interaction_count=0,
        similarity_score=0.86,
        final_score=0.86,
    )
    assert not calendar_match_is_confident(meeting, unrelated, threshold=0.82)
    assert calendar_match_is_confident(meeting, related, threshold=0.82)


@pytest.mark.asyncio
async def test_expired_google_connector_without_refresh_requires_reauth() -> None:
    connector = SimpleNamespace(
        connector_type="google",
        refresh_token=None,
        access_token="",
        token_expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    with pytest.raises(ReauthorizationRequiredError):
        await refresh_connector_token(connector)
