"""HTML parser with heading-path and DOM context extraction."""

from __future__ import annotations

from typing import Dict, List, Optional

from bs4 import BeautifulSoup, Tag

from app.ingestion.parsers.base import DocumentParser, ParsedDocument


class HTMLParser(DocumentParser):
    """Parse HTML documents into section-aware segments."""

    CONTENT_TAGS = {"p", "li", "pre", "blockquote", "td", "th"}
    HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}

    def parse(self, file_bytes: bytes) -> ParsedDocument:
        html = self._decode(file_bytes)
        soup = BeautifulSoup(html, "html.parser")

        title_text = None
        if soup.title and soup.title.string:
            title_text = soup.title.string.strip()

        metadata: Dict[str, str] = {
            "title": title_text,
            "content_format": "html",
        }
        segments = []
        heading_path: List[str] = []
        paragraph_index = 0
        block_index = 0

        body = soup.body or soup
        for node in body.descendants:
            if not isinstance(node, Tag):
                continue

            tag_name = node.name.lower()
            if tag_name in self.HEADING_TAGS:
                text = self._extract_node_text(node)
                if not text:
                    continue
                level = int(tag_name[1])
                heading_path = heading_path[: level - 1] + [text]
                block_index += 1
                segment = self._build_segment(
                    text,
                    segment_type="heading",
                    source_type="text/html",
                    block_index=block_index,
                    dom_path=self._dom_path(node),
                    heading_path=list(heading_path),
                    heading_path_start=list(heading_path),
                    heading_path_end=list(heading_path),
                    section_title=text,
                    section_title_start=text,
                    section_title_end=text,
                    extraction_confidence=0.95,
                )
                if segment:
                    segments.append(segment)
                continue

            if tag_name not in self.CONTENT_TAGS:
                continue
            if self._has_content_ancestor(node):
                continue

            text = self._extract_node_text(node)
            if not text:
                continue

            paragraph_index += 1
            block_index += 1
            section_title = heading_path[-1] if heading_path else None
            segment = self._build_segment(
                text,
                segment_type="paragraph",
                source_type="text/html",
                paragraph_index=paragraph_index,
                paragraph_start=paragraph_index,
                paragraph_end=paragraph_index,
                block_index=block_index,
                block_start=block_index,
                block_end=block_index,
                dom_path=self._dom_path(node),
                heading_path=list(heading_path),
                heading_path_start=list(heading_path),
                heading_path_end=list(heading_path),
                section_title=section_title,
                section_title_start=section_title,
                section_title_end=section_title,
                extraction_confidence=0.9,
            )
            if segment:
                segments.append(segment)

        if not title_text:
            for segment in segments:
                if segment.segment_type == "heading":
                    title_text = segment.text
                    break

        return self._build_document_from_segments(
            segments=segments,
            metadata=metadata,
            title=title_text,
        )

    def _decode(self, file_bytes: bytes) -> str:
        for encoding in ("utf-8", "latin-1", "cp1252"):
            try:
                return file_bytes.decode(encoding)
            except UnicodeDecodeError:
                continue
        return file_bytes.decode("utf-8", errors="ignore")

    def _extract_node_text(self, node: Tag) -> str:
        if node.name.lower() == "pre":
            return "\n".join(line.rstrip() for line in node.get_text("\n").splitlines()).strip()
        return " ".join(node.get_text(" ", strip=True).split())

    def _dom_path(self, node: Tag) -> str:
        parts: List[str] = []
        current: Optional[Tag] = node
        while current and isinstance(current, Tag):
            name = current.name.lower()
            identifier = current.get("id")
            if identifier:
                parts.append(f"{name}#{identifier}")
            else:
                parts.append(name)
            current = current.parent if isinstance(current.parent, Tag) else None
            if current is None or current.name.lower() == "[document]":
                break
        return ">".join(reversed(parts))

    def _has_content_ancestor(self, node: Tag) -> bool:
        parent = node.parent if isinstance(node.parent, Tag) else None
        while parent is not None:
            if parent.name and parent.name.lower() in self.CONTENT_TAGS:
                return True
            parent = parent.parent if isinstance(parent.parent, Tag) else None
        return False
