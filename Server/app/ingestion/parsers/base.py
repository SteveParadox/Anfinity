"""Base document parser."""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
import re

from app.ingestion.source_locations import ParsedSegment, SourceLocation, assign_char_offsets


@dataclass
class ParsedDocument:
    """Parsed document result."""
    text: str
    metadata: Dict[str, Any]
    title: Optional[str] = None
    author: Optional[str] = None
    page_count: Optional[int] = None
    word_count: Optional[int] = None
    segments: List[ParsedSegment] = field(default_factory=list)


class DocumentParser(ABC):
    """Abstract base class for document parsers."""
    
    @abstractmethod
    def parse(self, file_bytes: bytes) -> ParsedDocument:
        """Parse document bytes.
        
        Args:
            file_bytes: Raw file content
            
        Returns:
            Parsed document
        """
        pass
    
    def _clean_text(self, text: str) -> str:
        """Clean and normalize text.
        
        Args:
            text: Raw text
            
        Returns:
            Cleaned text
        """
        # Preserve line structure because downstream chunking relies on headings,
        # paragraphs, and page markers remaining intact.
        text = text.replace("\r\n", "\n").replace("\r", "\n")

        # Remove control characters except newlines and tabs.
        text = "".join(
            char for char in text
            if char in ("\n", "\t") or ord(char) >= 32
        )

        # Normalize intra-line whitespace without collapsing line breaks.
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r" *\n *", "\n", text)

        # Keep at most one blank line between content blocks.
        text = re.sub(r"\n{3,}", "\n\n", text)

        return text.strip()

    def _clean_inline_text(self, text: str) -> str:
        """Normalize one logical block of text."""
        return self._clean_text(text).strip()

    def _build_segment(
        self,
        text: str,
        *,
        segment_type: str = "paragraph",
        separator: str = "\n\n",
        metadata: Optional[Dict[str, Any]] = None,
        **location_fields: Any,
    ) -> Optional[ParsedSegment]:
        """Create a parsed segment, skipping empty content."""
        cleaned = self._clean_inline_text(text)
        if not cleaned:
            return None
        return ParsedSegment(
            text=cleaned,
            location=SourceLocation(**location_fields),
            segment_type=segment_type,
            separator=separator,
            metadata=dict(metadata or {}),
        )

    def _build_document_from_segments(
        self,
        *,
        segments: List[ParsedSegment],
        metadata: Dict[str, Any],
        title: Optional[str] = None,
        author: Optional[str] = None,
        page_count: Optional[int] = None,
    ) -> ParsedDocument:
        """Compose final parsed text and extracted char offsets from segments."""
        filtered_segments = [segment for segment in segments if segment and segment.text.strip()]
        full_text = assign_char_offsets(filtered_segments)

        resolved_title = title or metadata.get("title") or self._extract_title(full_text)
        return ParsedDocument(
            text=full_text,
            metadata=metadata,
            title=resolved_title,
            author=author or metadata.get("author"),
            page_count=page_count,
            word_count=self._count_words(full_text),
            segments=filtered_segments,
        )

    def _build_single_segment_document(
        self,
        *,
        text: str,
        metadata: Dict[str, Any],
        title: Optional[str] = None,
        author: Optional[str] = None,
        page_count: Optional[int] = None,
        extraction_confidence: float = 0.5,
    ) -> ParsedDocument:
        """Fallback builder for sources without structured regions."""
        segment = self._build_segment(
            text,
            extraction_confidence=extraction_confidence,
        )
        return self._build_document_from_segments(
            segments=[segment] if segment else [],
            metadata=metadata,
            title=title,
            author=author,
            page_count=page_count,
        )
    
    def _extract_title(self, text: str) -> Optional[str]:
        """Extract title from text.
        
        Args:
            text: Document text
            
        Returns:
            Extracted title or None
        """
        lines = text.strip().split('\n')
        if lines:
            first_line = lines[0].strip()
            if len(first_line) < 200 and not first_line.startswith('#'):
                return first_line
        return None
    
    def _count_words(self, text: str) -> int:
        """Count words in text.
        
        Args:
            text: Document text
            
        Returns:
            Word count
        """
        return len(text.split())
