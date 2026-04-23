"""Authoritative backend registry contract for automation trigger/action IDs."""

from __future__ import annotations

from typing import Any, Dict, List

from app.services.automation_registry_generated import (
    ACTION_EXECUTION,
    ACTION_TYPE_IDS,
    APPROVAL_PRIORITY_IDS,
    CONDITION_OPERATOR_IDS,
    HTTP_METHOD_IDS,
    NOTE_TYPE_IDS,
    TRIGGER_TYPE_IDS,
)

SUPPORTED_TRIGGER_TYPES = frozenset(TRIGGER_TYPE_IDS)
SUPPORTED_ACTION_TYPES = frozenset(ACTION_TYPE_IDS)
SUPPORTED_CONDITION_OPERATORS = frozenset(CONDITION_OPERATOR_IDS)
HTTP_ACTION_TYPES = frozenset(action_type for action_type, mode in ACTION_EXECUTION.items() if mode == "http")
BACKEND_ACTION_TYPES = SUPPORTED_ACTION_TYPES - HTTP_ACTION_TYPES

NOTE_TYPES = frozenset(NOTE_TYPE_IDS)
APPROVAL_PRIORITIES = frozenset(APPROVAL_PRIORITY_IDS)
HTTP_METHODS = frozenset(HTTP_METHOD_IDS)


def assert_registry_integrity() -> None:
    if len(TRIGGER_TYPE_IDS) != 10 or len(SUPPORTED_TRIGGER_TYPES) != len(TRIGGER_TYPE_IDS):
        raise RuntimeError("Automation trigger registry must define exactly 10 unique trigger types")
    if len(ACTION_TYPE_IDS) != 14 or len(SUPPORTED_ACTION_TYPES) != len(ACTION_TYPE_IDS):
        raise RuntimeError("Automation action registry must define exactly 14 unique action types")
    if len(CONDITION_OPERATOR_IDS) != 8 or len(SUPPORTED_CONDITION_OPERATORS) != len(CONDITION_OPERATOR_IDS):
        raise RuntimeError("Automation condition registry must define exactly 8 unique operators")
    if set(ACTION_EXECUTION) != SUPPORTED_ACTION_TYPES:
        raise RuntimeError("Automation action execution map must exactly match action types")
    if any(mode not in {"backend", "http"} for mode in ACTION_EXECUTION.values()):
        raise RuntimeError("Automation action execution modes must be backend or http")
    if not BACKEND_ACTION_TYPES.isdisjoint(HTTP_ACTION_TYPES):
        raise RuntimeError("Automation action execution modes must not overlap")


def automation_registry_payload() -> Dict[str, List[Any]]:
    assert_registry_integrity()
    return {
        "trigger_types": list(TRIGGER_TYPE_IDS),
        "action_types": list(ACTION_TYPE_IDS),
        "condition_operators": list(CONDITION_OPERATOR_IDS),
        "backend_action_types": sorted(BACKEND_ACTION_TYPES),
        "http_action_types": sorted(HTTP_ACTION_TYPES),
    }


assert_registry_integrity()
