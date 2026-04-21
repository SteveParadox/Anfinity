import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.api import answers as answers_api  # noqa: E402
from app.api import query as query_api  # noqa: E402
from app.api import search as search_api  # noqa: E402
from app.services.feedback_handler import FeedbackHandler  # noqa: E402
from app.services.semantic_search import SemanticSearchResult, SemanticSearchService  # noqa: E402


class _FakeExecuteResult:
    def __init__(self, *, first=None, scalar=None, scalar_items=None, rows=None):
        self._first = first
        self._scalar = scalar
        self._scalar_items = scalar_items or []
        self._rows = rows or []

    def first(self):
        return self._first

    def scalar_one_or_none(self):
        return self._scalar

    def scalar_one(self):
        return self._scalar

    def all(self):
        return self._rows

    def fetchall(self):
        return self._rows

    def scalars(self):
        return SimpleNamespace(all=lambda: self._scalar_items)


class _SequencedAsyncDB:
    def __init__(self, results):
        self.results = list(results)
        self.execute_calls = []
        self.added = []
        self.info = {}
        self.commits = 0

    async def execute(self, statement, params=None):
        self.execute_calls.append((statement, params))
        if not self.results:
            raise AssertionError("Unexpected extra execute call")
        return self.results.pop(0)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1


def test_answers_workspace_access_reuses_shared_context_helper():
    workspace_id = uuid4()
    user = SimpleNamespace(id=uuid4())
    db = SimpleNamespace()
    expected_context = SimpleNamespace(workspace_id=workspace_id, role="member")

    async def _run():
        with patch.object(answers_api, "get_workspace_context", return_value=expected_context) as mocked:
            result = await answers_api._verify_workspace_access(workspace_id, user, db)
            assert result is expected_context
            mocked.assert_awaited_once_with(workspace_id, user, db)

    asyncio.run(_run())


def test_semantic_tag_hydration_skips_query_for_document_only_results():
    document_result = SemanticSearchResult(
        chunk_id=uuid4(),
        document_id=uuid4(),
        document_title="Doc",
        content="document content",
        source_kind="document",
        source_type="upload",
        chunk_index=0,
        created_at=datetime.now(timezone.utc),
        interaction_count=0,
        similarity_score=0.8,
    )

    class _NoExecuteDB:
        async def execute(self, *args, **kwargs):
            raise AssertionError("Document-only result hydration should not query notes")

    service = object.__new__(SemanticSearchService)
    asyncio.run(service._hydrate_result_tags(_NoExecuteDB(), [document_result]))


def test_feedback_handler_batches_chunk_weight_work_and_dedupes_sources():
    answer_id = uuid4()
    workspace_id = uuid4()
    existing_doc_id = str(uuid4())
    new_doc_id = str(uuid4())

    answer_row = SimpleNamespace(
        workspace_id=workspace_id,
        sources=[
            {"chunk_id": "chunk-1", "document_id": existing_doc_id},
            {"chunk_id": "chunk-1", "document_id": existing_doc_id},
            {"chunk_id": "chunk-2", "document_id": new_doc_id},
        ],
    )
    existing_weight = SimpleNamespace(
        chunk_id="chunk-1",
        document_id=existing_doc_id,
        credibility_score=1.4,
    )
    upsert_rows = [
        SimpleNamespace(
            chunk_id="chunk-1",
            document_id=existing_doc_id,
            credibility_score=1.54,
            accuracy_rate=0.75,
            positive_feedback_count=3,
            negative_feedback_count=1,
            total_uses=4,
        ),
        SimpleNamespace(
            chunk_id="chunk-2",
            document_id=new_doc_id,
            credibility_score=1.0,
            accuracy_rate=1.0,
            positive_feedback_count=1,
            negative_feedback_count=0,
            total_uses=1,
        ),
    ]
    db = _SequencedAsyncDB(
        [
            _FakeExecuteResult(first=answer_row),
            _FakeExecuteResult(scalar_items=[existing_weight]),
            _FakeExecuteResult(rows=upsert_rows),
            _FakeExecuteResult(),
        ]
    )

    async def _run():
        result = await FeedbackHandler().process_answer_feedback(
            answer_id=answer_id,
            feedback_status="verified",
            comment="looks good",
            user_id=uuid4(),
            db=db,
        )
        assert result["feedback_status"] == "verified"
        assert len(result["chunks_updated"]) == 2
        assert len(db.execute_calls) == 4
        assert db.commits == 1

    asyncio.run(_run())


def test_query_verify_answer_uses_minimal_round_trips_and_persists_feedback():
    answer_id = uuid4()
    workspace_id = uuid4()
    user_id = uuid4()
    db = _SequencedAsyncDB(
        [
            _FakeExecuteResult(scalar=workspace_id),
            _FakeExecuteResult(scalar=answer_id),
        ]
    )

    async def _fake_workspace_context(*args, **kwargs):
        return SimpleNamespace(workspace_id=workspace_id)

    async def _run():
        with patch.object(query_api, "get_workspace_context", side_effect=_fake_workspace_context):
            response = await query_api.verify_answer(
                answer_id=answer_id,
                verification=query_api.VerificationRequest(status="approved", comment="verified"),
                current_user=SimpleNamespace(id=user_id),
                db=db,
            )
        assert response.answer_id == str(answer_id)
        assert response.status == "approved"
        assert len(db.execute_calls) == 2
        assert db.commits == 1
        assert len(db.added) == 1
        assert db.added[0].rating == 5
        assert db.added[0].workspace_id == workspace_id

    asyncio.run(_run())


def test_search_click_logging_skips_interaction_insert_for_duplicate_click():
    workspace_id = uuid4()
    search_log_id = uuid4()
    chunk_id = uuid4()
    user_id = uuid4()
    db = _SequencedAsyncDB(
        [
            _FakeExecuteResult(first=(1, False)),
        ]
    )

    async def _allow_permission(*args, **kwargs):
        return None

    async def _run():
        with patch.object(search_api, "ensure_workspace_permission", side_effect=_allow_permission):
            response = await search_api.log_search_click(
                search_log_id=search_log_id,
                chunk_id=chunk_id,
                workspace_ctx=SimpleNamespace(workspace_id=workspace_id),
                current_user=SimpleNamespace(id=user_id),
                db=db,
            )
        assert response.clicked_count == 1
        assert len(db.execute_calls) == 1
        assert db.commits == 1

    asyncio.run(_run())


def test_search_click_logging_inserts_note_interaction_only_for_new_click():
    workspace_id = uuid4()
    search_log_id = uuid4()
    chunk_id = uuid4()
    user_id = uuid4()
    db = _SequencedAsyncDB(
        [
            _FakeExecuteResult(first=(2, True)),
            _FakeExecuteResult(),
        ]
    )

    async def _allow_permission(*args, **kwargs):
        return None

    async def _run():
        with patch.object(search_api, "ensure_workspace_permission", side_effect=_allow_permission):
            response = await search_api.log_search_click(
                search_log_id=search_log_id,
                chunk_id=chunk_id,
                workspace_ctx=SimpleNamespace(workspace_id=workspace_id),
                current_user=SimpleNamespace(id=user_id),
                db=db,
            )
        assert response.clicked_count == 2
        assert len(db.execute_calls) == 2
        assert db.commits == 1

    asyncio.run(_run())
