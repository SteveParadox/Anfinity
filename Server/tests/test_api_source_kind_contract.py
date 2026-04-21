import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI
import httpx


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.api import query as query_api  # noqa: E402
from app.api import retrieval as retrieval_api  # noqa: E402
from app.api import search as search_api  # noqa: E402
from app.services.answer_generator import Citation, GeneratedAnswer  # noqa: E402
from app.services.semantic_search import SemanticSearchExecution, SemanticSearchResult  # noqa: E402


class _FakeAsyncDB:
    def __init__(self):
        self.added = []

    async def execute(self, *args, **kwargs):  # pragma: no cover - should not be used in these tests
        raise AssertionError("Unexpected DB execute call")

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        return None

    async def refresh(self, obj):
        if getattr(obj, "id", None) is None:
            obj.id = uuid4()


def _build_search_app(fake_db):
    app = FastAPI()
    app.include_router(search_api.router)
    app.dependency_overrides[search_api.get_current_active_user] = lambda: SimpleNamespace(id=uuid4())
    app.dependency_overrides[search_api.get_workspace_context] = lambda: SimpleNamespace(workspace_id=uuid4())

    async def _fake_db_dep():
        return fake_db

    app.dependency_overrides[search_api.get_db] = _fake_db_dep
    return app


def _build_query_app(fake_db):
    app = FastAPI()
    app.include_router(query_api.router)
    app.dependency_overrides[query_api.get_current_active_user] = lambda: SimpleNamespace(id=uuid4())

    async def _fake_db_dep():
        return fake_db

    app.dependency_overrides[query_api.get_db] = _fake_db_dep
    return app


def _build_retrieval_app(fake_db):
    app = FastAPI()
    app.include_router(retrieval_api.router)
    app.dependency_overrides[retrieval_api.get_current_user] = lambda: SimpleNamespace(id=uuid4())
    app.dependency_overrides[retrieval_api.get_db] = lambda: fake_db
    return app


async def _request(app: FastAPI, method: str, path: str, **kwargs):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.request(method, path, **kwargs)


def test_search_semantic_http_response_includes_source_kind():
    fake_db = _FakeAsyncDB()
    workspace_id = uuid4()
    note_id = uuid4()
    document_chunk_id = uuid4()
    document_id = uuid4()

    class _FakeSearchService:
        async def search(self, **kwargs):
            return SemanticSearchExecution(
                results=[
                    SemanticSearchResult(
                        chunk_id=note_id,
                        document_id=note_id,
                        document_title="Reading Note",
                        content="Dyslexia support note",
                        source_kind="note",
                        source_type="note",
                        chunk_index=0,
                            created_at=datetime.now(timezone.utc),
                        interaction_count=1,
                        similarity_score=0.83,
                        final_score=0.83,
                    ),
                    SemanticSearchResult(
                        chunk_id=document_chunk_id,
                        document_id=document_id,
                        document_title="Clinical Handbook",
                        content="Dyslexia assessment chapter",
                        source_kind="document",
                        source_type="upload",
                        chunk_index=2,
                        token_count=31,
                        context_before="Prior section summary",
                        context_after="Next section summary",
                        metadata={
                            "related_documents": [
                                {"document_id": "doc-2", "title": "Assessment Appendix"}
                            ]
                        },
                        created_at=datetime.now(timezone.utc),
                        interaction_count=0,
                        similarity_score=0.79,
                        final_score=0.79,
                    ),
                ],
                search_log_id="log-1",
                strategy="postgresql_hybrid_plus_retriever",
            )

    app = _build_search_app(fake_db)
    with patch.object(search_api, "ensure_workspace_permission") as ensure_permission, patch.object(
        search_api,
        "get_semantic_search_service",
        return_value=_FakeSearchService(),
    ):
        ensure_permission.return_value = None
        response = asyncio.run(
            _request(
                app,
                "POST",
                "/search/semantic",
                json={"query": "dyslexia support", "limit": 5},
            )
        )

    assert response.status_code == 200
    payload = response.json()
    assert [item["source_kind"] for item in payload["results"]] == ["note", "document"]
    assert payload["results"][1]["token_count"] == 31
    assert payload["results"][1]["context_before"] == "Prior section summary"
    assert payload["results"][1]["context_after"] == "Next section summary"
    assert payload["results"][1]["metadata"]["related_documents"][0]["title"] == "Assessment Appendix"


def test_query_http_response_includes_source_kind_on_sources():
    fake_db = _FakeAsyncDB()
    workspace_id = uuid4()
    document_chunk_id = uuid4()
    document_id = uuid4()
    note_chunk_id = uuid4()
    note_id = uuid4()
    captured_audit = {}

    class _FakeRetriever:
        def retrieve(self, **kwargs):
            return SimpleNamespace(
                chunks=[
                    SimpleNamespace(
                        chunk_id=document_chunk_id,
                        document_id=document_id,
                        similarity=0.81,
                        text="Clinical handbook section on dyslexia intervention.",
                        source_type="upload",
                        chunk_index=0,
                        document_title="Clinical Handbook",
                        token_count=20,
                        context_before=None,
                        context_after=None,
                        metadata={},
                    )
                ],
                confidence=0.62,
                avg_similarity=0.81,
                unique_documents=1,
            )

    class _FakeSemanticService:
        async def search(self, **kwargs):
            return SemanticSearchExecution(
                results=[
                        SemanticSearchResult(
                            chunk_id=note_chunk_id,
                            document_id=note_id,
                            document_title="Education Note",
                            content="Your note mentions dyslexia support plans.",
                            source_kind="note",
                            source_type="note",
                            chunk_index=0,
                            created_at=datetime.now(timezone.utc),
                            interaction_count=1,
                            similarity_score=0.74,
                            final_score=0.74,
                        )
                ],
                strategy="postgresql_hybrid",
            )

    class _FakeAnswerGenerator:
        async def generate(self, **kwargs):
            return GeneratedAnswer(
                answer_text="Your sources discuss dyslexia support in both a clinical handbook and an education note.",
                citations=[
                    Citation(
                        chunk_id=str(document_chunk_id),
                        document_id=str(document_id),
                        document_title="Clinical Handbook",
                        chunk_index=0,
                        similarity=0.81,
                        text_snippet="Clinical handbook section on dyslexia intervention.",
                    ),
                    Citation(
                        chunk_id=str(note_chunk_id),
                        document_id=str(note_id),
                        document_title="Education Note",
                        chunk_index=0,
                        similarity=0.74,
                        text_snippet="Your note mentions dyslexia support plans.",
                    ),
                ],
                confidence_score=78.0,
                model_used="test-model",
                tokens_used=42,
                generation_time_ms=10.0,
                average_similarity=0.775,
                unique_documents=2,
                metadata={},
                top_k=2,
            )

    class _FakeAuditLogger:
        def __init__(self, db, user_id):
            self.db = db
            self.user_id = user_id

        async def log(self, **kwargs):
            captured_audit.update(kwargs.get("metadata", {}))
            return None

    async def _fake_hydrate_sources(db, rag_chunks, include_sources):
        del db, include_sources
        return [
            query_api.Source(
                chunk_id=str(document_chunk_id),
                document_id=str(document_id),
                document_title="Clinical Handbook",
                source_kind="document",
                text="Clinical handbook section on dyslexia intervention.",
                similarity=0.81,
            )
        ], ["Clinical handbook section on dyslexia intervention."]

    app = _build_query_app(fake_db)
    with patch.object(query_api, "get_workspace_context") as get_workspace_context, patch.object(
        query_api, "get_rag_retriever", return_value=_FakeRetriever()
    ), patch.object(
        query_api, "get_semantic_search_service", return_value=_FakeSemanticService()
    ), patch.object(
        query_api, "get_answer_generator", return_value=_FakeAnswerGenerator()
    ), patch.object(
        query_api, "_hydrate_sources", side_effect=_fake_hydrate_sources
    ), patch.object(
        query_api, "AuditLogger", _FakeAuditLogger
    ):
        get_workspace_context.return_value = SimpleNamespace(workspace_id=workspace_id)
        response = asyncio.run(
            _request(
                app,
                "POST",
                "/query",
                json={
                    "workspace_id": str(workspace_id),
                    "query": "medical book on dyslexia",
                    "top_k": 2,
                    "include_sources": True,
                    "model": "test-model",
                },
            )
        )

    assert response.status_code == 200
    payload = response.json()
    assert {item["source_kind"] for item in payload["sources"]} == {"document", "note"}
    assert captured_audit["reliable_evidence"] is True
    assert captured_audit["candidate_chunks_considered"] == 2
    assert captured_audit["candidate_source_kinds"] == {"document": 1, "note": 1}
    assert captured_audit["avg_domain_alignment"] > 0.0
    assert captured_audit["avg_evidence_score"] > 0.0
    assert captured_audit["top_evidence_score"] > 0.0
    assert captured_audit["off_topic_ratio"] == 0.0
    assert captured_audit["sources_returned"] == 2


def test_query_generation_failure_drops_confidence_and_reports_sources_honestly():
    fake_db = _FakeAsyncDB()
    workspace_id = uuid4()
    document_chunk_id = uuid4()
    document_id = uuid4()
    note_chunk_id = uuid4()
    note_id = uuid4()
    captured_audit = {}

    class _FakeRetriever:
        def retrieve(self, **kwargs):
            return SimpleNamespace(
                chunks=[
                    SimpleNamespace(
                        chunk_id=document_chunk_id,
                        document_id=document_id,
                        similarity=0.79,
                        text="Clinical handbook section on dyslexia assessment and intervention.",
                        source_type="upload",
                        chunk_index=0,
                        document_title="Clinical Handbook",
                        token_count=20,
                        context_before=None,
                        context_after=None,
                        metadata={},
                    )
                ],
                confidence=0.64,
                avg_similarity=0.79,
                unique_documents=1,
            )

    class _FakeSemanticService:
        async def search(self, **kwargs):
            return SemanticSearchExecution(
                results=[
                    SemanticSearchResult(
                        chunk_id=note_chunk_id,
                        document_id=note_id,
                        document_title="Education Note",
                        content="The note mentions dyslexia accommodations and reading support.",
                        source_kind="note",
                        source_type="note",
                        chunk_index=0,
                        created_at=datetime.now(timezone.utc),
                        interaction_count=1,
                        similarity_score=0.72,
                        final_score=0.72,
                    )
                ],
                strategy="postgresql_hybrid",
            )

    class _FailingAnswerGenerator:
        async def generate(self, **kwargs):
            raise RuntimeError("ollama timeout")

    class _FakeAuditLogger:
        def __init__(self, db, user_id):
            self.db = db
            self.user_id = user_id

        async def log(self, **kwargs):
            captured_audit.update(kwargs.get("metadata", {}))
            return None

    async def _fake_hydrate_sources(db, rag_chunks, include_sources):
        del db, include_sources
        return [
            query_api.Source(
                chunk_id=str(document_chunk_id),
                document_id=str(document_id),
                document_title="Clinical Handbook",
                source_kind="document",
                text="Clinical handbook section on dyslexia assessment and intervention.",
                similarity=0.79,
            )
        ], ["Clinical handbook section on dyslexia assessment and intervention."]

    app = _build_query_app(fake_db)
    with patch.object(query_api, "get_workspace_context") as get_workspace_context, patch.object(
        query_api, "get_rag_retriever", return_value=_FakeRetriever()
    ), patch.object(
        query_api, "get_semantic_search_service", return_value=_FakeSemanticService()
    ), patch.object(
        query_api, "get_answer_generator", return_value=_FailingAnswerGenerator()
    ), patch.object(
        query_api, "_hydrate_sources", side_effect=_fake_hydrate_sources
    ), patch.object(
        query_api, "AuditLogger", _FakeAuditLogger
    ):
        get_workspace_context.return_value = SimpleNamespace(workspace_id=workspace_id)
        response = asyncio.run(
            _request(
                app,
                "POST",
                "/query",
                json={
                    "workspace_id": str(workspace_id),
                    "query": "medical book on dyslexia",
                    "top_k": 2,
                    "include_sources": True,
                    "model": "test-model",
                },
            )
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["answer"].startswith("I found source material that may be relevant")
    assert payload["confidence"] <= 0.12
    assert {item["source_kind"] for item in payload["sources"]} == {"document", "note"}
    assert captured_audit["answer_generation_status"] == "failed"
    assert captured_audit["answer_generation_error"] == "RuntimeError"
    assert captured_audit["reliable_evidence"] is True
    assert captured_audit["sources_returned"] == 2


def test_retrieve_top_k_http_response_includes_document_source_kind():
    fake_db = SimpleNamespace()
    workspace_id = uuid4()

    class _FakeRetriever:
        def retrieve(self, **kwargs):
            return SimpleNamespace(
                chunks=[
                    SimpleNamespace(
                        chunk_id="chunk-1",
                        document_id="doc-1",
                        similarity=0.88,
                        text="JavaScript DOM event handling explanation.",
                        source_type="upload",
                        chunk_index=0,
                        document_title="JavaScript Guide",
                        token_count=12,
                        context_before=None,
                        context_after=None,
                        metadata={},
                    )
                ],
                average_similarity=0.88,
                retrieval_time_ms=5.0,
                query_embedding_dim=768,
            )

    app = _build_retrieval_app(fake_db)
    with patch.object(retrieval_api, "_verify_workspace_access", return_value=True), patch.object(
        retrieval_api, "get_top_k_retriever", return_value=_FakeRetriever()
    ):
        response = asyncio.run(
            _request(
                app,
                "POST",
                f"/retrieve/top-k/{workspace_id}",
                json={
                    "query": "javascript dom event handling",
                    "top_k": 1,
                    "similarity_threshold": 0.45,
                    "rerank_by": "similarity",
                },
            )
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["chunks"][0]["source_kind"] == "document"
