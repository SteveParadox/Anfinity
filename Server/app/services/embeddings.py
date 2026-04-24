"""Embedding service for generating and managing embeddings."""
from dataclasses import dataclass
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

try:
    import tiktoken

    HAS_TIKTOKEN = True
except ImportError:
    HAS_TIKTOKEN = False


@dataclass
class _PreparedOllamaInput:
    original_text: str
    segments: List[str]

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
            api_key=getattr(settings, "OLLAMA_API_KEY", None),
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

    MAX_SINGLE_EMBED_TEXT_CHARS = 6000
    LONG_TEXT_CHUNK_CHARS = 5000
    LONG_TEXT_CHUNK_OVERLAP_CHARS = 500
    MAX_LONG_TEXT_CHUNKS = 8
    OLLAMA_SAFE_MAX_INPUT_TOKENS_768 = 1536
    OLLAMA_SAFE_MAX_INPUT_TOKENS_DEFAULT = 2000
    OLLAMA_SAFE_MAX_INPUT_CHARS_768 = 6000
    OLLAMA_SAFE_MAX_INPUT_CHARS_DEFAULT = 8000
    MAX_OLLAMA_SPLIT_DEPTH = 6

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
        self._tokenizer = None
        self._use_tiktoken = False

        if HAS_TIKTOKEN:
            try:
                self._tokenizer = tiktoken.get_encoding("cl100k_base")
                self._use_tiktoken = True
            except Exception:
                self._tokenizer = None
                self._use_tiktoken = False

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
            getter = getattr(app_config, "get_ollama_request_headers", None)
            if callable(getter):
                session.headers.update(getter())
            else:
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

    def _count_tokens(self, text: str) -> int:
        cleaned = (text or "").strip()
        if not cleaned:
            return 0
        if self._use_tiktoken and self._tokenizer is not None:
            try:
                return len(self._tokenizer.encode(cleaned))
            except Exception:
                pass
        return max(len(cleaned.split()), (len(cleaned) + 3) // 4)

    def _get_ollama_input_budget(self, model_name: Optional[str] = None) -> tuple[int, int]:
        resolved_model = str(model_name or self.get_model_name() or "").lower()
        is_768_dim_model = self.dimension == 768 or "nomic-embed" in resolved_model
        if is_768_dim_model:
            return (
                self.OLLAMA_SAFE_MAX_INPUT_TOKENS_768,
                self.OLLAMA_SAFE_MAX_INPUT_CHARS_768,
            )
        return (
            self.OLLAMA_SAFE_MAX_INPUT_TOKENS_DEFAULT,
            self.OLLAMA_SAFE_MAX_INPUT_CHARS_DEFAULT,
        )

    def _is_within_ollama_budget(
        self,
        text: str,
        *,
        token_budget: Optional[int] = None,
        char_budget: Optional[int] = None,
    ) -> bool:
        resolved_token_budget, resolved_char_budget = self._get_ollama_input_budget()
        token_budget = token_budget or resolved_token_budget
        char_budget = char_budget or resolved_char_budget
        cleaned = (text or "").strip()
        if not cleaned:
            return True
        return len(cleaned) <= char_budget and self._count_tokens(cleaned) <= token_budget

    def _truncate_text_to_budget(
        self,
        text: str,
        *,
        token_budget: Optional[int] = None,
        char_budget: Optional[int] = None,
    ) -> str:
        resolved_token_budget, resolved_char_budget = self._get_ollama_input_budget()
        token_budget = token_budget or resolved_token_budget
        char_budget = char_budget or resolved_char_budget
        cleaned = (text or "").strip()
        if not cleaned:
            return ""

        truncated = cleaned[:char_budget].strip()
        if not truncated:
            return ""

        while truncated and self._count_tokens(truncated) > token_budget:
            next_len = max(int(len(truncated) * 0.85), min(len(truncated) - 1, char_budget // 2))
            if next_len >= len(truncated):
                next_len = len(truncated) - 1
            if next_len <= 0:
                break
            truncated = truncated[:next_len].strip()

        return truncated or cleaned[: max(1, min(len(cleaned), char_budget // 2))].strip()

    @staticmethod
    def _find_split_position(text: str) -> int:
        midpoint = len(text) // 2
        delimiters = ("\n\n", "\n", ". ", "? ", "! ", "; ", ", ", " ")

        best_position = -1
        best_distance = None
        for delimiter in delimiters:
            left = text.rfind(delimiter, 0, midpoint)
            right = text.find(delimiter, midpoint)
            for candidate in (left, right):
                if candidate <= 0 or candidate >= len(text):
                    continue
                distance = abs(candidate - midpoint)
                if best_distance is None or distance < best_distance:
                    best_distance = distance
                    best_position = candidate + (len(delimiter) if delimiter.strip() else 0)

        if best_position > 0:
            return best_position
        return midpoint

    def _split_text_to_ollama_segments(
        self,
        text: str,
        *,
        token_budget: Optional[int] = None,
        char_budget: Optional[int] = None,
        force_split: bool = False,
        depth: int = 0,
    ) -> List[str]:
        resolved_token_budget, resolved_char_budget = self._get_ollama_input_budget()
        token_budget = token_budget or resolved_token_budget
        char_budget = char_budget or resolved_char_budget
        cleaned = (text or "").strip()
        if not cleaned:
            return []

        if not force_split and self._is_within_ollama_budget(
            cleaned,
            token_budget=token_budget,
            char_budget=char_budget,
        ):
            return [cleaned]

        if depth >= self.MAX_OLLAMA_SPLIT_DEPTH or len(cleaned) <= max(256, char_budget // 2):
            truncated = self._truncate_text_to_budget(
                cleaned,
                token_budget=token_budget,
                char_budget=char_budget,
            )
            return [truncated] if truncated else []

        split_at = self._find_split_position(cleaned)
        split_at = max(1, min(split_at, len(cleaned) - 1))
        left = cleaned[:split_at].strip()
        right = cleaned[split_at:].strip()

        if not left or not right:
            midpoint = max(1, min(len(cleaned) - 1, len(cleaned) // 2))
            left = cleaned[:midpoint].strip()
            right = cleaned[midpoint:].strip()

        if not left or not right:
            truncated = self._truncate_text_to_budget(
                cleaned,
                token_budget=token_budget,
                char_budget=char_budget,
            )
            return [truncated] if truncated else []

        segments: List[str] = []
        segments.extend(
            self._split_text_to_ollama_segments(
                left,
                token_budget=token_budget,
                char_budget=char_budget,
                depth=depth + 1,
            )
        )
        segments.extend(
            self._split_text_to_ollama_segments(
                right,
                token_budget=token_budget,
                char_budget=char_budget,
                depth=depth + 1,
            )
        )
        return segments

    def _prepare_ollama_inputs(self, texts: List[str]) -> List[_PreparedOllamaInput]:
        token_budget, char_budget = self._get_ollama_input_budget()
        prepared: List[_PreparedOllamaInput] = []

        for text in texts:
            segments = self._split_text_to_ollama_segments(
                text,
                token_budget=token_budget,
                char_budget=char_budget,
            )
            if len(segments) > 1:
                logger.info(
                    "Splitting oversized Ollama embedding input for model=%s into %d segments (chars=%d tokens=%d)",
                    self.get_model_name(),
                    len(segments),
                    len(text),
                    self._count_tokens(text),
                )
            prepared.append(_PreparedOllamaInput(original_text=text, segments=segments or [""]))

        return prepared

    @staticmethod
    def _extract_ollama_error_details(exc: Exception) -> tuple[Optional[int], str]:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
        response_text = ""
        if response is not None:
            response_text = str(getattr(response, "text", "") or "")
        return status, response_text or str(exc)

    def _is_ollama_context_length_error(self, exc: Exception) -> bool:
        status, payload = self._extract_ollama_error_details(exc)
        lowered = payload.lower()
        return (
            status == 400
            and (
                "context length" in lowered
                or "maximum context length" in lowered
                or "input length" in lowered
                or "too many tokens" in lowered
            )
        )

    def _is_ollama_embed_endpoint_unsupported(self, exc: Exception) -> bool:
        status, payload = self._extract_ollama_error_details(exc)
        return status == 404 or "/api/embed unsupported" in payload.lower()

    def _log_ollama_retry(
        self,
        *,
        exc: Exception,
        text: str,
        batch_size: int,
        retry_strategy: str,
        model_name: str,
    ) -> None:
        status, payload = self._extract_ollama_error_details(exc)
        logger.warning(
            "Ollama embedding retry: model=%s batch_size=%d failed_item_chars=%d failed_item_tokens=%d strategy=%s status=%s error=%s",
            model_name,
            batch_size,
            len(text),
            self._count_tokens(text),
            retry_strategy,
            status,
            payload[:240],
        )

    def _post_ollama_embed(
        self,
        *,
        session: requests.Session,
        base_url: str,
        model_name: str,
        timeout: int,
        batch: List[str],
    ) -> List[List[float]]:
        response = session.post(
            f"{base_url}/api/embed",
            json={"model": model_name, "input": batch},
            timeout=timeout,
        )
        if response.status_code == 404:
            raise requests.HTTPError("/api/embed unsupported", response=response)
        response.raise_for_status()
        return self._validate_ollama_embeddings_response(
            response.json(),
            expected_count=len(batch),
        )

    def _post_ollama_legacy_single(
        self,
        *,
        session: requests.Session,
        base_url: str,
        model_name: str,
        timeout: int,
        text: str,
    ) -> List[float]:
        response = session.post(
            f"{base_url}/api/embeddings",
            json={"model": model_name, "prompt": text},
            timeout=timeout,
        )
        response.raise_for_status()
        return self._validate_ollama_embeddings_response(
            response.json(),
            expected_count=1,
        )[0]

    def _embed_single_ollama_text(
        self,
        *,
        session: requests.Session,
        base_url: str,
        model_name: str,
        timeout: int,
        text: str,
        depth: int = 0,
    ) -> List[float]:
        try:
            return self._post_ollama_embed(
                session=session,
                base_url=base_url,
                model_name=model_name,
                timeout=timeout,
                batch=[text],
            )[0]
        except Exception as exc:
            self._log_ollama_retry(
                exc=exc,
                text=text,
                batch_size=1,
                retry_strategy="retry-individual",
                model_name=model_name,
            )

            if not self._is_ollama_embed_endpoint_unsupported(exc):
                try:
                    return self._post_ollama_legacy_single(
                        session=session,
                        base_url=base_url,
                        model_name=model_name,
                        timeout=timeout,
                        text=text,
                    )
                except Exception as legacy_exc:
                    exc = legacy_exc
                    self._log_ollama_retry(
                        exc=legacy_exc,
                        text=text,
                        batch_size=1,
                        retry_strategy="legacy-single",
                        model_name=model_name,
                    )

            if self._is_ollama_context_length_error(exc):
                token_budget, char_budget = self._get_ollama_input_budget(model_name)
                if depth < self.MAX_OLLAMA_SPLIT_DEPTH and len(text) > 1:
                    segments = self._split_text_to_ollama_segments(
                        text,
                        token_budget=max(128, token_budget // 2),
                        char_budget=max(256, char_budget // 2),
                        force_split=True,
                    )
                    if len(segments) > 1:
                        self._log_ollama_retry(
                            exc=exc,
                            text=text,
                            batch_size=1,
                            retry_strategy=f"split-item->{len(segments)}",
                            model_name=model_name,
                        )
                        child_embeddings = self._embed_ollama_batches(
                            texts=segments,
                            session=session,
                            base_url=base_url,
                            model_name=model_name,
                            timeout=timeout,
                            batch_size=1,
                            depth=depth + 1,
                        )
                        return self._pool_embeddings(
                            child_embeddings,
                            weights=[len(segment) for segment in segments],
                        )

                truncated = self._truncate_text_to_budget(
                    text,
                    token_budget=max(128, token_budget // 2),
                    char_budget=max(256, char_budget // 2),
                )
                if truncated and truncated != text and depth < self.MAX_OLLAMA_SPLIT_DEPTH:
                    self._log_ollama_retry(
                        exc=exc,
                        text=text,
                        batch_size=1,
                        retry_strategy=f"truncate-item->{len(truncated)}",
                        model_name=model_name,
                    )
                    return self._embed_single_ollama_text(
                        session=session,
                        base_url=base_url,
                        model_name=model_name,
                        timeout=timeout,
                        text=truncated,
                        depth=depth + 1,
                    )

            raise

    def _embed_ollama_batches(
        self,
        *,
        texts: List[str],
        session: requests.Session,
        base_url: str,
        model_name: str,
        timeout: int,
        batch_size: int,
        depth: int = 0,
    ) -> List[List[float]]:
        if not texts:
            return []

        embeddings: List[List[float]] = []
        index = 0
        current_batch_size = max(1, batch_size)

        while index < len(texts):
            batch = texts[index : index + current_batch_size]
            try:
                embeddings.extend(
                    self._post_ollama_embed(
                        session=session,
                        base_url=base_url,
                        model_name=model_name,
                        timeout=timeout,
                        batch=batch,
                    )
                )
                index += len(batch)
                continue
            except Exception as exc:
                if len(batch) > 1:
                    next_batch_size = max(1, len(batch) // 2)
                    self._log_ollama_retry(
                        exc=exc,
                        text=max(batch, key=len),
                        batch_size=len(batch),
                        retry_strategy=f"halve-batch->{next_batch_size}",
                        model_name=model_name,
                    )
                    embeddings.extend(
                        self._embed_ollama_batches(
                            texts=batch,
                            session=session,
                            base_url=base_url,
                            model_name=model_name,
                            timeout=timeout,
                            batch_size=next_batch_size,
                            depth=depth + 1,
                        )
                    )
                    index += len(batch)
                    continue

                embeddings.append(
                    self._embed_single_ollama_text(
                        session=session,
                        base_url=base_url,
                        model_name=model_name,
                        timeout=timeout,
                        text=batch[0],
                        depth=depth + 1,
                    )
                )
                index += 1

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
        ollama_batch_size = max(1, runtime.ollama.embedding_batch_size)
        ollama_timeout = runtime.ollama.embedding_timeout
        session = self._get_http()
        prepared_inputs = self._prepare_ollama_inputs(texts)
        flattened_segments: List[str] = []
        spans: List[tuple[int, int, List[int]]] = []

        for prepared in prepared_inputs:
            start = len(flattened_segments)
            flattened_segments.extend(prepared.segments)
            spans.append((start, len(prepared.segments), [len(segment) for segment in prepared.segments]))

        segment_embeddings = self._embed_ollama_batches(
            texts=flattened_segments,
            session=session,
            base_url=ollama_base_url,
            model_name=ollama_model,
            timeout=ollama_timeout,
            batch_size=ollama_batch_size,
        )

        pooled_embeddings: List[List[float]] = []
        for start, count, weights in spans:
            current_embeddings = segment_embeddings[start : start + count]
            if count > 1:
                pooled_embeddings.append(self._pool_embeddings(current_embeddings, weights=weights))
            else:
                pooled_embeddings.append(current_embeddings[0] if current_embeddings else [])

        return pooled_embeddings


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

    def _split_long_text_for_embedding(self, text: str) -> List[str]:
        """Split a long text into overlapping windows safe for local embedders."""
        cleaned = (text or "").strip()
        if not cleaned:
            return []
        if len(cleaned) <= self.MAX_SINGLE_EMBED_TEXT_CHARS:
            return [cleaned]

        chunk_size = self.LONG_TEXT_CHUNK_CHARS
        overlap = min(self.LONG_TEXT_CHUNK_OVERLAP_CHARS, max(chunk_size // 4, 1))
        step = max(chunk_size - overlap, 1)

        chunks: List[str] = []
        start = 0
        while start < len(cleaned) and len(chunks) < self.MAX_LONG_TEXT_CHUNKS:
            end = min(start + chunk_size, len(cleaned))
            window = cleaned[start:end].strip()
            if window:
                chunks.append(window)
            if end >= len(cleaned):
                break
            start += step

        return chunks or [cleaned[: self.MAX_SINGLE_EMBED_TEXT_CHARS]]

    def _pool_embeddings(
        self,
        embeddings: List[List[float]],
        weights: Optional[List[float]] = None,
    ) -> List[float]:
        """Combine multiple chunk embeddings into a single vector."""
        if not embeddings:
            return []
        if len(embeddings) == 1:
            return embeddings[0]

        dimension = len(embeddings[0])
        if weights is None or len(weights) != len(embeddings):
            weights = [1.0] * len(embeddings)

        total_weight = sum(max(float(weight), 0.0) for weight in weights) or float(len(embeddings))
        pooled = [0.0] * dimension

        for embedding, weight in zip(embeddings, weights):
            scaled_weight = max(float(weight), 0.0)
            for index, value in enumerate(embedding):
                pooled[index] += float(value) * scaled_weight

        return [value / total_weight for value in pooled]

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

        embed_inputs = self._split_long_text_for_embedding(cleaned)
        if len(embed_inputs) > 1:
            logger.info(
                "Long embedding input detected for model=%s; splitting into %d windows (chars=%d)",
                cache_model,
                len(embed_inputs),
                len(cleaned),
            )

        result = self.embed_batch(embed_inputs)
        if len(result) == len(embed_inputs) and len(result) > 1:
            weights = [len(chunk) for chunk in embed_inputs]
            embedding = self._pool_embeddings(result, weights=weights)
        else:
            embedding = result[0] if result else []

        if embedding and cache is not None:
            cache.set(cleaned, self.get_model_name(), embedding)
        return embedding

    def embed_query(self, text: str) -> List[float]:
        """Embed a query string with caching enabled."""
        return self.embed_text(text)

    def _embed_batch_uncached(self, texts: List[str]) -> List[List[float]]:
        if self.provider == "openai" and self.client:
            response = self.client.embeddings.create(
                model=self.model,
                input=texts,
            )
            sorted_data = sorted(response.data, key=lambda x: x.index)
            return [e.embedding for e in sorted_data]

        if self.provider == "cohere" and self.client:
            response = self.client.embed(
                texts=texts,
                model=self.model,
                input_type="search_document",
            )
            return response.embeddings

        if self.provider == "ollama":
            return self.embed_with_ollama(texts)

        raise RuntimeError("Embedding generation failed: no provider configured.")

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

        texts = [t.strip() for t in texts if t and t.strip()]
        if not texts:
            return []

        cache = self._get_cache()
        cache_model = self.get_model_name()
        cached_embeddings: dict[str, List[float]] = {}
        unique_texts = list(dict.fromkeys(texts))
        missing_texts: List[str] = []

        if cache is not None:
            for text in unique_texts:
                cached = cache.get(text, cache_model)
                if cached is not None:
                    cached_embeddings[text] = cached
                else:
                    missing_texts.append(text)
        else:
            missing_texts = unique_texts

        fresh_embeddings: dict[str, List[float]] = {}

        try:
            if missing_texts:
                generated_embeddings = self._embed_batch_uncached(missing_texts)
                if len(generated_embeddings) != len(missing_texts):
                    raise RuntimeError(
                        f"Embedding provider returned {len(generated_embeddings)} embeddings for {len(missing_texts)} texts"
                    )
                fresh_embeddings = {
                    text: embedding
                    for text, embedding in zip(missing_texts, generated_embeddings)
                }
                if cache is not None:
                    for text, embedding in fresh_embeddings.items():
                        cache.set(text, self.get_model_name(), embedding)

        except Exception as primary_err:
            if self.provider != "ollama":
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
                    fallback_targets = missing_texts or texts
                    fallback_embeddings = self.embed_with_ollama(fallback_targets)
                    fresh_embeddings = {
                        text: embedding
                        for text, embedding in zip(fallback_targets, fallback_embeddings)
                    }
                    if cache is not None:
                        for text, embedding in fresh_embeddings.items():
                            cache.set(text, self.get_model_name(), embedding)
                except Exception as ollama_err:
                    logger.error("Ollama fallback also failed: %s", ollama_err)
                    raise RuntimeError(
                        f"All embedding providers failed. "
                        f"Primary error: {primary_err}. "
                        f"Ollama error: {ollama_err}"
                    ) from ollama_err
            else:
                raise RuntimeError(
                    f"Embedding generation failed: {primary_err}"
                ) from primary_err

        embeddings = [
            cached_embeddings.get(text) or fresh_embeddings[text]
            for text in texts
        ]

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
