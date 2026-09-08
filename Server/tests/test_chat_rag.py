import asyncio
import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ["DEBUG"] = "false"

spec = importlib.util.spec_from_file_location("test_target_chat", ROOT / "app" / "api" / "chat.py")
chat = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules["test_target_chat"] = chat
spec.loader.exec_module(chat)


class ChatRAGTests(unittest.IsolatedAsyncioTestCase):
    def _source(self, title="Search Notes", similarity=0.84, source_id="S1"):
        return chat.StrictRAGSource(
            source_id=source_id,
            note_id=str(uuid4()),
            title=title,
            excerpt="BM25 helped reorder relevant note matches.",
            created_at="2026-04-01T00:00:00+00:00",
            similarity=similarity,
            similarity_percent=chat.calibrate_similarity_percent(similarity)
            if hasattr(chat, "calibrate_similarity_percent")
            else round(similarity * 100),
            citation=f"{title} | 2026-04-01 | 85%",
            chunk_id=str(uuid4()),
            chunk_index=0,
            support_level="supported",
        )

    def _parse_sse(self, chunks):
        events = []
        for chunk in chunks:
            event_type = None
            data = []
            for line in chunk.strip().splitlines():
                if line.startswith("event:"):
                    event_type = line.split(":", 1)[1].strip()
                if line.startswith("data:"):
                    data.append(line.split(":", 1)[1].strip())
            if data:
                parsed = json.loads("\n".join(data))
                parsed.setdefault("type", event_type)
                events.append(parsed)
        return events

    def test_build_rag_prompt_requires_strict_source_markers(self):
        sources = [
            self._source()
        ]

        prompt = chat.build_rag_system_prompt("What did I write about ranking?", sources)

        self.assertIn("[S1]", prompt)
        self.assertIn("Note title: \"Search Notes\"", prompt)
        self.assertIn("Do not answer from memory or world knowledge", prompt)
        self.assertIn("Similarity:", prompt)
        self.assertIn("Never invent note titles, dates, or similarity values", prompt)

    async def test_rag_stream_returns_grounded_not_found_without_calling_llm(self):
        workspace_id = uuid4()
        fake_user = SimpleNamespace(id=uuid4())
        retrieval = chat.StrictRAGResult(
            sources=[],
            answer_status="refusal",
            confidence="not_found",
            refusal_reason="no_note_evidence",
        )

        with patch.object(chat, "retrieve_context", AsyncMock(return_value=retrieval)), patch.object(
            chat,
            "generate_answer",
            AsyncMock(side_effect=AssertionError("LLM should not be called when there are no sources")),
        ):
            chunks = []
            async for chunk in chat._rag_stream(
                query="What do my notes say?",
                workspace_id=workspace_id,
                user=fake_user,
                db=None,
                history=None,
                top_k=6,
                threshold=0.3,
            ):
                chunks.append(chunk)

        events = self._parse_sse(chunks)

        self.assertEqual(events[0]["type"], "start")
        self.assertEqual(events[1]["type"], "token")
        self.assertEqual(events[1]["text"], chat.REFUSAL_TEXT)
        self.assertEqual(events[2]["type"], "sources")
        self.assertEqual(events[2]["sources"], [])
        self.assertEqual(events[3]["type"], "done")
        self.assertEqual(events[3]["answerStatus"], "refusal")

    async def test_rag_stream_replaces_model_citation_markers_and_returns_used_sources_only(self):
        workspace_id = uuid4()
        fake_user = SimpleNamespace(id=uuid4())
        used_source = self._source(title="Ranking Notes", source_id="S1")
        unused_source = self._source(title="Other Notes", source_id="S2")
        retrieval = chat.StrictRAGResult(
            sources=[used_source, unused_source],
            answer_status="supported",
            confidence="medium",
        )

        async def fake_stream(_messages):
            yield "You wrote that BM25 helped reorder matches [S1]."

        with patch.object(chat, "retrieve_context", AsyncMock(return_value=retrieval)), patch.object(
            chat,
            "_stream_with_ollama",
            fake_stream,
        ):
            chunks = []
            async for chunk in chat._rag_stream(
                query="What did I write about ranking?",
                workspace_id=workspace_id,
                user=fake_user,
                db=None,
                history=None,
                top_k=6,
                threshold=0.3,
            ):
                chunks.append(chunk)

        events = self._parse_sse(chunks)
        token_text = "".join(event.get("text", "") for event in events if event["type"] == "token")
        sources_event = next(event for event in events if event["type"] == "sources")

        self.assertIn("[Ranking Notes | 2026-04-01 | 85%]", token_text)
        self.assertEqual([source["sourceId"] for source in sources_event["sources"]], ["S1"])
        self.assertNotIn("Other Notes", token_text)

    def test_similarity_percent_is_coarse_and_bounded(self):
        self.assertEqual(chat.calibrate_similarity_percent(0), 0)
        self.assertEqual(chat.calibrate_similarity_percent(0.423), 40)
        self.assertEqual(chat.calibrate_similarity_percent(1.0), 95)

    def test_general_knowledge_query_requires_direct_note_support(self):
        source = self._source(title="Ranking Notes", similarity=0.82)
        source.score_components = {"answerability": 0.0}

        result = chat.evaluate_sources(
            [source],
            query="What is BM25?",
            min_score=0.46,
        )

        self.assertFalse(result.can_answer)
        self.assertEqual(result.refusal_reason, "general_knowledge_not_supported_by_notes")

    def test_personal_note_query_can_use_relevant_note_evidence(self):
        source = self._source(title="Ranking Notes", similarity=0.82)
        source.score_components = {"answerability": 0.0}

        result = chat.evaluate_sources(
            [source],
            query="What did I write about BM25?",
            min_score=0.46,
        )

        self.assertTrue(result.can_answer)
        self.assertEqual(result.answer_status, "supported")

    def test_single_partial_source_is_retained_for_transparent_feedback(self):
        source = self._source(title="Related Notes", similarity=0.40)

        result = chat.evaluate_sources(
            [source],
            query="What did I write about ranking?",
            min_score=0.46,
        )

        self.assertTrue(result.can_answer)
        self.assertEqual(result.answer_status, "partial")
        self.assertEqual(result.sources, [source])
        self.assertEqual(result.confidence, "low")

    def test_grounded_stream_guard_suppresses_uncited_general_text(self):
        source = self._source(title="Ranking Notes", source_id="S1")
        guard = chat.GroundedAnswerStreamGuard([source])

        leaked = guard.feed("BM25 is a general ranking function. ")
        grounded = guard.feed("You wrote that BM25 helped reorder matches [S1]. ")

        self.assertEqual(leaked, "")
        self.assertEqual(grounded, "You wrote that BM25 helped reorder matches [S1].")

    async def test_rag_stream_exposes_sources_when_model_omits_citations(self):
        workspace_id = uuid4()
        fake_user = SimpleNamespace(id=uuid4())
        retrieval = chat.StrictRAGResult(
            sources=[self._source(title="Ranking Notes", source_id="S1")],
            answer_status="supported",
            confidence="medium",
        )

        async def fake_stream(_messages):
            yield "BM25 is a general ranking function."

        with patch.object(chat, "retrieve_context", AsyncMock(return_value=retrieval)), patch.object(
            chat,
            "_stream_with_ollama",
            fake_stream,
        ):
            chunks = []
            async for chunk in chat._rag_stream(
                query="What is BM25?",
                workspace_id=workspace_id,
                user=fake_user,
                db=None,
                history=None,
                top_k=6,
                threshold=0.3,
            ):
                chunks.append(chunk)

        events = self._parse_sse(chunks)
        token_text = "".join(event.get("text", "") for event in events if event["type"] == "token")
        done_event = next(event for event in events if event["type"] == "done")

        self.assertEqual(token_text, chat.PARTIAL_GROUNDING_TEXT)
        self.assertEqual(done_event["answerStatus"], "partial")
        sources_event = next(event for event in events if event["type"] == "sources")
        self.assertEqual([source["sourceId"] for source in sources_event["sources"]], ["S1"])
        self.assertEqual(sources_event["refusalReason"], "no_citation_emitted")
        self.assertNotIn("general ranking function", token_text)

    async def test_rag_stream_does_not_append_fallback_after_partial_model_output(self):
        workspace_id = uuid4()
        fake_user = SimpleNamespace(id=uuid4())
        retrieval = chat.StrictRAGResult(
            sources=[self._source(title="Ranking Notes", source_id="S1")],
            answer_status="supported",
            confidence="medium",
        )

        async def failing_stream(_messages):
            yield "You wrote that BM25 helped reorder matches [S1]."
            raise RuntimeError("stream disconnected")

        with patch.object(chat, "retrieve_context", AsyncMock(return_value=retrieval)), patch.object(
            chat,
            "_stream_with_ollama",
            failing_stream,
        ), patch.object(
            chat,
            "generate_answer",
            AsyncMock(return_value="A second full answer [S1]."),
        ) as fallback:
            chunks = []
            async for chunk in chat._rag_stream(
                query="What did I write about ranking?",
                workspace_id=workspace_id,
                user=fake_user,
                db=None,
                history=None,
                top_k=6,
                threshold=0.3,
            ):
                chunks.append(chunk)

        events = self._parse_sse(chunks)
        token_text = "".join(event.get("text", "") for event in events if event["type"] == "token")

        self.assertIn("BM25 helped reorder", token_text)
        self.assertNotIn("second full answer", token_text)
        fallback.assert_not_awaited()

    async def test_generate_answer_uses_global_openai_provider_without_trying_ollama(self):
        messages = [{"role": "user", "content": "Answer from my notes."}]

        with patch.object(chat.settings, "LLM_PROVIDER", "openai"), patch.object(
            chat.settings, "LLM_USE_FALLBACK", False
        ), patch.object(chat.settings, "OPENAI_API_KEY", "test-key"), patch.object(
            chat.settings, "OLLAMA_ENABLED", False
        ), patch.object(
            chat,
            "_generate_with_ollama",
            AsyncMock(side_effect=AssertionError("Ollama must not be attempted")),
        ), patch.object(
            chat,
            "_generate_with_openai",
            AsyncMock(return_value="OpenAI answer [S1]."),
        ) as openai_generation:
            answer = await chat.generate_answer(messages)

        self.assertEqual(answer, "OpenAI answer [S1].")
        openai_generation.assert_awaited_once_with(messages)

    async def test_rag_stream_selects_global_openai_provider(self):
        workspace_id = uuid4()
        fake_user = SimpleNamespace(id=uuid4())
        retrieval = chat.StrictRAGResult(
            sources=[self._source(title="Ranking Notes", source_id="S1")],
            answer_status="supported",
            confidence="medium",
        )

        async def fake_openai_stream(_messages):
            yield "You wrote that BM25 helped reorder matches [S1]."

        async def unexpected_ollama_stream(_messages):
            raise AssertionError("Ollama must not be attempted")
            yield ""

        with patch.object(chat.settings, "LLM_PROVIDER", "openai"), patch.object(
            chat.settings, "LLM_USE_FALLBACK", False
        ), patch.object(chat.settings, "OPENAI_API_KEY", "test-key"), patch.object(
            chat.settings, "OLLAMA_ENABLED", False
        ), patch.object(chat, "retrieve_context", AsyncMock(return_value=retrieval)), patch.object(
            chat, "_stream_with_openai", fake_openai_stream
        ), patch.object(chat, "_stream_with_ollama", unexpected_ollama_stream):
            chunks = []
            async for chunk in chat._rag_stream(
                query="What did I write about ranking?",
                workspace_id=workspace_id,
                user=fake_user,
                db=None,
                history=None,
                top_k=6,
                threshold=0.3,
            ):
                chunks.append(chunk)

        events = self._parse_sse(chunks)
        token_text = "".join(event.get("text", "") for event in events if event["type"] == "token")
        self.assertIn("BM25 helped reorder", token_text)

    async def test_note_retrieval_query_scopes_to_owned_or_collaborated_notes(self):
        class EmptyResult:
            def scalars(self):
                return self

            def all(self):
                return []

        class FakeDB:
            statement = None

            async def execute(self, statement):
                self.statement = statement
                return EmptyResult()

        db = FakeDB()
        user = SimpleNamespace(id=uuid4(), is_superuser=False)

        await chat.retrieve_strict_note_context(
            query="What did I write about ranking?",
            workspace_id=uuid4(),
            user=user,
            db=db,
            limit=6,
        )

        statement_sql = str(db.statement)
        self.assertIn("note_collaborators", statement_sql)
        self.assertIn("notes.user_id", statement_sql)
        self.assertIn("note_collaborators.user_id", statement_sql)


if __name__ == "__main__":
    unittest.main()
