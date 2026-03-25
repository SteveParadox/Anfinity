"""Data file parser for JSON and CSV content using OpenAI analysis."""
import logging
import json
import csv
import time
import hashlib
from functools import wraps
from io import StringIO
from typing import Optional, Dict, Any, List, Callable, TypeVar

import openai

from app.ingestion.parsers.base import DocumentParser, ParsedDocument
from app.config import settings

logger = logging.getLogger(__name__)

# Initialize OpenAI client
client = openai.OpenAI(
    api_key=settings.OPENAI_API_KEY,
    timeout=30.0,
    max_retries=0,  # Handled manually below
)

F = TypeVar("F", bound=Callable[..., Any])

# ---------------------------------------------------------------------------
# Retry decorator (shared pattern — extract to a shared module if preferred)
# ---------------------------------------------------------------------------

def _with_retries(
    max_attempts: int = 3,
    backoff_base: float = 1.5,
    retryable: tuple = (openai.RateLimitError, openai.APITimeoutError, openai.InternalServerError),
) -> Callable[[F], F]:
    """Exponential-backoff retry decorator for OpenAI calls."""
    def decorator(fn: F) -> F:
        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exc: Exception | None = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return fn(*args, **kwargs)
                except retryable as exc:
                    last_exc = exc
                    if attempt == max_attempts:
                        break
                    wait = backoff_base ** attempt
                    logger.warning(
                        "OpenAI call failed (attempt %d/%d): %s — retrying in %.1fs",
                        attempt, max_attempts, exc, wait,
                    )
                    time.sleep(wait)
                except Exception:
                    raise
            raise last_exc  # type: ignore[misc]
        return wrapper  # type: ignore[return-value]
    return decorator


class DataParser(DocumentParser):
    """Parser for JSON and CSV data files with AI-powered analysis.

    Produces a structured ``ParsedDocument`` containing a human-readable title,
    schema summary, statistical insights, and a Markdown document suitable for
    downstream indexing or knowledge-base rendering.
    """

    MAX_DATA_CHARS: int = 3_000   # Characters forwarded to the LLM
    MAX_DATA_BYTES: int = 10_485_760  # Hard limit: 10 MB raw input
    MAX_ROWS_CSV: int = 100       # Maximum CSV rows included in LLM preview
    MAX_CELL_LENGTH: int = 50     # Truncate individual cell values beyond this

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def parse(self, file_bytes: bytes, data_type: Optional[str] = None) -> ParsedDocument:
        """Parse a data file from raw bytes.

        Args:
            file_bytes: Raw UTF-8 file content.
            data_type: ``'json'`` or ``'csv'``.  Auto-detected if omitted.

        Returns:
            ``ParsedDocument`` populated with AI-generated metadata.

        Raises:
            ValueError: On empty input, oversized input, or unsupported type.
        """
        if not file_bytes:
            raise ValueError("file_bytes must not be empty.")
        if len(file_bytes) > self.MAX_DATA_BYTES:
            raise ValueError(
                f"Input exceeds maximum allowed size ({self.MAX_DATA_BYTES:,} bytes)."
            )

        content = file_bytes.decode("utf-8", errors="replace")
        data_type = (data_type or self._detect_data_type(content)).lower()

        if data_type == "json":
            return self.parse_json(content)
        elif data_type == "csv":
            return self.parse_csv(content)
        else:
            raise ValueError(
                f"Unsupported data type: {data_type!r}. Expected 'json' or 'csv'."
            )

    def parse_json(self, json_content: str) -> ParsedDocument:
        """Parse and analyse a JSON string.

        Args:
            json_content: Raw JSON text.

        Returns:
            ``ParsedDocument`` with schema summary and structured analysis.

        Raises:
            ValueError: If *json_content* is not valid JSON.
        """
        if not isinstance(json_content, str) or not json_content.strip():
            raise ValueError("'json_content' must be a non-empty string.")

        logger.info("Parsing JSON data (%d chars)", len(json_content))
        fingerprint = hashlib.md5(json_content.encode()).hexdigest()[:8]

        try:
            parsed = json.loads(json_content)
        except json.JSONDecodeError as exc:
            logger.error("Invalid JSON [%s]: %s", fingerprint, exc)
            return self._create_error_document("JSON", str(exc), json_content)

        schema_summary = self._summarise_json_schema(parsed)
        preview = json_content[: self.MAX_DATA_CHARS]
        truncated = len(json_content) > self.MAX_DATA_CHARS

        try:
            analysis = self._analyze_data_with_openai(
                preview, "JSON",
                schema_hint=schema_summary,
                truncated=truncated,
            )
        except Exception as exc:
            logger.error("AI analysis failed for JSON [%s]: %s", fingerprint, exc, exc_info=True)
            analysis = self._fallback_analysis("JSON", preview)

        metadata: Dict[str, Any] = {
            "data_type": "json",
            "data_size": len(json_content),
            "truncated": truncated,
            "fingerprint": fingerprint,
            "schema_summary": schema_summary,
            "parser": "DataParser",
            "insights": analysis.get("insights", []),
        }

        return ParsedDocument(
            text=self._clean_text(analysis.get("structured_content", preview)),
            metadata=metadata,
            title=analysis.get("title"),
            word_count=len(analysis.get("summary", "").split()),
        )

    def parse_csv(self, csv_content: str) -> ParsedDocument:
        """Parse and analyse a CSV string.

        Args:
            csv_content: Raw CSV text.

        Returns:
            ``ParsedDocument`` with column schema and structured analysis.

        Raises:
            ValueError: If the CSV has no columns.
        """
        if not isinstance(csv_content, str) or not csv_content.strip():
            raise ValueError("'csv_content' must be a non-empty string.")

        logger.info("Parsing CSV data (%d chars)", len(csv_content))
        fingerprint = hashlib.md5(csv_content.encode()).hexdigest()[:8]

        try:
            reader = csv.DictReader(StringIO(csv_content))
            headers: List[str] = []
            rows: List[Dict[str, str]] = []

            for i, row in enumerate(reader):
                if i == 0:
                    headers = list(row.keys())
                if i < self.MAX_ROWS_CSV:
                    rows.append(dict(row))

            if not headers:
                raise ValueError("CSV file has no column headers.")

        except (csv.Error, ValueError) as exc:
            logger.error("CSV parse error [%s]: %s", fingerprint, exc)
            return self._create_error_document("CSV", str(exc), csv_content)

        preview = self._build_csv_preview(headers, rows)
        truncated = len(csv_content) > self.MAX_DATA_CHARS
        col_types = self._infer_column_types(rows, headers)

        try:
            analysis = self._analyze_data_with_openai(
                preview, "CSV",
                schema_hint=f"Columns: {', '.join(headers)}. Inferred types: {col_types}",
                truncated=truncated,
            )
        except Exception as exc:
            logger.error("AI analysis failed for CSV [%s]: %s", fingerprint, exc, exc_info=True)
            analysis = self._fallback_analysis("CSV", preview)

        metadata: Dict[str, Any] = {
            "data_type": "csv",
            "data_size": len(csv_content),
            "truncated": truncated,
            "fingerprint": fingerprint,
            "columns": headers,
            "inferred_column_types": col_types,
            "row_count_preview": len(rows),
            "parser": "DataParser",
            "insights": analysis.get("insights", []),
        }

        return ParsedDocument(
            text=self._clean_text(analysis.get("structured_content", preview)),
            metadata=metadata,
            title=analysis.get("title"),
            word_count=len(analysis.get("summary", "").split()),
        )

    # ------------------------------------------------------------------
    # OpenAI interaction
    # ------------------------------------------------------------------

    @_with_retries(max_attempts=3, backoff_base=1.5)
    def _analyze_data_with_openai(
        self,
        data: str,
        data_type: str,
        *,
        schema_hint: str = "",
        truncated: bool = False,
    ) -> Dict[str, Any]:
        """Call the OpenAI chat completion API to analyse a data payload.

        Args:
            data: Data content (already limited to ``MAX_DATA_CHARS``).
            data_type: Human-readable format label (``'JSON'`` or ``'CSV'``).
            schema_hint: Pre-computed schema description injected into the prompt.
            truncated: Whether the original file was larger than the preview.

        Returns:
            Validated analysis dictionary.
        """
        truncation_note = (
            "\n**Note:** The data below is a representative sample of a larger file. "
            "Limit statistical observations to what the sample demonstrates; do not extrapolate totals."
            if truncated else ""
        )

        system_prompt = (
            "You are a senior data engineer producing structured technical documentation "
            "for a knowledge-base indexing system. Your output must be precise, factual, "
            "and immediately useful to a data analyst or engineer encountering this dataset "
            "for the first time. Avoid speculation. Do not invent statistics not visible in the data. "
            "Return ONLY a single, valid JSON object — no Markdown fences, no commentary."
        )

        user_prompt = f"""Analyse the following {data_type} dataset and return a JSON object with exactly these four keys:

"title"
  A concise, noun-phrase title describing what this dataset contains (≤ 10 words).
  Examples: "Monthly Sales Transactions by Region", "User Event Log — Mobile App".

"summary"
  A plain-English technical summary covering:
  • **Purpose / domain** — What real-world entity or process does this data represent?
  • **Structure** — Describe the schema: key fields, their types, and what they measure.
  • **Observations** — Highlight notable values, distributions, or anomalies visible in the sample.
  Write 2–4 sentences. No bullet lists inside this JSON string value.

"structured_content"
  A complete Markdown document suitable for a data catalogue. Use this exact structure:
  # <title>
  ## Overview
  <2–3 sentence executive summary>
  ## Schema
  | Field | Type | Description |
  |-------|------|-------------|
  <one row per column / top-level key; infer types from values>
  ## Key Observations
  <3–5 factual bullet points drawn directly from the sample>
  ## Data Quality Notes
  <Missing values, inconsistent formats, outliers, or encoding issues observed>
  ## Sample
  ```
  <first 5–10 rows or representative JSON excerpt>
  ```

"insights"
  A JSON array of 3–5 short strings, each a single actionable or notable finding.
  Example: ["15 % of rows have null 'email' values", "Timestamps span 2022-01 to 2024-06"].
  Return an empty array if insufficient data is available.

Additional context — pre-computed schema hint:
{schema_hint or "Not available."}
{truncation_note}
---
{data_type} data sample:
{data}"""

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            max_tokens=1_500,
            temperature=0.1,
        )

        raw = response.choices[0].message.content or "{}"
        try:
            result: Dict[str, Any] = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.warning("JSON decode failed; attempting strip: %s", exc)
            cleaned = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            result = json.loads(cleaned)

        self._validate_analysis(result, data_type)
        logger.debug("Data analysis title: %r", result.get("title"))
        return result

    def _validate_analysis(self, result: Dict[str, Any], data_type: str) -> None:
        """Ensure required keys are present; fill defaults if not. Mutates in-place."""
        if not result.get("title") or not isinstance(result["title"], str):
            result["title"] = f"{data_type} Dataset"
        if not result.get("summary") or not isinstance(result["summary"], str):
            result["summary"] = f"Dataset in {data_type} format."
        if not result.get("structured_content") or not isinstance(result["structured_content"], str):
            result["structured_content"] = f"# {data_type} Dataset\n\n> AI analysis unavailable.\n"
        if not isinstance(result.get("insights"), list):
            result["insights"] = []

    # ------------------------------------------------------------------
    # Schema / type inference utilities
    # ------------------------------------------------------------------

    def _summarise_json_schema(self, parsed: Any, depth: int = 0) -> str:
        """Produce a concise schema description for a parsed JSON value.

        Only traverses one level of nesting to keep the hint brief.
        """
        if depth > 1:
            return type(parsed).__name__

        if isinstance(parsed, dict):
            fields = ", ".join(
                f"{k!r}: {self._summarise_json_schema(v, depth + 1)}"
                for k, v in list(parsed.items())[:20]
            )
            return f"object{{{fields}}}"
        elif isinstance(parsed, list):
            if parsed:
                item_type = self._summarise_json_schema(parsed[0], depth + 1)
                return f"array[{item_type}] ({len(parsed)} items)"
            return "array (empty)"
        else:
            return type(parsed).__name__

    def _infer_column_types(
        self, rows: List[Dict[str, str]], headers: List[str]
    ) -> Dict[str, str]:
        """Heuristically infer column data types from sampled CSV rows."""
        type_map: Dict[str, str] = {}
        for col in headers:
            values = [r.get(col, "") for r in rows if r.get(col, "").strip()]
            if not values:
                type_map[col] = "empty"
                continue

            # Probe the first non-empty value
            sample = values[0].strip()
            if all(v.lstrip("-").isdigit() for v in values[:20]):
                type_map[col] = "integer"
            else:
                try:
                    [float(v) for v in values[:20]]
                    type_map[col] = "float"
                except ValueError:
                    type_map[col] = "string"

        return type_map

    # ------------------------------------------------------------------
    # CSV preview builder
    # ------------------------------------------------------------------

    def _build_csv_preview(
        self, headers: List[str], rows: List[Dict[str, str]]
    ) -> str:
        """Reconstruct a truncated CSV string for the LLM preview."""
        def _clip(value: str) -> str:
            s = str(value)
            return s[: self.MAX_CELL_LENGTH - 1] + "…" if len(s) > self.MAX_CELL_LENGTH else s

        lines = [",".join(_clip(h) for h in headers)]
        for row in rows[: self.MAX_ROWS_CSV]:
            lines.append(",".join(_clip(str(row.get(h, ""))) for h in headers))

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _detect_data_type(self, content: str) -> str:
        """Detect data format from raw content."""
        stripped = content.strip()
        if stripped.startswith(("{", "[")):
            try:
                json.loads(stripped)
                return "json"
            except json.JSONDecodeError:
                pass
        return "csv"

    def _fallback_analysis(self, data_type: str, preview: str) -> Dict[str, Any]:
        """Return a minimal analysis when the AI call is unavailable."""
        return {
            "title": f"{data_type} Dataset",
            "summary": f"Dataset in {data_type} format. AI analysis unavailable.",
            "structured_content": (
                f"# {data_type} Dataset\n\n"
                "## Sample\n"
                f"```\n{preview[:500]}\n```\n\n"
                "> AI analysis was unavailable. Manual review recommended.\n"
            ),
            "insights": [],
        }

    def _create_error_document(
        self, data_type: str, error: str, content: str
    ) -> ParsedDocument:
        """Build a ``ParsedDocument`` that surfaces a parse error gracefully."""
        logger.warning("%s parsing error: %s", data_type, error)
        return ParsedDocument(
            text=self._clean_text(content[: self.MAX_DATA_CHARS]),
            metadata={
                "data_type": data_type.lower(),
                "parser": "DataParser",
                "error": error,
            },
            title=f"{data_type} Dataset (parse error)",
        )