"""Qdrant vector database client."""
from typing import List, Dict, Optional, Any
from uuid import uuid4

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance, VectorParams, PointStruct, 
    Filter, FieldCondition, MatchValue,
    ScoredPoint, HnswConfigDiff
)

from app.config import settings


class VectorIndex:
    """Qdrant vector index manager."""
    
    def __init__(self):
        self.client = QdrantClient(
            url=settings.QDRANT_URL,
            api_key=settings.QDRANT_API_KEY,
        )
        self.dimension = settings.EMBEDDING_DIMENSION
        self.collection_prefix = settings.QDRANT_COLLECTION_PREFIX
    
    def _get_collection_name(self, workspace_id: str) -> str:
        """Get collection name for workspace."""
        return f"{self.collection_prefix}_{workspace_id}"
    
    def create_collection(
        self, 
        workspace_id: str,
        distance: Distance = Distance.COSINE
    ) -> bool:
        """Create vector collection for workspace.
        
        Args:
            workspace_id: Workspace UUID
            distance: Distance metric (COSINE, EUCLID, DOT)
            
        Returns:
            True if created successfully
        """
        collection_name = self._get_collection_name(workspace_id)
        
        try:
            self.client.create_collection(
                collection_name=collection_name,
                vectors_config=VectorParams(
                    size=self.dimension,
                    distance=distance,
                ),
                hnsw_config=HnswConfigDiff(
                    m=16,
                    ef_construct=100,
                ),
            )
            return True
        except Exception as e:
            if "already exists" in str(e).lower():
                return True
            raise
    
    def delete_collection(self, workspace_id: str) -> bool:
        """Delete vector collection for workspace.
        
        Args:
            workspace_id: Workspace UUID
            
        Returns:
            True if deleted successfully
        """
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
        ids: Optional[List[str]] = None
    ) -> List[str]:
        """Insert or update vectors in batch.
        
        Args:
            workspace_id: Workspace UUID
            vectors: List of embedding vectors
            payloads: List of metadata payloads
            ids: Optional list of vector IDs (generated if not provided)
            
        Returns:
            List of vector IDs
        """
        collection_name = self._get_collection_name(workspace_id)
        
        # Ensure collection exists
        self.create_collection(workspace_id)
        
        # Generate IDs if not provided
        if ids is None:
            ids = [str(uuid4()) for _ in range(len(vectors))]
        
        # Create points
        points = [
            PointStruct(
                id=vector_id,
                vector=vector,
                payload=payload
            )
            for vector_id, vector, payload in zip(ids, vectors, payloads)
        ]
        
        # Upsert in batches
        batch_size = 100
        for i in range(0, len(points), batch_size):
            batch = points[i:i + batch_size]
            self.client.upsert(
                collection_name=collection_name,
                points=batch
            )
        
        return ids
    
    def search(
        self,
        workspace_id: str,
        query_vector: List[float],
        limit: int = 10,
        score_threshold: Optional[float] = None,
        filters: Optional[Dict[str, Any]] = None
    ) -> List[ScoredPoint]:
        """Search for similar vectors.
        
        Args:
            workspace_id: Workspace UUID
            query_vector: Query embedding vector
            limit: Maximum number of results
            score_threshold: Minimum similarity score
            filters: Optional metadata filters
            
        Returns:
            List of scored points
        """
        collection_name = self._get_collection_name(workspace_id)
        
        # Build filter
        search_filter = None
        if filters:
            conditions = []
            for key, value in filters.items():
                conditions.append(
                    FieldCondition(
                        key=key,
                        match=MatchValue(value=value)
                    )
                )
            if conditions:
                search_filter = Filter(must=conditions)
        
        # Search
        results = self.client.search(
            collection_name=collection_name,
            query_vector=query_vector,
            limit=limit,
            score_threshold=score_threshold,
            query_filter=search_filter,
        )
        
        return results
    
    def delete_vectors(
        self,
        workspace_id: str,
        vector_ids: List[str]
    ) -> bool:
        """Delete vectors by ID.
        
        Args:
            workspace_id: Workspace UUID
            vector_ids: List of vector IDs to delete
            
        Returns:
            True if deleted successfully
        """
        collection_name = self._get_collection_name(workspace_id)
        
        try:
            self.client.delete(
                collection_name=collection_name,
                points_selector=vector_ids
            )
            return True
        except Exception:
            return False
    
    def delete_by_filter(
        self,
        workspace_id: str,
        filters: Dict[str, Any]
    ) -> bool:
        """Delete vectors by metadata filter.
        
        Args:
            workspace_id: Workspace UUID
            filters: Metadata filters
            
        Returns:
            True if deleted successfully
        """
        collection_name = self._get_collection_name(workspace_id)
        
        # Build filter
        conditions = []
        for key, value in filters.items():
            conditions.append(
                FieldCondition(
                    key=key,
                    match=MatchValue(value=value)
                )
            )
        
        delete_filter = Filter(must=conditions)
        
        try:
            self.client.delete(
                collection_name=collection_name,
                points_selector=delete_filter
            )
            return True
        except Exception:
            return False
    
    def get_vector(
        self,
        workspace_id: str,
        vector_id: str
    ) -> Optional[ScoredPoint]:
        """Get vector by ID.
        
        Args:
            workspace_id: Workspace UUID
            vector_id: Vector ID
            
        Returns:
            Vector point if found
        """
        collection_name = self._get_collection_name(workspace_id)
        
        try:
            result = self.client.retrieve(
                collection_name=collection_name,
                ids=[vector_id],
                with_vectors=True,
                with_payload=True
            )
            return result[0] if result else None
        except Exception:
            return None
    
    def count_vectors(
        self,
        workspace_id: str,
        filters: Optional[Dict[str, Any]] = None
    ) -> int:
        """Count vectors in collection.
        
        Args:
            workspace_id: Workspace UUID
            filters: Optional metadata filters
            
        Returns:
            Number of vectors
        """
        collection_name = self._get_collection_name(workspace_id)
        
        # Build filter
        count_filter = None
        if filters:
            conditions = []
            for key, value in filters.items():
                conditions.append(
                    FieldCondition(
                        key=key,
                        match=MatchValue(value=value)
                    )
                )
            if conditions:
                count_filter = Filter(must=conditions)
        
        result = self.client.count(
            collection_name=collection_name,
            count_filter=count_filter
        )
        
        return result.count
    
    def collection_exists(self, workspace_id: str) -> bool:
        """Check if collection exists.
        
        Args:
            workspace_id: Workspace UUID
            
        Returns:
            True if collection exists
        """
        collection_name = self._get_collection_name(workspace_id)
        
        try:
            self.client.get_collection(collection_name)
            return True
        except Exception:
            return False


# Global vector index instance
vector_index = VectorIndex()
