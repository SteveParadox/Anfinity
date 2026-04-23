import asyncio
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.database.models import SourceType  # noqa: E402
from app.ingestion.embedding_batch_processor import BatchEmbeddingProcessor  # noqa: E402
from app.services.vector_db import VectorDBClient  # noqa: E402
from app.tasks import worker  # noqa: E402


class _FakeAsyncDB:
    def __init__(self):
        self.rollback_count = 0

    async def rollback(self):
        self.rollback_count += 1


class _FakeEmbeddingProvider:
    model_name = "fake-embedder"
    dimension = 128

    def embed(self, texts):
        return [[float(i + 1) for i in range(self.dimension)] for _ in texts]


class _FakeBatchVectorDB:
    def __init__(self):
        self.collections = []
        self.upserts = []
        self.deletes = []

    def create_collection(self, collection_name, embedding_dim=None):
        self.collections.append((collection_name, embedding_dim))
        return True

    def upsert_vectors(self, collection_name, points):
        self.upserts.append((collection_name, points))
        return True

    def delete_points(self, collection_name, ids):
        self.deletes.append((collection_name, list(ids)))
        return True


class _FakeWorkerDB:
    def __init__(self, commit_error: Exception | None = None):
        self.commit_error = commit_error
        self.added = []
        self.commit_count = 0
        self.rollback_count = 0

    def add(self, value):
        self.added.append(value)

    def commit(self):
        self.commit_count += 1
        if self.commit_error is not None:
            raise self.commit_error

    def rollback(self):
        self.rollback_count += 1


class _FakeWorkerVectorDB:
    def __init__(self):
        self.created = []
        self.upserts = []
        self.deletes = []

    def create_collection(self, collection_name, embedding_dim=None):
        self.created.append((collection_name, embedding_dim))
        return True

    def upsert_vectors(self, collection_name, points):
        self.upserts.append((collection_name, points))
        return True

    def delete_points(self, collection_name, ids):
        self.deletes.append((collection_name, list(ids)))
        return True


class _FakeWorkerEmbedder:
    def __init__(self, provider=None):
        self.provider = provider
        self.model_name = "fake-worker-embedder"
        self.dimension = 128

    def embed(self, texts):
        return [[float(i + 1) for i in range(self.dimension)] for _ in texts]


class VectorCleanupHardeningTests(unittest.TestCase):
    def test_vector_db_delete_points_reconnects_after_transient_connection_error(self):
        client = VectorDBClient(require_qdrant=False, embedding_dim=128)

        class _FlakyClient:
            def __init__(self):
                self.calls = 0

            def delete(self, collection_name=None, points_selector=None):
                self.calls += 1
                raise RuntimeError("connection reset by peer")

        class _RecoveredClient:
            def __init__(self):
                self.deleted = []

            def delete(self, collection_name=None, points_selector=None):
                self.deleted.append((collection_name, points_selector))

        flaky = _FlakyClient()
        recovered = _RecoveredClient()
        reconnects = []

        def _reconnect():
            reconnects.append("attempted")
            client.client = recovered
            client.is_connected = True
            return True

        client.client = flaky
        client.is_connected = True
        client._attempt_connect = _reconnect

        deleted = client.delete_points("workspace-a", ["vec-1", "vec-1", "vec-2"])

        self.assertTrue(deleted)
        self.assertEqual(flaky.calls, 1)
        self.assertEqual(reconnects, ["attempted"])
        self.assertEqual(
            recovered.deleted,
            [("workspace-a", ["vec-1", "vec-2"])],
        )

    def test_worker_cleanup_schedules_retry_when_immediate_delete_fails(self):
        fake_vector_db = SimpleNamespace(delete_points=lambda collection_name, ids: False)

        with (
            mock.patch.object(worker, "get_vector_db_client", return_value=fake_vector_db),
            mock.patch.object(worker.delete_vector_ids, "delay") as delayed_cleanup,
        ):
            cleaned = worker._cleanup_vector_ids(
                "workspace-a",
                ["vec-1", "vec-1", "vec-2"],
                reason="unit-test cleanup failure",
            )

        self.assertFalse(cleaned)
        delayed_cleanup.assert_called_once_with(
            "workspace-a",
            ["vec-1", "vec-2"],
            reason="unit-test cleanup failure",
        )

    def test_worker_index_vectors_rolls_back_and_cleans_up_on_commit_failure(self):
        fake_db = _FakeWorkerDB(commit_error=RuntimeError("sql commit failed"))
        fake_vector_db = _FakeWorkerVectorDB()
        document = SimpleNamespace(
            workspace_id=uuid4(),
            title="Cleanup Test",
            source_type=SourceType.UPLOAD,
        )
        chunks = [
            SimpleNamespace(
                text="Chunk text",
                index=0,
                token_count=4,
                context_before=None,
                context_after=None,
                metadata={"section_title": "Intro"},
            )
        ]
        db_chunks = [SimpleNamespace(id=uuid4(), chunk_status=None)]

        with (
            mock.patch.object(worker, "Embedder", _FakeWorkerEmbedder),
            mock.patch.object(worker, "get_vector_db_client", return_value=fake_vector_db),
        ):
            with self.assertRaises(RuntimeError) as exc:
                worker._index_vectors(
                    fake_db,
                    document,
                    "doc-1",
                    chunks,
                    db_chunks,
                    {},
                )

        self.assertIn("Failed to persist embedding metadata", str(exc.exception))
        self.assertEqual(fake_db.rollback_count, 1)
        self.assertEqual(len(fake_vector_db.upserts), 1)
        self.assertEqual(
            fake_vector_db.deletes,
            [(str(document.workspace_id), [str(db_chunks[0].id)])],
        )

    def test_batch_processor_cleans_up_vectors_when_metadata_persistence_fails(self):
        fake_db = _FakeAsyncDB()
        fake_vector_db = _FakeBatchVectorDB()
        processor = BatchEmbeddingProcessor(
            db=fake_db,
            embedding_provider=_FakeEmbeddingProvider(),
            vector_db=fake_vector_db,
            batch_size=10,
        )
        document = SimpleNamespace(
            id=uuid4(),
            title="Batch Cleanup",
            source_type=SourceType.UPLOAD,
        )
        batch_chunks = [
            SimpleNamespace(
                id=uuid4(),
                text="Chunk one",
                chunk_index=0,
                token_count=2,
                context_before=None,
                context_after=None,
                chunk_metadata={},
                created_at=datetime.now(timezone.utc),
            ),
            SimpleNamespace(
                id=uuid4(),
                text="Chunk two",
                chunk_index=1,
                token_count=2,
                context_before=None,
                context_after=None,
                chunk_metadata={},
                created_at=datetime.now(timezone.utc),
            ),
        ]

        async def _fail_save(_records):
            raise RuntimeError("embedding metadata flush failed")

        processor._save_embedding_metadata = _fail_save

        result = asyncio.run(
            processor._process_batch(
                batch_chunks=batch_chunks,
                document=document,
                workspace_id=uuid4(),
                collection_name="workspace-batch",
            )
        )

        self.assertEqual(result["processed"], 0)
        self.assertEqual(result["failed"], len(batch_chunks))
        self.assertTrue(
            any("Failed to persist embedding metadata" in error for error in result["errors"])
        )
        self.assertEqual(fake_db.rollback_count, 1)
        self.assertEqual(
            fake_vector_db.deletes,
            [
                (
                    "workspace-batch",
                    [str(batch_chunks[0].id), str(batch_chunks[1].id)],
                )
            ],
        )


if __name__ == "__main__":
    unittest.main()
