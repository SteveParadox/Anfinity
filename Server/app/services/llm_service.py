"""LLM Integration Service - Ollama primary with async OpenAI fallback."""
import asyncio
import logging
import re
import time
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Tuple

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


# ============================================================================
# Types
# ============================================================================

class LLMProvider(Enum):
    OPENAI = "openai"
    OLLAMA = "ollama"


@dataclass(frozen=True)
class LLMResponse:
    """Immutable LLM response wrapper."""
    answer: str
    tokens_used: int
    model: str
    provider: LLMProvider


# ============================================================================
# Pre-compiled patterns
# ============================================================================

_CITATION_RE = re.compile(r"\[?[Dd]ocument\s+(\d+)\]?")

_TOKEN_EXHAUSTION_FRAGMENTS = frozenset({
    "rate limit",
    "quota exceeded",
    "insufficient_quota",
    "tokens_per_min_limit_exceeded",
})


# ============================================================================
# Shared prompt helpers  (single source of truth)
# ============================================================================

_RAG_SYSTEM_PROMPT = (
    "You are a helpful assistant that answers questions based on the provided documents.\n"
    "Use only the information from the documents to answer the question.\n"
    "If the documents don't contain enough information, say so clearly.\n"
    "Cite the document numbers when referencing specific information.\n"
    "Be concise and accurate. "
    "If the retrieved text looks irrelevant, say there is not enough reliable evidence."
)


def _build_context_text(context_chunks: List[str]) -> str:
    return "\n\n".join(
        f"[Document {i + 1}]: {chunk}" for i, chunk in enumerate(context_chunks)
    )


def _build_messages(query: str, context_chunks: List[str]) -> List[dict]:
    """Assemble the full messages list consumed by both OpenAI and Ollama chat APIs."""
    user_content = f"Documents:\n{_build_context_text(context_chunks)}\n\nQuestion: {query}\n\nAnswer:"
    return [
        {"role": "system", "content": _RAG_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


# ============================================================================
# OllamaClient
# ============================================================================

class OllamaClient:
    """
    Thin wrapper around the `ollama` Python library.

    Key improvements over the original:
    - `is_available()` uses a TTL cache so the health-check HTTP call is
      not repeated on every generation request.
    - `chat()` sends the structured messages list so role boundaries are
      respected by the model.
    - `generate()` is kept for backward compatibility.
    - Availability check uses `httpx` (already a project dependency) instead
      of importing `requests` inside a method.
    """

    _AVAILABILITY_TTL = 10.0  # seconds between health checks

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "phi3:mini",
        timeout: int = 60,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.max_context_chunks = 4
        self.max_chunk_chars = 1_200

        self._available: Optional[bool] = None
        self._availability_checked_at: float = 0.0

        try:
            import ollama as _ollama_lib
            self._lib = _ollama_lib
            logger.info("✓ Ollama client initialised: %s", self.base_url)
        except ImportError:
            logger.error("ollama package not installed — Ollama backend disabled")
            self._lib = None

    # ------------------------------------------------------------------
    # Availability (TTL-cached)
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        """
        Return True if the Ollama server is reachable.
        Result is cached for `_AVAILABILITY_TTL` seconds to avoid
        a blocking HTTP round-trip on every generation call.
        """
        if self._lib is None:
            return False

        now = time.monotonic()
        if (now - self._availability_checked_at) < self._AVAILABILITY_TTL:
            return bool(self._available)

        try:
            with httpx.Client(timeout=3.0) as client:
                resp = client.get(f"{self.base_url}/api/tags")
            self._available = resp.status_code == 200
        except Exception as exc:
            logger.debug("Ollama health check failed: %s", exc)
            self._available = False

        self._availability_checked_at = now
        return bool(self._available)

    # ------------------------------------------------------------------
    # Chat (preferred — role-aware)
    # ------------------------------------------------------------------

    def chat(
        self,
        messages: List[dict],
        temperature: float = 0.3,
        num_predict: int = 1_000,
    ) -> Tuple[str, int]:
        """
        Generate a response using the Ollama chat API (role-aware).

        Args:
            messages: List of {"role": ..., "content": ...} dicts.
            temperature: Sampling temperature.
            num_predict: Max tokens to generate.

        Returns:
            (response_text, estimated_token_count)
        """
        if not self._lib:
            raise RuntimeError("Ollama client not initialised")

        logger.debug("Ollama chat (%s) temp=%s", self.model, temperature)
        response = self._lib.chat(
            model=self.model,
            messages=messages,
            stream=False,
            options={"temperature": temperature, "num_predict": num_predict},
        )
        text: str = response["message"]["content"]
        estimated_tokens = len(text) // 4
        logger.debug("Ollama chat → ~%d tokens", estimated_tokens)
        return text, estimated_tokens

    # ------------------------------------------------------------------
    # Generate (legacy / backward compat)
    # ------------------------------------------------------------------

    def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.3,
        num_predict: int = 1_000,
    ) -> Tuple[str, int]:
        """
        Generate a response using the flat Ollama generate API.
        Prefer `chat()` for new call sites.
        """
        if not self._lib:
            raise RuntimeError("Ollama client not initialised")

        full_prompt = f"{system_prompt}\n\n{prompt}" if system_prompt else prompt
        logger.debug("Ollama generate (%s) temp=%s", self.model, temperature)
        response = self._lib.generate(
            model=self.model,
            prompt=full_prompt,
            stream=False,
            options={"temperature": temperature, "num_predict": num_predict},
        )
        text: str = response.get("response", "")
        estimated_tokens = len(text) // 4
        logger.debug("Ollama generate → ~%d tokens", estimated_tokens)
        return text, estimated_tokens


# ============================================================================
# LLMService  (sync interface — wraps both backends)
# ============================================================================

class LLMService:
    """
    Generates answers using configurable primary + fallback LLM providers.

    Both sync (`generate_answer`) and async (`async_generate_answer`) APIs
    are exposed.  The async path uses `AsyncOpenAI` directly and offloads
    the blocking Ollama call to a thread executor so it never stalls the
    event loop.
    """

    def __init__(
        self,
        openai_api_key: Optional[str] = None,
        openai_model: str = "gpt-4o-mini",
        ollama_base_url: str = "http://localhost:11434",
        ollama_model: str = "phi3:mini",
        use_fallback: bool = True,
        primary_provider: str = "ollama",
    ) -> None:
        self.openai_model = openai_model
        self.use_fallback = use_fallback
        self.primary_provider = primary_provider

        # Sync OpenAI client (for sync path only)
        self._openai_sync = None
        api_key = openai_api_key or settings.OPENAI_API_KEY
        if api_key:
            try:
                from openai import OpenAI
                self._openai_sync = OpenAI(api_key=api_key, timeout=settings.OPENAI_TIMEOUT)
                logger.info("✓ OpenAI sync client initialised: %s", openai_model)
            except ImportError:
                logger.error("openai package not installed")

        self.ollama_client = OllamaClient(
            base_url=ollama_base_url,
            model=ollama_model,
            timeout=settings.OLLAMA_TIMEOUT,
        )

        # Provider dispatch tables — easy to extend with a third provider.
        self._sync_providers = {
            "openai": self._sync_openai,
            "ollama": self._sync_ollama,
        }
        self._async_providers = {
            "openai": self._async_openai,
            "ollama": self._async_ollama,
        }

    # ------------------------------------------------------------------
    # Internal sync backends
    # ------------------------------------------------------------------

    def _sync_ollama(self, messages: List[dict], temperature: float, max_tokens: int) -> LLMResponse:
        if not self.ollama_client.is_available():
            raise RuntimeError("Ollama not available")
        text, tokens = self.ollama_client.chat(messages, temperature=temperature, num_predict=max_tokens)
        return LLMResponse(answer=text, tokens_used=tokens, model=self.ollama_client.model, provider=LLMProvider.OLLAMA)

    def _sync_openai(self, messages: List[dict], temperature: float, max_tokens: int) -> LLMResponse:
        if not self._openai_sync:
            raise RuntimeError("OpenAI client not configured")
        resp = self._openai_sync.chat.completions.create(
            model=self.openai_model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return LLMResponse(
            answer=resp.choices[0].message.content or "",
            tokens_used=resp.usage.total_tokens,
            model=self.openai_model,
            provider=LLMProvider.OPENAI,
        )

    # ------------------------------------------------------------------
    # Internal async backends
    # ------------------------------------------------------------------

    async def _async_ollama(self, messages: List[dict], temperature: float, max_tokens: int) -> LLMResponse:
        """Run blocking Ollama call in a thread executor."""
        if not self.ollama_client.is_available():
            raise RuntimeError("Ollama not available")
        loop = asyncio.get_event_loop()
        text, tokens = await loop.run_in_executor(
            None,
            lambda: self.ollama_client.chat(messages, temperature=temperature, num_predict=max_tokens),
        )
        return LLMResponse(answer=text, tokens_used=tokens, model=self.ollama_client.model, provider=LLMProvider.OLLAMA)

    async def _async_openai(self, messages: List[dict], temperature: float, max_tokens: int) -> LLMResponse:
        """True async OpenAI call — no executor needed."""
        try:
            from openai import AsyncOpenAI
        except ImportError:
            raise RuntimeError("openai package not installed")

        api_key = getattr(settings, "OPENAI_API_KEY", None)
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY not configured")

        client = AsyncOpenAI(api_key=api_key, timeout=getattr(settings, "OPENAI_TIMEOUT", 30.0))
        resp = await client.chat.completions.create(
            model=self.openai_model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return LLMResponse(
            answer=resp.choices[0].message.content or "",
            tokens_used=resp.usage.total_tokens,
            model=self.openai_model,
            provider=LLMProvider.OPENAI,
        )

    # ------------------------------------------------------------------
    # Provider chain (generic, works for sync and async callables)
    # ------------------------------------------------------------------

    def _provider_order(self) -> List[str]:
        """Return [primary, fallback] provider names."""
        primary = self.primary_provider
        fallback = "openai" if primary == "ollama" else "ollama"
        return [primary] if not self.use_fallback else [primary, fallback]

    def _is_token_exhaustion(self, err: str) -> bool:
        lower = err.lower()
        return any(f in lower for f in _TOKEN_EXHAUSTION_FRAGMENTS)

    # ------------------------------------------------------------------
    # Public sync API
    # ------------------------------------------------------------------

    def generate_answer(
        self,
        query: str,
        context_chunks: List[str],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        force_ollama: bool = False,
    ) -> LLMResponse:
        """
        Generate an answer synchronously.
        Tries providers in configured order; raises RuntimeError if all fail.
        
        Args:
            query: The query string
            context_chunks: Context chunks for generation
            temperature: Model temperature (optional)
            max_tokens: Max tokens in response (optional)
            force_ollama: If True, skip directly to Ollama provider
        """
        temperature = temperature or settings.LLM_TEMPERATURE
        max_tokens = max_tokens or settings.LLM_MAX_TOKENS
        messages = _build_messages(query, context_chunks)
        last_exc: Optional[Exception] = None

        # Determine provider order: if force_ollama, prioritize Ollama
        if force_ollama:
            providers_to_try = ["ollama", "openai"]
        else:
            providers_to_try = list(self._provider_order())

        for provider_name in providers_to_try:
            fn = self._sync_providers.get(provider_name)
            if fn is None:
                continue
            try:
                logger.info("Trying sync provider: %s", provider_name)
                result = fn(messages, temperature, max_tokens)
                logger.info("✓ %s answered (%d tokens)", provider_name, result.tokens_used)
                return result
            except Exception as exc:
                logger.warning("%s failed: %s", provider_name, exc)
                if self._is_token_exhaustion(str(exc)):
                    logger.warning("Token exhaustion detected on %s", provider_name)
                last_exc = exc

        raise RuntimeError(f"All LLM providers failed. Last error: {last_exc}")

    # ------------------------------------------------------------------
    # Public async API
    # ------------------------------------------------------------------

    async def async_generate_answer(
        self,
        query: str,
        context_chunks: List[str],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        force_ollama: bool = False,
    ) -> LLMResponse:
        """
        Generate an answer asynchronously.
        Tries providers in configured order without blocking the event loop.
        
        Args:
            query: The query string
            context_chunks: Context chunks for generation
            temperature: Model temperature (optional)
            max_tokens: Max tokens in response (optional)
            force_ollama: If True, skip directly to Ollama provider
        """
        temperature = temperature or settings.LLM_TEMPERATURE
        max_tokens = max_tokens or settings.LLM_MAX_TOKENS
        messages = _build_messages(query, context_chunks)
        last_exc: Optional[Exception] = None

        # Determine provider order: if force_ollama, prioritize Ollama
        if force_ollama:
            providers_to_try = ["ollama", "openai"]
        else:
            providers_to_try = list(self._provider_order())

        for provider_name in providers_to_try:
            fn = self._async_providers.get(provider_name)
            if fn is None:
                continue
            try:
                logger.info("Trying async provider: %s", provider_name)
                result = await fn(messages, temperature, max_tokens)
                logger.info("✓ %s answered (%d tokens)", provider_name, result.tokens_used)
                return result
            except Exception as exc:
                logger.warning("%s failed: %s", provider_name, exc)
                if self._is_token_exhaustion(str(exc)):
                    logger.warning("Token exhaustion detected on %s", provider_name)
                last_exc = exc

        raise RuntimeError(f"All LLM providers failed. Last error: {last_exc}")

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    def extract_citations(self, text: str) -> List[int]:
        """Extract sorted, deduplicated [Document N] citations from answer text."""
        return sorted({int(m.group(1)) for m in _CITATION_RE.finditer(text)})

    def get_status(self) -> dict:
        return {
            "openai_available": self._openai_sync is not None,
            "openai_model": self.openai_model,
            "ollama_available": self.ollama_client.is_available(),
            "ollama_model": self.ollama_client.model,
            "fallback_enabled": self.use_fallback,
            "primary_provider": self.primary_provider,
        }


# ============================================================================
# Singleton
# ============================================================================

_llm_service: Optional[LLMService] = None


def get_llm_service() -> LLMService:
    """
    Return the application-wide LLMService singleton.

    Configuration is sourced entirely from `settings` so there is no
    ambiguity about which values take effect — callers cannot accidentally
    override the singleton with a different config mid-process.
    """
    global _llm_service
    if _llm_service is None:
        _llm_service = LLMService(
            openai_api_key=settings.OPENAI_API_KEY,
            openai_model=settings.OPENAI_MODEL,
            ollama_base_url=settings.OLLAMA_BASE_URL,
            ollama_model=settings.OLLAMA_MODEL,
            use_fallback=settings.LLM_USE_FALLBACK,
            primary_provider=settings.LLM_PROVIDER,
        )
        logger.info(
            "LLMService singleton created — primary=%s fallback=%s",
            settings.LLM_PROVIDER,
            settings.LLM_USE_FALLBACK,
        )
    return _llm_service