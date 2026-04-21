"""PDF document parser with page and block-level source locations."""

from __future__ import annotations

from statistics import median
from typing import Any, Dict, List

import fitz  # PyMuPDF

from app.ingestion.parsers.base import DocumentParser, ParsedDocument


class PDFParser(DocumentParser):
    """PDF document parser."""

    def parse(self, file_bytes: bytes) -> ParsedDocument:
        doc = fitz.open(stream=file_bytes, filetype="pdf")

        metadata: Dict[str, Any] = {}
        pdf_metadata = doc.metadata or {}
        metadata["title"] = pdf_metadata.get("title")
        metadata["author"] = pdf_metadata.get("author")
        metadata["subject"] = pdf_metadata.get("subject")
        metadata["creator"] = pdf_metadata.get("creator")
        metadata["creation_date"] = pdf_metadata.get("creationDate")
        metadata["modification_date"] = pdf_metadata.get("modDate")
        metadata["content_format"] = "pdf"
        metadata["extraction_method"] = "pymupdf_blocks"

        page_count = len(doc)
        pages_with_text = 0
        pages_without_text = 0
        total_extracted_chars = 0
        page_summaries: List[Dict[str, Any]] = []
        font_sizes: List[float] = []
        page_dicts: List[Dict[str, Any]] = []

        for page_number in range(page_count):
            page = doc[page_number]
            page_dict = page.get_text("dict")
            page_dicts.append(page_dict)
            page_text = page.get_text("text").strip()
            text_length = len(page_text)
            total_extracted_chars += text_length
            if text_length:
                pages_with_text += 1
            else:
                pages_without_text += 1

            page_summaries.append(
                {
                    "page_number": page_number + 1,
                    "width": page.rect.width,
                    "height": page.rect.height,
                    "text_length": text_length,
                }
            )

            for block in page_dict.get("blocks", []):
                if block.get("type") != 0:
                    continue
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        size = span.get("size")
                        if size is not None:
                            font_sizes.append(float(size))

        metadata["pages"] = page_summaries
        metadata["pages_with_text"] = pages_with_text
        metadata["pages_without_text"] = pages_without_text
        metadata["total_extracted_chars"] = total_extracted_chars

        baseline_font = median(font_sizes) if font_sizes else 12.0
        heading_path: List[str] = []
        paragraph_index = 0
        block_index = 0
        segments = []

        for page_number, page_dict in enumerate(page_dicts, start=1):
            line_cursor = 1
            for block in page_dict.get("blocks", []):
                if block.get("type") != 0:
                    continue

                line_texts: List[str] = []
                max_font_size = 0.0
                looks_bold = False
                for line in block.get("lines", []):
                    spans = line.get("spans", [])
                    line_text = "".join(span.get("text", "") for span in spans).strip()
                    if line_text:
                        line_texts.append(line_text)
                    for span in spans:
                        max_font_size = max(max_font_size, float(span.get("size") or 0.0))
                        font_name = str(span.get("font") or "").lower()
                        if "bold" in font_name:
                            looks_bold = True

                if not line_texts:
                    continue

                block_text = "\n".join(line_texts).strip()
                if not block_text:
                    continue

                line_start = line_cursor
                line_end = line_cursor + len(line_texts) - 1
                line_cursor = line_end + 1
                block_index += 1

                is_heading = self._looks_like_heading(
                    block_text=block_text,
                    max_font_size=max_font_size,
                    baseline_font=baseline_font,
                    line_count=len(line_texts),
                    looks_bold=looks_bold,
                )

                location_kwargs = {
                    "source_type": "application/pdf",
                    "page_number": page_number,
                    "page_start": page_number,
                    "page_end": page_number,
                    "line_start": line_start,
                    "line_end": line_end,
                    "block_index": block_index,
                    "block_start": block_index,
                    "block_end": block_index,
                    "bounding_boxes": [
                        {
                            "page_number": page_number,
                            "x0": block["bbox"][0],
                            "y0": block["bbox"][1],
                            "x1": block["bbox"][2],
                            "y1": block["bbox"][3],
                        }
                    ],
                }

                if is_heading:
                    level = self._heading_level(max_font_size=max_font_size, baseline_font=baseline_font)
                    heading_path = heading_path[: level - 1] + [block_text]
                    segment = self._build_segment(
                        block_text,
                        segment_type="heading",
                        heading_path=list(heading_path),
                        heading_path_start=list(heading_path),
                        heading_path_end=list(heading_path),
                        section_title=block_text,
                        section_title_start=block_text,
                        section_title_end=block_text,
                        extraction_confidence=0.65,
                        **location_kwargs,
                    )
                else:
                    paragraph_index += 1
                    section_title = heading_path[-1] if heading_path else None
                    segment = self._build_segment(
                        block_text,
                        segment_type="paragraph",
                        paragraph_index=paragraph_index,
                        paragraph_start=paragraph_index,
                        paragraph_end=paragraph_index,
                        heading_path=list(heading_path),
                        heading_path_start=list(heading_path),
                        heading_path_end=list(heading_path),
                        section_title=section_title,
                        section_title_start=section_title,
                        section_title_end=section_title,
                        extraction_confidence=0.85,
                        **location_kwargs,
                    )
                if segment:
                    segments.append(segment)

        doc.close()

        return self._build_document_from_segments(
            segments=segments,
            metadata=metadata,
            title=metadata.get("title"),
            author=metadata.get("author"),
            page_count=page_count,
        )

    def _looks_like_heading(
        self,
        *,
        block_text: str,
        max_font_size: float,
        baseline_font: float,
        line_count: int,
        looks_bold: bool,
    ) -> bool:
        normalized = " ".join(block_text.split())
        if not normalized or len(normalized) > 160:
            return False
        if line_count > 3:
            return False
        if normalized.endswith(".") and max_font_size < baseline_font + 2.0:
            return False
        return max_font_size >= baseline_font + 1.5 or (looks_bold and max_font_size >= baseline_font + 0.5)

    def _heading_level(self, *, max_font_size: float, baseline_font: float) -> int:
        if max_font_size >= baseline_font + 5:
            return 1
        if max_font_size >= baseline_font + 3:
            return 2
        return 3
