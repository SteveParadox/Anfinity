"""Embedding service for generating and managing embeddings."""
import logging
from threading import RLock
from typing import List, Optional

import requests
from app import config as app_config

logger = logging.getLogger(__name__)
settings = app_config.settings
_OPENAI_EMBEDDING_DIMENSIONS = {
    "text-embedding-ada-002": 1536,
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
}

try:
    from openai import OpenAI
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False

try:
    import cohere
    HAS_COHERE = True
except ImportError:
    HAS_COHERE = False

def _ai_runtime():
    getter = getattr(app_config, "get_ai_runtime_config", None)
    if callable(getter):
        return getter()

    class _Namespace:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    return _Namespace(
        openai=_Namespace(
            api_key=getattr(settings, "OPENAI_API_KEY", None),
            embedding_model=getattr(settings, "OPENAI_EMBEDDING_MODEL", "text-embedding-3-small"),
        ),
        ollama=_Namespace(
            base_url=getattr(settings, "OLLAMA_BASE_URL", "http://localhost:11434"),
            embedding_model=getattr(settings, "OLLAMA_EMBEDDING_MODEL", "nomic-embed-text"),
            embedding_timeout=int(getattr(settings, "OLLAMA_EMBED_TIMEOUT", getattr(settings, "OLLAMA_TIMEOUT", 60)) or 60),
            embedding_batch_size=int(getattr(settings, "OLLAMA_EMBED_BATCH_SIZE", getattr(settings, "EMBEDDING_BATCH_SIZE", 32)) or 32),
        ),
        embeddings=_Namespace(
            provider=str(getattr(settings, "EMBEDDING_PROVIDER", "ollama") or "ollama").lower(),
            dimension=int(getattr(settings, "EMBEDDING_DIMENSION", 768) or 768),
            cohere_api_key=getattr(settings, "COHERE_API_KEY", None),
            cohere_model=getattr(settings, "COHERE_EMBEDDING_MODEL", "embed-english-v3.0"),
        ),
    )


class EmbeddingService:
    """Service for generating text embeddings.

    Provider priority:
        1. openai  — text-embedding-3-small  (1536-dim)
        2. cohere  — embed-english-v3.0      (1024-dim)
        3. ollama  — nomic-embed-text        (768-dim)   ← real local fallback

    Mock / all-same-value vectors are never produced.  If every provider
    fails a RuntimeError is raised so callers know the truth instead of
    silently getting useless embeddings.
    """

    def __init__(self, provider: Optional[str] = None):
        """Initialize embedding service.

        Args:
            provider: "openai", "cohere", or "ollama"
        """
        runtime = _ai_runtime()
        self.provider = (provider or runtime.embeddings.provider or "ollama").lower()
        self.model = None
        self.dimension = runtime.embeddings.dimension
        self._http: Optional[requests.Session] = None
        self._cache = None
        self._cache_lock = RLock()

        if self.provider == "openai" and HAS_OPENAI:
            self.client = OpenAI(api_key=runtime.openai.api_key)
            self.model = runtime.openai.embedding_model
            self.dimension = _OPENAI_EMBEDDING_DIMENSIONS.get(self.model, 1536)

        elif self.provider == "cohere" and HAS_COHERE:
            self.client = cohere.Client(api_key=runtime.embeddings.cohere_api_key)
            self.model = runtime.embeddings.cohere_model
            self.dimension = 1024

        elif self.provider == "ollama":
            # Ollama is handled via HTTP in embed_with_ollama(); no SDK client.
            self.client = None
            self.model = runtime.ollama.embedding_model
            self.dimension = runtime.embeddings.dimension

        else:
            # No recognised provider — do NOT silently fall back to mock data.
            # STEP 1: surface the problem immediately at construction time.
            raise ValueError(
                f"Embedding provider '{self.provider}' is not available.  "
                "Install 'openai', 'cohere', or run Ollama locally and set "
                "provider='ollama'."
            )

    def _get_cache(self):
        """Lazily create the shared embeddings cache."""
        if self._cache is not None:
            return self._cache

        with self._cache_lock:
            if self._cache is None:
                try:
                    from app.services.hybrid_embeddings_cache import HybridEmbeddingsCache

                    self._cache = HybridEmbeddingsCache(enable_l2=True)
                except Exception as exc:
                    logger.warning("Embeddings cache unavailable: %s", exc)
                    self._cache = False
        return self._cache if self._cache is not False else None

    # ------------------------------------------------------------------
    # STEP 2: real Ollama integration
    # ------------------------------------------------------------------

    def _get_http(self) -> requests.Session:
        """Create a reusable HTTP session for embedding calls."""
        if self._http is None:
            session = requests.Session()
            session.headers.update({"Content-Type": "application/json"})
            self._http = session
        return self._http

    def _validate_ollama_embeddings_response(
        self,
        data: dict,
        expected_count: int,
    ) -> List[List[float]]:
        """Normalise Ollama responses across endpoint variants."""
        embeddings = data.get("embeddings")
        if embeddings is None and "embedding" in data:
            embeddings = [data["embedding"]]
        if not isinstance(embeddings, list):
            raise RuntimeError(f"Ollama response missing embeddings payload: {data}")
        if len(embeddings) != expected_count:
            raise RuntimeError(
                f"Ollama returned {len(embeddings)} embeddings for {expected_count} inputs"
            )
        return embeddings

    def embed_with_ollama(self, texts: List[str]) -> List[List[float]]:
        """Call a local Ollama instance to generate embeddings in batches.

        Uses /api/embed with a list input so we avoid one-request-per-text
        overhead. Falls back to the legacy /api/embeddings endpoint only when
        the server does not support batch embedding.
        """
        if not texts:
            return []

        runtime = _ai_runtime()
        ollama_base_url = runtime.ollama.base_url.rstrip("/")
        ollama_model = self.model or runtime.ollama.embedding_model
        ollama_batch_size = runtime.ollama.embedding_batch_size
        ollama_timeout = runtime.ollama.embedding_timeout
        session = self._get_http()
        embeddings: List[List[float]] = []

        for start in range(0, len(texts), ollama_batch_size):
            batch = texts[start : start + ollama_batch_size]
            try:
                response = session.post(
                    f"{ollama_base_url}/api/embed",
                    json={"model": ollama_model, "input": batch},
                    timeout=ollama_timeout,
                )
                if response.status_code == 404:
                    raise requests.HTTPError("/api/embed unsupported", response=response)
                response.raise_for_status()
                embeddings.extend(
                    self._validate_ollama_embeddings_response(
                        response.json(),
                        expected_count=len(batch),
                    )
                )
                continue
            except Exception as batch_err:
                logger.warning(
                    "Batch Ollama embedding failed for %d texts, falling back to legacy mode: %s",
                    len(batch),
                    batch_err,
                )

            for text in batch:
                response = session.post(
                    f"{ollama_base_url}/api/embeddings",
                    json={"model": ollama_model, "prompt": text},
                    timeout=ollama_timeout,
                )
                if response.status_code != 200:
                    raise RuntimeError(
                        f"Ollama embedding failed (HTTP {response.status_code}): "
                        f"{response.text[:200]}"
                    )
                embeddings.extend(
                    self._validate_ollama_embeddings_response(
                        response.json(),
                        expected_count=1,
                    )
                )

        return embeddings


    # ------------------------------------------------------------------
    # BONUS: safety / sanity check
    # ------------------------------------------------------------------

    EXPECTED_DIMS = {768, 1024, 1536}

    def is_valid_embedding(self, vec):
        if not isinstance(vec, list):
            return False

        if len(vec) not in self.EXPECTED_DIMS:
            return False

        if len(set(vec)) <= 2:
            return False

        return True

    def embed_text(self, text: str) -> List[float]:
        """Embed a single text string.

        Args:
            text: Text to embed.

        Returns:
            Embedding vector.

        Raises:
            RuntimeError: If embedding fails.
        """
        cleaned = (text or "").strip()
        if not cleaned:
            return []

        cache = self._get_cache()
        cache_model = self.get_model_name()
        if cache is not None:
            cached = cache.get(cleaned, cache_model)
            if cached is not None:
                return cached

        result = self.embed_batch([cleaned])
        embedding = result[0] if result else []
        if embedding and cache is not None:
            cache.set(cleaned, self.get_model_name(), embedding)
        return embedding

    def embed_query(self, text: str) -> List[float]:
        """Embed a query string with caching enabled."""
        return self.embed_text(text)

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Embed multiple texts.

        Args:
            texts: Texts to embed.

        Returns:
            List of validated embedding vectors.

        Raises:
            RuntimeError: If every provider fails or a vector fails
                          the sanity check.
        """
        if not texts:
            return []

        # Sanitise inputs
        texts = [t.strip() for t in texts if t and t.strip()]
        if not texts:
            return []

        embeddings: List[List[float]] = []

        try:
            if self.provider == "openai" and self.client:
                response = self.client.embeddings.create(
                    model=self.model,
                    input=texts,
                )
                # Maintain original order
                sorted_data = sorted(response.data, key=lambda x: x.index)
                embeddings = [e.embedding for e in sorted_data]

            elif self.provider == "cohere" and self.client:
                response = self.client.embed(
                    texts=texts,
                    model=self.model,
                    input_type="search_document",
                )
                embeddings = response.embeddings

            elif self.provider == "ollama":
                embeddings = self.embed_with_ollama(texts)

            else:
                # STEP 1: no silent mock fallback — fail loudly.
                raise RuntimeError("Embedding generation failed: no provider configured.")

        except Exception as primary_err:
            # Primary provider failed — attempt Ollama as emergency fallback
            # only when the primary was NOT already Ollama.
            if self.provider != "ollama":
                # FIX: Check if Ollama has the same dimension as primary provider
                # to prevent silent dimension mismatches during fallback
                ollama_dimension = _ai_runtime().embeddings.dimension
                if ollama_dimension != self.dimension:
                    logger.error(
                        "Cannot fall back to Ollama: dimension mismatch. "
                        "Primary provider '%s' uses %dD, but Ollama uses %dD. "
                        "This would corrupt query embeddings. "
                        "Ensure consistent EMBEDDING_PROVIDER across ingestion and query.",
                        self.provider,
                        self.dimension,
                        ollama_dimension,
                    )
                    raise RuntimeError(
                        f"Primary embedding provider '{self.provider}' failed and "
                        f"fallback provider (Ollama) has incompatible dimensions "
                        f"({self.dimension}D vs {ollama_dimension}D). "
                        f"Cannot safely fall back. Original error: {primary_err}"
                    ) from primary_err
                
                logger.warning(
                    "Primary embedding provider '%s' failed (%s). "
                    "Falling back to Ollama (dimension-compatible).",
                    self.provider,
                    primary_err,
                )
                try:
                    embeddings = self.embed_with_ollama(texts)
                except Exception as ollama_err:
                    logger.error("Ollama fallback also failed: %s", ollama_err)
                    # STEP 1: raise — never return fake vectors.
                    raise RuntimeError(
                        f"All embedding providers failed. "
                        f"Primary error: {primary_err}. "
                        f"Ollama error: {ollama_err}"
                    ) from ollama_err
            else:
                raise RuntimeError(
                    f"Embedding generation failed: {primary_err}"
                ) from primary_err

        # BONUS: validate every vector before returning
        for i, vec in enumerate(embeddings):
            if not self.is_valid_embedding(vec):
                raise RuntimeError(
                    f"Embedding at index {i} failed sanity check "
                    f"(length={len(vec)}, distinct_values={len(set(vec))}).  "
                    "This looks like a constant/mock vector — refusing to return it."
                )

        return embeddings

    def get_dimension(self) -> int:
        """Return the embedding dimension for the active provider."""
        return self.dimension

    def get_model_name(self) -> str:
        """Return the model name for the active provider."""
        return self.model or _ai_runtime().ollama.embedding_model


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_embedding_service: "EmbeddingService | None" = None


def get_embedding_service(provider: str = None) -> EmbeddingService:
    """Get or create the global EmbeddingService singleton.

    Args:
        provider: Override the EMBEDDING_PROVIDER env-var.

    Returns:
        EmbeddingService instance.
    """
    global _embedding_service

    if _embedding_service is None:
        provider = provider or _ai_runtime().embeddings.provider
        _embedding_service = EmbeddingService(provider)

    return _embedding_service
