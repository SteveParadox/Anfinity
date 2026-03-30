"""Embedding service for generating and managing embeddings."""
import os
import requests
from typing import List
import logging

logger = logging.getLogger(__name__)

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


# STEP 3: nomic-embed-text produces 768-dim vectors, not 1536.
OLLAMA_MODEL = "nomic-embed-text"
OLLAMA_DIMENSION = 768
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")


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

    def __init__(self, provider: str = "openai"):
        """Initialize embedding service.

        Args:
            provider: "openai", "cohere", or "ollama"
        """
        self.provider = provider
        self.model = None
        self.dimension = OLLAMA_DIMENSION  # safe default

        if provider == "openai" and HAS_OPENAI:
            self.client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
            self.model = "text-embedding-3-small"
            self.dimension = 1536  # FIXED: text-embedding-3-small is 1536D, not 768D

        elif provider == "cohere" and HAS_COHERE:
            self.client = cohere.Client(api_key=os.getenv("COHERE_API_KEY"))
            self.model = "embed-english-v3.0"
            self.dimension = 1024

        elif provider == "ollama":
            # Ollama is handled via HTTP in embed_with_ollama(); no SDK client.
            self.client = None
            self.dimension = OLLAMA_DIMENSION

        else:
            # No recognised provider — do NOT silently fall back to mock data.
            # STEP 1: surface the problem immediately at construction time.
            raise ValueError(
                f"Embedding provider '{provider}' is not available.  "
                "Install 'openai', 'cohere', or run Ollama locally and set "
                "provider='ollama'."
            )

    # ------------------------------------------------------------------
    # STEP 2: real Ollama integration
    # ------------------------------------------------------------------

    def embed_with_ollama(self, texts: List[str]) -> List[List[float]]:
        """Call a local Ollama instance to generate embeddings.

        Args:
            texts: Texts to embed.

        Returns:
            List of embedding vectors.

        Raises:
            RuntimeError: If the Ollama API returns a non-200 status or the
                          response is malformed.
        """
        embeddings: List[List[float]] = []

        for text in texts:
            response = requests.post(
                f"{OLLAMA_BASE_URL}/api/embeddings",
                json={"model": OLLAMA_MODEL, "prompt": text},
                timeout=30,
            )

            if response.status_code != 200:
                raise RuntimeError(
                    f"Ollama embedding failed (HTTP {response.status_code}): "
                    f"{response.text[:200]}"
                )

            data = response.json()

            if "embedding" not in data:
                raise RuntimeError(
                    f"Ollama response missing 'embedding' key: {data}"
                )

            embeddings.append(data["embedding"])

        return embeddings

    # ------------------------------------------------------------------
    # BONUS: safety / sanity check
    # ------------------------------------------------------------------

    EXPECTED_DIMS = {768, 1024, 1536}

    def is_valid_embedding(self, vec):
        if not isinstance(vec, list):
            return False

        if len(vec) not in EXPECTED_DIMS:
            return False

        if len(set(vec)) <= 2:
            return False

        return True
    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def embed_text(self, text: str) -> List[float]:
        """Embed a single text.

        Args:
            text: Text to embed.

        Returns:
            Embedding vector.
        """
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Embed multiple texts in batch.

        Args:
            texts: List of texts to embed.

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
                logger.warning(
                    "Primary embedding provider '%s' failed (%s). "
                    "Falling back to Ollama.",
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
        return self.model or OLLAMA_MODEL


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
        provider = provider or os.getenv("EMBEDDING_PROVIDER", "openai")
        _embedding_service = EmbeddingService(provider)

    return _embedding_service