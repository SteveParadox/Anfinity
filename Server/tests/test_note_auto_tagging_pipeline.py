from __future__ import annotations

from pathlib import Path
import re
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services.note_auto_tagging import classify_decay, classify_note_tags
from app.services.note_creation import (
    CAPTURE_PATHS,
    NoteCaptureIdempotencyConflict,
    NoteCapturedEvent,
    build_default_idempotency_key,
    ensure_idempotency_scope,
)


def test_capture_contract_has_all_note_creation_paths() -> None:
    assert CAPTURE_PATHS == {
        "api.notes",
        "automation.create_note",
        "integration.gmail",
        "integration.calendar",
        "integration.notion",
        "integration.shared",
    }


def test_note_captured_event_idempotency_key_is_stable() -> None:
    event = NoteCapturedEvent(
        workspace_id=uuid4(),
        user_id=uuid4(),
        title="Architecture decision",
        content="Use one convergence point for note capture.",
        capture_source="manual",
        capture_path="api.notes",
    )

    assert build_default_idempotency_key(event) == build_default_idempotency_key(event)
    assert "api.notes" in build_default_idempotency_key(event)


def test_capture_contract_rejects_unknown_paths() -> None:
    with pytest.raises(ValueError):
        NoteCapturedEvent(
            workspace_id=uuid4(),
            user_id=uuid4(),
            title="Bad path",
            content="This should not enter the shared pipeline.",
            capture_source="custom",
            capture_path="integration.some-provider",
        )


def test_idempotency_scope_rejects_cross_user_or_workspace_reuse() -> None:
    event = NoteCapturedEvent(
        workspace_id=uuid4(),
        user_id=uuid4(),
        title="Scoped",
        content="Idempotency keys must not leak across scopes.",
        capture_source="manual",
        capture_path="api.notes",
        idempotency_key="same-key",
    )
    existing = SimpleNamespace(workspace_id=uuid4(), user_id=event.user_id)

    with pytest.raises(NoteCaptureIdempotencyConflict):
        ensure_idempotency_scope(existing, event)


def test_classification_is_additive_and_normalized() -> None:
    result = classify_note_tags(
        "Launch plan",
        "Ship the #Roadmap update with Architecture and deployment notes.",
        existing_tags=["Manual Tag"],
    )

    assert result["mode"] == "additive"
    assert result["tags"][0] == "manual-tag"
    assert "roadmap" in result["tags"]
    assert result["confidence"] > 0


def test_decay_classification_uses_durable_and_volatile_signals() -> None:
    durable = classify_decay(None, "Architecture decision record", ["architecture"])
    volatile = classify_decay(None, "Daily follow up todo", ["meeting"])

    assert durable["decay_class"] == "durable"
    assert volatile["decay_class"] == "volatile"


def test_note_creation_is_centralized_to_service() -> None:
    server_root = Path(__file__).resolve().parents[1]
    offenders: list[str] = []
    for path in (server_root / "app").rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        if re.search(r"\bNote\(", text) and path.name not in {"models.py", "note_creation.py"}:
            offenders.append(str(path.relative_to(server_root)))

    assert offenders == []


def test_idempotency_path_locks_existing_capture_event() -> None:
    source = (Path(__file__).resolve().parents[1] / "app/services/note_creation.py").read_text(encoding="utf-8")

    assert ".with_for_update()" in source
    assert "IntegrityError" in source
    assert "idempotency_race" in source


def test_pipeline_gates_parallel_fanout_after_classification() -> None:
    source = (Path(__file__).resolve().parents[1] / "app/tasks/note_auto_tagging.py").read_text(encoding="utf-8")

    classification_position = source.index("classification_result = classify_note_tags_task.apply")
    failure_gate_position = source.index('classification_status not in {"success", "skipped"}')
    fanout_position = source.index(").apply_async()")

    assert classification_position < failure_gate_position < fanout_position


def test_pipeline_records_disabled_workspace_enrichment_steps() -> None:
    source = (Path(__file__).resolve().parents[1] / "app/tasks/note_auto_tagging.py").read_text(encoding="utf-8")

    assert "workspace_auto_tagging_disabled" in source
    assert "summary_generation_enabled" in source
    assert "connection_suggestions_enabled" in source
    assert "decay_classification_enabled" in source
    assert "disabled_enrichment_steps" in source


def test_connection_suggestions_wait_for_pipeline_embedding_step() -> None:
    source = (Path(__file__).resolve().parents[1] / "app/tasks/connection_suggestions.py").read_text(encoding="utf-8")

    assert "STEP_EMBEDDING" in source
    assert '"waiting_for": STEP_EMBEDDING' in source
    assert "Waiting for note embedding before connection suggestions" in source


def test_shared_integration_helper_uses_explicit_shared_capture_path() -> None:
    source = (Path(__file__).resolve().parents[1] / "app/services/integrations/sync_state.py").read_text(encoding="utf-8")

    assert 'capture_path or "integration.shared"' in source
    assert 'f"integration.{provider}"' not in source
