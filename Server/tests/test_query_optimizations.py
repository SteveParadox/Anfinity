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
from app.services.search_log_storage import insert_search_log  # noqa: E402
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
        self.flushes = 0

    async def execute(self, statement, params=None):
        self.execute_calls.append((statement, params))
        if not self.results:
            raise AssertionError("Unexpected extra execute call")
        return self.results.pop(0)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1

    async def flush(self):
        self.flushes += 1


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


def test_answers_feedback_rejects_invalid_feedback_type():
    answer_id = uuid4()
    workspace_id = uuid4()
    user_id = uuid4()
    answer = SimpleNamespace(
        id=answer_id,
        workspace_id=workspace_id,
        query_id=uuid4(),
        sources=[{"chunk_id": "chunk-1", "document_id": str(uuid4())}],
    )
    query_row = SimpleNamespace(id=answer.query_id, query_text="what happened")
    db = _SequencedAsyncDB([_FakeExecuteResult(first=(answer, query_row))])
    db.info["search_feedback_table_available"] = True

    async def _allow_workspace(*args, **kwargs):
        return SimpleNamespace(workspace_id=workspace_id)

    async def _run():
        request = answers_api.AnswerFeedbackRequest(answer_id=answer_id, feedback_type="not_allowed")
        with patch.object(answers_api, "_verify_workspace_access", side_effect=_allow_workspace):
            try:
                await answers_api.submit_answer_feedback(
                    answer_id=answer_id,
                    request=request,
                    current_user=SimpleNamespace(id=user_id),
                    db=db,
                )
                assert False, "Expected HTTPException for invalid feedback_type"
            except Exception as exc:  # noqa: BLE001
                assert getattr(exc, "status_code", None) == 422

    asyncio.run(_run())


def test_answers_feedback_requires_reason_for_negative_feedback():
    answer_id = uuid4()
    workspace_id = uuid4()
    user_id = uuid4()
    answer = SimpleNamespace(
        id=answer_id,
        workspace_id=workspace_id,
        query_id=uuid4(),
        sources=[{"chunk_id": "chunk-1", "document_id": str(uuid4())}],
    )
    query_row = SimpleNamespace(id=answer.query_id, query_text="what happened")
    db = _SequencedAsyncDB([_FakeExecuteResult(first=(answer, query_row))])
    db.info["search_feedback_table_available"] = True

    async def _allow_workspace(*args, **kwargs):
        return SimpleNamespace(workspace_id=workspace_id)

    async def _run():
        request = answers_api.AnswerFeedbackRequest(
            answer_id=answer_id,
            feedback_type="wrong_result",
            target_kind="answer",
        )
        with patch.object(answers_api, "_verify_workspace_access", side_effect=_allow_workspace):
            try:
                await answers_api.submit_answer_feedback(
                    answer_id=answer_id,
                    request=request,
                    current_user=SimpleNamespace(id=user_id),
                    db=db,
                )
                assert False, "Expected HTTPException for missing reason_code"
            except Exception as exc:  # noqa: BLE001
                assert getattr(exc, "status_code", None) == 422
                assert "reason_code" in str(getattr(exc, "detail", ""))

    asyncio.run(_run())


def test_answers_feedback_updates_existing_record_without_duplicate_insert():
    answer_id = uuid4()
    workspace_id = uuid4()
    user_id = uuid4()
    existing_feedback_id = uuid4()

    answer = SimpleNamespace(
        id=answer_id,
        workspace_id=workspace_id,
        query_id=uuid4(),
        sources=[{"chunk_id": "chunk-1", "document_id": str(uuid4())}],
    )
    query_row = SimpleNamespace(id=answer.query_id, query_text="project alpha status")
    existing_feedback = SimpleNamespace(
        id=existing_feedback_id,
        workspace_id=workspace_id,
        user_id=user_id,
        search_log_id=None,
        query_id=None,
        answer_id=answer_id,
        context_key=f"answer:{answer_id}",
        scope_key=f"answer:{answer_id}",
        target_kind="answer",
        target_result_id=None,
        feedback_type="wrong_result",
        rating_value=-1,
        reason_code="result_unrelated",
        comment="old",
        query_text="old query",
        query_embedding_provider=None,
        query_embedding_model=None,
        result_ids=[],
        result_snapshot=[],
        answer_snapshot={},
        retrieval_diagnostics={},
        metadata_json={},
    )

    db = _SequencedAsyncDB(
        [
            _FakeExecuteResult(first=(answer, query_row)),
            _FakeExecuteResult(scalar=existing_feedback),
            _FakeExecuteResult(),
        ]
    )
    db.info["search_feedback_table_available"] = True
    db.info["search_logs_supports_extended_feedback_context"] = True

    class _FakeFeedbackHandler:
        @staticmethod
        def feedback_signal_from_type(feedback_type, rating_value=None):
            if feedback_type == "correct":
                return 1
            if feedback_type == "wrong_result":
                return -1
            return 0

        async def apply_feedback_delta(self, **kwargs):
            return [
                {
                    "chunk_id": "chunk-1",
                    "document_id": str(uuid4()),
                    "old_weight": 1.0,
                    "new_weight": 1.1,
                    "accuracy": 1.0,
                    "positive_count": 1,
                    "negative_count": 0,
                    "total_uses": 1,
                }
            ]

    async def _allow_workspace(*args, **kwargs):
        return SimpleNamespace(workspace_id=workspace_id)

    async def _run():
        request = answers_api.AnswerFeedbackRequest(
            answer_id=answer_id,
            feedback_type="correct",
            target_kind="answer",
            query_text="project alpha status",
        )
        with patch.object(answers_api, "_verify_workspace_access", side_effect=_allow_workspace), patch.object(
            answers_api, "get_feedback_handler", return_value=_FakeFeedbackHandler()
        ):
            response = await answers_api.submit_answer_feedback(
                answer_id=answer_id,
                request=request,
                current_user=SimpleNamespace(id=user_id),
                db=db,
            )
        assert response.updated_existing is True
        assert response.feedback_status == "verified"
        assert response.feedback_id == str(existing_feedback_id)
        assert len(db.added) == 1
        assert getattr(db.added[0], "query_text", None) == "project alpha status"
        assert db.flushes == 2
        assert db.commits == 1

    asyncio.run(_run())


def test_answers_result_feedback_applies_delta_to_target_snapshot_source():
    answer_id = uuid4()
    workspace_id = uuid4()
    user_id = uuid4()
    search_log_id = uuid4()
    document_id = str(uuid4())
    captured = {}

    answer = SimpleNamespace(
        id=answer_id,
        workspace_id=workspace_id,
        query_id=uuid4(),
        sources=[{"chunk_id": "answer-source", "document_id": str(uuid4())}],
    )
    query_row = SimpleNamespace(id=answer.query_id, query_text="where is the launch plan")
    search_log = SimpleNamespace(
        id=search_log_id,
        workspace_id=workspace_id,
        user_id=user_id,
        query_text="where is the launch plan",
        result_chunk_ids=["chunk-1"],
        result_snapshot=[{"chunk_id": "chunk-1", "document_id": document_id, "rank": 0, "final_score": 0.88}],
        retrieval_metadata={"embedding_provider": "test", "embedding_model": "fake"},
    )
    db = _SequencedAsyncDB(
        [
            _FakeExecuteResult(first=(answer, query_row)),
            _FakeExecuteResult(scalar=search_log),
            _FakeExecuteResult(scalar=None),
        ]
    )
    db.info["search_feedback_table_available"] = True
    db.info["search_logs_supports_extended_feedback_context"] = True

    class _FakeFeedbackHandler:
        @staticmethod
        def feedback_signal_from_type(feedback_type, rating_value=None):
            if feedback_type == "correct":
                return 1
            if feedback_type == "wrong_result":
                return -1
            return 0

        async def apply_feedback_delta(self, **kwargs):
            captured.update(kwargs)
            return [
                {
                    "chunk_id": "chunk-1",
                    "document_id": document_id,
                    "old_weight": 1.0,
                    "new_weight": 1.1,
                    "accuracy": 1.0,
                    "positive_count": 1,
                    "negative_count": 0,
                    "total_uses": 1,
                }
            ]

    async def _allow_workspace(*args, **kwargs):
        return SimpleNamespace(workspace_id=workspace_id)

    async def _run():
        request = answers_api.AnswerFeedbackRequest(
            answer_id=answer_id,
            feedback_type="correct",
            target_kind="result",
            target_result_id=" chunk-1 ",
            search_log_id=search_log_id,
            query_text="where is the launch plan",
        )
        with patch.object(answers_api, "_verify_workspace_access", side_effect=_allow_workspace), patch.object(
            answers_api, "get_feedback_handler", return_value=_FakeFeedbackHandler()
        ):
            response = await answers_api.submit_answer_feedback(
                answer_id=answer_id,
                request=request,
                current_user=SimpleNamespace(id=user_id),
                db=db,
            )

        assert response.feedback_status == "recorded"
        assert response.target_result_id == "chunk-1"
        assert response.chunks_updated[0].chunk_id == "chunk-1"
        assert captured["answer_sources"] == [{"chunk_id": "chunk-1", "document_id": document_id}]
        assert captured["previous_signal"] == 0
        assert captured["new_signal"] == 1

    asyncio.run(_run())


def test_answers_feedback_rejects_foreign_search_log_context():
    answer_id = uuid4()
    workspace_id = uuid4()
    user_id = uuid4()
    search_log_id = uuid4()
    answer = SimpleNamespace(
        id=answer_id,
        workspace_id=workspace_id,
        query_id=uuid4(),
        sources=[{"chunk_id": "chunk-1", "document_id": str(uuid4())}],
    )
    query_row = SimpleNamespace(id=answer.query_id, query_text="where is the roadmap")
    search_log = SimpleNamespace(
        id=search_log_id,
        workspace_id=workspace_id,
        user_id=uuid4(),
        query_text="where is the roadmap",
        result_chunk_ids=["chunk-1"],
        result_snapshot=[],
        retrieval_metadata={},
    )
    db = _SequencedAsyncDB(
        [
            _FakeExecuteResult(first=(answer, query_row)),
            _FakeExecuteResult(scalar=search_log),
        ]
    )
    db.info["search_feedback_table_available"] = True
    db.info["search_logs_supports_extended_feedback_context"] = True

    async def _allow_workspace(*args, **kwargs):
        return SimpleNamespace(workspace_id=workspace_id)

    async def _run():
        request = answers_api.AnswerFeedbackRequest(
            answer_id=answer_id,
            feedback_type="correct",
            target_kind="result",
            target_result_id="chunk-1",
            search_log_id=search_log_id,
            query_text="where is the roadmap",
        )
        with patch.object(answers_api, "_verify_workspace_access", side_effect=_allow_workspace):
            try:
                await answers_api.submit_answer_feedback(
                    answer_id=answer_id,
                    request=request,
                    current_user=SimpleNamespace(id=user_id),
                    db=db,
                )
                assert False, "Expected foreign search_log_id to be hidden"
            except Exception as exc:  # noqa: BLE001
                assert getattr(exc, "status_code", None) == 404

    asyncio.run(_run())


def test_answers_feedback_creates_fallback_search_log_when_session_is_missing():
    answer_id = uuid4()
    workspace_id = uuid4()
    user_id = uuid4()

    answer = SimpleNamespace(
        id=answer_id,
        workspace_id=workspace_id,
        query_id=uuid4(),
        sources=[{"chunk_id": "chunk-1", "document_id": str(uuid4())}],
    )
    query_row = SimpleNamespace(id=answer.query_id, query_text="which launch notes mention billing")
    db = _SequencedAsyncDB(
        [
            _FakeExecuteResult(first=(answer, query_row)),
            _FakeExecuteResult(scalar=None),
            _FakeExecuteResult(),
        ]
    )
    db.info["search_feedback_table_available"] = True
    db.info["search_logs_supports_extended_feedback_context"] = True

    class _FakeFeedbackHandler:
        @staticmethod
        def feedback_signal_from_type(feedback_type, rating_value=None):
            if feedback_type == "correct":
                return 1
            if feedback_type == "wrong_result":
                return -1
            return 0

        async def apply_feedback_delta(self, **kwargs):
            return []

    async def _allow_workspace(*args, **kwargs):
        return SimpleNamespace(workspace_id=workspace_id)

    async def _run():
        request = answers_api.AnswerFeedbackRequest(
            answer_id=answer_id,
            feedback_type="correct",
            target_kind="result",
            target_result_id="chunk-1",
            query_text="which launch notes mention billing",
            result_ids=["chunk-1"],
            result_snapshot=[{"chunk_id": "chunk-1", "document_id": "doc-1", "rank": 0, "final_score": 0.91}],
            retrieval_diagnostics={"top_k": 10, "strategy": "ui_snapshot"},
        )
        with patch.object(answers_api, "_verify_workspace_access", side_effect=_allow_workspace), patch.object(
            answers_api, "get_feedback_handler", return_value=_FakeFeedbackHandler()
        ):
            response = await answers_api.submit_answer_feedback(
                answer_id=answer_id,
                request=request,
                current_user=SimpleNamespace(id=user_id),
                db=db,
            )

        assert response.search_log_id is not None
        assert response.feedback_status == "recorded"
        assert len(db.added) == 2
        search_log, feedback_row = db.added
        assert getattr(search_log, "query_text", None) == "which launch notes mention billing"
        assert feedback_row.search_log_id == search_log.id
        assert response.search_log_id == str(search_log.id)
        assert db.flushes == 2
        assert db.commits == 1

    asyncio.run(_run())


def test_answers_feedback_rejects_result_target_not_in_search_log():
    answer_id = uuid4()
    workspace_id = uuid4()
    user_id = uuid4()
    search_log_id = uuid4()

    answer = SimpleNamespace(
        id=answer_id,
        workspace_id=workspace_id,
        query_id=uuid4(),
        sources=[{"chunk_id": "chunk-1", "document_id": str(uuid4())}],
    )
    query_row = SimpleNamespace(id=answer.query_id, query_text="where is the roadmap")
    search_log = SimpleNamespace(
        id=search_log_id,
        workspace_id=workspace_id,
        query_text="where is the roadmap",
        result_chunk_ids=["chunk-abc"],
        result_snapshot=[],
        retrieval_metadata={},
    )
    db = _SequencedAsyncDB(
        [
            _FakeExecuteResult(first=(answer, query_row)),
            _FakeExecuteResult(scalar=search_log),
        ]
    )
    db.info["search_feedback_table_available"] = True
    db.info["search_logs_supports_extended_feedback_context"] = True

    async def _allow_workspace(*args, **kwargs):
        return SimpleNamespace(workspace_id=workspace_id)

    async def _run():
        request = answers_api.AnswerFeedbackRequest(
            answer_id=answer_id,
            feedback_type="irrelevant",
            target_kind="result",
            target_result_id="chunk-missing",
            search_log_id=search_log_id,
            query_text="where is the roadmap",
        )
        with patch.object(answers_api, "_verify_workspace_access", side_effect=_allow_workspace):
            try:
                await answers_api.submit_answer_feedback(
                    answer_id=answer_id,
                    request=request,
                    current_user=SimpleNamespace(id=user_id),
                    db=db,
                )
                assert False, "Expected HTTPException for invalid target_result_id"
            except Exception as exc:  # noqa: BLE001
                assert getattr(exc, "status_code", None) == 422

    asyncio.run(_run())


def test_insert_search_log_falls_back_to_legacy_search_logs_shape():
    db = _SequencedAsyncDB([_FakeExecuteResult()])
    db.info["search_logs_supports_extended_feedback_context"] = False

    async def _run():
        search_log = await insert_search_log(
            db,
            workspace_id=uuid4(),
            user_id=uuid4(),
            query_text="dyslexia support",
            result_chunk_ids=["chunk-1", "chunk-2"],
            result_count=2,
            result_snapshot=[{"chunk_id": "chunk-1", "rank": 0}],
            clicked_count=0,
            clicked_chunk_ids=[],
            search_duration_ms=18,
            retrieval_metadata={"strategy": "legacy-safe"},
            created_at=datetime.now(timezone.utc),
        )

        assert str(search_log.id)
        assert search_log.result_snapshot == [{"chunk_id": "chunk-1", "rank": 0}]
        assert search_log.retrieval_metadata == {"strategy": "legacy-safe"}
        assert len(db.added) == 0
        assert len(db.execute_calls) == 1
        statement, params = db.execute_calls[0]
        sql = str(statement)
        assert "INSERT INTO search_logs" in sql
        assert "result_snapshot" not in sql
        assert params["query_text"] == "dyslexia support"

    asyncio.run(_run())


def test_answers_feedback_returns_clear_error_when_feedback_table_is_missing():
    answer_id = uuid4()
    workspace_id = uuid4()
    user_id = uuid4()
    answer = SimpleNamespace(
        id=answer_id,
        workspace_id=workspace_id,
        query_id=uuid4(),
        sources=[],
    )
    query_row = SimpleNamespace(id=answer.query_id, query_text="project update")
    db = _SequencedAsyncDB([_FakeExecuteResult(first=(answer, query_row))])
    db.info["search_feedback_table_available"] = False

    async def _allow_workspace(*args, **kwargs):
        return SimpleNamespace(workspace_id=workspace_id)

    async def _run():
        request = answers_api.AnswerFeedbackRequest(
            answer_id=answer_id,
            feedback_type="correct",
            target_kind="answer",
            query_text="project update",
        )
        with patch.object(answers_api, "_verify_workspace_access", side_effect=_allow_workspace):
            try:
                await answers_api.submit_answer_feedback(
                    answer_id=answer_id,
                    request=request,
                    current_user=SimpleNamespace(id=user_id),
                    db=db,
                )
            except Exception as exc:
                assert getattr(exc, "status_code", None) == 503
                assert "database migration" in str(getattr(exc, "detail", "")).lower()
            else:
                raise AssertionError("Expected feedback submit to fail clearly when schema is missing")

    asyncio.run(_run())
