"""Qdrant vector database client."""
from __future__ import annotations

from typing import List, Dict, Optional, Any
from uuid import uuid4
import logging

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance, VectorParams, PointStruct,
    Filter, FieldCondition, MatchValue,
    ScoredPoint, HnswConfigDiff, PointIdsList,
)

from app.config import settings

logger = logging.getLogger(__name__)


class VectorIndex:
    """Qdrant vector index manager."""

    def __init__(self):
        self.client = QdrantClient(
            url=settings.QDRANT_URL,
            api_key=settings.QDRANT_API_KEY,
            timeout=getattr(settings, "QDRANT_TIMEOUT", 30),
        )
        self.dimension = settings.EMBEDDING_DIMENSION
        self.collection_prefix = settings.QDRANT_COLLECTION_PREFIX
        self.upsert_batch_size = max(1, int(getattr(settings, "QDRANT_UPSERT_BATCH_SIZE", 256)))
    
    def _get_collection_name(self, workspace_id: str) -> str:
        """Get collection name for workspace."""
        return f"{self.collection_prefix}_{workspace_id}"

    def _collection_dimension(self, collection_name: str) -> Optional[int]:
        try:
            info = self.client.get_collection(collection_name)
            return info.config.params.vectors.size
        except Exception:
            return None
    
    def create_collection(
        self,
        workspace_id: str,
        distance: Distance = Distance.COSINE,
        embedding_dim: Optional[int] = None,
    ) -> bool:
        """Create vector collection for workspace, validating dimensions."""
        collection_name = self._get_collection_name(workspace_id)
        dim = int(embedding_dim or self.dimension)
        self.dimension = dim

        try:
            existing_dim = self._collection_dimension(collection_name)
            if existing_dim is not None:
                if existing_dim != dim:
                    raise RuntimeError(
                        f"Collection {collection_name} already exists with dimension {existing_dim}, expected {dim}."
                    )
                return True

            self.client.create_collection(
                collection_name=collection_name,
                vectors_config=VectorParams(size=dim, distance=distance),
                hnsw_config=HnswConfigDiff(m=16, ef_construct=100),
            )
            return True
        except Exception as e:
            if "already exists" in str(e).lower():
                existing_dim = self._collection_dimension(collection_name)
                if existing_dim is not None and existing_dim != dim:
                    raise RuntimeError(
                        f"Collection {collection_name} already exists with dimension {existing_dim}, expected {dim}."
                    )
                return True
            raise
    
    def delete_collection(self, workspace_id: str) -> bool:
        """Delete vector collection for workspace."""
        collection_name = self._get_collection_name(workspace_id)
        try:
            self.client.delete_collection(collection_name=collection_name)
            return True
        except Exception:
            return False
    
    def upsert_vectors(
        self,
        workspace_id: str,
        vectors: List[List[float]],
        payloads: List[Dict[str, Any]],
        ids: Optional[List[str]] = None,
        batch_size: Optional[int] = None,
    ) -> List[str]:
        """Insert or update vectors in batch with validation."""
        if not vectors:
            return []
        if len(vectors) != len(payloads):
            raise ValueError(f"vectors/payloads length mismatch: {len(vectors)} != {len(payloads)}")

        actual_dim = len(vectors[0])
        if actual_dim <= 0:
            raise ValueError("First vector is empty")
        for i, vec in enumerate(vectors):
            if len(vec) != actual_dim:
                raise ValueError(f"Vector at index {i} has dim {len(vec)} but expected {actual_dim}")

        collection_name = self._get_collection_name(workspace_id)
        self.create_collection(workspace_id, embedding_dim=actual_dim)

        if ids is None:
            ids = [str(uuid4()) for _ in range(len(vectors))]
        elif len(ids) != len(vectors):
            raise ValueError(f"ids/vectors length mismatch: {len(ids)} != {len(vectors)}")

        points = [
            PointStruct(id=vector_id, vector=vector, payload=payload)
            for vector_id, vector, payload in zip(ids, vectors, payloads)
        ]

        batch_n = max(1, int(batch_size or self.upsert_batch_size))
        for i in range(0, len(points), batch_n):
            batch = points[i:i + batch_n]
            self.client.upsert(collection_name=collection_name, points=batch, wait=True)

        return ids
    
    def search(
        self,
        workspace_id: str,
        query_vector: List[float],
        limit: int = 10,
        score_threshold: Optional[float] = None,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[ScoredPoint]:
        """Search for similar vectors."""
        collection_name = self._get_collection_name(workspace_id)

        if not query_vector:
            return []

        search_filter = None
        if filters:
            conditions = [
                FieldCondition(key=key, match=MatchValue(value=value))
                for key, value in filters.items()
                if value is not None
            ]
            if conditions:
                search_filter = Filter(must=conditions)

        results = self.client.query_points(
            collection_name=collection_name,
            query=query_vector,
            limit=limit,
            score_threshold=score_threshold,
            query_filter=search_filter,
            with_payload=True,
        ).points
        return results
    
    def delete_vectors(self, workspace_id: str, vector_ids: List[str]) -> bool:
        """Delete vectors by ID."""
        if not vector_ids:
            return True
        collection_name = self._get_collection_name(workspace_id)
        try:
            self.client.delete(
                collection_name=collection_name,
                points_selector=PointIdsList(points=vector_ids),
                wait=True,
            )
            return True
        except Exception:
            return False
    
    def delete_by_filter(self, workspace_id: str, filters: Dict[str, Any]) -> bool:
        """Delete vectors by metadata filter."""
        collection_name = self._get_collection_name(workspace_id)
        conditions = [
            FieldCondition(key=key, match=MatchValue(value=value))
            for key, value in filters.items()
            if value is not None
        ]
        delete_filter = Filter(must=conditions)
        try:
            self.client.delete(collection_name=collection_name, points_selector=delete_filter, wait=True)
            return True
        except Exception:
            return False
    
        
# Global vector index instance
vector_index = VectorIndex()
