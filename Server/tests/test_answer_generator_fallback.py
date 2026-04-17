import asyncio
import importlib.util
import sys
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


class AnswerGeneratorFallbackTests(unittest.TestCase):
    def _load_answer_generator_module(self):
        fake_httpx = types.ModuleType("httpx")
        fake_httpx.AsyncClient = object
        fake_httpx.Timeout = lambda **kwargs: dict(kwargs)

        fake_app = types.ModuleType("app")
        fake_config = types.ModuleType("app.config")
        fake_config.settings = types.SimpleNamespace(
            OLLAMA_MODEL="phi3:mini",
            OLLAMA_FALLBACK_MODEL="phi3:mini",
            OPENAI_MODEL="gpt-4o-mini",
            LLM_TEMPERATURE=0.3,
            LLM_MAX_TOKENS=800,
            OLLAMA_BASE_URL="http://localhost:11434",
            OLLAMA_TIMEOUT=30,
            OLLAMA_CONNECT_TIMEOUT=10,
            OLLAMA_READ_TIMEOUT=120,
            OLLAMA_WRITE_TIMEOUT=30,
            OLLAMA_POOL_TIMEOUT=30,
            RAG_MAX_CONTEXT_CHUNKS=4,
            RAG_MAX_CHUNK_CHARS=1200,
            RAG_MAX_TOTAL_CONTEXT_CHARS=1800,
            RAG_COMPACT_CONTEXT_CHARS=900,
            RAG_MIN_ANSWER_CONFIDENCE=45.0,
        )
        fake_app.config = fake_config

        fake_services = types.ModuleType("app.services")
        fake_app.services = fake_services

        fake_cross_checker = types.ModuleType("app.services.retrieval_cross_checker")

        class RetrievalCrossChecker:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        class RetrievalValidation:
            pass

        fake_cross_checker.RetrievalCrossChecker = RetrievalCrossChecker
        fake_cross_checker.RetrievalValidation = RetrievalValidation

        relevance_module = _load_module(
            "test_target_retrieval_relevance_for_answer_generator",
            "app/services/retrieval_relevance.py",
            {
                "app": fake_app,
                "app.services": fake_services,
            },
        )

        fake_services.retrieval_relevance = relevance_module
        fake_services.retrieval_cross_checker = fake_cross_checker

        return _load_module(
            "test_target_answer_generator_fallback",
            "app/services/answer_generator.py",
            {
                "httpx": fake_httpx,
                "app": fake_app,
                "app.config": fake_config,
                "app.services": fake_services,
                "app.services.retrieval_cross_checker": fake_cross_checker,
                "app.services.retrieval_relevance": relevance_module,
            },
        )

    def test_select_chunks_for_generation_caps_total_context_and_prefers_best_chunks(self):
        answer_module = self._load_answer_generator_module()
        generator = answer_module.AnswerGenerator()

        chunks = [
            answer_module.RetrievedChunk(
                chunk_id="med-1",
                document_id="doc-med-1",
                similarity=0.79,
                text="A" * 1400,
                source_type="document",
                chunk_index=0,
                document_title="Clinical Handbook",
                token_count=250,
                metadata={
                    "generator_evidence_score": 0.82,
                    "generator_domain_alignment": 0.78,
                    "generator_lexical_overlap": 0.66,
                },
            ),
            answer_module.RetrievedChunk(
                chunk_id="med-2",
                document_id="doc-med-2",
                similarity=0.74,
                text="B" * 1400,
                source_type="document",
                chunk_index=0,
                document_title="Education Note",
                token_count=220,
                metadata={
                    "generator_evidence_score": 0.76,
                    "generator_domain_alignment": 0.72,
                    "generator_lexical_overlap": 0.61,
                },
            ),
            answer_module.RetrievedChunk(
                chunk_id="off-topic",
                document_id="doc-js",
                similarity=0.81,
                text="C" * 1400,
                source_type="document",
                chunk_index=0,
                document_title="JavaScript Events",
                token_count=240,
                metadata={
                    "generator_evidence_score": 0.08,
                    "generator_domain_alignment": 0.02,
                    "generator_lexical_overlap": 0.01,
                    "generator_off_topic": True,
                },
            ),
        ]

        selected = generator._select_chunks_for_generation("medical book on dyslexia", chunks)

        self.assertEqual([chunk.chunk_id for chunk in selected[:2]], ["med-1", "med-2"])
        self.assertNotIn("off-topic", [chunk.chunk_id for chunk in selected])
        self.assertLessEqual(sum(len(chunk.text) for chunk in selected), generator.max_total_context_chars)

    def test_generate_uses_extractive_grounded_fallback_when_llm_fails(self):
        answer_module = self._load_answer_generator_module()
        generator = answer_module.AnswerGenerator()

        async def _always_fail_llm(*args, **kwargs):
            raise RuntimeError("ollama timeout")

        generator._call_llm = _always_fail_llm

        chunks = [
            answer_module.RetrievedChunk(
                chunk_id="doc-1-chunk-1",
                document_id="doc-1",
                similarity=0.78,
                text=(
                    "The clinical handbook describes dyslexia as a learning disorder that benefits from "
                    "structured reading intervention and formal assessment. It recommends screening and "
                    "support planning for affected learners."
                ),
                source_type="document",
                chunk_index=0,
                document_title="Clinical Handbook",
                token_count=30,
                metadata={},
            ),
            answer_module.RetrievedChunk(
                chunk_id="note-1-chunk-1",
                document_id="note-1",
                similarity=0.71,
                text=(
                    "The education note highlights dyslexia accommodations, guided reading support, and "
                    "teacher strategies for classroom intervention."
                ),
                source_type="note",
                chunk_index=0,
                document_title="Education Note",
                token_count=18,
                metadata={"source_kind": "note"},
            ),
        ]

        result = asyncio.run(
            generator.generate(
                query="medical book on dyslexia",
                chunks=chunks,
                include_citations=True,
                top_k=2,
            )
        )

        self.assertNotIn("I found source material that may be relevant", result.answer_text)
        self.assertNotIn("I couldn't find enough reliable information", result.answer_text)
        self.assertIn("Clinical Handbook", result.answer_text)
        self.assertGreater(len(result.citations), 0)
        self.assertGreater(result.confidence_score, 0.0)
        self.assertEqual(result.model_used, "extractive-grounded-fallback")
        self.assertEqual(result.metadata["fallback_reason"], "extractive_grounded_fallback")
        self.assertEqual(result.metadata["llm_error"], "RuntimeError")

    def test_ollama_generate_uses_structured_timeout_and_payload_model(self):
        answer_module = self._load_answer_generator_module()
        captured = {}

        class _FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"message": {"content": "Grounded answer"}}

        class _FakeAsyncClient:
            def __init__(self, timeout=None, headers=None):
                captured["timeout"] = timeout
                captured["headers"] = headers

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def post(self, url, json):
                captured["url"] = url
                captured["payload"] = json
                return _FakeResponse()

        answer_module.httpx.AsyncClient = _FakeAsyncClient

        generator = answer_module.AnswerGenerator(model="llama3:8b")
        result = asyncio.run(
            generator._ollama_generate(
                "system prompt",
                "user prompt",
                model_override="phi3:mini",
                max_tokens_override=300,
                num_ctx_override=1600,
            )
        )

        self.assertEqual(result, "Grounded answer")
        self.assertEqual(captured["payload"]["model"], "phi3:mini")
        self.assertEqual(captured["payload"]["options"]["num_predict"], 300)
        self.assertEqual(captured["payload"]["options"]["num_ctx"], 1600)
        self.assertEqual(captured["timeout"]["connect"], 10.0)
        self.assertEqual(captured["timeout"]["read"], 120.0)
        self.assertEqual(captured["timeout"]["write"], 30.0)
        self.assertEqual(captured["timeout"]["pool"], 30.0)


if __name__ == "__main__":
    unittest.main()
