from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import AsyncMock

import pytest

from app.api import approval_workflows as api
from app.database.models import ApprovalWorkflowPriority, ApprovalWorkflowStatus


class FakeDb:
    def __init__(self) -> None:
        self.commit = AsyncMock()


@pytest.mark.asyncio
async def test_list_approval_workflows_forwards_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    current_user = SimpleNamespace(id=uuid4())
    workspace_id = uuid4()
    list_mock = AsyncMock(
        return_value=[
            {
                "note_id": str(uuid4()),
                "workspace_id": str(workspace_id),
                "title": "Release checklist",
                "summary": "Needs approval",
                "note_type": "note",
                "author_user_id": str(uuid4()),
                "approval_status": "submitted",
                "approval_priority": "high",
                "approval_due_at": None,
                "approval_submitted_at": None,
                "approval_submitted_by_user_id": None,
                "approval_decided_at": None,
                "approval_decided_by_user_id": None,
                "is_overdue": False,
                "available_actions": {
                    "submit": False,
                    "resubmit": False,
                    "cancel": False,
                    "approve": True,
                    "reject": True,
                    "request_changes": True,
                },
                "author": None,
                "submitted_by": None,
                "decided_by": None,
            }
        ]
    )
    fake_service = SimpleNamespace(list_dashboard_items=list_mock)
    monkeypatch.setattr(api, "_service", lambda _db: fake_service)

    response = await api.list_approval_workflows(
        workspace_id=workspace_id,
        status_filter=ApprovalWorkflowStatus.SUBMITTED,
        limit=25,
        current_user=current_user,
        db=object(),
    )

    assert len(response) == 1
    list_mock.assert_awaited_once_with(
        workspace_id=workspace_id,
        current_user=current_user,
        workflow_status=ApprovalWorkflowStatus.SUBMITTED,
        limit=25,
    )


@pytest.mark.asyncio
async def test_submit_note_for_approval_endpoint_serializes_service_response(monkeypatch: pytest.MonkeyPatch) -> None:
    note_id = uuid4()
    current_user = SimpleNamespace(id=uuid4())
    note = SimpleNamespace(id=note_id)
    db = FakeDb()
    submit_mock = AsyncMock(return_value=note)
    serialize_mock = AsyncMock(
        return_value={
            "note_id": str(note_id),
            "workspace_id": str(uuid4()),
            "title": "Policy memo",
            "summary": "Awaiting approval",
            "note_type": "note",
            "author_user_id": str(uuid4()),
            "approval_status": "submitted",
            "approval_priority": "critical",
            "approval_due_at": datetime.now(timezone.utc).isoformat(),
            "approval_submitted_at": datetime.now(timezone.utc).isoformat(),
            "approval_submitted_by_user_id": str(current_user.id),
            "approval_decided_at": None,
            "approval_decided_by_user_id": None,
            "is_overdue": False,
            "available_actions": {
                "submit": False,
                "resubmit": False,
                "cancel": True,
                "approve": False,
                "reject": False,
                "request_changes": False,
            },
            "author": None,
            "submitted_by": None,
            "decided_by": None,
        }
    )
    fake_service = SimpleNamespace(
        submit=submit_mock,
        serialize_item_for_user=serialize_mock,
    )
    monkeypatch.setattr(api, "_service", lambda _db: fake_service)

    payload = api.ApprovalSubmissionRequest(
        current_status=ApprovalWorkflowStatus.DRAFT,
        priority=ApprovalWorkflowPriority.CRITICAL,
        comment="Ship blocker",
    )

    response = await api.submit_note_for_approval(
        note_id=note_id,
        payload=payload,
        current_user=current_user,
        db=db,
    )

    submit_mock.assert_awaited_once_with(
        note_id=note_id,
        actor=current_user,
        expected_current_status=ApprovalWorkflowStatus.DRAFT,
        priority=ApprovalWorkflowPriority.CRITICAL,
        due_at=None,
        due_at_provided=False,
        comment="Ship blocker",
    )
    db.commit.assert_awaited_once()
    serialize_mock.assert_awaited_once_with(note=note, current_user=current_user)
    assert response.approval_status == ApprovalWorkflowStatus.SUBMITTED
    assert response.approval_priority == ApprovalWorkflowPriority.CRITICAL


@pytest.mark.asyncio
async def test_submit_note_for_approval_endpoint_marks_due_date_as_provided_when_null_is_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    note_id = uuid4()
    current_user = SimpleNamespace(id=uuid4())
    note = SimpleNamespace(id=note_id)
    db = FakeDb()
    submit_mock = AsyncMock(return_value=note)
    serialize_mock = AsyncMock(
        return_value={
            "note_id": str(note_id),
            "workspace_id": str(uuid4()),
            "title": "Policy memo",
            "summary": None,
            "note_type": "note",
            "author_user_id": str(uuid4()),
            "approval_status": "submitted",
            "approval_priority": "normal",
            "approval_due_at": None,
            "approval_submitted_at": None,
            "approval_submitted_by_user_id": str(current_user.id),
            "approval_decided_at": None,
            "approval_decided_by_user_id": None,
            "is_overdue": False,
            "available_actions": {
                "submit": False,
                "resubmit": False,
                "cancel": True,
                "approve": False,
                "reject": False,
                "request_changes": False,
            },
            "author": None,
            "submitted_by": None,
            "decided_by": None,
        }
    )
    fake_service = SimpleNamespace(
        submit=submit_mock,
        serialize_item_for_user=serialize_mock,
    )
    monkeypatch.setattr(api, "_service", lambda _db: fake_service)

    payload = api.ApprovalSubmissionRequest(
        current_status=ApprovalWorkflowStatus.DRAFT,
        due_at=None,
    )

    await api.submit_note_for_approval(
        note_id=note_id,
        payload=payload,
        current_user=current_user,
        db=db,
    )

    submit_mock.assert_awaited_once_with(
        note_id=note_id,
        actor=current_user,
        expected_current_status=ApprovalWorkflowStatus.DRAFT,
        priority=None,
        due_at=None,
        due_at_provided=True,
        comment=None,
    )
