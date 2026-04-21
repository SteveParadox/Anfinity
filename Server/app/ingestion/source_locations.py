"""Structured source-location helpers for citation-aware chunking."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence


def _clean_heading_path(values: Optional[Sequence[str]]) -> List[str]:
    cleaned: List[str] = []
    for value in values or []:
        text = str(value or "").strip()
        if text:
            cleaned.append(text)
    return cleaned


def _common_prefix(paths: Iterable[Sequence[str]]) -> List[str]:
    normalized = [_clean_heading_path(path) for path in paths if _clean_heading_path(path)]
    if not normalized:
        return []

    prefix = list(normalized[0])
    for path in normalized[1:]:
        new_prefix: List[str] = []
        for left, right in zip(prefix, path):
            if left != right:
                break
            new_prefix.append(left)
        prefix = new_prefix
        if not prefix:
            break
    return prefix


def _only_value(values: Iterable[Optional[Any]]) -> Optional[Any]:
    present = [value for value in values if value not in (None, [], {}, "")]
    if not present:
        return None
    first = present[0]
    return first if all(value == first for value in present[1:]) else None


@dataclass
class SourceLocation:
    """Structured location information for a parsed source region."""

    source_file_name: Optional[str] = None
    source_type: Optional[str] = None
    page_number: Optional[int] = None
    page_start: Optional[int] = None
    page_end: Optional[int] = None
    section_title: Optional[str] = None
    section_title_start: Optional[str] = None
    section_title_end: Optional[str] = None
    heading_path: List[str] = field(default_factory=list)
    heading_path_start: List[str] = field(default_factory=list)
    heading_path_end: List[str] = field(default_factory=list)
    paragraph_index: Optional[int] = None
    paragraph_start: Optional[int] = None
    paragraph_end: Optional[int] = None
    line_start: Optional[int] = None
    line_end: Optional[int] = None
    char_start: Optional[int] = None
    char_end: Optional[int] = None
    block_index: Optional[int] = None
    block_start: Optional[int] = None
    block_end: Optional[int] = None
    dom_path: Optional[str] = None
    dom_path_start: Optional[str] = None
    dom_path_end: Optional[str] = None
    bounding_boxes: List[Dict[str, Any]] = field(default_factory=list)
    extraction_confidence: Optional[float] = None
    spans_multiple_pages: bool = False
    spans_multiple_sections: bool = False

    def to_dict(self) -> Dict[str, Any]:
        data = {
            "source_file_name": self.source_file_name,
            "source_type": self.source_type,
            "page_number": self.page_number,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "section_title": self.section_title,
            "section_title_start": self.section_title_start,
            "section_title_end": self.section_title_end,
            "heading_path": list(self.heading_path),
            "heading_path_start": list(self.heading_path_start),
            "heading_path_end": list(self.heading_path_end),
            "paragraph_index": self.paragraph_index,
            "paragraph_start": self.paragraph_start,
            "paragraph_end": self.paragraph_end,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "block_index": self.block_index,
            "block_start": self.block_start,
            "block_end": self.block_end,
            "dom_path": self.dom_path,
            "dom_path_start": self.dom_path_start,
            "dom_path_end": self.dom_path_end,
            "bounding_boxes": list(self.bounding_boxes),
            "extraction_confidence": self.extraction_confidence,
            "spans_multiple_pages": self.spans_multiple_pages,
            "spans_multiple_sections": self.spans_multiple_sections,
        }
        return {
            key: value
            for key, value in data.items()
            if value not in (None, [], {}, "")
        }


@dataclass
class ParsedSegment:
    """Atomic source region used to build traceable chunks."""

    text: str
    location: SourceLocation = field(default_factory=SourceLocation)
    segment_type: str = "paragraph"
    separator: str = "\n\n"
    metadata: Dict[str, Any] = field(default_factory=dict)
    token_count: Optional[int] = None

    def to_metadata(self) -> Dict[str, Any]:
        data = {
            **self.metadata,
            **self.location.to_dict(),
            "segment_type": self.segment_type,
        }
        return {key: value for key, value in data.items() if value not in (None, [], {}, "")}


def assign_char_offsets(segments: Sequence[ParsedSegment]) -> str:
    """Build full text from *segments* while assigning extracted char offsets."""

    parts: List[str] = []
    cursor = 0
    filtered = [segment for segment in segments if str(segment.text or "").strip()]

    for index, segment in enumerate(filtered):
        text = str(segment.text or "").strip()
        segment.text = text
        segment.location.char_start = cursor
        cursor += len(text)
        segment.location.char_end = cursor
        parts.append(text)
        if index < len(filtered) - 1:
            separator = segment.separator or "\n\n"
            parts.append(separator)
            cursor += len(separator)

    return "".join(parts)


def slice_location(location: SourceLocation, text: str, start: int, end: int) -> SourceLocation:
    """Create a location for a text slice from *start* to *end* within *text*."""

    prefix = text[:start]
    body = text[start:end]

    line_start = None
    line_end = None
    if location.line_start is not None:
        line_start = int(location.line_start) + prefix.count("\n")
        line_end = line_start + body.count("\n")

    char_start = None
    char_end = None
    if location.char_start is not None:
        char_start = int(location.char_start) + start
        char_end = char_start + len(body)

    return SourceLocation(
        source_file_name=location.source_file_name,
        source_type=location.source_type,
        page_number=location.page_number,
        page_start=location.page_start,
        page_end=location.page_end,
        section_title=location.section_title,
        section_title_start=location.section_title_start,
        section_title_end=location.section_title_end,
        heading_path=list(location.heading_path),
        heading_path_start=list(location.heading_path_start),
        heading_path_end=list(location.heading_path_end),
        paragraph_index=location.paragraph_index,
        paragraph_start=location.paragraph_start,
        paragraph_end=location.paragraph_end,
        line_start=line_start,
        line_end=line_end,
        char_start=char_start,
        char_end=char_end,
        block_index=location.block_index,
        block_start=location.block_start,
        block_end=location.block_end,
        dom_path=location.dom_path,
        dom_path_start=location.dom_path_start,
        dom_path_end=location.dom_path_end,
        bounding_boxes=list(location.bounding_boxes),
        extraction_confidence=location.extraction_confidence,
        spans_multiple_pages=location.spans_multiple_pages,
        spans_multiple_sections=location.spans_multiple_sections,
    )


def merge_segment_metadata(
    segments: Sequence[ParsedSegment],
    *,
    base_metadata: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Merge segment-level locations into chunk-level metadata."""

    if not segments:
        return dict(base_metadata or {})

    locations = [segment.location for segment in segments]
    base = dict(base_metadata or {})

    page_starts = [value for value in (location.page_start or location.page_number for location in locations) if value is not None]
    page_ends = [value for value in (location.page_end or location.page_number for location in locations) if value is not None]
    paragraph_values = [location.paragraph_index for location in locations if location.paragraph_index is not None]
    block_values = [location.block_index for location in locations if location.block_index is not None]
    line_starts = [location.line_start for location in locations if location.line_start is not None]
    line_ends = [location.line_end for location in locations if location.line_end is not None]
    char_starts = [location.char_start for location in locations if location.char_start is not None]
    char_ends = [location.char_end for location in locations if location.char_end is not None]
    confidences = [float(location.extraction_confidence) for location in locations if location.extraction_confidence is not None]
    heading_paths = [location.heading_path or location.heading_path_start for location in locations if location.heading_path or location.heading_path_start]
    common_heading_path = _common_prefix(heading_paths)
    heading_path_start = _clean_heading_path(heading_paths[0]) if heading_paths else []
    heading_path_end = _clean_heading_path(heading_paths[-1]) if heading_paths else []

    section_titles = [
        location.section_title
        or (location.heading_path[-1] if location.heading_path else None)
        or (location.heading_path_start[-1] if location.heading_path_start else None)
        for location in locations
    ]
    single_section = _only_value(section_titles)
    source_file_name = _only_value([location.source_file_name for location in locations]) or base.get("source_file_name")
    source_type = _only_value([location.source_type for location in locations]) or base.get("source_type")
    dom_paths = [location.dom_path or location.dom_path_start for location in locations if location.dom_path or location.dom_path_start]
    dom_path = _only_value(dom_paths)

    single_page_for_lines = False
    if line_starts and line_ends:
        if not page_starts and not page_ends:
            single_page_for_lines = True
        elif page_starts and page_ends and min(page_starts) == max(page_ends):
            single_page_for_lines = True

    merged = {
        **base,
        "source_file_name": source_file_name,
        "source_type": source_type,
        "page_number": page_starts[0] if page_starts and page_ends and min(page_starts) == max(page_ends) else None,
        "page_start": min(page_starts) if page_starts else None,
        "page_end": max(page_ends) if page_ends else None,
        "section_title": single_section,
        "section_title_start": section_titles[0] if section_titles else None,
        "section_title_end": section_titles[-1] if section_titles else None,
        "heading_path": common_heading_path,
        "heading_path_start": heading_path_start,
        "heading_path_end": heading_path_end,
        "paragraph_index": paragraph_values[0] if paragraph_values and len(set(paragraph_values)) == 1 else None,
        "paragraph_start": min(paragraph_values) if paragraph_values else None,
        "paragraph_end": max(paragraph_values) if paragraph_values else None,
        "line_start": min(line_starts) if line_starts and single_page_for_lines else None,
        "line_end": max(line_ends) if line_ends and single_page_for_lines else None,
        "char_start": min(char_starts) if char_starts else None,
        "char_end": max(char_ends) if char_ends else None,
        "block_index": block_values[0] if block_values and len(set(block_values)) == 1 else None,
        "block_start": min(block_values) if block_values else None,
        "block_end": max(block_values) if block_values else None,
        "dom_path": dom_path,
        "dom_path_start": dom_paths[0] if dom_paths else None,
        "dom_path_end": dom_paths[-1] if dom_paths else None,
        "bounding_boxes": [
            box
            for location in locations
            for box in (location.bounding_boxes or [])
        ],
        "extraction_confidence": min(confidences) if confidences else base.get("extraction_confidence"),
        "spans_multiple_pages": bool(page_starts and page_ends and min(page_starts) != max(page_ends)),
        "spans_multiple_sections": bool(section_titles and len({value for value in section_titles if value}) > 1),
    }

    return {
        key: value
        for key, value in merged.items()
        if value not in (None, [], {}, "")
    }


def build_citation_label(metadata: Mapping[str, Any], *, document_title: Optional[str] = None) -> str:
    """Build a human-readable citation label without inventing missing fields."""

    label = str(
        metadata.get("source_file_name")
        or document_title
        or metadata.get("document_title")
        or "Untitled Document"
    ).strip()
    parts = [label]

    page_number = metadata.get("page_number")
    page_start = metadata.get("page_start")
    page_end = metadata.get("page_end")
    if page_number is not None:
        parts.append(f"page {page_number}")
    elif page_start is not None and page_end is not None:
        parts.append(f"pages {page_start}-{page_end}")

    section_title = metadata.get("section_title")
    if section_title:
        parts.append(f"section '{section_title}'")
    else:
        heading_path = _clean_heading_path(metadata.get("heading_path") or metadata.get("heading_path_end"))
        if heading_path:
            parts.append(f"heading '{heading_path[-1]}'")

    line_start = metadata.get("line_start")
    line_end = metadata.get("line_end")
    if line_start is not None and line_end is not None:
        parts.append(
            f"lines {line_start}-{line_end}"
            if line_start != line_end
            else f"line {line_start}"
        )

    paragraph_index = metadata.get("paragraph_index")
    if paragraph_index is not None:
        parts.append(f"paragraph {paragraph_index}")

    return ", ".join(parts)


def source_location_payload(metadata: Mapping[str, Any], *, document_title: Optional[str] = None) -> Dict[str, Any]:
    """Build an API-friendly location payload from chunk metadata."""

    location = {
        "source_file_name": metadata.get("source_file_name") or document_title,
        "source_type": metadata.get("source_type"),
        "page_number": metadata.get("page_number"),
        "page_start": metadata.get("page_start"),
        "page_end": metadata.get("page_end"),
        "section_title": metadata.get("section_title"),
        "section_title_start": metadata.get("section_title_start"),
        "section_title_end": metadata.get("section_title_end"),
        "heading_path": _clean_heading_path(metadata.get("heading_path")),
        "heading_path_start": _clean_heading_path(metadata.get("heading_path_start")),
        "heading_path_end": _clean_heading_path(metadata.get("heading_path_end")),
        "paragraph_index": metadata.get("paragraph_index"),
        "paragraph_start": metadata.get("paragraph_start"),
        "paragraph_end": metadata.get("paragraph_end"),
        "line_start": metadata.get("line_start"),
        "line_end": metadata.get("line_end"),
        "char_start": metadata.get("char_start"),
        "char_end": metadata.get("char_end"),
        "block_index": metadata.get("block_index"),
        "block_start": metadata.get("block_start"),
        "block_end": metadata.get("block_end"),
        "dom_path": metadata.get("dom_path"),
        "dom_path_start": metadata.get("dom_path_start"),
        "dom_path_end": metadata.get("dom_path_end"),
        "bounding_boxes": list(metadata.get("bounding_boxes") or []),
        "extraction_confidence": metadata.get("extraction_confidence"),
        "spans_multiple_pages": bool(metadata.get("spans_multiple_pages")),
        "spans_multiple_sections": bool(metadata.get("spans_multiple_sections")),
        "citation_label": build_citation_label(metadata, document_title=document_title),
    }
    return {
        key: value
        for key, value in location.items()
        if value not in (None, [], {}, "")
    }


def enrich_citation_metadata(
    metadata: Optional[Mapping[str, Any]],
    *,
    document_title: Optional[str] = None,
    source_type: Optional[str] = None,
) -> Dict[str, Any]:
    """Ensure chunk metadata carries normalized citation fields."""

    enriched = dict(metadata or {})
    if source_type and not enriched.get("source_type"):
        enriched["source_type"] = source_type
    if document_title and not enriched.get("source_file_name"):
        enriched.setdefault("source_file_name", document_title)

    enriched["citation_label"] = build_citation_label(enriched, document_title=document_title)
    enriched["source_location"] = source_location_payload(enriched, document_title=document_title)
    return enriched
