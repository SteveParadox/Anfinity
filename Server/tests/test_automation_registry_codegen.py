from __future__ import annotations

import json
from pathlib import Path

from app.services.automation_registry_generated import (
    ACTION_EXECUTION,
    ACTION_TYPE_IDS,
    APPROVAL_PRIORITY_IDS,
    CONDITION_OPERATOR_IDS,
    HTTP_METHOD_IDS,
    NOTE_TYPE_IDS,
    TRIGGER_TYPE_IDS,
)


def test_generated_automation_registry_matches_shared_manifest() -> None:
    manifest_path = Path(__file__).resolve().parents[2] / "automation-registry.manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert list(TRIGGER_TYPE_IDS) == manifest["triggerTypes"]
    assert list(ACTION_TYPE_IDS) == manifest["actionTypes"]
    assert list(CONDITION_OPERATOR_IDS) == manifest["conditionOperators"]
    assert ACTION_EXECUTION == manifest["actionExecution"]
    assert list(NOTE_TYPE_IDS) == manifest["noteTypes"]
    assert list(APPROVAL_PRIORITY_IDS) == manifest["approvalPriorities"]
    assert list(HTTP_METHOD_IDS) == manifest["httpMethods"]
