import importlib.util
import sys
import threading
import types
import unittest
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


class PerformancePathTests(unittest.TestCase):
    def test_embedding_service_embed_text_uses_cache(self):
        fake_requests = types.ModuleType("requests")
        fake_requests.Session = object

        fake_app = types.ModuleType("app")
        fake_config = types.ModuleType("app.config")
        fake_config.settings = types.SimpleNamespace()
        fake_app.config = fake_config

        module = _load_module(
            "test_target_embeddings_service",
            "app/services/embeddings.py",
            {
                "requests": fake_requests,
                "app": fake_app,
                "app.config": fake_config,
            },
        )

        class FakeCache:
            def __init__(self):
                self.store = {}

            def get(self, text, model):
                return self.store.get((text, model))

            def set(self, text, model, embedding):
                self.store[(text, model)] = embedding

        service = object.__new__(module.EmbeddingService)
        service.provider = "ollama"
        service.model = "nomic-embed-text"
        service.dimension = 768
        service._http = None
        service._cache = FakeCache()
        service._cache_lock = threading.RLock()

        calls = {"count": 0}

        def fake_embed_batch(texts):
            calls["count"] += 1
            return [[0.1, 0.2, 0.3] for _ in texts]

        service.embed_batch = fake_embed_batch

        first = service.embed_text("semantic search")
        second = service.embed_text("semantic search")

        self.assertEqual(first, [0.1, 0.2, 0.3])
        self.assertEqual(second, [0.1, 0.2, 0.3])
        self.assertEqual(calls["count"], 1)

    def test_rag_query_variant_expansion_stays_bounded(self):
        fake_app = types.ModuleType("app")
        fake_services = types.ModuleType("app.services")
        fake_embeddings = types.ModuleType("app.services.embeddings")
        fake_vector_db = types.ModuleType("app.services.vector_db")

        fake_embeddings.get_embedding_service = lambda: None
        fake_vector_db.get_vector_db_client = lambda: None
        fake_services.embeddings = fake_embeddings
        fake_services.vector_db = fake_vector_db
        fake_app.services = fake_services

        module = _load_module(
            "test_target_rag_retriever",
            "app/services/rag_retriever.py",
            {
                "app": fake_app,
                "app.services": fake_services,
                "app.services.embeddings": fake_embeddings,
                "app.services.vector_db": fake_vector_db,
            },
        )

        retriever = object.__new__(module.RAGRetriever)
        variants = retriever._expand_query_variants(
            "how to improve semantic search latency in ollama embeddings"
        )

        self.assertEqual(variants[0], "how to improve semantic search latency in ollama embeddings")
        self.assertLessEqual(len(variants), 2)


if __name__ == "__main__":
    unittest.main()
