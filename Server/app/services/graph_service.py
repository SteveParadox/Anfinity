"""Knowledge graph persistence and sync helpers."""

from __future__ import annotations

import itertools
import logging
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional
from uuid import UUID

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import GraphEdge, GraphEdgeType, GraphNode, GraphNodeType, Note

logger = logging.getLogger(__name__)

ENTITY_PATTERN = re.compile(r"\b[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+){0,2}\b")
INLINE_TAG_PATTERN = re.compile(r"#([a-zA-Z0-9_-]{2,50})")

SYMMETRIC_EDGE_TYPES = {
    GraphEdgeType.NOTE_RELATED_NOTE,
    GraphEdgeType.ENTITY_CO_OCCURS_WITH_ENTITY,
    GraphEdgeType.TAG_CO_OCCURS_WITH_TAG,
}


def _normalize_label(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip()).lower()


def _dedupe(items: Iterable[str]) -> List[str]:
    seen = set()
    result: List[str] = []
    for item in items:
        normalized = _normalize_label(item)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(item.strip())
    return result


def extract_entities_from_note(title: str, content: str) -> List[str]:
    text = f"{title}\n{content}"
    matches = ENTITY_PATTERN.findall(text)
    filtered = [
        match
        for match in matches
        if len(match) >= 3 and match.split()[0] not in {"The", "This", "That", "These", "Those", "And"}
    ]
    return _dedupe(filtered)[:25]


def extract_tags_from_note(note: Note) -> List[str]:
    inline_tags = [match.strip() for match in INLINE_TAG_PATTERN.findall(note.content or "")]
    explicit_tags = [str(tag).strip() for tag in (note.tags or []) if str(tag).strip()]
    return _dedupe([*explicit_tags, *inline_tags])[:25]


class GraphService:
    """Persisted graph builder and note sync service."""

    async def build_graph_data(
        self,
        db: AsyncSession,
        workspace_id: UUID,
        filters: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        filters = filters or {}
        node_types = set(filters.get("node_types") or [])
        edge_types = set(filters.get("edge_types") or [])
        search = _normalize_label(filters.get("search") or "")
        min_weight = float(filters.get("min_weight") or 0)
        include_isolated = bool(filters.get("include_isolated", True))

        node_query = select(GraphNode).where(GraphNode.workspace_id == workspace_id)
        if node_types:
            node_query = node_query.where(GraphNode.node_type.in_(list(node_types)))
        if search:
            node_query = node_query.where(GraphNode.normalized_label.contains(search))

        node_result = await db.execute(node_query)
        nodes = list(node_result.scalars().all())
        node_map = {node.id: node for node in nodes}
        if not node_map:
            return {
                "nodes": [],
                "edges": [],
                "stats": {
                    "total_nodes": 0,
                    "total_edges": 0,
                    "node_types": {},
                    "edge_types": {},
                },
            }

        edge_query = select(GraphEdge).where(
            GraphEdge.workspace_id == workspace_id,
            GraphEdge.weight >= min_weight,
            GraphEdge.source_node_id.in_(list(node_map.keys())),
            GraphEdge.target_node_id.in_(list(node_map.keys())),
        )
        if edge_types:
            edge_query = edge_query.where(GraphEdge.edge_type.in_(list(edge_types)))

        edge_result = await db.execute(edge_query)
        edges = list(edge_result.scalars().all())

        note_ids_by_node: dict[UUID, set[str]] = defaultdict(set)
        for edge in edges:
            source_node = node_map.get(edge.source_node_id)
            target_node = node_map.get(edge.target_node_id)
            if source_node and source_node.node_type == GraphNodeType.NOTE:
                note_ids_by_node[source_node.id].add(str(source_node.external_id))
                note_ids_by_node[target_node.id].add(str(source_node.external_id))
            if target_node and target_node.node_type == GraphNodeType.NOTE:
                note_ids_by_node[target_node.id].add(str(target_node.external_id))
                note_ids_by_node[source_node.id].add(str(target_node.external_id))

        if not include_isolated:
            connected_node_ids = {edge.source_node_id for edge in edges} | {edge.target_node_id for edge in edges}
            nodes = [node for node in nodes if node.id in connected_node_ids]
            node_map = {node.id: node for node in nodes}
            edges = [
                edge
                for edge in edges
                if edge.source_node_id in node_map and edge.target_node_id in node_map
            ]

        node_type_counts = Counter(node.node_type.value for node in nodes)
        edge_type_counts = Counter(edge.edge_type.value for edge in edges)

        return {
            "nodes": [
                {
                    "id": str(node.id),
                    "type": node.node_type.value,
                    "label": node.label,
                    "value": node.weight,
                    "metadata": {
                        **(node.node_metadata or {}),
                        "note_ids": sorted(note_ids_by_node.get(node.id, set())),
                    },
                }
                for node in nodes
            ],
            "edges": [
                {
                    "id": str(edge.id),
                    "source": str(edge.source_node_id),
                    "target": str(edge.target_node_id),
                    "type": edge.edge_type.value,
                    "weight": edge.weight,
                    "metadata": edge.edge_metadata or {},
                }
                for edge in edges
            ],
            "stats": {
                "total_nodes": len(nodes),
                "total_edges": len(edges),
                "node_types": dict(node_type_counts),
                "edge_types": dict(edge_type_counts),
            },
        }

    async def sync_note_to_graph(self, db: AsyncSession, note: Note) -> None:
        if not note.workspace_id:
            return

        workspace_id = note.workspace_id
        workspace_node = await self._upsert_node(
            db,
            workspace_id=workspace_id,
            node_type=GraphNodeType.WORKSPACE,
            external_id=str(workspace_id),
            label="Workspace",
            weight=5.0,
            metadata={"workspace_id": str(workspace_id)},
        )
        note_node = await self._upsert_node(
            db,
            workspace_id=workspace_id,
            node_type=GraphNodeType.NOTE,
            external_id=str(note.id),
            label=note.title or "Untitled Note",
            weight=max(2.0, 1.0 + min((note.word_count or 0) / 100.0, 8.0)),
            metadata={
                "note_id": str(note.id),
                "note_type": note.note_type,
                "tags": note.tags or [],
                "updated_at": note.updated_at.isoformat() if note.updated_at else None,
            },
        )

        await self._delete_note_derived_edges(db, workspace_id, note_node.id)
        await self._upsert_edge(
            db,
            workspace_id=workspace_id,
            edge_type=GraphEdgeType.WORKSPACE_CONTAINS_NOTE,
            source_node_id=workspace_node.id,
            target_node_id=note_node.id,
            weight=1.0,
            metadata={},
        )

        entity_nodes = []
        for entity in extract_entities_from_note(note.title, note.content):
            entity_node = await self._upsert_node(
                db,
                workspace_id=workspace_id,
                node_type=GraphNodeType.ENTITY,
                external_id=_normalize_label(entity),
                label=entity,
                weight=1.0,
                metadata={"entity_type": "extracted"},
            )
            entity_nodes.append(entity_node)
            await self._upsert_edge(
                db,
                workspace_id=workspace_id,
                edge_type=GraphEdgeType.NOTE_MENTIONS_ENTITY,
                source_node_id=note_node.id,
                target_node_id=entity_node.id,
                weight=1.0,
                metadata={},
            )

        tag_nodes = []
        for tag in extract_tags_from_note(note):
            tag_node = await self._upsert_node(
                db,
                workspace_id=workspace_id,
                node_type=GraphNodeType.TAG,
                external_id=_normalize_label(tag),
                label=tag,
                weight=1.0,
                metadata={"tag_source": "note"},
            )
            tag_nodes.append(tag_node)
            await self._upsert_edge(
                db,
                workspace_id=workspace_id,
                edge_type=GraphEdgeType.NOTE_HAS_TAG,
                source_node_id=note_node.id,
                target_node_id=tag_node.id,
                weight=1.0,
                metadata={},
            )

        if note.connections:
            connection_ids: List[UUID] = []
            for connection_id in note.connections:
                try:
                    connection_ids.append(UUID(str(connection_id)))
                except (TypeError, ValueError):
                    logger.debug("Skipping invalid note connection id %s for note %s", connection_id, note.id)

            connected_notes = await db.execute(
                select(Note).where(
                    Note.workspace_id == workspace_id,
                    Note.id.in_(connection_ids or [UUID(int=0)]),
                )
            )
            for connected_note in connected_notes.scalars().all():
                connected_note_node = await self._upsert_node(
                    db,
                    workspace_id=workspace_id,
                    node_type=GraphNodeType.NOTE,
                    external_id=str(connected_note.id),
                    label=connected_note.title or "Untitled Note",
                    weight=max(2.0, 1.0 + min((connected_note.word_count or 0) / 100.0, 8.0)),
                    metadata={
                        "note_id": str(connected_note.id),
                        "note_type": connected_note.note_type,
                        "tags": connected_note.tags or [],
                    },
                )
                await self._upsert_edge(
                    db,
                    workspace_id=workspace_id,
                    edge_type=GraphEdgeType.NOTE_LINKS_NOTE,
                    source_node_id=note_node.id,
                    target_node_id=connected_note_node.id,
                    weight=1.0,
                    metadata={},
                )

        await self._upsert_pair_edges(
            db,
            workspace_id=workspace_id,
            edge_type=GraphEdgeType.ENTITY_CO_OCCURS_WITH_ENTITY,
            nodes=entity_nodes,
        )
        await self._upsert_pair_edges(
            db,
            workspace_id=workspace_id,
            edge_type=GraphEdgeType.TAG_CO_OCCURS_WITH_TAG,
            nodes=tag_nodes,
        )
        await self._sync_related_note_edges(db, workspace_id, note, note_node.id, entity_nodes, tag_nodes)
        await self._cleanup_orphan_nodes(db, workspace_id)
        await db.commit()

    async def remove_note_from_graph(self, db: AsyncSession, workspace_id: UUID, note_id: UUID) -> None:
        note_node = await db.execute(
            select(GraphNode).where(
                GraphNode.workspace_id == workspace_id,
                GraphNode.node_type == GraphNodeType.NOTE,
                GraphNode.external_id == str(note_id),
            )
        )
        node = note_node.scalar_one_or_none()
        if node is None:
            return

        await db.execute(delete(GraphNode).where(GraphNode.id == node.id))
        await self._cleanup_orphan_nodes(db, workspace_id)
        await db.commit()

    async def _sync_related_note_edges(
        self,
        db: AsyncSession,
        workspace_id: UUID,
        note: Note,
        note_node_id: UUID,
        entity_nodes: List[GraphNode],
        tag_nodes: List[GraphNode],
    ) -> None:
        related_note_counts: Counter[UUID] = Counter()

        note_link_result = await db.execute(
            select(GraphEdge).where(
                GraphEdge.workspace_id == workspace_id,
                GraphEdge.edge_type.in_([GraphEdgeType.NOTE_MENTIONS_ENTITY, GraphEdgeType.NOTE_HAS_TAG]),
                GraphEdge.target_node_id.in_([node.id for node in [*entity_nodes, *tag_nodes]] or [UUID(int=0)]),
            )
        )
        current_note_id = str(note.id)
        for edge in note_link_result.scalars().all():
            if edge.source_node_id != note_node_id:
                related_note_counts[edge.source_node_id] += 1

        if related_note_counts:
            note_nodes = await db.execute(
                select(GraphNode).where(
                    GraphNode.workspace_id == workspace_id,
                    GraphNode.id.in_(list(related_note_counts.keys())),
                )
            )
            node_by_id = {node.id: node for node in note_nodes.scalars().all()}
            for other_node_id, shared_count in related_note_counts.items():
                other_node = node_by_id.get(other_node_id)
                if other_node is None or other_node.external_id == current_note_id:
                    continue
                await self._upsert_edge(
                    db,
                    workspace_id=workspace_id,
                    edge_type=GraphEdgeType.NOTE_RELATED_NOTE,
                    source_node_id=note_node_id,
                    target_node_id=other_node.id,
                    weight=min(1.0, 0.25 * shared_count),
                    metadata={"shared_signals": shared_count},
                )

    async def _delete_note_derived_edges(self, db: AsyncSession, workspace_id: UUID, note_node_id: UUID) -> None:
        await db.execute(
            delete(GraphEdge).where(
                GraphEdge.workspace_id == workspace_id,
                or_(
                    GraphEdge.source_node_id == note_node_id,
                    GraphEdge.target_node_id == note_node_id,
                ),
                GraphEdge.edge_type.in_(
                    [
                        GraphEdgeType.WORKSPACE_CONTAINS_NOTE,
                        GraphEdgeType.NOTE_MENTIONS_ENTITY,
                        GraphEdgeType.NOTE_HAS_TAG,
                        GraphEdgeType.NOTE_LINKS_NOTE,
                        GraphEdgeType.NOTE_RELATED_NOTE,
                    ]
                ),
            )
        )

    async def _cleanup_orphan_nodes(self, db: AsyncSession, workspace_id: UUID) -> None:
        candidate_nodes = (
            select(GraphNode.id)
            .where(
                GraphNode.workspace_id == workspace_id,
                GraphNode.node_type.in_([GraphNodeType.ENTITY, GraphNodeType.TAG]),
            )
            .subquery()
        )
        connected_nodes = (
            select(GraphEdge.source_node_id.label("node_id"))
            .where(GraphEdge.workspace_id == workspace_id)
            .union(
                select(GraphEdge.target_node_id.label("node_id")).where(GraphEdge.workspace_id == workspace_id)
            )
            .subquery()
        )
        orphan_node_ids = (
            select(candidate_nodes.c.id)
            .outerjoin(connected_nodes, connected_nodes.c.node_id == candidate_nodes.c.id)
            .where(connected_nodes.c.node_id.is_(None))
        )
        await db.execute(delete(GraphNode).where(GraphNode.id.in_(orphan_node_ids)))

    async def _upsert_pair_edges(
        self,
        db: AsyncSession,
        workspace_id: UUID,
        edge_type: GraphEdgeType,
        nodes: List[GraphNode],
    ) -> None:
        for left_node, right_node in itertools.combinations(nodes, 2):
            await self._upsert_edge(
                db,
                workspace_id=workspace_id,
                edge_type=edge_type,
                source_node_id=left_node.id,
                target_node_id=right_node.id,
                weight=1.0,
                metadata={},
            )

    async def _upsert_node(
        self,
        db: AsyncSession,
        workspace_id: UUID,
        node_type: GraphNodeType,
        external_id: str,
        label: str,
        weight: float,
        metadata: Dict[str, Any],
    ) -> GraphNode:
        result = await db.execute(
            select(GraphNode).where(
                GraphNode.workspace_id == workspace_id,
                GraphNode.node_type == node_type,
                GraphNode.external_id == external_id,
            )
        )
        node = result.scalar_one_or_none()
        if node is None:
            node = GraphNode(
                workspace_id=workspace_id,
                node_type=node_type,
                external_id=external_id,
                label=label,
                normalized_label=_normalize_label(label),
                weight=weight,
                node_metadata=metadata,
            )
            db.add(node)
            await db.flush()
            return node

        node.label = label
        node.normalized_label = _normalize_label(label)
        node.weight = weight
        node.node_metadata = metadata
        node.updated_at = datetime.now(timezone.utc)
        await db.flush()
        return node

    async def _upsert_edge(
        self,
        db: AsyncSession,
        workspace_id: UUID,
        edge_type: GraphEdgeType,
        source_node_id: UUID,
        target_node_id: UUID,
        weight: float,
        metadata: Dict[str, Any],
    ) -> GraphEdge:
        if edge_type in SYMMETRIC_EDGE_TYPES and str(source_node_id) > str(target_node_id):
            source_node_id, target_node_id = target_node_id, source_node_id

        result = await db.execute(
            select(GraphEdge).where(
                GraphEdge.workspace_id == workspace_id,
                GraphEdge.edge_type == edge_type,
                GraphEdge.source_node_id == source_node_id,
                GraphEdge.target_node_id == target_node_id,
            )
        )
        edge = result.scalar_one_or_none()
        if edge is None:
            edge = GraphEdge(
                workspace_id=workspace_id,
                edge_type=edge_type,
                source_node_id=source_node_id,
                target_node_id=target_node_id,
                weight=weight,
                edge_metadata=metadata,
            )
            db.add(edge)
            await db.flush()
            return edge

        edge.weight = weight
        edge.edge_metadata = metadata
        edge.updated_at = datetime.now(timezone.utc)
        await db.flush()
        return edge

    async def buildGraphData(
        self,
        db: AsyncSession,
        workspace_id: UUID,
        filters: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Compatibility wrapper for callers that expect camelCase naming."""
        return await self.build_graph_data(db=db, workspace_id=workspace_id, filters=filters)

    async def syncNoteToGraph(self, db: AsyncSession, note: Note) -> None:
        """Compatibility wrapper for callers that expect camelCase naming."""
        await self.sync_note_to_graph(db=db, note=note)


_graph_service: Optional[GraphService] = None


def get_graph_service() -> GraphService:
    global _graph_service
    if _graph_service is None:
        _graph_service = GraphService()
    return _graph_service


async def buildGraphData(
    db: AsyncSession,
    workspace_id: UUID,
    filters: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Module-level compatibility wrapper around the graph builder."""
    return await get_graph_service().build_graph_data(db=db, workspace_id=workspace_id, filters=filters)


async def syncNoteToGraph(db: AsyncSession, note: Note) -> None:
    """Module-level compatibility wrapper around note graph sync."""
    await get_graph_service().sync_note_to_graph(db=db, note=note)
