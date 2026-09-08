import asyncio
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException

from app.api import documents as documents_api
from app.api import workspaces as workspaces_api
from app.database.models import ChunkStatus, DocumentStatus


class _FakeExecuteResult:
    def __init__(self, *, rows=None, scalar=None):
        self._rows = rows or []
        self._scalar = scalar

    def all(self):
        return self._rows

    def scalar(self):
        return self._scalar


class _SequencedAsyncDB:
    def __init__(self, results):
        self.results = list(results)
        self.execute_calls = []

    async def execute(self, statement):
        self.execute_calls.append(statement)
        if not self.results:
            raise AssertionError("Unexpected extra execute call")
        return self.results.pop(0)


def test_workspace_stats_count_real_vectors_and_notes():
    workspace_id = uuid4()
    db = _SequencedAsyncDB(
        [
            _FakeExecuteResult(
                rows=[
                    (DocumentStatus.INDEXED, 3),
                    (DocumentStatus.PROCESSING, 1),
                    (DocumentStatus.FAILED, 2),
                ]
            ),
            _FakeExecuteResult(scalar=9),
            _FakeExecuteResult(scalar=4),
            _FakeExecuteResult(
                rows=[
                    (ChunkStatus.EMBEDDED, 7),
                    (ChunkStatus.PENDING, 2),
                ]
            ),
            _FakeExecuteResult(scalar=7),
        ]
    )

    async def _allow_permission(*args, **kwargs):
        return None

    async def _run():
        with patch.object(workspaces_api, "ensure_workspace_permission", side_effect=_allow_permission):
            return await workspaces_api.get_workspace_stats(
                workspace_id=workspace_id,
                current_user=SimpleNamespace(id=uuid4()),
                db=db,
            )

    response = asyncio.run(_run())

    assert response.documents["total"] == 6
    assert response.documents["indexed"] == 3
    assert response.documents["processing"] == 1
    assert response.documents["failed"] == 2
    assert response.notes == {"total": 9, "embedded": 4, "without_embeddings": 5}
    assert response.chunks["total"] == 9
    assert response.chunks["embedded"] == 7
    assert response.vectors == 7
    assert len(db.execute_calls) == 5


def test_upload_validation_accepts_markdown_text_plain():
    assert documents_api.validate_upload_file_type("notes.md", "text/plain") == ".md"


def test_upload_validation_rejects_html_uploads():
    try:
        documents_api.validate_upload_file_type("page.html", "text/html")
    except HTTPException as exc:
        assert exc.status_code == 415
        assert "Unsupported file extension" in exc.detail
    else:
        raise AssertionError("Expected HTML upload to be rejected")


def test_upload_validation_rejects_extension_mime_mismatch():
    try:
        documents_api.validate_upload_file_type("report.pdf", "text/plain")
    except HTTPException as exc:
        assert exc.status_code == 415
        assert "not supported for PDF uploads" in exc.detail
    else:
        raise AssertionError("Expected mismatched PDF MIME type to be rejected")
