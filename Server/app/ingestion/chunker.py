"""Smart text chunking with context preservation."""
from __future__ import annotations

import re
import logging
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

import tiktoken

from app.config import settings

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

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def count_tokens(self, text: str) -> int:
        """Return the number of tokens in *text*."""
        return len(self._tokenizer.encode(text))

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
        cleaned = text.strip()
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
                current_tokens = self.count_tokens("".join(current))
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
                current_tokens = self.count_tokens("\n\n".join(current))
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
                current_tokens = self.count_tokens(" ".join(current))
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
                    token_count=self.count_tokens(current.text + "\n\n" + chunk.text),
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