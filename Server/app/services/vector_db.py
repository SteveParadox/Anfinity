"""Vector database wrapper for Qdrant."""
from typing import List, Dict, Any, Optional
import logging
import os
from uuid import UUID

logger = logging.getLogger(__name__)

try:
    from qdrant_client import QdrantClient
    from qdrant_client.models import (
        PointStruct, Distance, VectorParams,
        FieldCondition, MatchValue, Filter, HasIdCondition,
    )
    HAS_QDRANT = True
except ImportError:
    HAS_QDRANT = False

# ─────────────────────────────────────────────────────────────────────────────
# Embedding dimension registry
# ─────────────────────────────────────────────────────────────────────────────
EMBEDDING_DIMENSIONS: Dict[str, int] = {
    # OpenAI
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
    # Ollama
    "nomic-embed-text": 768,
    "mxbai-embed-large": 1024,
    "llama2": 4096,
    # Cohere
    "embed-english-v3.0": 1024,
    "embed-english-v2.0": 4096,
    # Fallback
    "default": 768,
}


class VectorDBClient:
    """Client for Qdrant vector database.

    Key design decisions
    --------------------
    * **Lazy reconnection** – if Qdrant was down at construction time the
      singleton has ``self.client = None``.  Every public method calls
      ``_ensure_connected()`` first so the connection is established as soon as
      the server comes back, without needing to restart the worker.
    * **Explicit dimension wins** – callers always pass the actual embedding
      dimension to ``create_collection`` / ``upsert_vectors``, so the instance-
      level default is only a last-resort fallback.
    * **Race-safe collection management** – ``create_collection`` wraps the
      delete-and-recreate path in a try/except so a concurrent task that already
      fixed the collection doesn't cause a hard failure.
    """

    def __init__(
        self,
        url: str = None,
        api_key: str = None,
        embedding_dim: int = 768,   # ← changed default to 768 (safe fallback)
        require_qdrant: bool = True,
    ):
        self.url = url or os.getenv("QDRANT_URL", "http://localhost:6333")
        self.api_key = api_key or os.getenv("QDRANT_API_KEY")
        self.embedding_dim = embedding_dim
        self.require_qdrant = require_qdrant
        self.client: Optional["QdrantClient"] = None
        self.is_connected = False

        self.mock_storage: Dict[str, list] = {}

        # Attempt initial connection (non-fatal – lazy reconnect handles the rest)
        self._attempt_connect()

    # ─────────────────────────────────────────────────────────────────────────
    # Connection management
    # ─────────────────────────────────────────────────────────────────────────

    def _attempt_connect(self) -> bool:
        """Try to (re)connect to Qdrant.  Returns True on success."""
        if not HAS_QDRANT:
            logger.warning("Qdrant client not installed. Using mock vector DB.")
            return False

        try:
            client = QdrantClient(url=self.url, api_key=self.api_key, timeout=30.0)
            client.get_collections()          # lightweight connectivity probe
            self.client = client
            self.is_connected = True
            logger.info("✅ Connected to Qdrant at %s", self.url)
            return True
        except Exception as exc:
            self.client = None
            self.is_connected = False
            logger.error("Failed to connect to Qdrant: %s", exc)
            if self.require_qdrant:
                raise RuntimeError(
                    f"Qdrant is required but unavailable at {self.url}. "
                    "Set QDRANT_REQUIRED=false to use mock storage for development."
                ) from exc
            logger.warning(
                "Qdrant unavailable – falling back to in-memory mock storage. "
                "⚠️  VECTORS WILL NOT BE PERSISTED."
            )
            return False

    def _ensure_connected(self) -> None:
        """Reconnect to Qdrant if the current connection is stale.

        This is the fix for the "singleton created while Qdrant was down" bug:
        instead of permanently caching a disconnected client, we re-probe on
        every public operation when ``is_connected`` is False.
        """
        if self.is_connected and self.client is not None:
            return  # happy path – already connected

        logger.info("Qdrant not connected – attempting reconnection…")
        connected = self._attempt_connect()

        if not connected and self.require_qdrant:
            raise RuntimeError(
                f"Qdrant vector database is required but not connected. "
                f"Check Qdrant server at {self.url}"
            )

    def is_healthy(self) -> bool:
        """Probe Qdrant liveness without raising."""
        try:
            if self.client:
                self.client.get_collections()
                self.is_connected = True
                return True
        except Exception:
            self.is_connected = False
        return False

    # ─────────────────────────────────────────────────────────────────────────
    # Static helpers
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def get_dimension_for_model(model_name: str) -> int:
        """Return the vector dimension for *model_name*, falling back to 768."""
        return EMBEDDING_DIMENSIONS.get(model_name, EMBEDDING_DIMENSIONS["default"])

    # ─────────────────────────────────────────────────────────────────────────
    # Collection management
    # ─────────────────────────────────────────────────────────────────────────

    def create_collection(
        self,
        collection_name: str,
        embedding_dim: int = None,
    ) -> bool:
        """Ensure a collection exists with the correct vector dimension.

        If the collection already exists with the *wrong* dimension it is
        deleted and re-created.  The delete-and-recreate is wrapped in an
        extra try/except so a concurrent task that already fixed the collection
        does not cause a hard failure here.

        Args:
            collection_name: Qdrant collection name (usually workspace_id).
            embedding_dim:   Required vector size.  Uses the instance default
                             when omitted, but callers should always pass this
                             explicitly based on the active embedding model.

        Returns:
            True on success, False on error.
        """
        # ── reconnect if needed ────────────────────────────────────────────
        self._ensure_connected()

        dim = embedding_dim or self.embedding_dim

        # ── mock path ─────────────────────────────────────────────────────
        if not self.client:
            logger.debug("Mock: Creating collection %s (dim=%d)", collection_name, dim)
            self.mock_storage.setdefault(collection_name, [])
            return True

        # ── real Qdrant path ──────────────────────────────────────────────
        try:
            collections = self.client.get_collections()
            existing_names = {c.name for c in collections.collections}

            if collection_name in existing_names:
                info = self.client.get_collection(collection_name)
                existing_dim: int = info.config.params.vectors.size

                if existing_dim == dim:
                    logger.debug(
                        "Collection '%s' already exists with correct dim=%d",
                        collection_name, dim,
                    )
                    return True

                # ── dimension mismatch: delete and recreate ────────────────
                logger.warning(
                    "🔄 DIMENSION MISMATCH in '%s': stored=%dD, required=%dD. "
                    "Deleting collection and recreating — all existing vectors "
                    "for this workspace will be lost.",
                    collection_name, existing_dim, dim,
                )
                try:
                    self.client.delete_collection(collection_name)
                    logger.info("Deleted mismatched collection '%s'", collection_name)
                except Exception as del_exc:
                    # Another concurrent task may have already deleted it.
                    logger.warning(
                        "Could not delete collection '%s' (maybe already gone): %s",
                        collection_name, del_exc,
                    )

            # ── create (or recreate after deletion) ───────────────────────
            self.client.create_collection(
                collection_name=collection_name,
                vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
            )
            logger.info(
                "✅ Created Qdrant collection '%s' (dim=%d)", collection_name, dim
            )
            return True

        except Exception as exc:
            logger.error(
                "Error creating collection '%s': %s", collection_name, exc
            )
            return False

    def delete_collection(self, collection_name: str) -> bool:
        """Delete a collection entirely."""
        self._ensure_connected()

        if not self.client:
            self.mock_storage.pop(collection_name, None)
            return True

        try:
            self.client.delete_collection(collection_name)
            logger.info("Deleted collection '%s'", collection_name)
            return True
        except Exception as exc:
            logger.error("Error deleting collection '%s': %s", collection_name, exc)
            return False

    def get_collection_dimension(self, collection_name: str) -> Optional[int]:
        """Return the stored vector dimension for *collection_name*, or None."""
        if not self.client:
            return None
        try:
            info = self.client.get_collection(collection_name)
            return info.config.params.vectors.size
        except Exception:
            return None

    # ─────────────────────────────────────────────────────────────────────────
    # Vector operations
    # ─────────────────────────────────────────────────────────────────────────

    def upsert_vectors(
        self,
        collection_name: str,
        points: List[Dict[str, Any]],
    ) -> bool:
        """Insert or update vectors with automatic dimension mismatch recovery.

        Each element of *points* must contain:
            ``{"id": str, "vector": List[float], "payload": Dict}``

        Returns True on success, False on error.
        
        Dimension Mismatch Handling:
            If vectors don't match the collection's expected dimension,
            automatically deletes the collection and recreates it with
            the correct dimension, then retries the upsert once.
        """
        if not points:
            return True

        self._ensure_connected()

        if not self.client:
            # Mock path
            bucket = self.mock_storage.setdefault(collection_name, [])
            existing_ids = {p["id"] for p in bucket}
            bucket[:] = [p for p in bucket if p["id"] not in existing_ids]
            bucket.extend(points)
            logger.debug(
                "Mock: upserted %d vectors to '%s'", len(points), collection_name
            )
            return True

        # Sample actual dimension from first vector
        actual_dim = len(points[0].get("vector", [])) if points else 768

        try:
            # ── PRE-FLIGHT: Check if collection exists and has matching dimension ──────
            try:
                collection_info = self.client.get_collection(collection_name)
                expected_dim = collection_info.config.params.vectors.size
                
                if actual_dim != expected_dim:
                    # ── MISMATCH DETECTED: Attempt active recovery ────────────────────
                    logger.warning(
                        "\n"
                        "🔄 DIMENSION MISMATCH DETECTED - AUTO-RECOVERY STARTED\n"
                        f"   Collection:      {collection_name}\n"
                        f"   Expected:        {expected_dim}D (in schema)\n"
                        f"   Got:             {actual_dim}D (vectors ready)\n"
                        f"   Qdrant URL:      {self.url}\n"
                        "\n"
                        "ACTION: Deleting mismatched collection and recreating with correct dimension…\n"
                    )
                    
                    # Delete the mismatched collection
                    try:
                        self.client.delete_collection(collection_name)
                        logger.info(f"✅ Deleted mismatched collection: {collection_name} ({expected_dim}D)")
                    except Exception as del_error:
                        logger.error(f"❌ Failed to delete collection: {del_error}")
                        return False
                    
                    # Recreate with correct dimension
                    try:
                        self.client.create_collection(
                            collection_name=collection_name,
                            vectors_config=VectorParams(size=actual_dim, distance=Distance.COSINE),
                        )
                        logger.info(
                            f"✅ Recreated collection: {collection_name} with {actual_dim}D vectors"
                        )
                    except Exception as create_error:
                        logger.error(f"❌ Failed to recreate collection: {create_error}")
                        return False
                    
                    # Continue to upsert with the new collection
                    
            except Exception as probe_error:
                # If collection doesn't exist or we can't check it, just proceed with upsert
                logger.debug(f"Could not probe collection: {probe_error}")
            
            # ── UPSERT: Prepare and upload vectors ──────────────────────────────────
            qdrant_points = [
                PointStruct(
                    id=p["id"],
                    vector=p["vector"],
                    payload=p.get("payload", {}),
                )
                for p in points
            ]
            
            self.client.upsert(collection_name=collection_name, points=qdrant_points)
            logger.info(
                f"✅ Successfully upserted {len(points)} vectors to '{collection_name}' ({actual_dim}D)"
            )
            return True
            
        except Exception as exc:
            error_str = str(exc)
            
            # ── FALLBACK: If upsert still fails, try one-time recovery ─────────────────
            if "dimension" in error_str.lower():
                logger.warning(
                    f"\n"
                    f"⚠️  Upsert failed with dimension error — attempting one-time recovery\n"
                    f"   Collection: {collection_name}\n"
                    f"   Error: {error_str}\n"
                )
                
                try:
                    # Delete and recreate
                    self.client.delete_collection(collection_name)
                    logger.info(f"✅ Deleted collection for recovery: {collection_name}")
                    
                    self.client.create_collection(
                        collection_name=collection_name,
                        vectors_config=VectorParams(size=actual_dim, distance=Distance.COSINE),
                    )
                    logger.info(f"✅ Recreated collection for recovery: {collection_name} ({actual_dim}D)")
                    
                    # ONE RETRY: Attempt upsert again
                    qdrant_points = [
                        PointStruct(
                            id=p["id"],
                            vector=p["vector"],
                            payload=p.get("payload", {}),
                        )
                        for p in points
                    ]
                    
                    self.client.upsert(collection_name=collection_name, points=qdrant_points)
                    logger.info(
                        f"✅ Recovery successful! Upserted {len(points)} vectors after recreation"
                    )
                    return True
                    
                except Exception as recovery_error:
                    logger.error(
                        f"❌ Recovery failed: {recovery_error}",
                        exc_info=True,
                    )
                    return False
                    
            # Not a dimension error - other issue
            elif "collection" in error_str.lower() and "not found" in error_str.lower():
                logger.error(
                    f"Collection '{collection_name}' not found in Qdrant at {self.url} — will retry",
                )
            elif "connection" in error_str.lower():
                logger.error(
                    f"Connection error to Qdrant at {self.url}: {error_str}",
                )
                self.is_connected = False
            else:
                logger.error(
                    f"Error upserting {len(points)} vectors to '{collection_name}': {error_str}",
                    exc_info=True,
                )
            
            return False

    def search_similar(
        self,
        collection_name: str,
        query_vector: List[float],
        workspace_id: str = None,
        limit: int = 10,
        score_threshold: float = 0.0,
    ) -> List[Dict[str, Any]]:
        """Return the *limit* most similar vectors to *query_vector*.

        Each result dict contains ``{"id", "similarity", "payload"}``.
        """
        self._ensure_connected()

        if not self.client:
            # Mock: return stored points in insertion order
            points = self.mock_storage.get(collection_name, [])
            return [
                {
                    "id": p["id"],
                    "similarity": 0.85 - (i * 0.05),
                    "payload": p.get("payload", {}),
                }
                for i, p in enumerate(points[:limit])
            ]

        try:
            query_filter = None
            if workspace_id:
                query_filter = Filter(
                    must=[
                        FieldCondition(
                            key="workspace_id",
                            match=MatchValue(value=workspace_id),
                        )
                    ]
                )

            results = self.client.search(
                collection_name=collection_name,
                query_vector=query_vector,
                query_filter=query_filter,
                limit=limit,
                score_threshold=score_threshold,
            )
            return [
                {
                    "id": str(r.id),
                    "similarity": r.score,
                    "payload": r.payload or {},
                }
                for r in results
            ]
        except Exception as exc:
            logger.error(
                "Error searching collection '%s': %s", collection_name, exc
            )
            return []

    def delete_points(
        self,
        collection_name: str,
        ids: List[str],
    ) -> bool:
        """Delete specific vector IDs from a collection."""
        if not ids:
            return True

        self._ensure_connected()

        if not self.client:
            bucket = self.mock_storage.get(collection_name, [])
            id_set = set(ids)
            self.mock_storage[collection_name] = [
                p for p in bucket if p["id"] not in id_set
            ]
            return True

        try:
            self.client.delete(
                collection_name=collection_name,
                points_selector=ids,
            )
            logger.debug(
                "Deleted %d vectors from '%s'", len(ids), collection_name
            )
            return True
        except Exception as exc:
            logger.error(
                "Error deleting vectors from '%s': %s", collection_name, exc
            )
            return False

    def delete_by_filter(
        self,
        collection_name: str,
        filters: Dict[str, Any],
    ) -> bool:
        """Delete all vectors whose payload matches *filters*.

        Only ``str`` equality filters are supported here.
        """
        self._ensure_connected()

        if not self.client:
            # Mock: naive scan
            bucket = self.mock_storage.get(collection_name, [])
            self.mock_storage[collection_name] = [
                p for p in bucket
                if not all(
                    p.get("payload", {}).get(k) == v
                    for k, v in filters.items()
                )
            ]
            return True

        try:
            conditions = [
                FieldCondition(key=k, match=MatchValue(value=v))
                for k, v in filters.items()
            ]
            self.client.delete(
                collection_name=collection_name,
                points_selector=Filter(must=conditions),
            )
            logger.debug(
                "Deleted vectors from '%s' matching %s", collection_name, filters
            )
            return True
        except Exception as exc:
            logger.error(
                "Error deleting vectors by filter from '%s': %s",
                collection_name, exc,
            )
            return False


# ─────────────────────────────────────────────────────────────────────────────
# Singleton factory
# ─────────────────────────────────────────────────────────────────────────────
# NOTE: The singleton caches the *client connection*, NOT the embedding
# dimension.  Callers must always pass `embedding_dim` explicitly to
# `create_collection` so the correct dimension is used regardless of which
# model was active when the singleton was first initialised.

_vector_db_client: Optional[VectorDBClient] = None


def get_vector_db_client(embedding_dim: int = None) -> VectorDBClient:
    """Return the shared VectorDBClient, creating it on first call.

    ``embedding_dim`` is intentionally NOT baked into the singleton – it must
    be supplied to ``create_collection`` at call-time so the correct dimension
    is always used regardless of which embedding model is active.

    The client will transparently reconnect to Qdrant on each public method
    call if the previous connection was unavailable (e.g. Qdrant restarted
    while the Celery worker was running).
    """
    global _vector_db_client

    if _vector_db_client is None:
        from app.config import settings

        _vector_db_client = VectorDBClient(
            # Default dim is irrelevant – create_collection always receives it
            # explicitly from _index_vectors.  Set to a safe value nonetheless.
            embedding_dim=embedding_dim or 768,
            require_qdrant=settings.QDRANT_REQUIRED,
        )

    return _vector_db_client