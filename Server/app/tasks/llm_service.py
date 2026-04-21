"""LLM integration service with centralized provider configuration."""

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Tuple

import httpx

from app import config as app_config

logger = logging.getLogger(__name__)
settings = app_config.settings
_OLLAMA_MAX_CONCURRENT_REQUESTS = max(
    1,
    int(getattr(settings, "OLLAMA_MAX_CONCURRENT_REQUESTS", 2) or 2),
)
_OLLAMA_ASYNC_SEMAPHORE = asyncio.Semaphore(_OLLAMA_MAX_CONCURRENT_REQUESTS)


SUPPORTED_OLLAMA_MODELS = {
    "phi3:mini",
    "gpt-oss:20b-cloud",
    "gpt-oss:120b-cloud",
    "qwen2:0.5b",
}


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


_CITATION_RE = re.compile(r"\[?[Dd]ocument\s+(\d+)\]?")
_TOKEN_EXHAUSTION_FRAGMENTS = frozenset(
    {
        "rate limit",
        "quota exceeded",
        "insufficient_quota",
        "tokens_per_min_limit_exceeded",
    }
)

_RAG_SYSTEM_PROMPT = (
    "You are a helpful assistant that answers questions based on the provided documents.\n"
    "Use only the information from the documents to answer the question.\n"
    "If the documents don't contain enough information, say so clearly.\n"
    "Cite the document numbers when referencing specific information.\n"
    "Be concise and accurate. "
    "If the retrieved text looks irrelevant, say there is not enough reliable evidence."
)


def _ai_runtime():
    """Return centralized AI runtime config with a fallback for lightweight tests."""
    getter = getattr(app_config, "get_ai_runtime_config", None)
    if callable(getter):
        return getter()

    class _Namespace:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    return _Namespace(
        ollama=_Namespace(
            base_url=getattr(settings, "OLLAMA_BASE_URL", "http://localhost:11434"),
            api_key=getattr(settings, "OLLAMA_API_KEY", None),
            llm_model=getattr(settings, "OLLAMA_MODEL", "phi3:mini"),
            timeout=int(getattr(settings, "OLLAMA_TIMEOUT", 150) or 150),
            enabled=bool(getattr(settings, "OLLAMA_ENABLED", True)),
            max_concurrent_requests=int(getattr(settings, "OLLAMA_MAX_CONCURRENT_REQUESTS", 2) or 2),
        ),
        openai=_Namespace(
            api_key=getattr(settings, "OPENAI_API_KEY", None),
            llm_model=getattr(settings, "OPENAI_MODEL", "gpt-4o-mini"),
            timeout=int(getattr(settings, "OPENAI_TIMEOUT", 30) or 30),
        ),
        llm=_Namespace(
            provider=str(getattr(settings, "LLM_PROVIDER", "ollama") or "ollama").lower(),
            use_fallback=bool(getattr(settings, "LLM_USE_FALLBACK", True)),
            temperature=float(getattr(settings, "LLM_TEMPERATURE", 0.3) or 0.3),
            max_tokens=int(getattr(settings, "LLM_MAX_TOKENS", 1000) or 1000),
            openai_model=getattr(settings, "OPENAI_MODEL", "gpt-4o-mini"),
            ollama_model=getattr(settings, "OLLAMA_MODEL", "phi3:mini"),
        ),
    )


def _ollama_headers(*, include_content_type: bool = True) -> dict[str, str]:
    getter = getattr(app_config, "get_ollama_request_headers", None)
    if callable(getter):
        return getter(include_content_type=include_content_type)

    runtime = _ai_runtime()
    headers: dict[str, str] = {}
    if include_content_type:
        headers["Content-Type"] = "application/json"
    if getattr(runtime.ollama, "api_key", None):
        headers["Authorization"] = f"Bearer {runtime.ollama.api_key}"
    return headers


def _build_context_text(context_chunks: List[str]) -> str:
    return "\n\n".join(
        f"[Document {i + 1}]: {chunk}" for i, chunk in enumerate(context_chunks)
    )


def _build_messages(query: str, context_chunks: List[str]) -> List[dict]:
    """Assemble the shared message list for OpenAI and Ollama."""
    user_content = (
        f"Documents:\n{_build_context_text(context_chunks)}\n\n"
        f"Question: {query}\n\nAnswer:"
    )
    return [
        {"role": "system", "content": _RAG_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


class OllamaClient:
    """Thin wrapper around the Ollama-compatible HTTP API with cached health checks."""

    _AVAILABILITY_TTL = 10.0

    def __init__(
        self,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: Optional[int] = None,
    ) -> None:
        runtime = _ai_runtime()
        self.base_url = (base_url or runtime.ollama.base_url).rstrip("/")
        self.model = model or runtime.ollama.llm_model
        self.timeout = int(timeout or runtime.ollama.timeout)
        self.max_context_chunks = 4
        self.max_chunk_chars = 1200
        self._available: Optional[bool] = None
        self._availability_checked_at = 0.0
        self._headers = _ollama_headers()
        logger.info("Ollama client initialised: %s", self.base_url)

        if self.model not in SUPPORTED_OLLAMA_MODELS:
            logger.warning("Unknown Ollama model configured: %s", self.model)

    def set_model(self, model: str) -> None:
        if model not in SUPPORTED_OLLAMA_MODELS:
            logger.warning("Unknown Ollama model '%s'; continuing anyway", model)
        self.model = model
        logger.info("Ollama model switched to: %s", self.model)

    def is_available(self) -> bool:
        now = time.monotonic()
        if (now - self._availability_checked_at) < self._AVAILABILITY_TTL:
            return bool(self._available)

        try:
            with httpx.Client(timeout=3.0, headers=_ollama_headers(include_content_type=False)) as client:
                response = client.get(f"{self.base_url}/api/tags")
            self._available = response.status_code == 200
        except Exception as exc:
            logger.debug("Ollama health check failed: %s", exc)
            self._available = False

        self._availability_checked_at = now
        return bool(self._available)

    def chat(
        self,
        messages: List[dict],
        temperature: float = 0.3,
        num_predict: int = 1000,
    ) -> Tuple[str, int]:
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "keep_alive": "15m",
            "options": {"temperature": temperature, "num_predict": num_predict},
        }
        timeout = httpx.Timeout(connect=5.0, read=float(self.timeout), write=30.0, pool=5.0)
        try:
            with httpx.Client(timeout=timeout, headers=self._headers) as client:
                response = client.post(f"{self.base_url}/api/chat", json=payload)
                response.raise_for_status()
                data = response.json()
        except httpx.TimeoutException as exc:
            raise RuntimeError(
                f"Ollama chat timed out after {self.timeout}s "
                f"(model={self.model}, messages={len(messages)}, num_predict={num_predict})"
            ) from exc
        except httpx.HTTPError as exc:
            raise RuntimeError(f"Ollama chat request failed: {exc}") from exc
        text = ((data.get("message") or {}).get("content") or "")
        return text, len(text) // 4

    def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.3,
        num_predict: int = 1000,
    ) -> Tuple[str, int]:
        payload = {
            "model": self.model,
            "prompt": f"{system_prompt}\n\n{prompt}" if system_prompt else prompt,
            "stream": False,
            "keep_alive": "15m",
            "options": {"temperature": temperature, "num_predict": num_predict},
        }
        timeout = httpx.Timeout(connect=5.0, read=float(self.timeout), write=30.0, pool=5.0)
        try:
            with httpx.Client(timeout=timeout, headers=self._headers) as client:
                response = client.post(f"{self.base_url}/api/generate", json=payload)
                response.raise_for_status()
                data = response.json()
        except httpx.TimeoutException as exc:
            raise RuntimeError(
                f"Ollama generate timed out after {self.timeout}s "
                f"(model={self.model}, prompt_chars={len(prompt)}, num_predict={num_predict})"
            ) from exc
        except httpx.HTTPError as exc:
            raise RuntimeError(f"Ollama generate request failed: {exc}") from exc
        text = data.get("response", "")
        return text, len(text) // 4


class LLMService:
    """Synchronous and asynchronous LLM access with centralized config."""

    def __init__(
        self,
        openai_api_key: Optional[str] = None,
        openai_model: Optional[str] = None,
        ollama_base_url: Optional[str] = None,
        ollama_model: Optional[str] = None,
        use_fallback: Optional[bool] = None,
        primary_provider: Optional[str] = None,
    ) -> None:
        runtime = _ai_runtime()
        self.openai_model = openai_model or runtime.llm.openai_model
        self.ollama_model = ollama_model or runtime.llm.ollama_model
        self.use_fallback = runtime.llm.use_fallback if use_fallback is None else use_fallback
        self.primary_provider = str(primary_provider or runtime.llm.provider or "ollama").lower()
        self._openai_timeout = runtime.openai.timeout
        self._ollama_timeout = runtime.ollama.timeout

        self._openai_sync = None
        api_key = openai_api_key or runtime.openai.api_key
        if api_key:
            try:
                from openai import OpenAI

                self._openai_sync = OpenAI(api_key=api_key, timeout=self._openai_timeout)
                logger.info("OpenAI sync client initialised: %s", self.openai_model)
            except ImportError:
                logger.error("openai package not installed")

        self.ollama_client = OllamaClient(
            base_url=ollama_base_url or runtime.ollama.base_url,
            model=self.ollama_model,
            timeout=self._ollama_timeout,
        )
        self._sync_providers = {
            "ollama": self._sync_ollama,
            "openai": self._sync_openai,
        }
        self._async_providers = {
            "ollama": self._async_ollama,
            "openai": self._async_openai,
        }

    def _sync_ollama(self, messages: List[dict], temperature: float, max_tokens: int) -> LLMResponse:
        if not self.ollama_client.is_available():
            raise RuntimeError("Ollama not available")
        text, tokens = self.ollama_client.chat(
            messages,
            temperature=temperature,
            num_predict=max_tokens,
        )
        return LLMResponse(
            answer=text,
            tokens_used=tokens,
            model=self.ollama_client.model,
            provider=LLMProvider.OLLAMA,
        )

    def _sync_openai(self, messages: List[dict], temperature: float, max_tokens: int) -> LLMResponse:
        if not self._openai_sync:
            raise RuntimeError("OpenAI client not configured")

        response = self._openai_sync.chat.completions.create(
            model=self.openai_model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return LLMResponse(
            answer=response.choices[0].message.content or "",
            tokens_used=response.usage.total_tokens,
            model=self.openai_model,
            provider=LLMProvider.OPENAI,
        )

    async def _async_ollama(self, messages: List[dict], temperature: float, max_tokens: int) -> LLMResponse:
        if not self.ollama_client.is_available():
            raise RuntimeError("Ollama not available")

        async with _OLLAMA_ASYNC_SEMAPHORE:
            loop = asyncio.get_running_loop()
            text, tokens = await loop.run_in_executor(
                None,
                lambda: self.ollama_client.chat(
                    messages,
                    temperature=temperature,
                    num_predict=max_tokens,
                ),
            )

        return LLMResponse(
            answer=text,
            tokens_used=tokens,
            model=self.ollama_client.model,
            provider=LLMProvider.OLLAMA,
        )

    async def _async_openai(self, messages: List[dict], temperature: float, max_tokens: int) -> LLMResponse:
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise RuntimeError("openai package not installed") from exc

        runtime = _ai_runtime()
        api_key = runtime.openai.api_key
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY not configured")

        client = AsyncOpenAI(api_key=api_key, timeout=runtime.openai.timeout)
        response = await client.chat.completions.create(
            model=self.openai_model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return LLMResponse(
            answer=response.choices[0].message.content or "",
            tokens_used=response.usage.total_tokens,
            model=self.openai_model,
            provider=LLMProvider.OPENAI,
        )

    def _provider_order(self, primary_override: Optional[str] = None) -> List[str]:
        primary = str(primary_override or self.primary_provider or "ollama").lower()
        if primary not in {"ollama", "openai"}:
            logger.warning("Unknown LLM provider '%s', defaulting to ollama", primary)
            primary = "ollama"
        fallback = "openai" if primary == "ollama" else "ollama"
        ordered = [primary] if not self.use_fallback else [primary, fallback]
        configured = [provider for provider in ordered if self._provider_is_configured(provider)]
        return configured or [primary]

    def _provider_is_configured(self, provider_name: str) -> bool:
        runtime = _ai_runtime()
        if provider_name == "openai":
            return bool(runtime.openai.api_key)
        if provider_name == "ollama":
            return bool(runtime.ollama.enabled)
        return False

    def _is_token_exhaustion(self, err: str) -> bool:
        lowered = err.lower()
        return any(fragment in lowered for fragment in _TOKEN_EXHAUSTION_FRAGMENTS)

    def generate_answer(
        self,
        query: str,
        context_chunks: List[str],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        force_ollama: bool = False,
    ) -> LLMResponse:
        runtime = _ai_runtime()
        resolved_temperature = temperature if temperature is not None else runtime.llm.temperature
        resolved_max_tokens = max_tokens if max_tokens is not None else runtime.llm.max_tokens
        messages = _build_messages(query, context_chunks)
        primary_override = "ollama" if force_ollama else None
        last_exc: Optional[Exception] = None

        for provider_name in self._provider_order(primary_override=primary_override):
            provider = self._sync_providers.get(provider_name)
            if provider is None:
                continue
            try:
                logger.info("Trying sync provider: %s", provider_name)
                return provider(messages, resolved_temperature, resolved_max_tokens)
            except Exception as exc:
                logger.warning("%s failed: %s", provider_name, exc)
                if self._is_token_exhaustion(str(exc)):
                    logger.warning("Token exhaustion detected on %s", provider_name)
                last_exc = exc

        raise RuntimeError(f"All LLM providers failed. Last error: {last_exc}")

    async def async_generate_answer(
        self,
        query: str,
        context_chunks: List[str],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        force_ollama: bool = False,
    ) -> LLMResponse:
        runtime = _ai_runtime()
        resolved_temperature = temperature if temperature is not None else runtime.llm.temperature
        resolved_max_tokens = max_tokens if max_tokens is not None else runtime.llm.max_tokens
        messages = _build_messages(query, context_chunks)
        primary_override = "ollama" if force_ollama else None
        last_exc: Optional[Exception] = None

        for provider_name in self._provider_order(primary_override=primary_override):
            provider = self._async_providers.get(provider_name)
            if provider is None:
                continue
            try:
                logger.info("Trying async provider: %s", provider_name)
                return await provider(messages, resolved_temperature, resolved_max_tokens)
            except Exception as exc:
                logger.warning("%s failed: %s", provider_name, exc)
                if self._is_token_exhaustion(str(exc)):
                    logger.warning("Token exhaustion detected on %s", provider_name)
                last_exc = exc

        raise RuntimeError(f"All LLM providers failed. Last error: {last_exc}")

    def extract_citations(self, text: str) -> List[int]:
        return sorted({int(match.group(1)) for match in _CITATION_RE.finditer(text)})

    def get_status(self) -> dict:
        return {
            "openai_available": self._openai_sync is not None,
            "openai_model": self.openai_model,
            "ollama_available": self.ollama_client.is_available(),
            "ollama_model": self.ollama_client.model,
            "fallback_enabled": self.use_fallback,
            "primary_provider": self.primary_provider,
        }


_llm_service: Optional[LLMService] = None


def get_llm_service(
    model: Optional[str] = None,
    *,
    openai_model: Optional[str] = None,
    primary_provider: Optional[str] = None,
    use_fallback: Optional[bool] = None,
) -> LLMService:
    """Return the application-wide LLM service singleton."""
    runtime = _ai_runtime()
    has_overrides = any(
        value is not None
        for value in (model, openai_model, primary_provider, use_fallback)
    )
    if has_overrides:
        return LLMService(
            openai_api_key=runtime.openai.api_key,
            openai_model=openai_model or runtime.llm.openai_model,
            ollama_base_url=runtime.ollama.base_url,
            ollama_model=model or runtime.llm.ollama_model,
            use_fallback=runtime.llm.use_fallback if use_fallback is None else use_fallback,
            primary_provider=primary_provider or runtime.llm.provider,
        )

    global _llm_service
    if _llm_service is None:
        _llm_service = LLMService(
            openai_api_key=runtime.openai.api_key,
            openai_model=runtime.llm.openai_model,
            ollama_base_url=runtime.ollama.base_url,
            ollama_model=runtime.llm.ollama_model,
            use_fallback=runtime.llm.use_fallback,
            primary_provider=runtime.llm.provider,
        )
        logger.info(
            "LLMService singleton created - primary=%s fallback=%s",
            runtime.llm.provider,
            runtime.llm.use_fallback,
        )
    return _llm_service
