"""Plain text and Markdown parser with line-aware segments."""

from __future__ import annotations

import re
from typing import Dict, List, Optional

from app.ingestion.parsers.base import DocumentParser, ParsedDocument


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
_ATX_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_SETEXT_MARKER_RE = re.compile(r"^(=+|-+)\s*$")


class TextParser(DocumentParser):
    """Plain text and Markdown parser."""

    def parse(self, file_bytes: bytes) -> ParsedDocument:
        """Parse text bytes into line-aware segments."""
        text = self._decode_text(file_bytes)
        text = text.replace("\r\n", "\n").replace("\r", "\n")

        metadata: Dict[str, str] = {}
        frontmatter_match = _FRONTMATTER_RE.match(text)
        if frontmatter_match:
            metadata.update(self._parse_frontmatter(frontmatter_match.group(1)))
            text = text[frontmatter_match.end():]

        lines = text.split("\n")
        segments = []
        heading_path: List[str] = []
        paragraph_lines: List[str] = []
        paragraph_start: Optional[int] = None
        paragraph_index = 0
        block_index = 0

        def flush_paragraph(line_end: int) -> None:
            nonlocal paragraph_lines, paragraph_start, paragraph_index, block_index
            content_lines = [line.rstrip() for line in paragraph_lines]
            if not content_lines:
                paragraph_lines = []
                paragraph_start = None
                return
            paragraph_text = "\n".join(content_lines).strip()
            if not paragraph_text:
                paragraph_lines = []
                paragraph_start = None
                return

            paragraph_index += 1
            block_index += 1
            segment = self._build_segment(
                paragraph_text,
                segment_type="paragraph",
                separator="\n\n",
                source_type="text/markdown",
                line_start=paragraph_start,
                line_end=line_end,
                paragraph_index=paragraph_index,
                block_index=block_index,
                heading_path=list(heading_path),
                heading_path_start=list(heading_path),
                heading_path_end=list(heading_path),
                section_title=heading_path[-1] if heading_path else None,
                section_title_start=heading_path[-1] if heading_path else None,
                section_title_end=heading_path[-1] if heading_path else None,
                extraction_confidence=1.0,
            )
            if segment:
                segments.append(segment)
            paragraph_lines = []
            paragraph_start = None

        for index, raw_line in enumerate(lines, start=1):
            line = raw_line.rstrip()
            stripped = line.strip()

            if not stripped:
                flush_paragraph(index - 1)
                continue

            atx_match = _ATX_HEADING_RE.match(stripped)
            setext_level = None
            if not atx_match and index < len(lines):
                next_line = lines[index].rstrip() if index < len(lines) else ""
                marker = _SETEXT_MARKER_RE.match(next_line.strip())
                if marker and stripped and not paragraph_lines:
                    setext_level = 1 if marker.group(1).startswith("=") else 2

            if atx_match or setext_level is not None:
                flush_paragraph(index - 1)
                if atx_match:
                    level = len(atx_match.group(1))
                    heading_text = atx_match.group(2).strip()
                    consumed_lines = 1
                else:
                    level = setext_level or 2
                    heading_text = stripped
                    consumed_lines = 2

                heading_path = heading_path[: level - 1] + [heading_text]
                block_index += 1
                heading_segment = self._build_segment(
                    heading_text,
                    segment_type="heading",
                    separator="\n\n",
                    source_type="text/markdown",
                    line_start=index,
                    line_end=index + consumed_lines - 1,
                    block_index=block_index,
                    heading_path=list(heading_path),
                    heading_path_start=list(heading_path),
                    heading_path_end=list(heading_path),
                    section_title=heading_text,
                    section_title_start=heading_text,
                    section_title_end=heading_text,
                    extraction_confidence=1.0,
                )
                if heading_segment:
                    segments.append(heading_segment)

                if consumed_lines == 2:
                    # Skip the underline marker line.
                    lines[index] = ""
                continue

            if paragraph_start is None:
                paragraph_start = index
            paragraph_lines.append(line)

        flush_paragraph(len(lines))

        metadata.setdefault("content_format", "markdown" if any(segment.segment_type == "heading" for segment in segments) else "text")
        title = metadata.get("title")
        if not title:
            for segment in segments:
                if segment.segment_type == "heading":
                    title = segment.text
                    break
        if not title:
            title = self._extract_title("\n".join(segment.text for segment in segments))

        return self._build_document_from_segments(
            segments=segments,
            metadata=metadata,
            title=title,
            author=metadata.get("author"),
        )

    def _decode_text(self, file_bytes: bytes) -> str:
        for encoding in ("utf-8", "latin-1", "cp1252"):
            try:
                return file_bytes.decode(encoding)
            except UnicodeDecodeError:
                continue
        return file_bytes.decode("utf-8", errors="ignore")

    def _parse_frontmatter(self, frontmatter_text: str) -> Dict[str, str]:
        metadata: Dict[str, str] = {}
        for line in frontmatter_text.split("\n"):
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            metadata[key.strip()] = value.strip()
        return metadata
