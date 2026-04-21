"""Vector database wrapper for Qdrant."""
from typing import List, Dict, Any, Optional
import logging
import math
import os
import time
from uuid import UUID

logger = logging.getLogger(__name__)

try:
    from qdrant_client import QdrantClient
    from qdrant_client.models import (
        PointStruct, Distance, VectorParams,
        FieldCondition, MatchValue, Filter,
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

# ─────────────────────────────────────────────────────────────────────────────
# Safety constants
# ─────────────────────────────────────────────────────────────────────────────

ALLOW_DESTRUCTIVE_RECREATE: bool = (
    os.getenv("QDRANT_ALLOW_DESTRUCTIVE_RECREATE", "false").lower() == "true"
)

RECONNECT_COOLDOWN_SECONDS: float = 5.0

DEFAULT_SCORE_THRESHOLD: float = 0.6
DEFAULT_UPSERT_BATCH_SIZE: int = max(
    1,
    int(os.getenv("QDRANT_UPSERT_BATCH_SIZE", "128")),
)
RETRYABLE_CONNECTION_ERROR_MARKERS = (
    "connection",
    "connect",
    "timeout",
    "temporarily unavailable",
    "unavailable",
    "refused",
    "reset by peer",
    "broken pipe",
    "network",
    "socket",
    "transport",
    "disconnect",
)


# ─────────────────────────────────────────────────────────────────────────────
# Standalone vector validation helper
# ─────────────────────────────────────────────────────────────────────────────

def is_valid_vector(vec: Any, expected_dim: int) -> bool:
    """Return True only if *vec* looks like a real, usable embedding."""
    if not isinstance(vec, list):
        return False
    if len(vec) != expected_dim:
        return False
    if len(vec) < 100:
        return False
    if all(v == vec[0] for v in vec):
        return False
    if len(set(vec)) < 10:
        return False
    return True


def cosine_similarity(left: List[float], right: List[float]) -> float:
    """Return cosine similarity for two dense vectors."""
    if not left or not right or len(left) != len(right):
        return 0.0

    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (left_norm * right_norm)


class VectorDBClient:
    """Client for the Qdrant vector database."""

    def __init__(
        self,
        url: str = None,
        api_key: str = None,
        embedding_dim: int = 768,
        require_qdrant: bool = True,
    ):
        self.url = url or os.getenv("QDRANT_URL", "http://localhost:6333")
        self.api_key = api_key or os.getenv("QDRANT_API_KEY")
        self.embedding_dim = embedding_dim
        self.require_qdrant = require_qdrant

        self.client: Optional["QdrantClient"] = None
        self.is_connected: bool = False
        self._last_connect_attempt: float = 0.0

        self.mock_storage: Dict[str, list] = {}

        self._attempt_connect()

    # ─────────────────────────────────────────────────────────────────────────
    # Connection management
    # ─────────────────────────────────────────────────────────────────────────

    def _attempt_connect(self) -> bool:
        self._last_connect_attempt = time.monotonic()

        if not HAS_QDRANT:
            logger.warning("qdrant-client not installed — using mock vector DB.")
            return False

        try:
            client = QdrantClient(url=self.url, api_key=self.api_key, timeout=30.0)
            client.get_collections()
            self.client = client
            self.is_connected = True
            logger.info("Connected to Qdrant at %s", self.url)
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
                "Qdrant unavailable — falling back to in-memory mock storage. "
                "VECTORS WILL NOT BE PERSISTED."
            )
            return False

    def _invalidate_connection(self, reason: Optional[str] = None) -> None:
        if reason:
            logger.warning("Invalidating Qdrant connection: %s", reason)
        self.client = None
        self.is_connected = False
        self._last_connect_attempt = 0.0

    @staticmethod
    def _is_retryable_connection_error(exc: Exception) -> bool:
        error_text = f"{type(exc).__name__}: {exc}".lower()
        return any(marker in error_text for marker in RETRYABLE_CONNECTION_ERROR_MARKERS)

    def _execute_with_reconnect(self, operation_name: str, func):
        try:
            return func()
        except Exception as exc:
            if not self._is_retryable_connection_error(exc):
                raise

            logger.warning(
                "%s hit a retryable Qdrant error; reconnecting once: %s",
                operation_name,
                exc,
            )
            self._invalidate_connection(str(exc))
            self._ensure_connected(force=True)
            if self.client is None:
                raise RuntimeError(
                    f"Qdrant client unavailable after reconnect attempt during {operation_name}"
                ) from exc
            return func()

    def _ensure_connected(self, force: bool = False) -> None:
        if self.is_connected and self.client is not None:
            return

        elapsed = time.monotonic() - self._last_connect_attempt
        if not force and elapsed < RECONNECT_COOLDOWN_SECONDS:
            if self.require_qdrant:
                raise RuntimeError(
                    f"Qdrant unavailable at {self.url}. "
                    f"Next reconnect attempt in "
                    f"{RECONNECT_COOLDOWN_SECONDS - elapsed:.1f}s."
                )
            return

        logger.info("Qdrant not connected — attempting reconnection…")
        connected = self._attempt_connect()

        if not connected and self.require_qdrant:
            raise RuntimeError(
                f"Qdrant vector database is required but not connected. "
                f"Check Qdrant server at {self.url}."
            )

    def is_healthy(self) -> bool:
        try:
            if self.client:
                self.client.get_collections()
                self.is_connected = True
                return True
        except Exception as exc:
            self._invalidate_connection(str(exc))
        return False

    # ─────────────────────────────────────────────────────────────────────────
    # Static helpers
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def get_dimension_for_model(model_name: str) -> int:
        return EMBEDDING_DIMENSIONS.get(model_name, EMBEDDING_DIMENSIONS["default"])

    @staticmethod
    def versioned_collection_name(base_name: str, dim: int) -> str:
        return f"{base_name}_{dim}d"

    # ─────────────────────────────────────────────────────────────────────────
    # Collection management
    # ─────────────────────────────────────────────────────────────────────────

    def create_collection(
        self,
        collection_name: str,
        embedding_dim: int = None,
    ) -> bool:
        self._ensure_connected()

        dim = embedding_dim or self.embedding_dim

        if not self.client:
            logger.debug("Mock: creating collection '%s' (dim=%d)", collection_name, dim)
            self.mock_storage.setdefault(collection_name, [])
            return True

        try:
            collections = self._execute_with_reconnect(
                f"create collection '{collection_name}'",
                lambda: self.client.get_collections(),
            )
            existing_names = {c.name for c in collections.collections}

            if collection_name in existing_names:
                info = self._execute_with_reconnect(
                    f"inspect collection '{collection_name}'",
                    lambda: self.client.get_collection(collection_name),
                )
                existing_dim: int = info.config.params.vectors.size

                if existing_dim == dim:
                    logger.debug(
                        "Collection '%s' already exists with correct dim=%d",
                        collection_name, dim,
                    )
                    return True

                if not ALLOW_DESTRUCTIVE_RECREATE:
                    raise RuntimeError(
                        f"Dimension mismatch for collection '{collection_name}': "
                        f"stored={existing_dim}D, required={dim}D.  "
                        "Refusing to delete — set QDRANT_ALLOW_DESTRUCTIVE_RECREATE=true "
                        "to enable automatic recreation (DATA WILL BE LOST).  "
                        "Consider using versioned_collection_name() instead."
                    )

                logger.warning(
                    "DIMENSION MISMATCH in '%s': stored=%dD, required=%dD. "
                    "ALLOW_DESTRUCTIVE_RECREATE=true — deleting collection.",
                    collection_name, existing_dim, dim,
                )
                try:
                    self._execute_with_reconnect(
                        f"delete collection '{collection_name}'",
                        lambda: self.client.delete_collection(collection_name),
                    )
                    logger.info("Deleted mismatched collection '%s'", collection_name)
                except Exception as del_exc:
                    logger.warning(
                        "Could not delete collection '%s': %s",
                        collection_name, del_exc,
                    )

            self._execute_with_reconnect(
                f"create collection '{collection_name}'",
                lambda: self.client.create_collection(
                    collection_name=collection_name,
                    vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
                ),
            )
            logger.info("Created Qdrant collection '%s' (dim=%d)", collection_name, dim)
            return True

        except RuntimeError:
            raise
        except Exception as exc:
            logger.error("Error creating collection '%s': %s", collection_name, exc)
            return False

    def delete_collection(self, collection_name: str) -> bool:
        self._ensure_connected()

        if not self.client:
            self.mock_storage.pop(collection_name, None)
            return True

        try:
            self._execute_with_reconnect(
                f"delete collection '{collection_name}'",
                lambda: self.client.delete_collection(collection_name),
            )
            logger.info("Deleted collection '%s'", collection_name)
            return True
        except Exception as exc:
            logger.error("Error deleting collection '%s': %s", collection_name, exc)
            return False

    def get_collection_dimension(self, collection_name: str) -> Optional[int]:
        if not self.client:
            return None
        try:
            info = self._execute_with_reconnect(
                f"inspect collection '{collection_name}'",
                lambda: self.client.get_collection(collection_name),
            )
            return info.config.params.vectors.size
        except Exception as exc:
            if self._is_retryable_connection_error(exc):
                self._invalidate_connection(str(exc))
            return None

    def collection_exists(self, collection_name: str) -> bool:
        if not self.client:
            return collection_name in self.mock_storage
        try:
            info = self._execute_with_reconnect(
                f"inspect collection '{collection_name}'",
                lambda: self.client.get_collection(collection_name),
            )
            return info is not None
        except Exception as exc:
            if self._is_retryable_connection_error(exc):
                self._invalidate_connection(str(exc))
            return False

    def resolve_collection_name(
        self,
        base_name: str,
        embedding_dim: Optional[int] = None,
    ) -> str:
        """Resolve to a versioned collection when one exists, else use base name."""
        dim = embedding_dim or self.embedding_dim
        versioned = self.versioned_collection_name(base_name, dim)

        if self.collection_exists(versioned):
            return versioned
        if self.collection_exists(base_name):
            return base_name
        return base_name

    # ─────────────────────────────────────────────────────────────────────────
    # Vector operations
    # ─────────────────────────────────────────────────────────────────────────

    def upsert_vectors(
        self,
        collection_name: str,
        points: List[Dict[str, Any]],
    ) -> bool:
        if not points:
            return True

        self._ensure_connected()

        first_vec = points[0].get("vector") or []
        actual_dim = len(first_vec)

        if actual_dim == 0:
            logger.error(
                "upsert_vectors: first point '%s' has an empty vector — aborting.",
                points[0].get("id"),
            )
            return False

        invalid = [
            p.get("id")
            for p in points
            if not is_valid_vector(p.get("vector"), actual_dim)
        ]
        if invalid:
            logger.error(
                "upsert_vectors: %d invalid vector(s) detected. Offending IDs: %s. Aborting.",
                len(invalid), invalid[:10],
            )
            return False

        if not self.client:
            bucket = self.mock_storage.setdefault(collection_name, [])
            incoming_ids = {p["id"] for p in points}
            bucket[:] = [p for p in bucket if p["id"] not in incoming_ids]
            bucket.extend(points)
            logger.debug("Mock: upserted %d vectors to '%s'", len(points), collection_name)
            return True

        try:
            info = self._execute_with_reconnect(
                f"inspect collection '{collection_name}'",
                lambda: self.client.get_collection(collection_name),
            )
            stored_dim: int = info.config.params.vectors.size

            if actual_dim != stored_dim:
                if not ALLOW_DESTRUCTIVE_RECREATE:
                    logger.error(
                        "Dimension mismatch for '%s': collection=%dD, vectors=%dD. Aborting.",
                        collection_name, stored_dim, actual_dim,
                    )
                    return False

                logger.warning(
                    "DIMENSION MISMATCH '%s': collection=%dD vs vectors=%dD. Recreating.",
                    collection_name, stored_dim, actual_dim,
                )
                self._execute_with_reconnect(
                    f"recreate collection '{collection_name}'",
                    lambda: self.client.delete_collection(collection_name),
                )
                self._execute_with_reconnect(
                    f"recreate collection '{collection_name}'",
                    lambda: self.client.create_collection(
                        collection_name=collection_name,
                        vectors_config=VectorParams(size=actual_dim, distance=Distance.COSINE),
                    ),
                )

        except Exception as probe_exc:
            logger.debug(
                "Could not probe collection '%s' before upsert: %s",
                collection_name, probe_exc,
            )
            if not self.create_collection(collection_name, embedding_dim=actual_dim):
                logger.error(
                    "upsert_vectors: failed to create missing collection '%s' (dim=%d)",
                    collection_name,
                    actual_dim,
                )
                return False

        try:
            total_points = len(points)

            def _do_upsert():
                for start in range(0, total_points, DEFAULT_UPSERT_BATCH_SIZE):
                    batch = points[start:start + DEFAULT_UPSERT_BATCH_SIZE]
                    qdrant_points = [
                        PointStruct(
                            id=p["id"],
                            vector=p["vector"],
                            payload=p.get("payload", {}),
                        )
                        for p in batch
                    ]
                    self.client.upsert(collection_name=collection_name, points=qdrant_points)

            self._execute_with_reconnect(
                f"upsert vectors into '{collection_name}'",
                _do_upsert,
            )
            logger.info(
                "Upserted %d vectors to '%s' (dim=%d)",
                total_points, collection_name, actual_dim,
            )
            return True

        except Exception as exc:
            error_str = str(exc).lower()

            if self._is_retryable_connection_error(exc):
                logger.error("Connection error to Qdrant at %s: %s", self.url, exc)
                self._invalidate_connection(str(exc))
            elif "not found" in error_str and "collection" in error_str:
                logger.error(
                    "Collection '%s' not found — call create_collection() first.",
                    collection_name,
                )
            else:
                logger.error(
                    "Error upserting %d vectors to '%s': %s",
                    len(points), collection_name, exc, exc_info=True,
                )
            return False

    def search_similar(
        self,
        collection_name: str,
        query_vector: List[float],
        workspace_id: str = None,
        limit: int = 10,
        score_threshold: float = DEFAULT_SCORE_THRESHOLD,
    ) -> List[Dict[str, Any]]:
        """Return the *limit* most similar vectors to *query_vector*."""
        if not query_vector:
            logger.error("search_similar: query_vector is empty — returning []")
            return []

        self._ensure_connected()
        expected_dim = self.get_collection_dimension(collection_name) or len(query_vector) or self.embedding_dim
        if not is_valid_vector(query_vector, expected_dim):
            logger.error(
                "search_similar: query vector is invalid (dim=%d, distinct=%d, "
                "expected_dim=%d) — returning [].",
                len(query_vector) if isinstance(query_vector, list) else -1,
                len(set(query_vector)) if isinstance(query_vector, list) else 0,
                expected_dim,
            )
            return []

        if not self.client:
            points = self.mock_storage.get(collection_name, [])
            scored_points = []
            for point in points:
                payload = point.get("payload", {})
                point_workspace_id = payload.get("workspace_id")
                if workspace_id is not None and str(point_workspace_id) != str(workspace_id):
                    continue
                vector = point.get("vector")
                if not is_valid_vector(vector, expected_dim):
                    continue
                similarity = cosine_similarity(query_vector, vector)
                if similarity < score_threshold:
                    continue
                scored_points.append(
                    {
                        "id": point["id"],
                        "similarity": similarity,
                        "payload": payload,
                    }
                )
            scored_points.sort(key=lambda item: item["similarity"], reverse=True)
            return scored_points[:limit]

        try:
            query_filter = None
            if workspace_id is not None:
                query_filter = Filter(
                    must=[
                        FieldCondition(
                            key="workspace_id",
                            match=MatchValue(value=str(workspace_id)),
                        )
                    ]
                )

            results = self._execute_with_reconnect(
                f"search collection '{collection_name}'",
                lambda: self.client.query_points(
                    collection_name=collection_name,
                    query=query_vector,
                    query_filter=query_filter,
                    limit=limit,
                    score_threshold=score_threshold,
                    with_payload=True,
                ).points,
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
            logger.error("Error searching collection '%s': %s", collection_name, exc)
            return []

    def delete_points(
        self,
        collection_name: str,
        ids: List[str],
    ) -> bool:
        if not ids:
            return True

        self._ensure_connected()

        if not self.client:
            id_set = set(ids)
            bucket = self.mock_storage.get(collection_name, [])
            self.mock_storage[collection_name] = [
                p for p in bucket if p["id"] not in id_set
            ]
            return True

        try:
            normalized_ids = list(dict.fromkeys(str(point_id) for point_id in ids if point_id))
            self._execute_with_reconnect(
                f"delete points from '{collection_name}'",
                lambda: self.client.delete(
                    collection_name=collection_name,
                    points_selector=normalized_ids,
                ),
            )
            logger.debug("Deleted %d vectors from '%s'", len(normalized_ids), collection_name)
            return True
        except Exception as exc:
            logger.error("Error deleting vectors from '%s': %s", collection_name, exc)
            return False

    def delete_by_filter(
        self,
        collection_name: str,
        filters: Dict[str, Any],
    ) -> bool:
        self._ensure_connected()

        if not self.client:
            bucket = self.mock_storage.get(collection_name, [])
            self.mock_storage[collection_name] = [
                p for p in bucket
                if not all(
                    str(p.get("payload", {}).get(k)) == str(v)
                    for k, v in filters.items()
                )
            ]
            return True

        try:
            conditions = [
                FieldCondition(key=k, match=MatchValue(value=str(v)))
                for k, v in filters.items()
            ]
            self._execute_with_reconnect(
                f"delete filtered points from '{collection_name}'",
                lambda: self.client.delete(
                    collection_name=collection_name,
                    points_selector=Filter(must=conditions),
                ),
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

_vector_db_client: Optional[VectorDBClient] = None


def get_vector_db_client(embedding_dim: int = None) -> VectorDBClient:
    """Return the shared VectorDBClient, creating it on first call.

    FIX B6: the original code defaulted embedding_dim to 768 when the arg was
    omitted, which means any caller that didn't know the active model's dimension
    at construction time silently stamped the singleton with the wrong value.
    `search_similar` then fell back to `self.embedding_dim` (768) for the
    `is_valid_vector` check whenever `get_collection_dimension` returned None
    (e.g. collection not yet created), rejecting valid 1536-D query vectors.

    Resolution: when no dim is provided, derive it from the EmbeddingService
    singleton so the VectorDBClient always knows the true active dimension.
    `create_collection` and `upsert_vectors` still accept explicit `embedding_dim`
    overrides — this only fixes the instance-level fallback default.
    """
    global _vector_db_client

    if _vector_db_client is None:
        if embedding_dim is None:
            # Derive the correct dimension from the active EmbeddingService
            # rather than hard-coding 768.  Import locally to avoid circular
            # imports at module load time.
            try:
                from app.services.embeddings import get_embedding_service
                embedding_dim = get_embedding_service().get_dimension()
                logger.debug(
                    "VectorDBClient singleton: derived embedding_dim=%d from EmbeddingService",
                    embedding_dim,
                )
            except Exception as exc:
                embedding_dim = 768
                logger.warning(
                    "Could not derive embedding_dim from EmbeddingService (%s); "
                    "defaulting to %d.  Ensure EMBEDDING_PROVIDER is configured "
                    "before the first vector search.",
                    exc, embedding_dim,
                )

        from app.config import settings

        _vector_db_client = VectorDBClient(
            embedding_dim=embedding_dim,
            require_qdrant=settings.QDRANT_REQUIRED,
        )

    return _vector_db_client
