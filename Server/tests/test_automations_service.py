from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.services.automation_registry import automation_registry_payload
from app.services.automations import (
    SUPPORTED_ACTION_TYPES,
    SUPPORTED_CONDITION_OPERATORS,
    SUPPORTED_TRIGGER_TYPES,
    validate_automation_config,
)


def test_automation_registry_contract_counts() -> None:
    assert len(SUPPORTED_TRIGGER_TYPES) == 11
    assert len(SUPPORTED_ACTION_TYPES) == 14
    assert SUPPORTED_CONDITION_OPERATORS == {
        "equals",
        "not_equals",
        "contains",
        "not_contains",
        "matches_regex",
        "greater_than",
        "less_than",
        "exists",
    }
    registry = automation_registry_payload()
    assert len(registry["trigger_types"]) == 11
    assert "competitive_intelligence.urgent_finding" in registry["trigger_types"]
    assert len(registry["action_types"]) == 14
    assert set(registry["backend_action_types"]).isdisjoint(set(registry["http_action_types"]))


def test_validate_automation_config_accepts_supported_config() -> None:
    validate_automation_config(
        trigger_type="note.created",
        conditions=[
            {"path": "note.title", "operator": "contains", "value": "Launch"},
            {"any": [{"path": "payload.metadata.source", "operator": "equals", "value": "manual"}]},
        ],
        actions=[
            {
                "type": "send_notification",
                "config": {
                    "recipientUserIds": ["user-1"],
                    "title": "{{note.title}}",
                    "message": "Created in {{workspace.id}}",
                },
            }
        ],
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"trigger_type": "missing.trigger", "conditions": [], "actions": [{"type": "send_notification", "config": {}}]},
        {"trigger_type": "note.created", "conditions": [{"path": "bad..path", "operator": "equals"}], "actions": [{"type": "send_notification", "config": {}}]},
        {"trigger_type": "note.created", "conditions": [{"path": "note.__proto__.polluted", "operator": "equals"}], "actions": [{"type": "send_notification", "config": {}}]},
        {"trigger_type": "note.created", "conditions": [{"path": "note.title", "operator": "around"}], "actions": [{"type": "send_notification", "config": {}}]},
        {"trigger_type": "note.created", "conditions": [], "actions": [{"type": "missing_action", "config": {}}]},
        {"trigger_type": "note.created", "conditions": [], "actions": [{"type": "send_notification", "config": {"title": "Missing recipients", "message": "Nope"}}]},
        {"trigger_type": "note.created", "conditions": [], "actions": [{"type": "call_webhook", "config": {"url": "ftp://example.com/hook", "method": "POST"}}]},
        {"trigger_type": "note.created", "conditions": [], "actions": [{"type": "set_note_type", "config": {"noteId": "{{note.id}}", "noteType": "bogus"}}]},
    ],
)
def test_validate_automation_config_rejects_malformed_config(payload: dict) -> None:
    with pytest.raises(HTTPException):
        validate_automation_config(
            trigger_type=payload["trigger_type"],
            conditions=payload["conditions"],
            actions=payload["actions"],
        )
