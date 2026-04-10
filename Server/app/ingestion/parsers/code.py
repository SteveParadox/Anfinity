"""Code snippet parser using LLM for intelligent analysis."""
import logging
import json
import time
import hashlib
from functools import wraps
from typing import Optional, Dict, Any, Callable, TypeVar

from app.ingestion.parsers.base import DocumentParser, ParsedDocument
from app.services.llm_service import get_llm_service

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])

# ---------------------------------------------------------------------------
# Retry decorator
# ---------------------------------------------------------------------------

def _with_retries(
    max_attempts: int = 3,
    backoff_base: float = 1.5,
) -> Callable[[Any], Any]:
    """Exponential-backoff retry decorator for LLM calls.

    Args:
        max_attempts: Total number of attempts (including the first).
        backoff_base: Multiplier for exponential back-off (seconds).
    """
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
                        "LLM call failed (attempt %d/%d): %s — retrying in %.1fs",
                        attempt, max_attempts, exc, wait,
                    )
                    time.sleep(wait)
            raise last_exc  # type: ignore[misc]
        return wrapper  # type: ignore[return-value]
    return decorator


class CodeParser(DocumentParser):
    """Parser for code snippets with AI-powered documentation.

    Produces a structured ``ParsedDocument`` containing a descriptive title,
    a developer-focused explanation, and a fully formatted Markdown page that
    is ready for downstream indexing or rendering.
    """

    MAX_CODE_LENGTH: int = 4_000   # Characters forwarded to the LLM
    MAX_CODE_BYTES: int = 512_000  # Hard upper limit on raw input (~500 KB)

    SUPPORTED_LANGUAGES: tuple = (
        "python", "javascript", "typescript", "java", "cpp", "csharp",
        "go", "rust", "ruby", "php", "swift", "kotlin", "scala",
        "r", "matlab", "sql", "bash", "c",
    )

    EXTENSION_MAP: dict = {
        ".py": "python",    ".js": "javascript", ".ts": "typescript",
        ".jsx": "javascript", ".tsx": "typescript", ".java": "java",
        ".cpp": "cpp",      ".cc": "cpp",         ".cxx": "cpp",
        ".c": "c",          ".cs": "csharp",      ".go": "go",
        ".rs": "rust",      ".rb": "ruby",         ".php": "php",
        ".swift": "swift",  ".kt": "kotlin",       ".scala": "scala",
        ".r": "r",          ".m": "matlab",        ".sql": "sql",
        ".sh": "bash",
    }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def parse(self, file_bytes: bytes, language: Optional[str] = None) -> ParsedDocument:
        """Parse raw code bytes.

        Args:
            file_bytes: Raw source-code content (UTF-8 recommended).
            language: Programming language.  Detected from content if omitted.

        Returns:
            ``ParsedDocument`` populated with AI-generated metadata.

        Raises:
            ValueError: If *file_bytes* exceeds ``MAX_CODE_BYTES``.
        """
        if not file_bytes:
            raise ValueError("file_bytes must not be empty.")
        if len(file_bytes) > self.MAX_CODE_BYTES:
            raise ValueError(
                f"Input exceeds maximum allowed size ({self.MAX_CODE_BYTES:,} bytes)."
            )

        try:
            code = file_bytes.decode("utf-8", errors="replace")
            return self.parse_code(code, language or "unknown")
        except (UnicodeDecodeError, ValueError):
            raise
        except Exception as exc:
            logger.error("Unexpected error in parse(): %s", exc, exc_info=True)
            raise

    def parse_code(self, code: str, language: str = "unknown") -> ParsedDocument:
        """Parse a source-code string with AI analysis.

        Args:
            code: Source code.
            language: Programming language name.

        Returns:
            ``ParsedDocument`` with title, explanation, and Markdown content.
        """
        if not isinstance(code, str) or not code.strip():
            raise ValueError("'code' must be a non-empty string.")

        language = (language or "unknown").strip().lower()
        logger.info("Parsing %s code snippet (%d chars)", language, len(code))

        code_fingerprint = hashlib.md5(code.encode()).hexdigest()[:8]
        code_preview = code[: self.MAX_CODE_LENGTH]
        truncated = len(code) > self.MAX_CODE_LENGTH

        try:
            analysis = self._analyze_with_openai(code_preview, language, truncated=truncated)
        except Exception as exc:
            logger.error(
                "AI analysis failed for %s snippet [%s]: %s",
                language, code_fingerprint, exc, exc_info=True,
            )
            analysis = self._fallback_analysis(code_preview, language)

        metadata: Dict[str, Any] = {
            "code_language": language,
            "code_lines": code.count("\n") + 1,
            "code_length": len(code),
            "truncated": truncated,
            "fingerprint": code_fingerprint,
            "parser": "CodeParser",
            "explanation": analysis.get("explanation"),
        }

        return ParsedDocument(
            text=self._clean_text(analysis.get("structured_content", code)),
            metadata=metadata,
            title=analysis.get("title"),
            word_count=len(analysis.get("explanation", "").split()),
        )

    def detect_language(self, filename: str) -> Optional[str]:
        """Detect programming language from a filename's extension.

        Args:
            filename: Source filename (with extension).

        Returns:
            Canonical language name, or ``None`` if unrecognised.
        """
        if not filename:
            return None
        filename = filename.strip().lower()
        for ext, lang in self.EXTENSION_MAP.items():
            if filename.endswith(ext):
                return lang
        return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @_with_retries(max_attempts=3, backoff_base=1.5)
    def _analyze_with_openai(
        self, code: str, language: str, *, truncated: bool = False
    ) -> Dict[str, Any]:
        """Call the OpenAI chat completion API to analyse a code snippet.

        Args:
            code: Code to analyse (already truncated to ``MAX_CODE_LENGTH``).
            language: Programming language name.
            truncated: Whether the original source was longer than the preview.

        Returns:
            Validated analysis dictionary.
        """
        truncation_note = (
            "\n**Note:** The snippet below is a truncated preview of a larger file."
            if truncated else ""
        )

        system_prompt = (
            "You are a senior software engineer producing structured technical documentation "
            "for a knowledge-base indexing system. Your output must be precise, factual, and "
            "immediately useful to another engineer who encounters this code six months from now. "
            "Avoid marketing language. Do not speculate beyond what the code demonstrates. "
            "Return ONLY a single, valid JSON object — no Markdown fences, no commentary."
        )

        user_prompt = f"""Analyse the following {language} code snippet and return a JSON object with exactly these four keys:

"title"
  A concise, noun-phrase title describing what the code does (≤ 10 words).
  Examples: "JWT Authentication Middleware", "CSV Batch Import Pipeline".

"explanation"
  A developer-focused technical summary structured as follows:
  • **Purpose** — What problem does this code solve and why does it exist?
  • **Key logic** — Walk through the primary algorithm, data flow, or control path.
  • **Design decisions** — Notable patterns, abstractions, or trade-offs the author made.
  • **Gotchas & edge cases** — Anything non-obvious that a maintainer must know.
  Write in plain technical prose, 3–5 paragraphs. No bullet lists inside the value.

"structured_content"
  A complete Markdown document suitable for a developer wiki. Use this exact structure:
  # <title>
  ## Overview
  <2–3 sentence executive summary>
  ## What It Does
  <functional description>
  ## Key Logic
  <algorithmic/control-flow walkthrough>
  ## Public API / Entry Points
  <functions, classes, or endpoints exposed — omit section if none>
  ## Usage Example
  <minimal, runnable example — omit section if not reasonably inferable>
  ## Caveats & Known Limitations
  <edge cases, assumptions, or missing error handling>
  ## Source
  ```{language}
  <the original code snippet verbatim>
  ```

"dependencies"
  A JSON array of strings listing any external libraries, frameworks, or services
  referenced in the code. Return an empty array if there are none.
{truncation_note}
---
Code snippet:
```{language}
{code}
```"""

        # Use LLMService with Ollama as primary (OpenAI as fallback if configured)
        llm_service = get_llm_service(primary_provider="ollama")
        llm_response = llm_service.generate_answer(
            query=user_prompt,
            context_chunks=[code],
            temperature=0.1,
            max_tokens=2_000,
        )

        raw = llm_response.answer or "{}"
        try:
            result: Dict[str, Any] = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.warning("JSON decode failed; attempting strip: %s", exc)
            # Strip accidental Markdown fences emitted despite the instruction
            try:
                cleaned = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
                result = json.loads(cleaned)  # Let this propagate if it also fails
            except json.JSONDecodeError:
                logger.error("Failed to parse LLM response as JSON after cleanup: %s", exc)
                result = {}

        self._validate_analysis(result, language, code)
        logger.debug("Code analysis title: %r", result.get("title"))
        return result

    def _validate_analysis(
        self, result: Dict[str, Any], language: str, code: str
    ) -> None:
        """Ensure the parsed result has all required keys; fill defaults if not.

        Mutates *result* in-place.
        """
        if not result.get("title") or not isinstance(result["title"], str):
            result["title"] = f"{language.capitalize()} Code Snippet"

        if not result.get("explanation") or not isinstance(result["explanation"], str):
            result["explanation"] = f"Code snippet written in {language}."

        if not result.get("structured_content") or not isinstance(result["structured_content"], str):
            result["structured_content"] = self._format_code_fallback(code, language)

        if not isinstance(result.get("dependencies"), list):
            result["dependencies"] = []

    def _fallback_analysis(self, code: str, language: str) -> Dict[str, Any]:
        """Return a minimal analysis dict when the AI call is unavailable."""
        return {
            "title": f"{language.capitalize()} Code Snippet",
            "explanation": f"Code snippet written in {language}. AI analysis unavailable.",
            "structured_content": self._format_code_fallback(code, language),
            "dependencies": [],
        }

    def _format_code_fallback(self, code: str, language: str) -> str:
        """Produce a minimal Markdown document without AI assistance."""
        return (
            f"# {language.capitalize()} Code Snippet\n\n"
            "## Source\n"
            f"```{language}\n{code}\n```\n\n"
            "> AI analysis was unavailable. Manual review recommended.\n"
        )
