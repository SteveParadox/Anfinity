import importlib.util
import sys
import time
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


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


class RetrievalHardeningTests(unittest.TestCase):
    def test_vector_db_mock_search_uses_real_cosine_and_workspace_filter(self):
        module = _load_module(
            "test_target_vector_db",
            "app/services/vector_db.py",
            {},
        )

        query_vector = [float(i + 1) for i in range(128)]
        client = module.VectorDBClient(require_qdrant=False, embedding_dim=len(query_vector))
        client.client = None
        client.is_connected = False
        client._last_connect_attempt = time.monotonic()
        client.mock_storage = {
            "workspace-a": [
                {
                    "id": "match",
                    "vector": list(query_vector),
                    "payload": {"workspace_id": "workspace-a", "chunk_text": "primary match"},
                },
                {
                    "id": "mismatch",
                    "vector": [float(-(i + 1)) for i in range(128)],
                    "payload": {"workspace_id": "workspace-a", "chunk_text": "opposite vector"},
                },
                {
                    "id": "other-workspace",
                    "vector": list(query_vector),
                    "payload": {"workspace_id": "workspace-b", "chunk_text": "wrong workspace"},
                },
            ]
        }

        results = client.search_similar(
            collection_name="workspace-a",
            query_vector=query_vector,
            workspace_id="workspace-a",
            limit=5,
            score_threshold=0.1,
        )

        self.assertEqual([item["id"] for item in results], ["match"])
        self.assertAlmostEqual(results[0]["similarity"], 1.0, places=6)

    def test_top_k_retriever_recency_prefers_newer_chunks(self):
        fake_app = types.ModuleType("app")
        fake_services = types.ModuleType("app.services")
        fake_embeddings = types.ModuleType("app.services.embeddings")
        fake_vector_db = types.ModuleType("app.services.vector_db")
        fake_config = types.ModuleType("app.config")

        fake_embeddings.get_embedding_service = lambda: None
        fake_vector_db.get_vector_db_client = lambda embedding_dim=None: None
        fake_config.settings = types.SimpleNamespace(EMBEDDING_PROVIDER="test")
        fake_services.embeddings = fake_embeddings
        fake_services.vector_db = fake_vector_db
        fake_app.services = fake_services

        module = _load_module(
            "test_target_top_k_retriever",
            "app/services/top_k_retriever.py",
            {
                "app": fake_app,
                "app.services": fake_services,
                "app.services.embeddings": fake_embeddings,
                "app.services.vector_db": fake_vector_db,
                "app.config": fake_config,
            },
        )

        retriever = object.__new__(module.TopKRetriever)
        now = datetime.now(timezone.utc)
        older = module.RetrievedChunk(
            chunk_id="older",
            document_id="doc-1",
            similarity=0.92,
            text="older chunk",
            source_type="note",
            chunk_index=0,
            document_title="Older",
            token_count=10,
            metadata={"created_at": (now - timedelta(days=120)).isoformat()},
        )
        newer = module.RetrievedChunk(
            chunk_id="newer",
            document_id="doc-2",
            similarity=0.80,
            text="newer chunk",
            source_type="note",
            chunk_index=0,
            document_title="Newer",
            token_count=10,
            metadata={"created_at": (now - timedelta(days=2)).isoformat()},
        )

        reranked = retriever._rerank_by_recency([older, newer])

        self.assertEqual([chunk.chunk_id for chunk in reranked], ["newer", "older"])

    def test_top_k_retriever_dedupes_and_caps_document_overflow(self):
        fake_app = types.ModuleType("app")
        fake_services = types.ModuleType("app.services")
        fake_embeddings = types.ModuleType("app.services.embeddings")
        fake_vector_db = types.ModuleType("app.services.vector_db")
        fake_config = types.ModuleType("app.config")

        fake_embeddings.get_embedding_service = lambda: None
        fake_vector_db.get_vector_db_client = lambda embedding_dim=None: None
        fake_config.settings = types.SimpleNamespace(EMBEDDING_PROVIDER="test")
        fake_services.embeddings = fake_embeddings
        fake_services.vector_db = fake_vector_db
        fake_app.services = fake_services

        module = _load_module(
            "test_target_top_k_retriever_dedupe",
            "app/services/top_k_retriever.py",
            {
                "app": fake_app,
                "app.services": fake_services,
                "app.services.embeddings": fake_embeddings,
                "app.services.vector_db": fake_vector_db,
                "app.config": fake_config,
            },
        )

        retriever = object.__new__(module.TopKRetriever)
        chunks = [
            module.RetrievedChunk("chunk-1", "doc-1", 0.91, "same text", "note", 0, "Doc 1", 20, metadata={}),
            module.RetrievedChunk("chunk-1", "doc-1", 0.89, "same text", "note", 0, "Doc 1", 20, metadata={}),
            module.RetrievedChunk("chunk-2", "doc-1", 0.88, "another text", "note", 1, "Doc 1", 20, metadata={}),
            module.RetrievedChunk("chunk-3", "doc-1", 0.87, "third text", "note", 2, "Doc 1", 20, metadata={}),
            module.RetrievedChunk("chunk-4", "doc-2", 0.86, "other doc", "note", 0, "Doc 2", 20, metadata={}),
        ]

        deduped = retriever._dedupe_chunks(chunks)
        capped = retriever._apply_document_cap(deduped, top_k=4)

        self.assertEqual([chunk.chunk_id for chunk in deduped], ["chunk-1", "chunk-2", "chunk-3", "chunk-4"])
        self.assertEqual([chunk.document_id for chunk in capped[:3]].count("doc-1"), 2)


if __name__ == "__main__":
    unittest.main()
