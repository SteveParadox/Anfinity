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


if __name__ == "__main__":
    unittest.main()
