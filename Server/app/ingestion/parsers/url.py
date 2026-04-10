"""URL content parser using Jina Reader and LLM extraction."""
import asyncio
import logging
import json
import time
from functools import wraps
from typing import Optional, Dict, Any, Callable, TypeVar
from urllib.parse import urlparse, ParseResult

import aiohttp

from app.ingestion.parsers.base import DocumentParser, ParsedDocument
from app.services.llm_service import get_llm_service

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])

# Schemes that are safe to fetch
_ALLOWED_SCHEMES: frozenset = frozenset({"http", "https"})

# ---------------------------------------------------------------------------
# Retry decorator for general async operations
# ---------------------------------------------------------------------------

def _with_retries(
    max_attempts: int = 3,
    backoff_base: float = 1.5,
) -> Callable[[Any], Any]:
    """Exponential-backoff retry decorator for transient failures."""
    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exc: Exception | None = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return fn(*args, **kwargs)
                except Exception as exc:
                    last_exc = exc
                    if attempt == max_attempts:
                        break
                    wait = backoff_base ** attempt
                    logger.warning(
                        "LLM extraction failed (attempt %d/%d): %s — retrying in %.1fs",
                        attempt, max_attempts, exc, wait,
                    )
                    time.sleep(wait)
            raise last_exc  # type: ignore[misc]
        return wrapper  # type: ignore[return-value]
    return decorator


class URLParser(DocumentParser):
    """Parser for web URLs using the Jina Reader API + LLM extraction.

    Fetches the human-readable content of a URL via Jina Reader, then uses
    LLM (Ollama primary, OpenAI fallback) to clean, structure, and extract metadata, 
    producing a ``ParsedDocument`` ready for knowledge-base indexing.
    """

    JINA_READER_BASE: str = "https://r.jina.ai"
    JINA_TIMEOUT_SECONDS: float = 20.0
    JINA_MAX_RETRIES: int = 2         # Jina-specific fetch retries
    JINA_BACKOFF_BASE: float = 2.0

    MAX_CONTENT_CHARS: int = 6_000    # Characters forwarded to the LLM
    MAX_RAW_BYTES: int = 5_242_880    # Hard limit on Jina response: 5 MB

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def parse_url(self, url: str) -> ParsedDocument:
        """Fetch and parse content from a URL.

        Args:
            url: Fully qualified HTTP/HTTPS URL.

        Returns:
            ``ParsedDocument`` with extracted title, cleaned body, and metadata.

        Raises:
            ValueError: If the URL is invalid, uses a disallowed scheme, or
                        fetching / extraction fails irrecoverably.
        """
        parsed_url = self._validate_url(url)
        logger.info("Parsing URL: %s", url)

        raw_content = await self._fetch_url_content(url)
        extracted = self._extract_with_openai(url, parsed_url, raw_content)

        metadata: Dict[str, Any] = {
            "source_url": url,
            "site_name": extracted.get("site_name"),
            "author": extracted.get("author"),
            "publish_date": extracted.get("publish_date"),
            "content_type": extracted.get("content_type", "text/html"),
            "language": extracted.get("language"),
            "parser": "URLParser",
        }

        # Drop None values to keep metadata tidy
        metadata = {k: v for k, v in metadata.items() if v is not None}

        return ParsedDocument(
            text=self._clean_text(extracted.get("content") or raw_content[: self.MAX_CONTENT_CHARS]),
            metadata=metadata,
            title=extracted.get("title"),
            author=extracted.get("author"),
            word_count=len((extracted.get("content") or "").split()),
        )

    def parse(self, file_bytes: bytes) -> ParsedDocument:
        """Not implemented — use ``parse_url()`` instead.

        Raises:
            NotImplementedError: Always.
        """
        raise NotImplementedError(
            "URLParser does not support byte-level parsing. Use parse_url(url) instead."
        )

    # ------------------------------------------------------------------
    # URL validation
    # ------------------------------------------------------------------

    def _validate_url(self, url: str) -> ParseResult:
        """Validate and parse a URL string.

        Args:
            url: Candidate URL.

        Returns:
            ``urllib.parse.ParseResult`` for the validated URL.

        Raises:
            ValueError: On empty input, missing scheme/host, or disallowed scheme.
        """
        if not url or not url.strip():
            raise ValueError("URL must not be empty.")

        url = url.strip()
        parsed = urlparse(url)

        if not parsed.scheme:
            raise ValueError(f"URL is missing a scheme: {url!r}")
        if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
            raise ValueError(
                f"URL scheme {parsed.scheme!r} is not allowed. "
                f"Expected one of: {sorted(_ALLOWED_SCHEMES)}."
            )
        if not parsed.netloc:
            raise ValueError(f"URL is missing a host: {url!r}")

        return parsed

    # ------------------------------------------------------------------
    # Jina Reader fetch
    # ------------------------------------------------------------------

    async def _fetch_url_content(self, url: str) -> str:
        """Retrieve plain-text page content via the Jina Reader API.

        Retries on transient network errors with exponential back-off.

        Args:
            url: Original target URL.

        Returns:
            Plain-text page content.

        Raises:
            ValueError: If all attempts fail or the response is unusable.
        """
        reader_url = f"{self.JINA_READER_BASE}/{url}"
        headers = {
            "Accept": "text/plain",
            "User-Agent": "DocumentIndexer/1.0 (+internal)",
        }
        timeout = aiohttp.ClientTimeout(total=self.JINA_TIMEOUT_SECONDS)

        last_exc: Exception | None = None

        for attempt in range(1, self.JINA_MAX_RETRIES + 2):
            try:
                async with aiohttp.ClientSession(headers=headers) as session:
                    async with session.get(reader_url, timeout=timeout) as response:
                        if response.status == 429:
                            retry_after = float(response.headers.get("Retry-After", self.JINA_BACKOFF_BASE ** attempt))
                            logger.warning(
                                "Jina Reader rate-limited (attempt %d). Waiting %.1fs.",
                                attempt, retry_after,
                            )
                            await asyncio.sleep(retry_after)
                            continue

                        if response.status != 200:
                            raise ValueError(
                                f"Jina Reader returned HTTP {response.status} for {url!r}."
                            )

                        # Guard against oversized responses
                        content_length = int(response.headers.get("Content-Length", 0))
                        if content_length > self.MAX_RAW_BYTES:
                            raise ValueError(
                                f"Jina Reader response ({content_length:,} bytes) "
                                f"exceeds the {self.MAX_RAW_BYTES:,}-byte limit."
                            )

                        content = await response.text(encoding="utf-8", errors="replace")
                        if not content or not content.strip():
                            raise ValueError(f"Jina Reader returned empty content for {url!r}.")

                        logger.debug(
                            "Jina Reader fetched %d chars for %s (attempt %d)",
                            len(content), url, attempt,
                        )
                        return content

            except asyncio.TimeoutError as exc:
                last_exc = ValueError(f"Timeout fetching {url!r} after {self.JINA_TIMEOUT_SECONDS}s.")
                logger.warning("Jina Reader timeout (attempt %d/%d).", attempt, self.JINA_MAX_RETRIES + 1)
            except aiohttp.ClientError as exc:
                last_exc = ValueError(f"Network error fetching {url!r}: {exc}")
                logger.warning("Jina Reader network error (attempt %d/%d): %s", attempt, self.JINA_MAX_RETRIES + 1, exc)
            except ValueError:
                raise  # Propagate explicit ValueError immediately

            if attempt <= self.JINA_MAX_RETRIES:
                wait = self.JINA_BACKOFF_BASE ** attempt
                logger.info("Retrying Jina Reader in %.1fs…", wait)
                await asyncio.sleep(wait)

        raise last_exc or ValueError(f"Failed to fetch {url!r} after {self.JINA_MAX_RETRIES + 1} attempts.")

    # ------------------------------------------------------------------
    # OpenAI extraction
    # ------------------------------------------------------------------

    @_with_retries(max_attempts=3, backoff_base=1.5)
    def _extract_with_openai(
        self,
        url: str,
        parsed_url: ParseResult,
        raw_content: str,
    ) -> Dict[str, Any]:
        """Extract structured metadata and cleaned body from raw page content using LLM.

        Uses Ollama as primary LLM provider to avoid OpenAI quota errors.

        Args:
            url: Original URL (used for attribution and fallback).
            parsed_url: Pre-parsed URL components.
            raw_content: Raw text from Jina Reader.

        Returns:
            Validated extraction dictionary.
        """
        content_preview = raw_content[: self.MAX_CONTENT_CHARS]
        truncated = len(raw_content) > self.MAX_CONTENT_CHARS
        truncation_note = (
            "\n**Note:** The content below is a leading excerpt of a longer page. "
            "Base all metadata on what is visible; do not extrapolate."
            if truncated else ""
        )

        system_prompt = (
            "You are a senior technical content analyst extracting structured metadata from web pages "
            "for a knowledge-base indexing system. Your output must be factual and grounded in the "
            "provided content. Do not invent authors, dates, or publication names not explicitly stated. "
            "Strip navigation menus, cookie banners, ads, footers, and other boilerplate — retain only "
            "the substantive article or page body. "
            "Return ONLY a single, valid JSON object — no Markdown fences, no commentary."
        )

        user_prompt = f"""Extract structured information from the web page content below and return a JSON object with exactly these keys:

"title"
  The primary article or page heading as it appears in the content.
  If unavailable, derive a concise descriptive title (≤ 12 words) from the body text.
  Do NOT use the domain name as a title.

"content"
  The full substantive body of the page, cleaned as follows:
  • Remove: navigation links, cookie/GDPR notices, subscription prompts, share buttons,
    comment sections, related-article teasers, footer text, and sidebar widgets.
  • Preserve: all paragraphs, section headings, lists, tables, code blocks, and captions
    that form part of the main article or documentation.
  • Preserve original line breaks and Markdown-style formatting where present.
  • Do not summarise — return the full cleaned text.

"author"
  The author's full name as a plain string, or null if not stated.

"publish_date"
  ISO 8601 date string (YYYY-MM-DD) if a publication or last-updated date is explicitly
  stated in the content, otherwise null.

"site_name"
  The name of the publication, website, or organisation that owns this page
  (e.g. "The Guardian", "Stripe Documentation", "MDN Web Docs"), or null if ambiguous.

"content_type"
  One of: "article", "documentation", "blog_post", "product_page", "forum_thread",
  "academic_paper", "news_story", "tutorial", "landing_page", "other".
  Choose the single best match.

"language"
  ISO 639-1 two-letter language code of the main content (e.g. "en", "fr", "de"), or null.
{truncation_note}
---
URL: {url}

Raw page content:
{content_preview}"""

        # Use LLMService with Ollama as primary (OpenAI as fallback if configured)
        llm_service = get_llm_service(primary_provider="ollama")
        llm_response = llm_service.generate_answer(
            query=user_prompt,
            context_chunks=[content_preview],
            temperature=0.0,
            max_tokens=2_000,
        )

        raw = llm_response.answer or "{}"
        try:
            result: Dict[str, Any] = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.warning("JSON decode failed; attempting strip: %s", exc)
            try:
                cleaned = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
                result = json.loads(cleaned)
            except json.JSONDecodeError:
                logger.error("Failed to parse LLM response as JSON after cleanup: %s", exc)
                result = {}

        self._validate_extraction(result, parsed_url, raw_content)
        logger.debug("URL extraction title: %r", result.get("title"))
        return result

    def _validate_extraction(
        self,
        result: Dict[str, Any],
        parsed_url: ParseResult,
        raw_content: str,
    ) -> None:
        """Ensure required keys are present; apply safe defaults. Mutates in-place."""
        if not result.get("title") or not isinstance(result["title"], str):
            result["title"] = parsed_url.netloc

        if not result.get("content") or not isinstance(result["content"], str):
            result["content"] = raw_content[: self.MAX_CONTENT_CHARS]

        for nullable_key in ("author", "publish_date", "site_name", "language"):
            if nullable_key not in result:
                result[nullable_key] = None

        valid_types = {
            "article", "documentation", "blog_post", "product_page",
            "forum_thread", "academic_paper", "news_story", "tutorial",
            "landing_page", "other",
        }
        if result.get("content_type") not in valid_types:
            result["content_type"] = "other"
