import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]


def _load_module(module_name: str, relative_path: str, extra_modules: dict[str, object]):
    for name, module in extra_modules.items():
        sys.modules[name] = module

    spec = importlib.util.spec_from_file_location(module_name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class _FakeSearchLog:
    def __init__(self, **kwargs):
        self.id = kwargs.pop("id", None)
        for key, value in kwargs.items():
            setattr(self, key, value)


class _FakeSearchQuery:
    def __init__(self, **kwargs):
        self.id = kwargs.pop("id", None)
        for key, value in kwargs.items():
            setattr(self, key, value)


class _FakeColumn:
    def in_(self, values):
        return ("in", values)


class _FakeNote:
    id = _FakeColumn()
    tags = _FakeColumn()


class _FakeResult:
    def __init__(self, rows=None):
        self._rows = rows or []

    def fetchall(self):
        return self._rows


class _FakeDB:
    class _NestedTransaction:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    def __init__(self, rows=None, fail_update_query_embedding_vector: bool = False):
        self.added = []
        self.executed = []
        self.committed = False
        self.rolled_back = False
        self.rows = rows or []
        self.fail_update_query_embedding_vector = fail_update_query_embedding_vector

    def add(self, obj):
        self.added.append(obj)

    def begin_nested(self):
        return self._NestedTransaction()

    async def flush(self):
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid4()

    async def execute(self, stmt, params=None):
        if self.fail_update_query_embedding_vector and "query_embedding_vector" in str(stmt):
            raise RuntimeError("column query_embedding_vector does not exist")
        self.executed.append((stmt, params))
        return _FakeResult(self.rows)

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True


class SemanticSearchServiceTests(unittest.TestCase):
    def _load_semantic_search_module(self, postgres_service, retriever):
        fake_app = types.ModuleType("app")

        fake_database = types.ModuleType("app.database")
        fake_models = types.ModuleType("app.database.models")
        fake_models.SearchLog = _FakeSearchLog
        fake_models.SearchQuery = _FakeSearchQuery
        fake_models.Note = _FakeNote
        fake_database.models = fake_models

        fake_services = types.ModuleType("app.services")

        fake_embeddings = types.ModuleType("app.services.embeddings")

        class FakeEmbeddingService:
            dimension = 3

            def embed_query(self, text):
                return [0.1, 0.2, 0.3]

        fake_embeddings.get_embedding_service = lambda: FakeEmbeddingService()

        fake_postgresql = types.ModuleType("app.services.postgresql_search")
        fake_postgresql.get_postgresql_search_service = lambda: postgres_service

        fake_rag = types.ModuleType("app.services.rag_retriever")

        class FakeRetrievedChunk:
            def __init__(self, **kwargs):
                for key, value in kwargs.items():
                    setattr(self, key, value)

        class FakeRAGRetriever:
            pass

        fake_rag.RetrievedChunk = FakeRetrievedChunk
        fake_rag.RAGRetriever = FakeRAGRetriever
        fake_rag.get_rag_retriever = lambda: retriever

        fake_services.embeddings = fake_embeddings
        fake_services.postgresql_search = fake_postgresql
        fake_services.rag_retriever = fake_rag
        fake_app.database = fake_database
        fake_app.services = fake_services

        relevance_module = _load_module(
            "test_target_semantic_relevance",
            "app/services/retrieval_relevance.py",
            {
                "app": fake_app,
                "app.services": fake_services,
            },
        )

        fake_sqlalchemy = types.ModuleType("sqlalchemy")
        fake_sqlalchemy.text = lambda sql: sql
        fake_sqlalchemy.select = lambda *args: types.SimpleNamespace(where=lambda *conds: ("select", args, conds))
        fake_sqlalchemy_ext = types.ModuleType("sqlalchemy.ext")
        fake_sqlalchemy_ext_asyncio = types.ModuleType("sqlalchemy.ext.asyncio")
        fake_sqlalchemy_ext_asyncio.AsyncSession = object

        return _load_module(
            "test_target_semantic_search_service",
            "app/services/semantic_search.py",
            {
                "app": fake_app,
                "app.database": fake_database,
                "app.database.models": fake_models,
                "app.services": fake_services,
                "app.services.embeddings": fake_embeddings,
                "app.services.postgresql_search": fake_postgresql,
                "app.services.rag_retriever": fake_rag,
                "app.services.retrieval_relevance": relevance_module,
                "sqlalchemy": fake_sqlalchemy,
                "sqlalchemy.ext": fake_sqlalchemy_ext,
                "sqlalchemy.ext.asyncio": fake_sqlalchemy_ext_asyncio,
            },
        )

    def test_search_prefers_postgresql_hybrid_and_logs_execution(self):
        workspace_id = uuid4()
        user_id = uuid4()
        note_id = uuid4()
        chunk_id = uuid4()
        document_id = uuid4()

        class FakePostgresService:
            async def hybrid_search(self, **kwargs):
                return [
                    {
                        "note_id": str(note_id),
                        "title": "Hybrid result",
                        "content": "semantic search content",
                        "note_type": "note",
                        "created_at": "2026-04-01T00:00:00",
                        "embedding_similarity": 0.0,
                        "interaction_score": 0.4,
                        "highlight": "semantic search content",
                    }
                ]

        class FakeRetriever:
            def __init__(self):
                self.called = False

            def retrieve(self, **kwargs):
                self.called = True
                chunk = types.SimpleNamespace(
                    chunk_id=chunk_id,
                    document_id=document_id,
                    document_title="Document fallback",
                    text="semantic search content in a document chunk",
                    source_type="document",
                    chunk_index=0,
                    similarity=0.78,
                    metadata={"created_at": "2026-04-02T00:00:00", "interaction_count": 1},
                )
                return types.SimpleNamespace(chunks=[chunk])

        retriever = FakeRetriever()
        module = self._load_semantic_search_module(FakePostgresService(), retriever)
        service = module.SemanticSearchService(retriever, module.get_embedding_service())
        db = _FakeDB()

        execution = asyncio.run(
            service.search(
                workspace_id=workspace_id,
                user_id=user_id,
                query="semantic search",
                limit=5,
                filters={},
                db=db,
            )
        )

        self.assertEqual(execution.strategy, "postgresql_hybrid_plus_retriever")
        self.assertEqual(len(execution.results), 2)
        self.assertIsNotNone(execution.search_log_id)
        self.assertTrue(retriever.called)
        self.assertIn("note", {result.source_kind for result in execution.results})
        self.assertIn("document", {result.source_kind for result in execution.results})
        self.assertFalse(db.rolled_back)

    def test_search_falls_back_to_retriever_when_postgresql_unavailable(self):
        workspace_id = uuid4()
        user_id = uuid4()
        chunk_id = uuid4()
        document_id = uuid4()

        class FailingPostgresService:
            async def hybrid_search(self, **kwargs):
                raise RuntimeError("undefined_function hybrid_search")

        class FakeRetriever:
            def retrieve(self, **kwargs):
                chunk = types.SimpleNamespace(
                    chunk_id=chunk_id,
                    document_id=document_id,
                    document_title="Fallback doc",
                    text="ollama fallback chunk",
                    source_type="document",
                    chunk_index=0,
                    similarity=0.82,
                    metadata={"created_at": "2026-04-02T00:00:00", "interaction_count": 3},
                )
                return types.SimpleNamespace(chunks=[chunk])

        retriever = FakeRetriever()
        module = self._load_semantic_search_module(FailingPostgresService(), retriever)
        service = module.SemanticSearchService(retriever, module.get_embedding_service())
        db = _FakeDB()

        execution = asyncio.run(
            service.search(
                workspace_id=workspace_id,
                user_id=user_id,
                query="ollama fallback",
                limit=5,
                filters={},
                db=db,
            )
        )

        self.assertEqual(execution.strategy, "retriever_fallback")
        self.assertEqual(len(execution.results), 1)
        self.assertEqual(str(execution.results[0].chunk_id), str(chunk_id))
        self.assertIsNotNone(execution.search_log_id)

    def test_postgresql_results_use_hybrid_semantic_score_and_tag_filter(self):
        workspace_id = uuid4()
        user_id = uuid4()
        note_a = uuid4()
        note_b = uuid4()

        class FakePostgresService:
            async def hybrid_search(self, **kwargs):
                return [
                    {
                        "note_id": str(note_a),
                        "title": "Vector dominant",
                        "content": "semantic system design",
                        "note_type": "note",
                        "created_at": "2026-04-01T00:00:00",
                        "embedding_similarity": 0.82,
                        "text_score": 0.05,
                        "interaction_score": 0.10,
                        "highlight": "semantic system design",
                    },
                    {
                        "note_id": str(note_b),
                        "title": "Hybrid dominant",
                        "content": "semantic search bm25 ranking",
                        "note_type": "note",
                        "created_at": "2026-04-01T00:00:00",
                        "embedding_similarity": 0.55,
                        "text_score": 0.95,
                        "interaction_score": 0.10,
                        "highlight": "semantic search bm25 ranking",
                    },
                ]

        class FakeRetriever:
            def retrieve(self, **kwargs):
                chunk = types.SimpleNamespace(
                    chunk_id=uuid4(),
                    document_id=uuid4(),
                    document_title="Document result",
                    text="semantic search bm25 ranking from a document",
                    source_type="document",
                    chunk_index=0,
                    similarity=0.76,
                    metadata={"created_at": "2026-04-03T00:00:00", "interaction_count": 0, "tags": ["search"]},
                )
                return types.SimpleNamespace(chunks=[chunk])

        retriever = FakeRetriever()
        module = self._load_semantic_search_module(FakePostgresService(), retriever)
        service = module.SemanticSearchService(retriever, module.get_embedding_service())
        db = _FakeDB(rows=[
            (note_a, ["systems"]),
            (note_b, ["search", "ranking"]),
        ])

        execution = asyncio.run(
            service.search(
                workspace_id=workspace_id,
                user_id=user_id,
                query="semantic search bm25",
                limit=5,
                filters={"tags": ["search"]},
                db=db,
            )
        )

        self.assertGreaterEqual(len(execution.results), 2)
        self.assertEqual(str(execution.results[0].document_id), str(note_b))
        self.assertAlmostEqual(execution.results[0].similarity_score, 0.67, places=2)
        self.assertEqual(execution.results[0].tags, ["search", "ranking"])
        self.assertIn("document", {result.source_type for result in execution.results})
        self.assertIn("note", {result.source_kind for result in execution.results})
        self.assertIn("document", {result.source_kind for result in execution.results})

    def test_search_can_be_restricted_to_notes_only(self):
        workspace_id = uuid4()
        user_id = uuid4()
        note_id = uuid4()

        class FakePostgresService:
            async def hybrid_search(self, **kwargs):
                return [
                    {
                        "note_id": str(note_id),
                        "title": "Note result",
                        "content": "dyslexia support strategies",
                        "note_type": "note",
                        "created_at": "2026-04-01T00:00:00",
                        "embedding_similarity": 0.71,
                        "text_score": 0.82,
                        "interaction_score": 0.10,
                        "highlight": "dyslexia support strategies",
                    }
                ]

        class FakeRetriever:
            def __init__(self):
                self.called = False

            def retrieve(self, **kwargs):
                self.called = True
                return types.SimpleNamespace(chunks=[])

        retriever = FakeRetriever()
        module = self._load_semantic_search_module(FakePostgresService(), retriever)
        service = module.SemanticSearchService(retriever, module.get_embedding_service())
        db = _FakeDB(rows=[(note_id, ["education"])])

        execution = asyncio.run(
            service.search(
                workspace_id=workspace_id,
                user_id=user_id,
                query="dyslexia support",
                limit=5,
                filters={},
                db=db,
                include_postgresql=True,
                include_retriever=False,
            )
        )

        self.assertEqual(execution.strategy, "postgresql_hybrid")
        self.assertEqual(len(execution.results), 1)
        self.assertEqual(execution.results[0].source_kind, "note")
        self.assertFalse(retriever.called)

    def test_log_search_execution_survives_missing_query_embedding_vector_column(self):
        workspace_id = uuid4()
        user_id = uuid4()
        chunk_id = uuid4()
        document_id = uuid4()

        class FakePostgresService:
            async def hybrid_search(self, **kwargs):
                return []

        class FakeRetriever:
            def retrieve(self, **kwargs):
                return types.SimpleNamespace(chunks=[])

        module = self._load_semantic_search_module(FakePostgresService(), FakeRetriever())
        service = module.SemanticSearchService(FakeRetriever(), module.get_embedding_service())
        db = _FakeDB(fail_update_query_embedding_vector=True)

        search_log_id = asyncio.run(
            service.log_search_execution(
                db=db,
                user_id=user_id,
                workspace_id=workspace_id,
                query="rags",
                results=[
                    module.SemanticSearchResult(
                        chunk_id=chunk_id,
                        document_id=document_id,
                        document_title="Fallback doc",
                        content="content",
                        source_kind="note",
                        source_type="note",
                        chunk_index=0,
                        created_at=module.datetime.now(module.timezone.utc),
                        interaction_count=0,
                        similarity_score=0.8,
                    )
                ],
                search_duration_ms=123,
            )
        )

        self.assertIsNotNone(search_log_id)
        self.assertFalse(db.rolled_back)
        self.assertTrue(any(isinstance(obj, _FakeSearchLog) for obj in db.added))


if __name__ == "__main__":
    unittest.main()
