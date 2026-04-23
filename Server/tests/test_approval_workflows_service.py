from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.database.models import (
    ApprovalWorkflowPriority,
    ApprovalWorkflowStatus,
    Note,
    NoteApprovalTransition,
    NoteCollaborationRole,
    User as DBUser,
    UserNotification,
    UserNotificationType,
)
from app.services import approval_workflows as service


class FakeResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def scalar(self):
        return self.value

    def scalars(self):
        return self

    def all(self):
        return self.value


class FakeDb:
    def __init__(self, *results):
        self._results = list(results)
        self.added = []
        self.execute = AsyncMock(side_effect=self._execute)
        self.flush = AsyncMock()

    def add(self, obj):
        self.added.append(obj)

    async def _execute(self, *_args, **_kwargs):
        if not self._results:
            raise AssertionError("Unexpected execute() call")
        return FakeResult(self._results.pop(0))


def _make_user(email: str, full_name: str, *, is_superuser: bool = False) -> DBUser:
    user = DBUser(email=email, full_name=full_name, is_superuser=1 if is_superuser else 0)
    user.id = uuid4()
    return user


def _make_note(
    *,
    owner: DBUser,
    approval_status: ApprovalWorkflowStatus = ApprovalWorkflowStatus.DRAFT,
    approval_priority: ApprovalWorkflowPriority = ApprovalWorkflowPriority.NORMAL,
) -> Note:
    note = Note(
        id=uuid4(),
        workspace_id=uuid4(),
        user_id=owner.id,
        title="Quarterly roadmap",
        content="Long-form content",
        approval_status=approval_status,
        approval_priority=approval_priority,
    )
    note.owner = owner
    note.approval_submitted_by = None
    note.approval_decided_by = None
    return note


def test_transition_map_allows_expected_transitions() -> None:
    transitions = service.ApprovalWorkflowService.TRANSITIONS

    assert transitions[ApprovalWorkflowStatus.DRAFT] == {ApprovalWorkflowStatus.SUBMITTED}
    assert transitions[ApprovalWorkflowStatus.SUBMITTED] == {
        ApprovalWorkflowStatus.APPROVED,
        ApprovalWorkflowStatus.REJECTED,
        ApprovalWorkflowStatus.NEEDS_CHANGES,
        ApprovalWorkflowStatus.CANCELLED,
    }
    assert transitions[ApprovalWorkflowStatus.NEEDS_CHANGES] == {
        ApprovalWorkflowStatus.SUBMITTED,
        ApprovalWorkflowStatus.CANCELLED,
    }
    assert transitions[ApprovalWorkflowStatus.APPROVED] == frozenset()
    assert transitions[ApprovalWorkflowStatus.REJECTED] == frozenset()


@pytest.mark.asyncio
async def test_submit_creates_transition_and_notification_fanout(monkeypatch: pytest.MonkeyPatch) -> None:
    actor = _make_user("editor@example.com", "Editor")
    owner = _make_user("owner@example.com", "Owner")
    reviewer = _make_user("reviewer@example.com", "Reviewer")
    note = _make_note(owner=owner)
    db = FakeDb()
    workflow_service = service.ApprovalWorkflowService(db)

    monkeypatch.setattr(workflow_service, "_load_note_for_update", AsyncMock(return_value=note))
    monkeypatch.setattr(workflow_service, "_load_note_detail", AsyncMock(return_value=note))
    monkeypatch.setattr(workflow_service, "_ensure_can_submit", AsyncMock())
    monkeypatch.setattr(
        workflow_service,
        "_submission_recipients",
        AsyncMock(return_value=[reviewer.id, owner.id, reviewer.id, actor.id]),
    )
    audit_mock = AsyncMock()
    monkeypatch.setattr(service, "log_audit_event", audit_mock)

    due_at = datetime.now(timezone.utc) + timedelta(days=3)
    created = await workflow_service.submit(
        note_id=note.id,
        actor=actor,
        expected_current_status=ApprovalWorkflowStatus.DRAFT,
        priority=ApprovalWorkflowPriority.HIGH,
        due_at=due_at,
        due_at_provided=True,
        comment="Please review this before Thursday.",
    )

    assert created is note
    assert note.approval_status == ApprovalWorkflowStatus.SUBMITTED
    assert note.approval_priority == ApprovalWorkflowPriority.HIGH
    assert note.approval_due_at == due_at
    assert note.approval_submitted_by_user_id == actor.id
    assert note.approval_submitted_at is not None

    transitions = [item for item in db.added if isinstance(item, NoteApprovalTransition)]
    notifications = [item for item in db.added if isinstance(item, UserNotification)]

    assert len(transitions) == 1
    assert transitions[0].from_status == ApprovalWorkflowStatus.DRAFT
    assert transitions[0].to_status == ApprovalWorkflowStatus.SUBMITTED
    assert len(notifications) == 2
    assert {notification.user_id for notification in notifications} == {reviewer.id, owner.id}
    assert all(notification.notification_type == UserNotificationType.APPROVAL_SUBMITTED for notification in notifications)
    audit_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_approve_rejects_stale_client_state(monkeypatch: pytest.MonkeyPatch) -> None:
    reviewer = _make_user("reviewer@example.com", "Reviewer")
    owner = _make_user("owner@example.com", "Owner")
    note = _make_note(owner=owner, approval_status=ApprovalWorkflowStatus.SUBMITTED)
    db = FakeDb()
    workflow_service = service.ApprovalWorkflowService(db)

    monkeypatch.setattr(workflow_service, "_load_note_for_update", AsyncMock(return_value=note))
    monkeypatch.setattr(workflow_service, "_ensure_can_review", AsyncMock())

    with pytest.raises(HTTPException) as exc_info:
        await workflow_service.approve(
            note_id=note.id,
            actor=reviewer,
            expected_current_status=ApprovalWorkflowStatus.DRAFT,
        )

    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_submit_rejects_due_dates_in_the_past(monkeypatch: pytest.MonkeyPatch) -> None:
    actor = _make_user("editor@example.com", "Editor")
    owner = _make_user("owner@example.com", "Owner")
    note = _make_note(owner=owner)
    db = FakeDb()
    workflow_service = service.ApprovalWorkflowService(db)

    monkeypatch.setattr(workflow_service, "_load_note_for_update", AsyncMock(return_value=note))
    monkeypatch.setattr(workflow_service, "_ensure_can_submit", AsyncMock())

    with pytest.raises(HTTPException) as exc_info:
        await workflow_service.submit(
            note_id=note.id,
            actor=actor,
            expected_current_status=ApprovalWorkflowStatus.DRAFT,
            due_at=datetime.now(timezone.utc) - timedelta(minutes=5),
            due_at_provided=True,
        )

    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_reject_requires_comment() -> None:
    reviewer = _make_user("reviewer@example.com", "Reviewer")
    db = FakeDb()
    workflow_service = service.ApprovalWorkflowService(db)

    with pytest.raises(HTTPException) as exc_info:
        await workflow_service.reject(
            note_id=uuid4(),
            actor=reviewer,
            expected_current_status=ApprovalWorkflowStatus.SUBMITTED,
            comment="",
        )

    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_request_changes_notifies_submitter_and_owner_without_duplicates(monkeypatch: pytest.MonkeyPatch) -> None:
    reviewer = _make_user("reviewer@example.com", "Reviewer")
    submitter = _make_user("submitter@example.com", "Submitter")
    note = _make_note(owner=submitter, approval_status=ApprovalWorkflowStatus.SUBMITTED)
    note.approval_submitted_by_user_id = submitter.id
    db = FakeDb()
    workflow_service = service.ApprovalWorkflowService(db)

    monkeypatch.setattr(workflow_service, "_load_note_for_update", AsyncMock(return_value=note))
    monkeypatch.setattr(workflow_service, "_load_note_detail", AsyncMock(return_value=note))
    monkeypatch.setattr(workflow_service, "_ensure_can_review", AsyncMock())
    monkeypatch.setattr(service, "log_audit_event", AsyncMock())

    created = await workflow_service.request_changes(
        note_id=note.id,
        actor=reviewer,
        expected_current_status=ApprovalWorkflowStatus.SUBMITTED,
        comment="Please tighten the risk analysis.",
    )

    assert created is note
    notifications = [item for item in db.added if isinstance(item, UserNotification)]
    assert len(notifications) == 1
    assert notifications[0].user_id == submitter.id
    assert notifications[0].notification_type == UserNotificationType.APPROVAL_NEEDS_CHANGES


@pytest.mark.asyncio
async def test_list_dashboard_items_applies_status_filter_and_permission_aware_actions(monkeypatch: pytest.MonkeyPatch) -> None:
    viewer = _make_user("viewer@example.com", "Viewer")
    owner = _make_user("owner@example.com", "Owner")
    submitted = _make_note(owner=owner, approval_status=ApprovalWorkflowStatus.SUBMITTED)
    submitted.approval_due_at = datetime.now(timezone.utc) + timedelta(days=1)
    submitted.approval_submitted_by = owner
    draft = _make_note(owner=owner, approval_status=ApprovalWorkflowStatus.DRAFT)
    db = FakeDb([submitted, draft])
    workflow_service = service.ApprovalWorkflowService(db)

    monkeypatch.setattr(service, "ensure_workspace_permission", AsyncMock(return_value=SimpleNamespace(role="admin")))
    monkeypatch.setattr(
        service,
        "get_workspace_permissions_for_user",
        AsyncMock(
            return_value={
                "workflows": {"view": True, "create": True, "manage": True},
                "notes": {"update": True},
            }
        ),
    )

    items = await workflow_service.list_dashboard_items(
        workspace_id=submitted.workspace_id,
        current_user=viewer,
        workflow_status=ApprovalWorkflowStatus.SUBMITTED,
        limit=50,
    )

    assert db.execute.await_count == 1
    executed_query = str(db.execute.await_args_list[0].args[0])
    assert "approval_status" in executed_query
    assert len(items) == 2
    assert items[0]["available_actions"]["approve"] is True
    assert items[0]["available_actions"]["submit"] is False
    assert items[1]["available_actions"]["submit"] is True


@pytest.mark.asyncio
async def test_list_dashboard_items_uses_per_note_update_access_for_submit_actions(monkeypatch: pytest.MonkeyPatch) -> None:
    viewer = _make_user("viewer@example.com", "Viewer")
    owner = _make_user("owner@example.com", "Owner")
    editable = _make_note(owner=owner, approval_status=ApprovalWorkflowStatus.DRAFT)
    blocked = _make_note(owner=owner, approval_status=ApprovalWorkflowStatus.DRAFT)
    db = FakeDb([editable, blocked], [(editable.id, NoteCollaborationRole.EDITOR)])
    workflow_service = service.ApprovalWorkflowService(db)

    monkeypatch.setattr(service, "ensure_workspace_permission", AsyncMock(return_value=SimpleNamespace(role="member")))
    monkeypatch.setattr(
        service,
        "get_workspace_permissions_for_user",
        AsyncMock(
            return_value={
                "workflows": {"view": True, "create": True, "manage": False},
                "notes": {"view": True, "update": False},
            }
        ),
    )

    items = await workflow_service.list_dashboard_items(
        workspace_id=editable.workspace_id,
        current_user=viewer,
        limit=50,
    )

    assert db.execute.await_count == 2
    assert items[0]["available_actions"]["submit"] is True
    assert items[1]["available_actions"]["submit"] is False


@pytest.mark.asyncio
async def test_get_dashboard_summary_returns_counts_and_overdue(monkeypatch: pytest.MonkeyPatch) -> None:
    viewer = _make_user("viewer@example.com", "Viewer")
    db = FakeDb(
        [
            (ApprovalWorkflowStatus.SUBMITTED, 2),
            (ApprovalWorkflowStatus.APPROVED, 1),
        ],
        1,
    )
    workflow_service = service.ApprovalWorkflowService(db)

    monkeypatch.setattr(service, "ensure_workspace_permission", AsyncMock(return_value=SimpleNamespace(role="admin")))
    monkeypatch.setattr(
        service,
        "get_workspace_permissions_for_user",
        AsyncMock(
            return_value={
                "workflows": {"view": True, "create": True, "manage": True},
                "notes": {"view": True, "update": True},
            }
        ),
    )

    summary = await workflow_service.get_dashboard_summary(
        workspace_id=uuid4(),
        current_user=viewer,
    )

    assert summary.total == 3
    assert summary.counts_by_status["submitted"] == 2
    assert summary.counts_by_status["approved"] == 1
    assert summary.counts_by_status["draft"] == 0
    assert summary.overdue == 1


@pytest.mark.asyncio
async def test_get_dashboard_summary_applies_note_visibility_filter_when_workspace_note_view_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    viewer = _make_user("viewer@example.com", "Viewer")
    db = FakeDb(
        [
            (ApprovalWorkflowStatus.SUBMITTED, 1),
        ],
        0,
    )
    workflow_service = service.ApprovalWorkflowService(db)

    monkeypatch.setattr(service, "ensure_workspace_permission", AsyncMock(return_value=SimpleNamespace(role="member")))
    monkeypatch.setattr(
        service,
        "get_workspace_permissions_for_user",
        AsyncMock(
            return_value={
                "workflows": {"view": True, "create": False, "manage": False},
                "notes": {"view": False, "update": False},
            }
        ),
    )

    summary = await workflow_service.get_dashboard_summary(
        workspace_id=uuid4(),
        current_user=viewer,
    )

    executed_query = str(db.execute.await_args_list[0].args[0])
    assert "note_collaborators" in executed_query
    assert "notes.user_id" in executed_query
    assert summary.total == 1


@pytest.mark.asyncio
async def test_serialize_item_for_user_uses_note_access_for_submit_actions(monkeypatch: pytest.MonkeyPatch) -> None:
    viewer = _make_user("viewer@example.com", "Viewer")
    owner = _make_user("owner@example.com", "Owner")
    note = _make_note(owner=owner)
    db = FakeDb()
    workflow_service = service.ApprovalWorkflowService(db)

    monkeypatch.setattr(
        service,
        "ensure_note_permission",
        AsyncMock(return_value=SimpleNamespace(can_update=False)),
    )
    monkeypatch.setattr(service, "ensure_workspace_permission", AsyncMock(return_value=SimpleNamespace(role="member")))
    monkeypatch.setattr(
        service,
        "get_workspace_permissions_for_user",
        AsyncMock(
            return_value={
                "workflows": {"view": True, "create": True, "manage": False},
                "notes": {"view": True, "update": True},
            }
        ),
    )

    serialized = await workflow_service.serialize_item_for_user(note=note, current_user=viewer)

    assert serialized["available_actions"]["submit"] is False
