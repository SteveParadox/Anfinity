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


class _FakeHTTPError(Exception):
    def __init__(self, message: str, response=None):
        super().__init__(message)
        self.response = response


class _FakeResponse:
    def __init__(self, status_code: int, payload=None, text: str = ""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text or ""

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise _FakeHTTPError(self.text or f"HTTP {self.status_code}", response=self)


class _FakeSession:
    def __init__(self, responder):
        self._responder = responder
        self.headers = {}
        self.calls = []

    def post(self, url, json=None, timeout=None):
        self.calls.append((url, json, timeout))
        return self._responder(url, json or {}, timeout)


class PerformancePathTests(unittest.TestCase):
    def _load_embeddings_module(self, responder):
        fake_requests = types.ModuleType("requests")
        session = _FakeSession(responder)
        fake_requests.Session = lambda: session
        fake_requests.HTTPError = _FakeHTTPError

        fake_app = types.ModuleType("app")
        fake_config = types.ModuleType("app.config")
        fake_config.settings = types.SimpleNamespace(
            OPENAI_API_KEY=None,
            OPENAI_EMBEDDING_MODEL="text-embedding-3-small",
            OLLAMA_BASE_URL="http://ollama.local",
            OLLAMA_API_KEY=None,
            OLLAMA_EMBEDDING_MODEL="nomic-embed-text",
            OLLAMA_EMBED_TIMEOUT=30,
            OLLAMA_TIMEOUT=30,
            OLLAMA_EMBED_BATCH_SIZE=4,
            EMBEDDING_BATCH_SIZE=4,
            EMBEDDING_PROVIDER="ollama",
            EMBEDDING_DIMENSION=768,
            COHERE_API_KEY=None,
            COHERE_EMBEDDING_MODEL="embed-english-v3.0",
            REDIS_URL="redis://cache.invalid:6379/0",
        )
        fake_config.get_ollama_request_headers = lambda: {"Content-Type": "application/json"}
        fake_app.config = fake_config

        module = _load_module(
            "test_target_embeddings_service_runtime",
            "app/services/embeddings.py",
            {
                "requests": fake_requests,
                "app": fake_app,
                "app.config": fake_config,
            },
        )
        return module, session

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

    def test_ollama_embedding_normal_batch_succeeds(self):
        def responder(url, payload, timeout):
            inputs = payload.get("input", [])
            return _FakeResponse(
                200,
                {"embeddings": [[float(index + 1), 0.1, 0.2] for index, _ in enumerate(inputs)]},
            )

        module, session = self._load_embeddings_module(responder)
        service = module.EmbeddingService(provider="ollama")
        service._cache = None
        service.is_valid_embedding = lambda vec: True

        embeddings = service.embed_batch(["alpha", "beta"])

        self.assertEqual(len(embeddings), 2)
        self.assertEqual(len(session.calls), 1)
        self.assertEqual(session.calls[0][1]["input"], ["alpha", "beta"])

    def test_ollama_embedding_oversized_input_is_split_before_request(self):
        observed_inputs = []

        def responder(url, payload, timeout):
            inputs = payload.get("input", [])
            observed_inputs.extend(inputs)
            return _FakeResponse(
                200,
                {"embeddings": [[0.3, 0.2, 0.1] for _ in inputs]},
            )

        module, session = self._load_embeddings_module(responder)
        service = module.EmbeddingService(provider="ollama")
        service._cache = None
        service.is_valid_embedding = lambda vec: True
        oversized = ("A" * 4500) + "\n\n" + ("B" * 4500) + "\n\n" + ("C" * 4500)

        embedding = service.embed_batch([oversized])

        self.assertEqual(len(embedding), 1)
        self.assertGreater(len(observed_inputs), 1)
        self.assertTrue(all(len(item) <= service.OLLAMA_SAFE_MAX_INPUT_CHARS_768 for item in observed_inputs))
        self.assertTrue(all(call[0].endswith("/api/embed") for call in session.calls))

    def test_ollama_embedding_batch_failure_halves_batch_size(self):
        seen_batch_sizes = []

        def responder(url, payload, timeout):
            inputs = payload.get("input", [])
            seen_batch_sizes.append(len(inputs))
            if len(inputs) > 1:
                return _FakeResponse(400, text="input exceeds maximum context length")
            return _FakeResponse(200, {"embeddings": [[0.4, 0.5, 0.6]]})

        module, _ = self._load_embeddings_module(responder)
        service = module.EmbeddingService(provider="ollama")
        service._cache = None
        service.is_valid_embedding = lambda vec: True

        embeddings = service.embed_batch(["one", "two", "three", "four"])

        self.assertEqual(len(embeddings), 4)
        self.assertIn(4, seen_batch_sizes)
        self.assertIn(2, seen_batch_sizes)
        self.assertGreaterEqual(seen_batch_sizes.count(1), 4)

    def test_rag_query_variant_expansion_stays_bounded(self):
        fake_app = types.ModuleType("app")
        fake_services = types.ModuleType("app.services")
        fake_embeddings = types.ModuleType("app.services.embeddings")
        fake_vector_db = types.ModuleType("app.services.vector_db")
        fake_relevance = types.ModuleType("app.services.retrieval_relevance")

        fake_embeddings.get_embedding_service = lambda: None
        fake_vector_db.get_vector_db_client = lambda: None
        fake_relevance.analyze_chunk_relevance = lambda *args, **kwargs: None
        fake_relevance.analyze_query_intent = lambda *args, **kwargs: None
        fake_services.embeddings = fake_embeddings
        fake_services.vector_db = fake_vector_db
        fake_services.retrieval_relevance = fake_relevance
        fake_app.services = fake_services

        module = _load_module(
            "test_target_rag_retriever",
            "app/services/rag_retriever.py",
            {
                "app": fake_app,
                "app.services": fake_services,
                "app.services.embeddings": fake_embeddings,
                "app.services.vector_db": fake_vector_db,
                "app.services.retrieval_relevance": fake_relevance,
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
