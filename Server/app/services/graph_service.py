"""Knowledge graph persistence, clustering, and sync helpers."""

from __future__ import annotations

import itertools
import json
import logging
import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence
from uuid import UUID

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import (
    GraphCluster,
    GraphClusterMembership,
    GraphEdge,
    GraphEdgeType,
    GraphNode,
    GraphNodeType,
    Note,
    Workspace,
)

logger = logging.getLogger(__name__)

ENTITY_PATTERN = re.compile(r"\b[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+){0,2}\b")
INLINE_TAG_PATTERN = re.compile(r"#([a-zA-Z0-9_-]{2,50})")
CLUSTER_LABEL_MAX_WORDS = 4
CLUSTER_FALLBACK_STOPWORDS = {
    "and",
    "for",
    "from",
    "into",
    "note",
    "notes",
    "tag",
    "tags",
    "entity",
    "entities",
    "the",
    "with",
    "your",
}

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


def _slugify(value: str, fallback: str = "cluster") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", _normalize_label(value))
    slug = slug.strip("-")
    return slug or fallback


def _parse_embedding(raw_embedding: Any) -> Optional[List[float]]:
    if raw_embedding in (None, "", []):
        return None
    if isinstance(raw_embedding, list):
        try:
            vector = [float(value) for value in raw_embedding]
        except (TypeError, ValueError):
            return None
        return vector or None
    if isinstance(raw_embedding, str):
        try:
            parsed = json.loads(raw_embedding)
        except json.JSONDecodeError:
            return None
        return _parse_embedding(parsed)
    return None


def _average_vectors(vectors: Sequence[Sequence[float]]) -> Optional[List[float]]:
    if not vectors:
        return None
    length = len(vectors[0])
    if length == 0 or any(len(vector) != length for vector in vectors):
        return None
    totals = [0.0] * length
    for vector in vectors:
        for index, value in enumerate(vector):
            totals[index] += float(value)
    divisor = float(len(vectors))
    averaged = [value / divisor for value in totals]
    magnitude = math.sqrt(sum(value * value for value in averaged))
    if magnitude <= 0:
        return None
    return [value / magnitude for value in averaged]


def _cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(float(a) * float(b) for a, b in zip(left, right))
    left_norm = math.sqrt(sum(float(value) * float(value) for value in left))
    right_norm = math.sqrt(sum(float(value) * float(value) for value in right))
    if left_norm <= 0 or right_norm <= 0:
        return 0.0
    similarity = dot / (left_norm * right_norm)
    return max(-1.0, min(1.0, similarity))


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
    """Persisted graph builder, clustering service, and note sync helper."""

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
                "clusters": [],
                "stats": {
                    "total_nodes": 0,
                    "total_edges": 0,
                    "total_clusters": 0,
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
                note_ids_by_node[edge.target_node_id].add(str(source_node.external_id))
            if target_node and target_node.node_type == GraphNodeType.NOTE:
                note_ids_by_node[target_node.id].add(str(target_node.external_id))
                note_ids_by_node[edge.source_node_id].add(str(target_node.external_id))

        if not include_isolated:
            connected_node_ids = {edge.source_node_id for edge in edges} | {edge.target_node_id for edge in edges}
            nodes = [node for node in nodes if node.id in connected_node_ids]
            node_map = {node.id: node for node in nodes}
            edges = [edge for edge in edges if edge.source_node_id in node_map and edge.target_node_id in node_map]

        cluster_payload = await self._build_cluster_payload(db, workspace_id, set(node_map.keys()))
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
                        **cluster_payload["node_metadata"].get(node.id, {}),
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
            "clusters": cluster_payload["clusters"],
            "stats": {
                "total_nodes": len(nodes),
                "total_edges": len(edges),
                "total_clusters": len(cluster_payload["clusters"]),
                "node_types": dict(node_type_counts),
                "edge_types": dict(edge_type_counts),
            },
        }

    async def list_workspace_ids(self, db: AsyncSession) -> List[UUID]:
        result = await db.execute(select(Workspace.id).order_by(Workspace.created_at.asc()))
        return list(result.scalars().all())

    async def build_cluster_input(self, db: AsyncSession, workspace_id: UUID) -> Dict[str, Any]:
        node_result = await db.execute(select(GraphNode).where(GraphNode.workspace_id == workspace_id))
        nodes = list(node_result.scalars().all())
        if not nodes:
            return {
                "workspace_id": str(workspace_id),
                "nodes": [],
                "stats": {"total_nodes": 0, "embeddable_nodes": 0, "embedding_dimension": 0},
            }

        node_map = {node.id: node for node in nodes}
        edge_result = await db.execute(select(GraphEdge).where(GraphEdge.workspace_id == workspace_id))
        edges = list(edge_result.scalars().all())
        note_vectors = await self._load_note_embeddings_by_graph_node(db, workspace_id, nodes)
        derived_vectors: dict[UUID, list[list[float]]] = defaultdict(list)

        for edge in edges:
            source_node = node_map.get(edge.source_node_id)
            target_node = node_map.get(edge.target_node_id)
            if source_node and target_node:
                source_note_vector = note_vectors.get(source_node.id)
                target_note_vector = note_vectors.get(target_node.id)
                if source_node.node_type == GraphNodeType.NOTE and source_note_vector and target_node.node_type != GraphNodeType.NOTE:
                    derived_vectors[target_node.id].append(source_note_vector)
                if target_node.node_type == GraphNodeType.NOTE and target_note_vector and source_node.node_type != GraphNodeType.NOTE:
                    derived_vectors[source_node.id].append(target_note_vector)

        all_note_vectors = list(note_vectors.values())
        embeddable_nodes = []
        embedding_dimension = 0
        for node in nodes:
            vector = note_vectors.get(node.id)
            if vector is None and node.node_type in {GraphNodeType.ENTITY, GraphNodeType.TAG}:
                vector = _average_vectors(derived_vectors.get(node.id, []))
            elif vector is None and node.node_type == GraphNodeType.WORKSPACE:
                vector = _average_vectors(all_note_vectors)

            if not vector:
                continue

            if not embedding_dimension:
                embedding_dimension = len(vector)
            embeddable_nodes.append(
                {
                    "id": str(node.id),
                    "type": node.node_type.value,
                    "label": node.label,
                    "value": float(node.weight or 1.0),
                    "metadata": node.node_metadata or {},
                    "embedding": vector,
                }
            )

        return {
            "workspace_id": str(workspace_id),
            "nodes": embeddable_nodes,
            "stats": {
                "total_nodes": len(nodes),
                "embeddable_nodes": len(embeddable_nodes),
                "embedding_dimension": embedding_dimension,
            },
        }

    async def sync_workspace_clusters(
        self,
        db: AsyncSession,
        workspace_id: UUID,
        clusters: Sequence[Dict[str, Any]],
        algorithm_metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        existing_cluster_ids = (
            select(GraphCluster.id).where(GraphCluster.workspace_id == workspace_id).subquery()
        )
        await db.execute(
            delete(GraphClusterMembership).where(GraphClusterMembership.cluster_id.in_(select(existing_cluster_ids.c.id)))
        )
        await db.execute(delete(GraphCluster).where(GraphCluster.workspace_id == workspace_id))
        await db.flush()

        node_result = await db.execute(select(GraphNode).where(GraphNode.workspace_id == workspace_id))
        node_map = {str(node.id): node for node in node_result.scalars().all()}
        created_clusters = []

        for index, cluster in enumerate(clusters):
            members = []
            for member in cluster.get("members") or []:
                node = node_map.get(str(member.get("node_id") or ""))
                if node is None:
                    continue
                members.append(
                    {
                        "node": node,
                        "score": float(member.get("score") or 0.0),
                        "rank": int(member.get("rank") or 0),
                    }
                )

            if not members:
                continue

            summary = self._build_cluster_summary(index=index, members=members, cluster=cluster, algorithm_metadata=algorithm_metadata or {})
            label, description, label_model = await self._generate_cluster_copy(summary)
            cluster_metadata = {
                **(cluster.get("metadata") or {}),
                **(algorithm_metadata or {}),
                "cluster_key": summary["cluster_key"],
                "node_count": len(members),
                "sample_labels": summary["sample_labels"],
                "node_types": summary["node_types"],
                "label_model": label_model,
            }

            graph_cluster = GraphCluster(
                workspace_id=workspace_id,
                cluster_key=summary["cluster_key"],
                label=label,
                description=description,
                importance=summary["importance"],
                cluster_metadata=cluster_metadata,
            )
            db.add(graph_cluster)
            await db.flush()

            ordered_members = sorted(members, key=lambda item: (-item["score"], item["rank"], item["node"].label))
            for rank, member in enumerate(ordered_members):
                db.add(
                    GraphClusterMembership(
                        workspace_id=workspace_id,
                        cluster_id=graph_cluster.id,
                        node_id=member["node"].id,
                        membership_score=member["score"],
                        cluster_rank=rank,
                        membership_metadata={
                            "node_type": member["node"].node_type.value,
                            "source_rank": member["rank"],
                        },
                    )
                )

            created_clusters.append(graph_cluster)

        await db.commit()
        return {
            "workspace_id": str(workspace_id),
            "clusters_saved": len(created_clusters),
            "nodes_clustered": sum(len(cluster.get("members") or []) for cluster in clusters),
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

    async def _build_cluster_payload(
        self,
        db: AsyncSession,
        workspace_id: UUID,
        node_ids: set[UUID],
    ) -> Dict[str, Any]:
        if not node_ids:
            return {"clusters": [], "node_metadata": {}}

        result = await db.execute(
            select(GraphClusterMembership, GraphCluster)
            .join(GraphCluster, GraphClusterMembership.cluster_id == GraphCluster.id)
            .where(
                GraphCluster.workspace_id == workspace_id,
                GraphClusterMembership.node_id.in_(list(node_ids)),
            )
            .order_by(GraphCluster.importance.desc(), GraphClusterMembership.cluster_rank.asc())
        )
        rows = result.all()
        if not rows:
            return {"clusters": [], "node_metadata": {}}

        latest_graph_update = datetime.min.replace(tzinfo=timezone.utc)
        node_update_result = await db.execute(
            select(func.max(GraphNode.updated_at)).where(GraphNode.workspace_id == workspace_id)
        )
        node_updated_at = node_update_result.scalar()
        if node_updated_at:
            latest_graph_update = max(latest_graph_update, node_updated_at)
        edge_update_result = await db.execute(
            select(func.max(GraphEdge.updated_at)).where(GraphEdge.workspace_id == workspace_id)
        )
        edge_updated_at = edge_update_result.scalar()
        if edge_updated_at:
            latest_graph_update = max(latest_graph_update, edge_updated_at)

        clusters_by_id: dict[UUID, Dict[str, Any]] = {}
        node_metadata: dict[UUID, Dict[str, Any]] = {}
        for membership, cluster in rows:
            stale = bool(cluster.updated_at and latest_graph_update and cluster.updated_at < latest_graph_update)
            cluster_payload = clusters_by_id.setdefault(
                cluster.id,
                {
                    "id": str(cluster.id),
                    "key": cluster.cluster_key,
                    "label": cluster.label,
                    "description": cluster.description,
                    "importance": float(cluster.importance or 1.0),
                    "node_ids": [],
                    "node_count": 0,
                    "metadata": {
                        **(cluster.cluster_metadata or {}),
                        "stale": stale,
                    },
                },
            )
            cluster_payload["node_ids"].append(str(membership.node_id))
            cluster_payload["node_count"] += 1
            node_metadata[membership.node_id] = {
                "cluster_id": str(cluster.id),
                "cluster_key": cluster.cluster_key,
                "cluster_label": cluster.label,
                "cluster_description": cluster.description,
                "cluster_score": float(membership.membership_score or 0.0),
                "cluster_rank": int(membership.cluster_rank or 0),
            }

        return {
            "clusters": list(clusters_by_id.values()),
            "node_metadata": node_metadata,
        }

    async def _load_note_embeddings_by_graph_node(
        self,
        db: AsyncSession,
        workspace_id: UUID,
        nodes: Sequence[GraphNode],
    ) -> Dict[UUID, List[float]]:
        note_node_ids = {
            node.id: node.external_id
            for node in nodes
            if node.workspace_id == workspace_id and node.node_type == GraphNodeType.NOTE
        }
        note_ids = []
        for external_id in note_node_ids.values():
            try:
                note_ids.append(UUID(str(external_id)))
            except (TypeError, ValueError):
                continue

        if not note_ids:
            return {}

        note_result = await db.execute(
            select(Note.id, Note.embedding).where(
                Note.workspace_id == workspace_id,
                Note.id.in_(note_ids),
            )
        )
        note_embeddings = {str(note_id): _parse_embedding(raw_embedding) for note_id, raw_embedding in note_result.all()}
        return {
            node_id: note_embeddings.get(external_id)
            for node_id, external_id in note_node_ids.items()
            if note_embeddings.get(external_id)
        }

    def _build_cluster_summary(
        self,
        *,
        index: int,
        members: Sequence[Dict[str, Any]],
        cluster: Dict[str, Any],
        algorithm_metadata: Dict[str, Any],
    ) -> Dict[str, Any]:
        labels = [member["node"].label for member in members[:10] if member.get("node")]
        node_types = Counter(member["node"].node_type.value for member in members if member.get("node"))
        importance = sum(float(member.get("score") or 0.0) for member in members) / max(len(members), 1)
        cluster_key = _slugify(str(cluster.get("key") or f"cluster-{index + 1}"), fallback=f"cluster-{index + 1}")
        return {
            "cluster_key": cluster_key,
            "sample_labels": labels[:8],
            "node_types": dict(node_types),
            "importance": max(1.0, importance),
            "algorithm_metadata": algorithm_metadata,
        }

    async def _generate_cluster_copy(self, summary: Dict[str, Any]) -> tuple[str, str, str]:
        fallback_label, fallback_description = self._build_cluster_fallback_copy(summary)
        if not settings.OPENAI_API_KEY:
            return fallback_label, fallback_description, "heuristic"

        try:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY, timeout=float(settings.OPENAI_TIMEOUT))
            prompt = (
                "Generate a compact cluster label and description for a knowledge graph cluster.\n"
                "Return strict JSON with keys label and description.\n"
                f"Label must be 2 to {CLUSTER_LABEL_MAX_WORDS} words.\n"
                "Description must be exactly one sentence.\n"
                f"Sample labels: {summary['sample_labels']}\n"
                f"Node types: {summary['node_types']}\n"
                f"Algorithm metadata: {summary['algorithm_metadata']}"
            )
            response = await client.chat.completions.create(
                model="gpt-4o-mini",
                temperature=0.2,
                response_format={"type": "json_object"},
                messages=[
                    {
                        "role": "system",
                        "content": "You create concise semantic cluster names for knowledge graph visualizations.",
                    },
                    {
                        "role": "user",
                        "content": prompt,
                    },
                ],
            )
            content = response.choices[0].message.content or "{}"
            payload = json.loads(content)
            label = self._sanitize_cluster_label(str(payload.get("label") or fallback_label), fallback_label)
            description = self._sanitize_cluster_description(
                str(payload.get("description") or fallback_description),
                fallback_description,
            )
            return label, description, "gpt-4o-mini"
        except Exception as exc:
            logger.warning("Falling back to heuristic cluster copy: %s", exc)
            return fallback_label, fallback_description, "heuristic"

    def _build_cluster_fallback_copy(self, summary: Dict[str, Any]) -> tuple[str, str]:
        token_counts: Counter[str] = Counter()
        for label in summary["sample_labels"]:
            for token in re.split(r"[^a-zA-Z0-9]+", label.lower()):
                if len(token) < 3 or token in CLUSTER_FALLBACK_STOPWORDS:
                    continue
                token_counts[token] += 1

        if token_counts:
            title_tokens = [token.title() for token, _ in token_counts.most_common(CLUSTER_LABEL_MAX_WORDS)]
            label = " ".join(title_tokens[:CLUSTER_LABEL_MAX_WORDS])
        else:
            dominant_type = next(iter(summary["node_types"].keys()), "knowledge")
            label = f"{dominant_type.replace('_', ' ').title()} Cluster"

        top_samples = ", ".join(summary["sample_labels"][:3]) or "related knowledge"
        description = f"Cluster centered on {top_samples} and the nearby concepts linked across this workspace."
        return self._sanitize_cluster_label(label, "Knowledge Cluster"), self._sanitize_cluster_description(
            description,
            "Cluster centered on related notes, entities, and tags in this workspace.",
        )

    def _sanitize_cluster_label(self, value: str, fallback: str) -> str:
        cleaned = re.sub(r"\s+", " ", (value or "").strip())
        if not cleaned:
            cleaned = fallback
        words = cleaned.split()
        if len(words) < 2:
            cleaned = fallback
            words = cleaned.split()
        if len(words) > CLUSTER_LABEL_MAX_WORDS:
            cleaned = " ".join(words[:CLUSTER_LABEL_MAX_WORDS])
        return cleaned[:120]

    def _sanitize_cluster_description(self, value: str, fallback: str) -> str:
        cleaned = re.sub(r"\s+", " ", (value or "").strip())
        if not cleaned:
            cleaned = fallback
        if not cleaned.endswith("."):
            cleaned += "."
        return cleaned[:500]

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

    async def buildClusterInput(self, db: AsyncSession, workspace_id: UUID) -> Dict[str, Any]:
        """Compatibility wrapper for callers that expect camelCase naming."""
        return await self.build_cluster_input(db=db, workspace_id=workspace_id)

    async def syncWorkspaceClusters(
        self,
        db: AsyncSession,
        workspace_id: UUID,
        clusters: Sequence[Dict[str, Any]],
        algorithm_metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Compatibility wrapper for callers that expect camelCase naming."""
        return await self.sync_workspace_clusters(
            db=db,
            workspace_id=workspace_id,
            clusters=clusters,
            algorithm_metadata=algorithm_metadata,
        )

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


async def buildClusterInput(db: AsyncSession, workspace_id: UUID) -> Dict[str, Any]:
    """Module-level compatibility wrapper around cluster input assembly."""
    return await get_graph_service().build_cluster_input(db=db, workspace_id=workspace_id)


async def syncWorkspaceClusters(
    db: AsyncSession,
    workspace_id: UUID,
    clusters: Sequence[Dict[str, Any]],
    algorithm_metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Module-level compatibility wrapper around cluster persistence."""
    return await get_graph_service().sync_workspace_clusters(
        db=db,
        workspace_id=workspace_id,
        clusters=clusters,
        algorithm_metadata=algorithm_metadata,
    )


async def syncNoteToGraph(db: AsyncSession, note: Note) -> None:
    """Module-level compatibility wrapper around note graph sync."""
    await get_graph_service().sync_note_to_graph(db=db, note=note)
