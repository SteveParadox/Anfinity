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
    def test_build_rag_prompt_requires_strict_inline_citations(self):
        sources = [
            chat.RAGSource(
                noteId=str(uuid4()),
                title="Search Notes",
                excerpt="BM25 helped reorder relevant note matches.",
                createdAt="2026-04-01T00:00:00+00:00",
                similarity=0.84,
            )
        ]

        prompt = chat.build_rag_system_prompt("What did I write about ranking?", sources)

        self.assertIn("[Note Title | YYYY-MM-DD | NN%]", prompt)
        self.assertIn("Do not answer from memory or world knowledge", prompt)
        self.assertIn("Relevance: 84%", prompt)

    async def test_rag_stream_returns_grounded_not_found_without_calling_llm(self):
        workspace_id = uuid4()
        fake_user = SimpleNamespace(id=uuid4())

        with patch.object(chat, "retrieve_context", AsyncMock(return_value=[])), patch.object(
            chat,
            "generate_answer",
            AsyncMock(side_effect=AssertionError("LLM should not be called when there are no sources")),
        ):
            events = []
            async for chunk in chat._rag_stream(
                query="What do my notes say?",
                workspace_id=workspace_id,
                user=fake_user,
                db=None,
                history=None,
                top_k=6,
                threshold=0.3,
            ):
                events.append(json.loads(chunk))

        self.assertEqual(events[0]["type"], "sources")
        self.assertEqual(events[0]["sources"], [])
        self.assertEqual(events[1]["type"], "token")
        self.assertEqual(events[1]["text"], "I couldn't find enough in your notes to answer that.")
        self.assertEqual(events[2]["type"], "done")
        self.assertEqual(events[2]["followUpQuestions"], [])


if __name__ == "__main__":
    unittest.main()
