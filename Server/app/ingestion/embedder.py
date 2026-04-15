"""Embedding providers for text vectorization."""
from typing import List, Optional, Dict, Any
import hashlib
import json
import logging
from abc import ABC, abstractmethod
from datetime import datetime
import time

from app.config import get_ollama_request_headers, settings

logger = logging.getLogger(__name__)


class EmbeddingProvider(ABC):
    """Abstract base class for embedding providers."""
    
    @abstractmethod
    def embed(self, texts: List[str]) -> List[List[float]]:
        """Embed texts into vectors.
        
        Args:
            texts: List of texts to embed
            
        Returns:
            List of embedding vectors
        """
        pass
    
    @property
    @abstractmethod
    def dimension(self) -> int:
        """Return embedding dimension."""
        pass
    
    @property
    @abstractmethod
    def model_name(self) -> str:
        """Return model name."""
        pass
    
    @property
    def model_info(self) -> Dict[str, Any]:
        """Get model information for reproducibility tracking.
        
        Returns:
            Dict with model metadata
        """
        return {
            "provider": self.__class__.__name__,
            "model_name": self.model_name,
            "dimension": self.dimension,
            "timestamp": datetime.utcnow().isoformat()
        }


class OpenAIEmbedder(EmbeddingProvider):
    """OpenAI embedding provider (lazy imports, safe retries with fallback)."""

    def __init__(self, model: Optional[str] = None, fallback_provider: Optional[EmbeddingProvider] = None):
        try:
            from openai import OpenAI
        except Exception as e:
            raise ImportError("openai package is required for OpenAIEmbedder: pip install openai") from e

        self.client = OpenAI(api_key=settings.OPENAI_API_KEY)
        self._model = model or settings.OPENAI_EMBEDDING_MODEL
        self._dimension = self._get_dimension()
        self._fallback_provider = fallback_provider
        self._fallback_max_retries = settings.EMBEDDING_FALLBACK_MAX_RETRIES
        
        # Track which provider actually succeeded (for accurate model_name/dimension reporting)
        self._actual_provider_used = "openai"  # Default, updated if fallback succeeds

    def _get_dimension(self) -> int:
        dimensions = {
            "text-embedding-ada-002": 1536,
            "text-embedding-3-small": 1536,
            "text-embedding-3-large": 3072,
        }
        return dimensions.get(self._model, 1536)

    def embed(self, texts: List[str]) -> List[List[float]]:
        """Embed texts with OpenAI, fall back to Ollama on quota/rate limit errors."""
        batch_size = settings.EMBEDDING_BATCH_SIZE
        all_embeddings: List[List[float]] = []

        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            embedding_failed = False
            last_error: Optional[Exception] = None
            is_quota_error = False

            # Exponential backoff retries for transient API errors
            for attempt in range(self._fallback_max_retries + 1):
                try:
                    response = self.client.embeddings.create(
                        model=self._model,
                        input=batch,
                    )

                    batch_embeddings = [item.embedding for item in response.data]
                    if len(batch_embeddings) != len(batch):
                        raise ValueError("OpenAI returned unexpected number of embeddings")

                    all_embeddings.extend(batch_embeddings)
                    embedding_failed = False
                    break
                    
                except Exception as e:
                    last_error = e
                    error_str = str(e)
                    is_quota_error = "429" in error_str or "insufficient_quota" in error_str or "RateLimitError" in str(type(e).__name__)
                    
                    # Fall back to Ollama on quota/rate limit errors (after max retries)
                    if is_quota_error and attempt >= self._fallback_max_retries:
                        embedding_failed = True
                        break
                    
                    # Retry with backoff on transient errors
                    if attempt < self._fallback_max_retries:
                        time.sleep(2 ** attempt)
                        continue
                    else:
                        # Last attempt failed and not a quota error, re-raise
                        embedding_failed = True
                        break

            # Fall back to Ollama if OpenAI failed with quota error
            if embedding_failed and self._fallback_provider and is_quota_error:
                # FIX: Check if fallback provider has compatible dimensions
                primary_dim = self._dimension
                fallback_dim = self._fallback_provider.dimension
                
                if primary_dim != fallback_dim:
                    logger.error(
                        "Cannot fall back to %s: dimension mismatch detected! "
                        "Primary provider uses %dD, fallback uses %dD. "
                        "This would corrupt the collection. "
                        "SOLUTION: Either increase OpenAI quota, or re-index all documents with "
                        "EMBEDDING_PROVIDER='%s' (dimension %dD) to match your fallback provider.",
                        self._fallback_provider.__class__.__name__,
                        primary_dim,
                        fallback_dim,
                        self._fallback_provider.__class__.__name__.lower(),
                        fallback_dim,
                    )
                    raise RuntimeError(
                        f"OpenAI quota exceeded and fallback provider has incompatible dimensions "
                        f"({primary_dim}D vs {fallback_dim}D). Cannot embed. "
                        f"SOLUTION: Increase OpenAI quota or re-index with compatible provider. "
                        f"Original error: {last_error}"
                    ) from last_error
                
                logger.warning(
                    "⚠️ OpenAI embedding quota/rate limit exceeded. "
                    "Attempting fallback to %s (dimension-compatible: %dD)...",
                    self._fallback_provider.model_name,
                    fallback_dim
                )
                try:
                    fallback_embeddings = self._fallback_provider.embed(batch)
                    all_embeddings.extend(fallback_embeddings)
                    # Track that we successfully used the fallback provider
                    self._actual_provider_used = "fallback"
                    logger.info(f" Successfully embedded batch using {self._fallback_provider.model_name}")
                except Exception as fallback_error:
                    logger.error(
                        f" Fallback embedding failed: {fallback_error}\n"
                        f"   Fallback provider: {self._fallback_provider.__class__.__name__}\n"
                        f"   Base URL: {getattr(self._fallback_provider, '_base_url', 'N/A')}\n"
                        f"   Original OpenAI error: {last_error}"
                    )
                    raise RuntimeError(
                        f"Both OpenAI and {self._fallback_provider.__class__.__name__} embedding failed. "
                        f"OpenAI: {last_error}. "
                        f"{self._fallback_provider.__class__.__name__}: {fallback_error}"
                    ) from fallback_error
            elif embedding_failed and not is_quota_error:
                # Non-quota error from OpenAI, don't try fallback
                logger.error(f" OpenAI embedding failed (non-quota error): {last_error}")
                raise RuntimeError(f"OpenAI embedding failed: {last_error}") from last_error
            elif embedding_failed and not self._fallback_provider:
                raise RuntimeError(f"OpenAI embedding failed with quota/rate limit, but no fallback provider configured")

        return all_embeddings

    @property
    def dimension(self) -> int:
        # Return dimension of the provider that actually succeeded
        if self._actual_provider_used == "fallback" and self._fallback_provider:
            return self._fallback_provider.dimension
        return self._dimension

    @property
    def model_name(self) -> str:
        # Return model name of the provider that actually succeeded
        if self._actual_provider_used == "fallback" and self._fallback_provider:
            return self._fallback_provider.model_name
        return self._model



class CohereEmbedder(EmbeddingProvider):
    """Cohere embedding provider (lazy import)."""

    def __init__(self, model: Optional[str] = None):
        try:
            import cohere
        except Exception as e:
            raise ImportError("cohere package is required for CohereEmbedder: pip install cohere") from e

        self.client = cohere.Client(settings.COHERE_API_KEY)
        self._model = model or settings.COHERE_EMBEDDING_MODEL
        self._dimension = 1024

    def embed(self, texts: List[str]) -> List[List[float]]:
        batch_size = 96
        all_embeddings: List[List[float]] = []

        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            for attempt in range(3):
                try:
                    response = self.client.embed(texts=batch, model=self._model, input_type="search_document")
                    if not hasattr(response, "embeddings"):
                        raise ValueError("Cohere response missing embeddings")
                    if len(response.embeddings) != len(batch):
                        raise ValueError("Cohere returned unexpected number of embeddings")
                    all_embeddings.extend(response.embeddings)
                    break
                except Exception:
                    if attempt < 2:
                        time.sleep(2 ** attempt)
                        continue
                    raise

        return all_embeddings

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def model_name(self) -> str:
        return self._model


class BGEEmbedder(EmbeddingProvider):
    """Local BGE embedding provider using sentence-transformers (lazy import)."""

    def __init__(self, model_name: Optional[str] = None):
        try:
            from sentence_transformers import SentenceTransformer
        except Exception as e:
            raise ImportError("sentence-transformers is required for BGEEmbedder: pip install sentence-transformers") from e

        self._model_name = model_name or settings.BGE_MODEL_NAME
        self._model = SentenceTransformer(self._model_name)
        self._dimension = self._model.get_sentence_embedding_dimension()

    def embed(self, texts: List[str]) -> List[List[float]]:
        batch_size = 32
        all_embeddings: List[List[float]] = []

        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            embeddings = self._model.encode(batch, convert_to_list=True)
            all_embeddings.extend(embeddings)

        return all_embeddings

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def model_name(self) -> str:
        return self._model_name


class OllamaEmbedder(EmbeddingProvider):
    """Ollama embedding provider (local embeddings via nomic-embed-text or similar)."""

    def __init__(self, model: Optional[str] = None, base_url: Optional[str] = None, fallback_provider: Optional[EmbeddingProvider] = None):
        """Initialize Ollama embedder.
        
        Args:
            model: Ollama model name (default: nomic-embed-text)
            base_url: Ollama server URL (default: http://localhost:11434)
            fallback_provider: Provider to fall back to if Ollama fails
        """
        try:
            import requests
        except Exception as e:
            raise ImportError("requests package is required for OllamaEmbedder: pip install requests") from e
        
        self._model = model or settings.OLLAMA_EMBEDDING_MODEL
        self._base_url = (base_url or settings.OLLAMA_BASE_URL).rstrip("/")
        self._dimension = 768  # Default for nomic-embed-text; will be updated after first call
        self._requests = requests
        self._session = requests.Session()
        self._session.headers.update(get_ollama_request_headers())
        self._fallback_provider = fallback_provider
        self._actual_provider_used = "ollama"  # Track which provider was actually used
        
        logger.info(f"🔧 OllamaEmbedder initializing with base_url={self._base_url}, model={self._model}")
        
        # Verify Ollama server is accessible
        try:
            response = self._requests.get(
                f"{self._base_url}/api/tags",
                timeout=5,
                headers=get_ollama_request_headers(include_content_type=False),
            )
            response.raise_for_status()
            logger.info(f"✅ Ollama server is accessible at {self._base_url}")
        except Exception as e:
            error_msg = f"Cannot connect to Ollama server at {self._base_url}: {e}"
            logger.error(error_msg)
            raise ConnectionError(error_msg) from e

    def _normalise_embeddings_payload(self, data: Dict[str, Any], expected: int) -> List[List[float]]:
        embeddings = data.get("embeddings")
        if embeddings is None and "embedding" in data:
            embeddings = [data["embedding"]]
        if not isinstance(embeddings, list) or len(embeddings) != expected:
            raise ValueError(
                f"Ollama returned malformed embeddings payload (expected {expected}, got {type(embeddings).__name__ if embeddings is not None else 'None'})"
            )
        return embeddings

    def embed(self, texts: List[str]) -> List[List[float]]:
        """Embed texts using Ollama, with OpenAI fallback if available."""
        if not texts:
            return []

        batch_size = max(1, min(getattr(settings, "EMBEDDING_BATCH_SIZE", 48), 64))
        all_embeddings: List[List[float]] = []

        try:
            for i in range(0, len(texts), batch_size):
                batch = texts[i : i + batch_size]
                try:
                    response = self._session.post(
                        f"{self._base_url}/api/embed",
                        json={"model": self._model, "input": batch},
                        timeout=settings.OLLAMA_TIMEOUT,
                    )
                    if response.status_code == 404:
                        raise RuntimeError("/api/embed unsupported")
                    response.raise_for_status()
                    batch_embeddings = self._normalise_embeddings_payload(response.json(), len(batch))
                except Exception as batch_error:
                    logger.warning(
                        "Batch Ollama embedding failed for %d texts, falling back to legacy mode: %s",
                        len(batch),
                        batch_error,
                    )
                    batch_embeddings = []
                    for text in batch:
                        response = self._session.post(
                            f"{self._base_url}/api/embeddings",
                            json={"model": self._model, "prompt": text},
                            timeout=settings.OLLAMA_TIMEOUT,
                        )
                        response.raise_for_status()
                        batch_embeddings.extend(self._normalise_embeddings_payload(response.json(), 1))

                for embedding in batch_embeddings:
                    if self._dimension != len(embedding):
                        self._dimension = len(embedding)
                all_embeddings.extend(batch_embeddings)

        except Exception as ollama_err:
            if self._fallback_provider:
                logger.warning(
                    f"⚠️ Ollama embedding failed. Attempting fallback to {self._fallback_provider.model_name}..."
                )
                try:
                    all_embeddings = self._fallback_provider.embed(texts)
                    self._actual_provider_used = "fallback"
                    logger.info(f"✅ Successfully embedded using {self._fallback_provider.model_name}")
                    return all_embeddings
                except Exception as fallback_err:
                    logger.error(f"❌ Fallback embedding also failed: {fallback_err}")
                    raise RuntimeError(
                        f"Both Ollama and fallback embedding failed. "
                        f"Ollama: {ollama_err}. "
                        f"Fallback: {fallback_err}"
                    ) from fallback_err
            raise RuntimeError(f"Ollama embedding failed and no fallback provider configured: {ollama_err}") from ollama_err

        return all_embeddings

    @property
    def dimension(self) -> int:
        # Return dimension of provider that actually succeeded
        if self._actual_provider_used == "fallback" and self._fallback_provider:
            return self._fallback_provider.dimension
        return self._dimension

    @property
    def model_name(self) -> str:
        # Return model name of provider that actually succeeded
        if self._actual_provider_used == "fallback" and self._fallback_provider:
            return self._fallback_provider.model_name
        return self._model


class EmbeddingCache:
    """Simple in-memory cache for embeddings."""
    
    def __init__(self):
        self._cache = {}
    
    def _compute_hash(self, text: str, model: str) -> str:
        """Compute hash for text + model combination."""
        content = f"{model}:{text}"
        return hashlib.sha256(content.encode()).hexdigest()
    
    def get(self, text: str, model: str) -> Optional[List[float]]:
        """Get cached embedding."""
        key = self._compute_hash(text, model)
        return self._cache.get(key)
    
    def set(self, text: str, model: str, embedding: List[float]):
        """Cache embedding."""
        key = self._compute_hash(text, model)
        self._cache[key] = embedding
    
    def get_batch(
        self,
        texts: List[str],
        model: str,
    ) -> tuple[List[str], Dict[str, List[float]]]:
        """Get cached embeddings for batch.

        Returns:
            Tuple of (missed_texts, cached_map)
        """
        missed: List[str] = []
        cached_map: Dict[str, List[float]] = {}

        for text in texts:
            embedding = self.get(text, model)
            if embedding is not None:
                cached_map[text] = embedding
            else:
                missed.append(text)

        return missed, cached_map
    
    def set_batch(
        self,
        texts: List[str],
        model: str,
        embeddings: List[List[float]],
    ):
        """Cache embeddings for batch."""
        for text, embedding in zip(texts, embeddings):
            self.set(text, model, embedding)


class Embedder:
    """Main embedder class with hybrid caching and provider selection."""
    
    def __init__(self, provider: Optional[str] = None):
        self.provider_name = provider or settings.EMBEDDING_PROVIDER
        self._provider = self._create_provider()
        
        # Use hybrid cache (L1 in-memory + L2 Redis)
        from app.services.hybrid_embeddings_cache import HybridEmbeddingsCache
        self._cache = HybridEmbeddingsCache(enable_l2=True)

        # Lazy-safe tokenizer: try tiktoken, fallback to simple split
        try:
            import tiktoken

            self._tokenizer = tiktoken.get_encoding("cl100k_base")
            self._use_tiktoken = True
        except Exception:
            self._tokenizer = None
            self._use_tiktoken = False
    
    def _create_provider(self) -> EmbeddingProvider:
        """Create embedding provider based on configuration.
        
        Note: Swapped to use Ollama as primary (768D) and OpenAI as fallback (1536D)
        because OpenAI quota is exhausted. This avoids dimension conflicts.
        """
        # FIX: Use Ollama as PRIMARY provider (more reliable locally)
        # and OpenAI as FALLBACK (when Ollama unavailable)
        if self.provider_name == "ollama":
            # Ollama is primary - create OpenAI fallback if enabled
            fallback_provider = None
            if settings.EMBEDDING_FALLBACK_ENABLED:
                try:
                    fallback_provider = OpenAIEmbedder()
                    logger.info(f" OpenAI fallback initialized for embeddings ({fallback_provider.model_name})")
                except Exception as e:
                    logger.warning(f" OpenAI fallback unavailable: {e}. Will proceed without fallback.")
            
            return OllamaEmbedder(fallback_provider=fallback_provider)
        
        # Original providers still available if explicitly configured
        elif self.provider_name == "openai":
            # Create fallback provider if enabled
            fallback_provider = None
            if settings.EMBEDDING_FALLBACK_ENABLED:
                try:
                    fallback_provider = OllamaEmbedder()
                    logger.info(f"✅ Ollama fallback initialized for embeddings ({fallback_provider.model_name})")
                except Exception as e:
                    logger.warning(f"⚠️  Ollama fallback unavailable: {e}. Will proceed without fallback.")
            
            return OpenAIEmbedder(fallback_provider=fallback_provider)
        elif self.provider_name == "cohere":
            return CohereEmbedder()
        elif self.provider_name == "bge":
            return BGEEmbedder()
        else:
            raise ValueError(f"Unknown embedding provider: {self.provider_name}")
    
    def embed(self, texts: List[str], use_cache: bool = True) -> List[List[float]]:
        """Embed texts with optional caching.
        
        Args:
            texts: List of texts to embed
            use_cache: Whether to use caching
            
        Returns:
            List of embedding vectors
        """
        if not texts:
            return []

        if not use_cache:
            return self._provider.embed(texts)

        missed_texts, cached_map = self._cache.get_batch(texts, self._provider.model_name)

        new_embeddings: List[List[float]] = []
        if missed_texts:
            new_embeddings = self._provider.embed(missed_texts)
            if len(new_embeddings) != len(missed_texts):
                raise ValueError("Embedding provider returned unexpected number of embeddings for missed texts")
            self._cache.set_batch(missed_texts, self._provider.model_name, new_embeddings)

        # Build a mapping for newly embedded texts
        new_map: Dict[str, List[float]] = {t: e for t, e in zip(missed_texts, new_embeddings)}

        # Assemble final result preserving input order
        result: List[List[float]] = []
        for text in texts:
            if text in cached_map:
                result.append(cached_map[text])
            else:
                result.append(new_map[text])

        return result
    
    def embed_query(self, text: str) -> List[float]:
        """Embed a single query text.
        
        Args:
            text: Query text
            
        Returns:
            Embedding vector
        """
        embeddings = self.embed([text])
        return embeddings[0] if embeddings else []
    
    def count_tokens(self, text: str) -> int:
        """Count tokens in text.
        
        Args:
            text: Input text
            
        Returns:
            Token count
        """
        if self._use_tiktoken and self._tokenizer:
            return len(self._tokenizer.encode(text))
        # Fallback: approximate by whitespace
        return len(text.split())
    
    @property
    def dimension(self) -> int:
        """Get embedding dimension."""
        return self._provider.dimension
    
    @property
    def model_name(self) -> str:
        """Get model name."""
        return self._provider.model_name


# No global embedder instance is created here to avoid import-time
# side-effects (heavy model imports or missing deps). Callers should
# instantiate `Embedder(provider=...)` when needed.
