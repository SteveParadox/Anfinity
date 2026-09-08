import asyncio
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ["DEBUG"] = "false"

from app.services.graph_service import GraphService
from app.database.models import GraphEdgeType, GraphNodeType


def _make_node(node_id: str, label: str):
    return SimpleNamespace(
        id=node_id,
        label=label,
        node_type=SimpleNamespace(value="note"),
    )


class _RecordingAsyncDB:
    def __init__(self) -> None:
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return None


class _FakeScalarResult:
    def __init__(self, items):
        self.items = items

    def scalars(self):
        return self

    def all(self):
        return self.items


class _SequencedGraphDB:
    def __init__(self, results):
        self.results = list(results)

    async def execute(self, _statement):
        if not self.results:
            raise AssertionError("Unexpected graph query")
        return self.results.pop(0)


class GraphServiceGuardTests(unittest.TestCase):
    def test_prepare_clusters_for_sync_dedupes_members_and_keeps_best_assignment(self) -> None:
        service = GraphService()
        node_a = _make_node("node-a", "Alpha")
        node_b = _make_node("node-b", "Bravo")

        prepared = service._prepare_clusters_for_sync(
            node_map={
                "node-a": node_a,
                "node-b": node_b,
            },
            clusters=[
                {
                    "key": "cluster-one",
                    "members": [
                        {"node_id": "node-a", "score": 0.25, "rank": 3},
                        {"node_id": "node-a", "score": 0.55, "rank": 1},
                        {"node_id": "node-b", "score": 0.40, "rank": 2},
                    ],
                },
                {
                    "key": "cluster-two",
                    "members": [
                        {"node_id": "node-a", "score": 0.95, "rank": 0},
                        {"node_id": "missing-node", "score": 1.00, "rank": 0},
                    ],
                },
            ],
        )

        self.assertEqual(len(prepared), 2)
        self.assertEqual([member["node"].id for member in prepared[0]["members"]], ["node-b"])
        self.assertEqual([member["node"].id for member in prepared[1]["members"]], ["node-a"])
        self.assertEqual(prepared[1]["members"][0]["score"], 0.95)

    def test_prepare_clusters_for_sync_skips_invalid_payload_without_members(self) -> None:
        service = GraphService()

        prepared = service._prepare_clusters_for_sync(
            node_map={},
            clusters=[
                {
                    "key": "cluster-one",
                    "members": [{"node_id": "missing-node", "score": 0.5, "rank": 0}],
                }
            ],
        )

        self.assertEqual(prepared, [])

    def test_delete_note_derived_edges_keeps_inbound_explicit_note_links(self) -> None:
        service = GraphService()
        fake_db = _RecordingAsyncDB()

        asyncio.run(
            service._delete_note_derived_edges(
                fake_db,
                workspace_id=uuid4(),
                note_node_id=uuid4(),
            )
        )

        self.assertEqual(len(fake_db.statements), 3)

        workspace_delete_sql = str(fake_db.statements[0])
        explicit_link_delete_sql = str(fake_db.statements[1])
        related_link_delete_sql = str(fake_db.statements[2])

        self.assertIn("target_node_id", workspace_delete_sql)
        self.assertNotIn("source_node_id", workspace_delete_sql)

        self.assertIn("source_node_id", explicit_link_delete_sql)
        self.assertNotIn("target_node_id", explicit_link_delete_sql)

        self.assertIn("source_node_id", related_link_delete_sql)
        self.assertIn("target_node_id", related_link_delete_sql)

    def test_build_graph_data_applies_default_response_limits(self) -> None:
        service = GraphService()
        workspace_id = uuid4()
        node_one = SimpleNamespace(
            id=uuid4(),
            label="Alpha",
            node_type=GraphNodeType.NOTE,
            external_id=str(uuid4()),
            weight=2.0,
            node_metadata={},
        )
        node_two = SimpleNamespace(
            id=uuid4(),
            label="Bravo",
            node_type=GraphNodeType.NOTE,
            external_id=str(uuid4()),
            weight=1.0,
            node_metadata={},
        )
        edge_one = SimpleNamespace(
            id=uuid4(),
            source_node_id=node_one.id,
            target_node_id=node_one.id,
            edge_type=GraphEdgeType.NOTE_RELATED_NOTE,
            weight=0.9,
            edge_metadata={},
        )
        edge_two = SimpleNamespace(
            id=uuid4(),
            source_node_id=node_one.id,
            target_node_id=node_one.id,
            edge_type=GraphEdgeType.NOTE_RELATED_NOTE,
            weight=0.8,
            edge_metadata={},
        )
        fake_db = _SequencedGraphDB(
            [
                _FakeScalarResult([node_one, node_two]),
                _FakeScalarResult([edge_one, edge_two]),
            ]
        )

        async def _noop_seed(_db, _workspace_id):
            return None

        async def _empty_cluster_payload(_db, _workspace_id, _node_ids):
            return {"clusters": [], "node_metadata": {}}

        service.ensure_workspace_graph_seeded = _noop_seed
        service._build_cluster_payload = _empty_cluster_payload

        graph = asyncio.run(
            service.build_graph_data(
                db=fake_db,
                workspace_id=workspace_id,
                filters={"node_limit": 1, "edge_limit": 1},
            )
        )

        self.assertEqual(len(graph["nodes"]), 1)
        self.assertEqual(len(graph["edges"]), 1)
        self.assertTrue(graph["stats"]["limited"])
        self.assertEqual(graph["stats"]["node_limit"], 1)
        self.assertEqual(graph["stats"]["edge_limit"], 1)


if __name__ == "__main__":
    unittest.main()
