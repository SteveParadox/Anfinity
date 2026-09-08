from types import SimpleNamespace
from unittest import mock
from uuid import uuid4

import pytest

from app.tasks import dlq


class _FakeQuery:
    def __init__(self, item):
        self.item = item

    def filter(self, *_args, **_kwargs):
        return self

    def first(self):
        return self.item


class _FakeSession:
    def __init__(self, item):
        self.item = item
        self.commit_count = 0
        self.rollback_count = 0
        self.closed = False

    def query(self, _model):
        return _FakeQuery(self.item)

    def commit(self):
        self.commit_count += 1

    def rollback(self):
        self.rollback_count += 1

    def close(self):
        self.closed = True


def test_retry_item_requeues_known_task_with_document_fallback_args():
    document_id = uuid4()
    item = SimpleNamespace(
        id=uuid4(),
        status=dlq.DLQStatus.PENDING,
        task_name="process_document",
        args={},
        kwargs={},
        document_id=document_id,
        workspace_id=uuid4(),
        retry_count=0,
        reviewed_at=None,
        admin_notes=None,
    )
    session = _FakeSession(item)

    with (
        mock.patch.object(dlq, "SyncSessionLocal", return_value=session),
        mock.patch.object(dlq.celery_app, "send_task", return_value=SimpleNamespace(id="queued-task-1")) as send_task,
    ):
        result = dlq.DLQManager.retry_item(item.id)

    send_task.assert_called_once_with(
        "app.tasks.worker.process_document",
        args=[str(document_id)],
        kwargs={},
    )
    assert result["status"] == "retry_enqueued"
    assert result["task_id"] == "queued-task-1"
    assert result["retry_count"] == 1
    assert item.status == dlq.DLQStatus.IN_RETRY
    assert item.retry_count == 1
    assert "queued-task-1" in item.admin_notes
    assert session.commit_count == 1
    assert session.closed is True


def test_retry_item_blocks_terminal_string_statuses_without_queueing():
    item = SimpleNamespace(
        id=uuid4(),
        status="resolved",
        task_name="process_document",
        args=[str(uuid4())],
        kwargs={},
        document_id=uuid4(),
        workspace_id=uuid4(),
        retry_count=1,
        reviewed_at=None,
        admin_notes=None,
    )
    session = _FakeSession(item)

    with (
        mock.patch.object(dlq, "SyncSessionLocal", return_value=session),
        mock.patch.object(dlq.celery_app, "send_task") as send_task,
        pytest.raises(ValueError, match="resolved"),
    ):
        dlq.DLQManager.retry_item(item.id)

    send_task.assert_not_called()
    assert session.rollback_count == 1
    assert session.closed is True
