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


class RagRelevanceGuardrailTests(unittest.TestCase):
    def _load_modules(self):
        fake_app = types.ModuleType("app")
        fake_services = types.ModuleType("app.services")
        fake_embeddings = types.ModuleType("app.services.embeddings")
        fake_vector_db = types.ModuleType("app.services.vector_db")

        fake_embeddings.get_embedding_service = lambda: None
        fake_vector_db.get_vector_db_client = lambda embedding_dim=None: None
        fake_services.embeddings = fake_embeddings
        fake_services.vector_db = fake_vector_db
        fake_app.services = fake_services

        relevance_module = _load_module(
            "test_target_retrieval_relevance",
            "app/services/retrieval_relevance.py",
            {
                "app": fake_app,
                "app.services": fake_services,
            },
        )

        rag_module = _load_module(
            "test_target_rag_retriever_guardrails",
            "app/services/rag_retriever.py",
            {
                "app": fake_app,
                "app.services": fake_services,
                "app.services.embeddings": fake_embeddings,
                "app.services.vector_db": fake_vector_db,
                "app.services.retrieval_relevance": relevance_module,
            },
        )
        return relevance_module, rag_module

    def _make_retriever(self, rag_module):
        retriever = object.__new__(rag_module.RAGRetriever)
        retriever.similarity_threshold = 0.45
        retriever.top_k = 5
        retriever.max_documents = 5
        return retriever

    def test_medical_book_on_dyslexia_filters_javascript_contamination(self):
        _, rag_module = self._load_modules()
        retriever = self._make_retriever(rag_module)
        query = "medical book on dyslexia"

        chunks = [
            rag_module.RetrievedChunk(
                chunk_id="js-1",
                document_id="doc-js",
                text="JavaScript DOM event handling with addEventListener and bubbling examples.",
                similarity=0.84,
                chunk_index=0,
                source_type="document",
                metadata={},
                document_title="JavaScript DOM Guide",
            ),
            rag_module.RetrievedChunk(
                chunk_id="med-1",
                document_id="doc-med",
                text="This clinical handbook discusses dyslexia, reading interventions, assessment, and educational support for learners.",
                similarity=0.71,
                chunk_index=0,
                source_type="document",
                metadata={},
                document_title="Dyslexia Clinical Handbook",
            ),
        ]

        kept, discarded = retriever._rerank_with_fallbacks(query, chunks, top_k=2)

        self.assertEqual([chunk.chunk_id for chunk in kept], ["med-1"])
        self.assertIn("js-1", discarded)

    def test_javascript_dom_event_handling_prefers_programming_material(self):
        _, rag_module = self._load_modules()
        retriever = self._make_retriever(rag_module)
        query = "javascript dom event handling"

        chunks = [
            rag_module.RetrievedChunk(
                chunk_id="med-1",
                document_id="doc-med",
                text="A medical overview of dyslexia symptoms, cognitive assessment, and interventions.",
                similarity=0.79,
                chunk_index=0,
                source_type="document",
                metadata={},
                document_title="Dyslexia Overview",
            ),
            rag_module.RetrievedChunk(
                chunk_id="js-1",
                document_id="doc-js",
                text="JavaScript DOM event handling covers listeners, capture, bubbling, and delegation patterns.",
                similarity=0.74,
                chunk_index=0,
                source_type="document",
                metadata={},
                document_title="Event Handling in JavaScript",
            ),
        ]

        kept, _ = retriever._rerank_with_fallbacks(query, chunks, top_k=2)

        self.assertEqual([chunk.chunk_id for chunk in kept], ["js-1"])

    def test_crypto_trading_strategies_excludes_programming_notes(self):
        _, rag_module = self._load_modules()
        retriever = self._make_retriever(rag_module)
        query = "crypto trading strategies"

        chunks = [
            rag_module.RetrievedChunk(
                chunk_id="prog-1",
                document_id="doc-prog",
                text="Programming book chapter on recursion and stack frames in Python.",
                similarity=0.77,
                chunk_index=0,
                source_type="document",
                metadata={},
                document_title="Programming Book on Recursion",
            ),
            rag_module.RetrievedChunk(
                chunk_id="crypto-1",
                document_id="doc-crypto",
                text="Crypto trading strategies include risk management, momentum setups, and volatility controls.",
                similarity=0.73,
                chunk_index=0,
                source_type="document",
                metadata={},
                document_title="Crypto Trading Strategies",
            ),
        ]

        kept, _ = retriever._rerank_with_fallbacks(query, chunks, top_k=2)

        self.assertEqual([chunk.chunk_id for chunk in kept], ["crypto-1"])

    def test_programming_book_on_recursion_remains_supported(self):
        _, rag_module = self._load_modules()
        retriever = self._make_retriever(rag_module)
        query = "programming book on recursion"

        chunks = [
            rag_module.RetrievedChunk(
                chunk_id="prog-1",
                document_id="doc-prog",
                text="This programming book explains recursion, base cases, and recursive tree traversal.",
                similarity=0.69,
                chunk_index=0,
                source_type="document",
                metadata={},
                document_title="Programming Book on Recursion",
            ),
            rag_module.RetrievedChunk(
                chunk_id="crypto-1",
                document_id="doc-crypto",
                text="Crypto market notes about bitcoin entries and breakout trading setups.",
                similarity=0.76,
                chunk_index=0,
                source_type="document",
                metadata={},
                document_title="Crypto Notes",
            ),
        ]

        kept, _ = retriever._rerank_with_fallbacks(query, chunks, top_k=2)

        self.assertEqual([chunk.chunk_id for chunk in kept], ["prog-1"])

    def test_off_topic_chunks_collapse_confidence(self):
        _, rag_module = self._load_modules()
        retriever = self._make_retriever(rag_module)
        query = "medical book on dyslexia"

        off_topic_chunks = [
            rag_module.RetrievedChunk(
                chunk_id="js-1",
                document_id="doc-js",
                text="JavaScript DOM event handling with event delegation and bubbling.",
                similarity=0.86,
                chunk_index=0,
                source_type="document",
                metadata={"lexical_overlap": 0.0, "domain_alignment": 0.0, "evidence_score": 0.04, "off_topic": True},
                document_title="JavaScript Events",
            ),
            rag_module.RetrievedChunk(
                chunk_id="crypto-1",
                document_id="doc-crypto",
                text="Crypto trading strategies with leverage management and momentum signals.",
                similarity=0.82,
                chunk_index=0,
                source_type="document",
                metadata={"lexical_overlap": 0.0, "domain_alignment": 0.0, "evidence_score": 0.05, "off_topic": True},
                document_title="Crypto Strategy Notes",
            ),
        ]

        confidence = retriever._compute_confidence(off_topic_chunks, query=query)

        self.assertLess(confidence, 0.20)


if __name__ == "__main__":
    unittest.main()
