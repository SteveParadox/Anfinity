import asyncio
import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("DEBUG", "false")

from app.database.models import Chunk, Document, SourceType  # noqa: E402
from app.ingestion.chunker import Chunker  # noqa: E402
from app.ingestion.embedding_batch_processor import BatchEmbeddingProcessor  # noqa: E402
from app.ingestion.parsers.text import TextParser  # noqa: E402
from app.api.query import _hydrate_answer_chunks  # noqa: E402


class _FakeAsyncResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class _FakeAsyncDB:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.executed = []
        self.flush_count = 0

    async def execute(self, stmt, *args, **kwargs):
        self.executed.append(str(stmt))
        return _FakeAsyncResult(self.rows)

    async def flush(self):
        self.flush_count += 1

    async def commit(self):
        return None

    async def rollback(self):
        return None


class _FakeEmbeddingProvider:
    model_name = "fake-embedder"
    dimension = 4

    def __init__(self):
        self.calls = []

    def embed(self, texts):
        self.calls.append(list(texts))
        return [
            [float(index + 1), 0.1, 0.2, 0.3]
            for index, _ in enumerate(texts)
        ]


class _FakeVectorDB:
    def __init__(self):
        self.collections = []
        self.upserts = []

    def create_collection(self, collection_name, embedding_dim=None):
        self.collections.append((collection_name, embedding_dim))
        return True

    def upsert_vectors(self, collection_name, points):
        self.upserts.append((collection_name, points))
        return True


class RagPipelineAuditTests(unittest.TestCase):
    def test_markdown_chunking_preserves_structure_and_metadata(self):
        parser = TextParser()
        parsed = parser.parse(
            b"---\ntitle: RAG Notes\nauthor: Ada\n---\n\n# Intro\n\n"
            b"Chunking preserves headings.\n\n"
            b"## Details\n\n"
            b"Embeddings must be created per chunk.\n\n"
            b"### Retrieval\n\n"
            b"Hydration should use full chunk text."
        )

        self.assertEqual(parsed.title, "RAG Notes")
        self.assertIn("# Intro", parsed.text)
        self.assertIn("## Details", parsed.text)
        self.assertIn("\n\n", parsed.text)

        chunker = Chunker(chunk_size=12, chunk_overlap=4)
        chunks = chunker.chunk_text(parsed.text, metadata=parsed.metadata)

        self.assertGreaterEqual(len(chunks), 3)
        self.assertEqual([chunk.index for chunk in chunks], list(range(len(chunks))))
        self.assertTrue(all(chunk.metadata.get("author") == "Ada" for chunk in chunks))
        self.assertTrue(any(chunk.context_after for chunk in chunks[:-1]))

    def test_batch_embedding_processor_embeds_each_chunk_and_stores_full_payload(self):
        provider = _FakeEmbeddingProvider()
        vector_db = _FakeVectorDB()
        db = _FakeAsyncDB()
        processor = BatchEmbeddingProcessor(
            db=db,
            embedding_provider=provider,
            vector_db=vector_db,
            batch_size=10,
        )

        document = SimpleNamespace(
            id=uuid4(),
            title="RAG Guide",
            source_type=SourceType.UPLOAD,
        )
        batch_chunks = [
            SimpleNamespace(
                id=uuid4(),
                text="Chunk one explains embeddings.",
                chunk_index=0,
                token_count=5,
                context_before=None,
                context_after="Chunk two explains retrieval.",
                chunk_metadata={"section": "intro"},
                created_at=datetime.now(timezone.utc),
            ),
            SimpleNamespace(
                id=uuid4(),
                text="Chunk two explains retrieval.",
                chunk_index=1,
                token_count=5,
                context_before="Chunk one explains embeddings.",
                context_after=None,
                chunk_metadata={"section": "retrieval"},
                created_at=datetime.now(timezone.utc),
            ),
        ]

        result = asyncio.run(
            processor._process_batch(
                batch_chunks=batch_chunks,
                document=document,
                workspace_id=uuid4(),
                collection_name="workspace-1",
            )
        )

        self.assertEqual(result["processed"], 2)
        self.assertEqual(result["failed"], 0)
        self.assertEqual(provider.calls, [[chunk.text for chunk in batch_chunks]])
        self.assertEqual(vector_db.collections[-1], ("workspace-1", 4))
        upsert_points = vector_db.upserts[-1][1]
        self.assertEqual(len(upsert_points), 2)
        self.assertEqual(upsert_points[0]["payload"]["chunk_text"], batch_chunks[0].text)
        self.assertEqual(upsert_points[0]["payload"]["document_title"], "RAG Guide")
        self.assertEqual(upsert_points[0]["payload"]["metadata"]["section"], "intro")

    def test_answer_chunk_hydration_uses_sql_text_instead_of_sparse_vector_payload(self):
        chunk_id = uuid4()
        document_id = uuid4()
        db_chunk = Chunk(
            id=chunk_id,
            document_id=document_id,
            chunk_index=3,
            text="Full chunk text from SQL storage.",
            token_count=7,
            context_before="Prior context",
            context_after="Next context",
            chunk_metadata={"section": "sql"},
        )
        db_chunk.created_at = datetime(2026, 4, 20, tzinfo=timezone.utc)
        db_document = Document(
            id=document_id,
            workspace_id=uuid4(),
            title="Hydrated Document",
            source_type=SourceType.UPLOAD,
        )
        fake_db = _FakeAsyncDB(rows=[(db_chunk, db_document)])

        hydrated = asyncio.run(
            _hydrate_answer_chunks(
                fake_db,
                [
                    SimpleNamespace(
                        chunk_id=str(chunk_id),
                        document_id=str(document_id),
                        similarity=0.91,
                        text="preview only",
                        source_type="upload",
                        chunk_index=0,
                        document_title="Preview title",
                        token_count=1,
                        context_before=None,
                        context_after=None,
                        metadata={"source_kind": "document"},
                    )
                ],
            )
        )

        self.assertEqual(len(hydrated), 1)
        self.assertEqual(hydrated[0].text, "Full chunk text from SQL storage.")
        self.assertEqual(hydrated[0].document_title, "Hydrated Document")
        self.assertEqual(hydrated[0].chunk_index, 3)
        self.assertEqual(hydrated[0].context_before, "Prior context")
        self.assertEqual(hydrated[0].metadata["section"], "sql")


if __name__ == "__main__":
    unittest.main()
