"""Smart text chunking with context preservation."""
from __future__ import annotations

import re
import logging
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import tiktoken

from app.config import settings
from app.ingestion.source_locations import (
    ParsedSegment,
    enrich_citation_metadata,
    merge_segment_metadata,
    slice_location,
)

if TYPE_CHECKING:
    from app.ingestion.parsers.base import ParsedDocument

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class TextChunk:
    """Text chunk with metadata."""

    text: str
    index: int
    token_count: int
    metadata: Dict[str, Any] = field(default_factory=dict)
    context_before: Optional[str] = None
    context_after: Optional[str] = None

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise TypeError(f"TextChunk.text must be str, got {type(self.text)}")
        if self.token_count < 0:
            raise ValueError("token_count cannot be negative")


# ---------------------------------------------------------------------------
# Chunker
# ---------------------------------------------------------------------------

class Chunker:
    """Smart text chunker with recursive splitting.

    Splitting hierarchy:
      1. Markdown headings
      2. Paragraphs (double newline)
      3. Sentences
      4. Hard character-limit fallback (for pathological single-sentence blobs)
    
    OPTIMIZED: Token count caching prevents repeated re-encoding of the same
    text fragments during chunk building and merging, significantly reducing
    CPU overhead for large documents.
    """

    # Compiled once at class level to avoid re-compiling on every call.
    _HEADING_RE = re.compile(r"^(#{1,6}\s+.+)$", re.MULTILINE)
    _SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")

    def __init__(
        self,
        chunk_size: Optional[int] = None,
        chunk_overlap: Optional[int] = None,
        max_tokens: Optional[int] = None,
        context_window: int = 100,
    ) -> None:
        self.chunk_size: int = chunk_size or settings.CHUNK_SIZE
        self.chunk_overlap: int = chunk_overlap or settings.CHUNK_OVERLAP
        self.max_tokens: int = max_tokens or settings.CHUNK_MAX_TOKENS
        # Characters of neighbouring chunk text exposed as context.
        self.context_window = context_window

        if self.chunk_overlap >= self.chunk_size:
            raise ValueError(
                f"chunk_overlap ({self.chunk_overlap}) must be less than "
                f"chunk_size ({self.chunk_size})"
            )

        self._tokenizer = tiktoken.get_encoding("cl100k_base")
        # Token count cache to avoid re-encoding the same text fragments
        self._token_cache: Dict[str, int] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def count_tokens(self, text: str) -> int:
        """Return the number of tokens in *text*.
        
        Uses a simple cache to avoid re-encoding the same text fragments
        during chunk building and merging (significant CPU savings).
        """
        if text in self._token_cache:
            return self._token_cache[text]
        count = len(self._tokenizer.encode(text))
        self._token_cache[text] = count
        return count

    def chunk_text(
        self,
        text: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> List[TextChunk]:
        """Chunk *text* using the recursive splitting strategy.

        Args:
            text:     Raw input text.
            metadata: Optional key/value pairs attached to every chunk.

        Returns:
            Ordered list of :class:`TextChunk` objects.
        """
        meta = metadata or {}
        # Normalize newlines early: convert \r\n and \r to \n for consistency
        cleaned = text.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not cleaned:
            return []

        token_count = self.count_tokens(cleaned)
        if token_count <= self.chunk_size:
            return [
                TextChunk(
                    text=cleaned,
                    index=0,
                    token_count=token_count,
                    metadata=meta,
                )
            ]

        chunks = self._split_by_headings(cleaned, meta)
        chunks = self._merge_small_chunks(chunks)
        chunks = self._add_context(chunks)
        return chunks

    def chunk_parsed_document(
        self,
        parsed_document: ParsedDocument,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> List[TextChunk]:
        """Chunk a parsed document while preserving source-location traceability."""
        base_metadata = {
            **(parsed_document.metadata or {}),
            **(metadata or {}),
        }
        segments = list(parsed_document.segments or [])
        if not segments:
            return self.chunk_text(parsed_document.text, metadata=base_metadata)

        expanded_segments = self._expand_segments_for_chunking(segments)
        if not expanded_segments:
            return []

        chunks: List[TextChunk] = []
        current: List[ParsedSegment] = []
        current_tokens = 0
        chunk_index = 0

        for segment in expanded_segments:
            segment_tokens = segment.token_count or self.count_tokens(segment.text)
            segment.token_count = segment_tokens

            if current and current_tokens + segment_tokens > self.chunk_size:
                chunks.append(self._build_traceable_chunk(current, chunk_index, base_metadata))
                chunk_index += 1
                current = self._segment_overlap(current)
                current_tokens = sum(part.token_count or self.count_tokens(part.text) for part in current)

            current.append(segment)
            current_tokens += segment_tokens

        if current:
            chunks.append(self._build_traceable_chunk(current, chunk_index, base_metadata))

        return self._add_context(chunks)

    # ------------------------------------------------------------------
    # Splitting strategies
    # ------------------------------------------------------------------

    def _split_by_headings(
        self,
        text: str,
        metadata: Dict[str, Any],
    ) -> List[TextChunk]:
        """Split on Markdown headings; fall through to paragraph splitting
        when no headings are present or the result is a single block."""

        # BUG FIX: the original code wrapped the already-capturing pattern in
        # *another* capture group: re.split(f'({heading_pattern})', ...).
        # A pattern like r'(^(#{1,6}\s+.+)$)' causes each heading to appear
        # twice in the split result (once per capture group).  We use the
        # compiled single-group pattern directly.
        parts = self._HEADING_RE.split(text)

        # Filter empty strings that result from leading/trailing splits.
        parts = [p for p in parts if p.strip()]

        if len(parts) <= 1:
            # No headings found – delegate immediately.
            return self._split_by_paragraphs(text, metadata)

        chunks: List[TextChunk] = []
        current: List[str] = []
        current_tokens = 0
        chunk_index = 0

        for part in parts:
            part_tokens = self.count_tokens(part)

            if current_tokens + part_tokens > self.chunk_size and current:
                chunk_text = "".join(current).strip()
                chunks.append(
                    TextChunk(
                        text=chunk_text,
                        index=chunk_index,
                        token_count=self.count_tokens(chunk_text),
                        metadata=dict(metadata),
                    )
                )
                chunk_index += 1
                overlap = self._get_overlap_text(current, separator="")
                current = [overlap, part] if overlap else [part]
                # Use cached count from overlap + current part (avoid recount)
                current_tokens = (
                    (self.count_tokens(overlap) if overlap else 0) +
                    self.count_tokens(part)
                )
            else:
                current.append(part)
                current_tokens += part_tokens

        if current:
            chunk_text = "".join(current).strip()
            chunks.append(
                TextChunk(
                    text=chunk_text,
                    index=chunk_index,
                    token_count=self.count_tokens(chunk_text),
                    metadata=dict(metadata),
                )
            )

        # If heading-splitting produced only one block, try paragraphs.
        if len(chunks) <= 1:
            return self._split_by_paragraphs(text, metadata)

        return chunks

    def _split_by_paragraphs(
        self,
        text: str,
        metadata: Dict[str, Any],
    ) -> List[TextChunk]:
        """Split on blank lines (double newline)."""

        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        chunks: List[TextChunk] = []
        current: List[str] = []
        current_tokens = 0
        chunk_index = 0

        for para in paragraphs:
            para_tokens = self.count_tokens(para)

            if para_tokens > self.chunk_size:
                # Flush whatever we have first.
                if current:
                    chunk_text = "\n\n".join(current).strip()
                    chunks.append(
                        TextChunk(
                            text=chunk_text,
                            index=chunk_index,
                            token_count=self.count_tokens(chunk_text),
                            metadata=dict(metadata),
                        )
                    )
                    chunk_index += 1
                    current = []
                    current_tokens = 0

                sentence_chunks = self._split_by_sentences(para, metadata, chunk_index)
                chunks.extend(sentence_chunks)
                chunk_index += len(sentence_chunks)
                continue

            if current_tokens + para_tokens > self.chunk_size and current:
                chunk_text = "\n\n".join(current).strip()
                chunks.append(
                    TextChunk(
                        text=chunk_text,
                        index=chunk_index,
                        token_count=self.count_tokens(chunk_text),
                        metadata=dict(metadata),
                    )
                )
                chunk_index += 1
                overlap = self._get_overlap_text(current, separator="\n\n")
                current = [overlap, para] if overlap else [para]
                # Use cached count from overlap + current part (avoid recount)
                current_tokens = (
                    (self.count_tokens(overlap) if overlap else 0) +
                    para_tokens
                )
            else:
                current.append(para)
                current_tokens += para_tokens

        if current:
            chunk_text = "\n\n".join(current).strip()
            chunks.append(
                TextChunk(
                    text=chunk_text,
                    index=chunk_index,
                    token_count=self.count_tokens(chunk_text),
                    metadata=dict(metadata),
                )
            )

        return chunks

    def _split_by_sentences(
        self,
        text: str,
        metadata: Dict[str, Any],
        start_index: int = 0,
    ) -> List[TextChunk]:
        """Split on sentence boundaries; falls back to hard character splits
        for pathologically long sentences."""

        sentences = [s.strip() for s in self._SENTENCE_RE.split(text) if s.strip()]
        chunks: List[TextChunk] = []
        current: List[str] = []
        current_tokens = 0
        chunk_index = start_index

        for sentence in sentences:
            sentence_tokens = self.count_tokens(sentence)

            # Hard fallback: single sentence larger than the chunk budget.
            if sentence_tokens > self.chunk_size:
                if current:
                    chunk_text = " ".join(current).strip()
                    chunks.append(
                        TextChunk(
                            text=chunk_text,
                            index=chunk_index,
                            token_count=self.count_tokens(chunk_text),
                            metadata=dict(metadata),
                        )
                    )
                    chunk_index += 1
                    current = []
                    current_tokens = 0

                hard_chunks = self._hard_split(sentence, metadata, chunk_index)
                chunks.extend(hard_chunks)
                chunk_index += len(hard_chunks)
                continue

            if current_tokens + sentence_tokens > self.chunk_size and current:
                chunk_text = " ".join(current).strip()
                chunks.append(
                    TextChunk(
                        text=chunk_text,
                        index=chunk_index,
                        token_count=self.count_tokens(chunk_text),
                        metadata=dict(metadata),
                    )
                )
                chunk_index += 1
                overlap = self._get_overlap_text(current, separator=" ")
                current = [overlap, sentence] if overlap else [sentence]
                # Use cached count from overlap + current part (avoid recount)
                current_tokens = (
                    (self.count_tokens(overlap) if overlap else 0) +
                    sentence_tokens
                )
            else:
                current.append(sentence)
                current_tokens += sentence_tokens

        if current:
            chunk_text = " ".join(current).strip()
            chunks.append(
                TextChunk(
                    text=chunk_text,
                    index=chunk_index,
                    token_count=self.count_tokens(chunk_text),
                    metadata=dict(metadata),
                )
            )

        return chunks

    def _hard_split(
        self,
        text: str,
        metadata: Dict[str, Any],
        start_index: int = 0,
    ) -> List[TextChunk]:
        """Last-resort: split on token boundaries for extremely long blobs."""

        tokens = self._tokenizer.encode(text)
        chunks: List[TextChunk] = []
        chunk_index = start_index
        stride = self.chunk_size - self.chunk_overlap

        for start in range(0, len(tokens), stride):
            token_slice = tokens[start : start + self.chunk_size]
            chunk_text = self._tokenizer.decode(token_slice).strip()
            if chunk_text:
                chunks.append(
                    TextChunk(
                        text=chunk_text,
                        index=chunk_index,
                        token_count=len(token_slice),
                        metadata=dict(metadata),
                    )
                )
                chunk_index += 1

        return chunks

    def _expand_segments_for_chunking(self, segments: List[ParsedSegment]) -> List[ParsedSegment]:
        """Split oversized segments while preserving location metadata."""
        expanded: List[ParsedSegment] = []
        for segment in segments:
            text = str(segment.text or "").strip()
            if not text:
                continue

            token_count = self.count_tokens(text)
            segment.token_count = token_count
            if token_count <= self.chunk_size:
                expanded.append(segment)
                continue

            expanded.extend(self._split_segment(segment))
        return expanded

    def _split_segment(self, segment: ParsedSegment) -> List[ParsedSegment]:
        """Split one oversized parsed segment into smaller traceable units."""
        pieces: List[ParsedSegment] = []
        text = segment.text
        sentences = [
            part.strip()
            for part in self._SENTENCE_RE.split(text)
            if part and part.strip()
        ]

        # Fall back to a hard token split for pathological paragraphs.
        if len(sentences) <= 1:
            return self._hard_split_segment(segment)

        cursor = 0
        current_parts: List[str] = []
        current_start = 0
        current_tokens = 0

        for sentence in sentences:
            start = text.find(sentence, cursor)
            if start < 0:
                start = cursor
            end = start + len(sentence)
            cursor = end
            sentence_tokens = self.count_tokens(sentence)

            if current_parts and current_tokens + sentence_tokens > self.chunk_size:
                merged_text = " ".join(current_parts).strip()
                pieces.append(
                    ParsedSegment(
                        text=merged_text,
                        location=slice_location(segment.location, text, current_start, start),
                        segment_type=segment.segment_type,
                        separator=segment.separator,
                        metadata=dict(segment.metadata or {}),
                        token_count=self.count_tokens(merged_text),
                    )
                )
                current_parts = [sentence]
                current_start = start
                current_tokens = sentence_tokens
            else:
                if not current_parts:
                    current_start = start
                current_parts.append(sentence)
                current_tokens += sentence_tokens

        if current_parts:
            merged_text = " ".join(current_parts).strip()
            pieces.append(
                ParsedSegment(
                    text=merged_text,
                    location=slice_location(segment.location, text, current_start, len(text)),
                    segment_type=segment.segment_type,
                    separator=segment.separator,
                    metadata=dict(segment.metadata or {}),
                    token_count=self.count_tokens(merged_text),
                )
            )

        return [piece for piece in pieces if piece.text.strip()] or self._hard_split_segment(segment)

    def _hard_split_segment(self, segment: ParsedSegment) -> List[ParsedSegment]:
        """Token-based fallback split that keeps char and line offsets honest."""
        tokens = self._tokenizer.encode(segment.text)
        stride = self.chunk_size - self.chunk_overlap
        pieces: List[ParsedSegment] = []
        source_text = segment.text
        text_cursor = 0

        for start in range(0, len(tokens), stride):
            token_slice = tokens[start : start + self.chunk_size]
            piece_text = self._tokenizer.decode(token_slice).strip()
            if not piece_text:
                continue
            found_at = source_text.find(piece_text, text_cursor)
            if found_at < 0:
                found_at = text_cursor
            text_cursor = found_at + len(piece_text)
            pieces.append(
                ParsedSegment(
                    text=piece_text,
                    location=slice_location(segment.location, source_text, found_at, text_cursor),
                    segment_type=segment.segment_type,
                    separator=segment.separator,
                    metadata=dict(segment.metadata or {}),
                    token_count=len(token_slice),
                )
            )
        return pieces

    # ------------------------------------------------------------------
    # Post-processing helpers
    # ------------------------------------------------------------------

    def _get_overlap_text(
        self,
        parts: List[str],
        separator: str = "\n\n",
    ) -> Optional[str]:
        """Return the tail of *parts* that fits within :attr:`chunk_overlap` tokens."""

        overlap_tokens = 0
        overlap_parts: List[str] = []

        for part in reversed(parts):
            part_tokens = self.count_tokens(part)
            if overlap_tokens + part_tokens > self.chunk_overlap:
                break
            overlap_parts.insert(0, part)
            overlap_tokens += part_tokens

        return separator.join(overlap_parts) if overlap_parts else None

    def _segment_overlap(self, segments: List[ParsedSegment]) -> List[ParsedSegment]:
        """Return trailing segments that fit within the overlap token budget."""
        overlap_tokens = 0
        overlap_segments: List[ParsedSegment] = []

        for segment in reversed(segments):
            segment_tokens = segment.token_count or self.count_tokens(segment.text)
            if overlap_tokens + segment_tokens > self.chunk_overlap:
                break
            overlap_segments.insert(0, segment)
            overlap_tokens += segment_tokens

        return overlap_segments

    def _build_traceable_chunk(
        self,
        segments: List[ParsedSegment],
        index: int,
        base_metadata: Dict[str, Any],
    ) -> TextChunk:
        """Build a chunk from parsed segments with merged citation metadata."""
        text_parts: List[str] = []
        for segment_index, segment in enumerate(segments):
            text_parts.append(segment.text)
            if segment_index < len(segments) - 1:
                text_parts.append(segment.separator or "\n\n")
        text = "".join(text_parts).strip()
        token_count = self.count_tokens(text)
        merged_metadata = merge_segment_metadata(segments, base_metadata=base_metadata)
        merged_metadata = enrich_citation_metadata(
            merged_metadata,
            document_title=str(base_metadata.get("source_file_name") or base_metadata.get("document_title") or ""),
            source_type=str(base_metadata.get("source_type") or ""),
        )
        return TextChunk(
            text=text,
            index=index,
            token_count=token_count,
            metadata=merged_metadata,
        )

    def _merge_small_chunks(self, chunks: List[TextChunk]) -> List[TextChunk]:
        """Merge adjacent chunks that are individually under-sized.

        BUG FIX: the original implementation discarded the metadata of the
        second chunk when merging.  We now keep the *first* chunk's metadata
        (since it carries the document-level keys) while logging a warning
        if the two chunks had different metadata.
        """

        if len(chunks) <= 1:
            return chunks

        merged: List[TextChunk] = []
        current: Optional[TextChunk] = None

        for chunk in chunks:
            if current is None:
                current = chunk
                continue

            combined_tokens = current.token_count + chunk.token_count
            if combined_tokens <= self.chunk_size:
                if current.metadata != chunk.metadata:
                    logger.debug(
                        "Merging chunks with differing metadata; keeping first chunk's metadata. "
                        "chunk_indices=(%d, %d)",
                        current.index,
                        chunk.index,
                    )
                current = TextChunk(
                    text=current.text + "\n\n" + chunk.text,
                    index=current.index,
                    token_count=current.token_count + 2 + chunk.token_count,  # 2 tokens for "\n\n"
                    metadata=current.metadata,
                )
            else:
                merged.append(current)
                current = chunk

        if current is not None:
            merged.append(current)

        # Re-sequence indices.
        for i, chunk in enumerate(merged):
            chunk.index = i

        return merged

    def _add_context(self, chunks: List[TextChunk]) -> List[TextChunk]:
        """Attach a small window of neighbouring text to each chunk."""

        for i, chunk in enumerate(chunks):
            if i > 0:
                prev_text = chunks[i - 1].text
                chunk.context_before = (
                    prev_text[-self.context_window :]
                    if len(prev_text) > self.context_window
                    else prev_text
                )
            if i < len(chunks) - 1:
                next_text = chunks[i + 1].text
                chunk.context_after = (
                    next_text[: self.context_window]
                    if len(next_text) > self.context_window
                    else next_text
                )

        return chunks


# ---------------------------------------------------------------------------
# Module-level singleton (backward-compatible with existing imports)
# ---------------------------------------------------------------------------
chunker = Chunker()


def chunk_text(
    text: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> List[TextChunk]:
    """Module-level convenience function for text chunking.
    
    Delegates to the singleton Chunker instance.
    
    Args:
        text: Raw input text to chunk
        metadata: Optional metadata to attach to chunks
        
    Returns:
        List of TextChunk objects
    """
    return chunker.chunk_text(text, metadata)


def chunk_parsed_document(
    parsed_document: ParsedDocument,
    metadata: Optional[Dict[str, Any]] = None,
) -> List[TextChunk]:
    """Module-level convenience wrapper for traceable parsed-document chunking."""
    return chunker.chunk_parsed_document(parsed_document, metadata)
