"""Chunk-level evidence and highlight extraction for semantic search.

This module is deliberately model-agnostic. It can use embeddings when the
caller provides them, but it still produces honest lexical/evidence highlights
when embeddings are unavailable.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence
from uuid import NAMESPACE_URL, uuid5

from app.services.retrieval_relevance import analyze_chunk_relevance, analyze_query_intent
from app.services.vector_db import cosine_similarity


@dataclass
class SearchTextChunk:
    """Searchable section of a note or document."""

    chunk_id: str
    note_id: str
    text: str
    start_offset: int
    end_offset: int
    chunk_index: int
    heading: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class HighlightSpan:
    """A focused source span that explains a match."""

    text: str
    start_offset: int
    end_offset: int
    score: float
    matched_terms: List[str] = field(default_factory=list)
    heading: Optional[str] = None
    confidence: str = "low"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text,
            "start_offset": self.start_offset,
            "end_offset": self.end_offset,
            "score": round(max(0.0, min(self.score, 1.0)), 4),
            "matched_terms": self.matched_terms,
            "heading": self.heading,
            "confidence": self.confidence,
        }


@dataclass
class MatchedChunkEvidence:
    """A matched chunk plus explainable score components."""

    chunk: SearchTextChunk
    score: float
    semantic_score: float
    lexical_score: float
    evidence_score: float
    domain_alignment: float
    off_topic: bool
    highlights: List[HighlightSpan]
    confidence: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "chunk_id": self.chunk.chunk_id,
            "note_id": self.chunk.note_id,
            "chunk_index": self.chunk.chunk_index,
            "text": self.chunk.text,
            "start_offset": self.chunk.start_offset,
            "end_offset": self.chunk.end_offset,
            "heading": self.chunk.heading,
            "score": round(max(0.0, min(self.score, 1.0)), 4),
            "semantic_score": round(max(0.0, min(self.semantic_score, 1.0)), 4),
            "lexical_score": round(max(0.0, min(self.lexical_score, 1.0)), 4),
            "evidence_score": round(max(0.0, min(self.evidence_score, 1.0)), 4),
            "domain_alignment": round(max(0.0, min(self.domain_alignment, 1.0)), 4),
            "off_topic": self.off_topic,
            "confidence": self.confidence,
            "highlights": [highlight.to_dict() for highlight in self.highlights],
            "metadata": dict(self.chunk.metadata or {}),
        }


class SearchHighlightExtractor:
    """Create retrieval-friendly note chunks and source-grounded highlights."""

    _HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
    _SENTENCE_RE = re.compile(r"[^.!?\n]+(?:[.!?]+|$)", re.MULTILINE)
    _WORD_RE = re.compile(r"[a-zA-Z0-9_:-]+")
    MIN_HIGHLIGHT_SCORE = 0.12

    def __init__(
        self,
        *,
        target_chunk_chars: int = 1300,
        min_chunk_chars: int = 220,
        max_chunk_chars: int = 1800,
        max_highlights_per_chunk: int = 2,
    ) -> None:
        self.target_chunk_chars = target_chunk_chars
        self.min_chunk_chars = min_chunk_chars
        self.max_chunk_chars = max_chunk_chars
        self.max_highlights_per_chunk = max_highlights_per_chunk

    def chunk_note(
        self,
        *,
        note_id: str,
        title: str,
        content: str,
        tags: Optional[Sequence[str]] = None,
    ) -> List[SearchTextChunk]:
        """Split a note into sections with stable IDs and content offsets."""
        cleaned = content or ""
        if not cleaned.strip():
            return []

        sections = self._split_sections(cleaned)
        chunks: List[SearchTextChunk] = []
        for heading, start, end in sections:
            section_text = cleaned[start:end].strip()
            if not section_text:
                continue
            chunks.extend(
                self._split_section(
                    note_id=note_id,
                    title=title,
                    heading=heading,
                    section_text=section_text,
                    section_start=start + self._leading_whitespace_len(cleaned[start:end]),
                    tags=tags,
                    start_index=len(chunks),
                )
            )

        return chunks or [
            self._build_chunk(
                note_id=note_id,
                title=title,
                heading=None,
                text=cleaned.strip(),
                start_offset=self._leading_whitespace_len(cleaned),
                chunk_index=0,
                tags=tags,
            )
        ]

    def score_chunks(
        self,
        *,
        query: str,
        chunks: Sequence[SearchTextChunk],
        query_embedding: Optional[List[float]] = None,
        chunk_embeddings: Optional[Dict[str, List[float]]] = None,
        note_semantic_score: float = 0.0,
        note_text_score: float = 0.0,
        source_type: str = "note",
        max_chunks: int = 3,
    ) -> List[MatchedChunkEvidence]:
        """Score chunks and return the strongest explainable matches."""
        if not chunks:
            return []

        intent = analyze_query_intent(query)
        scored: List[MatchedChunkEvidence] = []
        for chunk in chunks:
            relevance = analyze_chunk_relevance(
                query,
                chunk.text,
                title=str(chunk.metadata.get("title") or ""),
                tags=chunk.metadata.get("tags") or [],
                metadata=chunk.metadata,
                source_type=source_type,
            )
            semantic = self._chunk_semantic_score(
                chunk.chunk_id,
                query_embedding=query_embedding,
                chunk_embeddings=chunk_embeddings,
                fallback=note_semantic_score,
            )
            lexical = max(relevance.lexical_overlap, min(note_text_score, 1.0) * 0.35)
            exact_bonus = 0.08 if intent.normalized_query.lower() in chunk.text.lower() else 0.0
            heading_bonus = self._heading_bonus(query, chunk.heading)
            mismatch_penalty = 0.28 if relevance.off_topic else 0.0

            if query_embedding and chunk_embeddings and chunk.chunk_id in chunk_embeddings:
                score = (
                    semantic * 0.48
                    + min(note_semantic_score, 1.0) * 0.18
                    + lexical * 0.12
                    + relevance.evidence_score * 0.18
                    + heading_bonus
                    + exact_bonus
                    - mismatch_penalty
                )
            else:
                score = (
                    min(note_semantic_score, 1.0) * 0.42
                    + lexical * 0.20
                    + relevance.evidence_score * 0.28
                    + heading_bonus
                    + exact_bonus
                    - mismatch_penalty
                )

            score = max(0.0, min(score, 1.0))
            confidence = self._confidence(score, relevance.evidence_score, semantic, relevance.off_topic)
            highlights = self.extract_highlights(
                query=query,
                chunk=chunk,
                source_type=source_type,
                max_highlights=self.max_highlights_per_chunk,
            )

            scored.append(
                MatchedChunkEvidence(
                    chunk=chunk,
                    score=score,
                    semantic_score=semantic,
                    lexical_score=lexical,
                    evidence_score=relevance.evidence_score,
                    domain_alignment=relevance.domain_alignment,
                    off_topic=relevance.off_topic,
                    highlights=highlights,
                    confidence=confidence,
                )
            )

        scored.sort(key=lambda item: item.score, reverse=True)
        kept = [item for item in scored if self._passes_quality_floor(item, intent.is_domain_specific)]
        return kept[:max_chunks]

    def extract_highlights(
        self,
        *,
        query: str,
        chunk: SearchTextChunk,
        source_type: str = "note",
        max_highlights: int = 2,
    ) -> List[HighlightSpan]:
        """Extract sentence-level highlights from the actual matched chunk."""
        sentences = self._sentence_spans(chunk.text)
        if not sentences:
            return [
                HighlightSpan(
                    text=chunk.text[:280],
                    start_offset=chunk.start_offset,
                    end_offset=min(chunk.end_offset, chunk.start_offset + 280),
                    score=0.0,
                    matched_terms=[],
                    heading=chunk.heading,
                    confidence="low",
                )
            ]

        query_terms = self._query_terms(query)
        scored: List[HighlightSpan] = []
        for local_start, local_end, sentence in sentences:
            trimmed = sentence.strip()
            if not trimmed:
                continue
            relevance = analyze_chunk_relevance(
                query,
                trimmed,
                title=str(chunk.metadata.get("title") or ""),
                tags=chunk.metadata.get("tags") or [],
                metadata=chunk.metadata,
                source_type=source_type,
            )
            matched_terms = self._matched_terms(query_terms, trimmed)
            density = min(len(matched_terms) / max(len(query_terms), 1), 1.0)
            quote_bonus = 0.10 if query.strip().lower() and query.strip().lower() in trimmed.lower() else 0.0
            length_penalty = 0.0 if len(trimmed) <= 360 else min((len(trimmed) - 360) / 900, 0.18)
            score = max(
                0.0,
                min(
                    relevance.evidence_score * 0.55
                    + relevance.lexical_overlap * 0.25
                    + density * 0.15
                    + quote_bonus
                    - length_penalty,
                    1.0,
                ),
            )
            start = chunk.start_offset + local_start + self._leading_whitespace_len(sentence)
            end = min(chunk.start_offset + local_end, start + len(trimmed))
            scored.append(
                HighlightSpan(
                    text=self._trim_highlight_text(trimmed),
                    start_offset=start,
                    end_offset=end,
                    score=score,
                    matched_terms=matched_terms,
                    heading=chunk.heading,
                    confidence=self._confidence(score, relevance.evidence_score, score, relevance.off_topic),
                )
            )

        scored.sort(key=lambda item: item.score, reverse=True)
        deduped: List[HighlightSpan] = []
        used_ranges: List[range] = []
        for span in scored:
            if span.score < self.MIN_HIGHLIGHT_SCORE and not span.matched_terms:
                continue
            candidate_range = range(span.start_offset, span.end_offset)
            if any(self._ranges_overlap(candidate_range, existing) for existing in used_ranges):
                continue
            deduped.append(span)
            used_ranges.append(candidate_range)
            if len(deduped) >= max_highlights:
                break

        if deduped:
            return sorted(deduped, key=lambda item: item.start_offset)

        return []

    def chunk_id_for_note_span(self, note_id: str, start_offset: int, end_offset: int) -> str:
        raw = f"{note_id}:{start_offset}:{end_offset}"
        return str(uuid5(NAMESPACE_URL, raw))

    def _split_sections(self, content: str) -> List[tuple[Optional[str], int, int]]:
        matches = list(self._HEADING_RE.finditer(content))
        if not matches:
            return [(None, 0, len(content))]

        sections: List[tuple[Optional[str], int, int]] = []
        intro = content[: matches[0].start()].strip()
        if intro:
            sections.append((None, 0, matches[0].start()))

        for index, match in enumerate(matches):
            heading = match.group(2).strip()
            start = match.start()
            end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
            sections.append((heading, start, end))
        return sections

    def _split_section(
        self,
        *,
        note_id: str,
        title: str,
        heading: Optional[str],
        section_text: str,
        section_start: int,
        tags: Optional[Sequence[str]],
        start_index: int,
    ) -> List[SearchTextChunk]:
        paragraphs = list(self._paragraph_spans(section_text))
        if not paragraphs:
            return []

        chunks: List[SearchTextChunk] = []
        current_parts: List[str] = []
        current_start: Optional[int] = None
        current_end = 0

        for local_start, local_end, paragraph in paragraphs:
            paragraph_text = paragraph.strip()
            if not paragraph_text:
                continue
            if current_start is None:
                current_start = local_start + self._leading_whitespace_len(paragraph)

            proposed = "\n\n".join([*current_parts, paragraph_text]).strip()
            if current_parts and len(proposed) > self.target_chunk_chars:
                chunk_text = "\n\n".join(current_parts).strip()
                chunks.append(
                    self._build_chunk(
                        note_id=note_id,
                        title=title,
                        heading=heading,
                        text=chunk_text,
                        start_offset=section_start + (current_start or 0),
                        chunk_index=start_index + len(chunks),
                        tags=tags,
                    )
                )
                current_parts = [paragraph_text]
                current_start = local_start + self._leading_whitespace_len(paragraph)
                current_end = local_end
                continue

            if len(paragraph_text) > self.max_chunk_chars:
                if current_parts:
                    chunk_text = "\n\n".join(current_parts).strip()
                    chunks.append(
                        self._build_chunk(
                            note_id=note_id,
                            title=title,
                            heading=heading,
                            text=chunk_text,
                            start_offset=section_start + (current_start or 0),
                            chunk_index=start_index + len(chunks),
                            tags=tags,
                        )
                    )
                    current_parts = []
                    current_start = None
                chunks.extend(
                    self._split_long_paragraph(
                        note_id=note_id,
                        title=title,
                        heading=heading,
                        paragraph=paragraph_text,
                        paragraph_start=section_start + local_start + self._leading_whitespace_len(paragraph),
                        tags=tags,
                        start_index=start_index + len(chunks),
                    )
                )
                current_end = local_end
                continue

            current_parts.append(paragraph_text)
            current_end = local_end

        if current_parts:
            chunk_text = "\n\n".join(current_parts).strip()
            chunks.append(
                self._build_chunk(
                    note_id=note_id,
                    title=title,
                    heading=heading,
                    text=chunk_text,
                    start_offset=section_start + (current_start or 0),
                    chunk_index=start_index + len(chunks),
                    tags=tags,
                )
            )

        return self._merge_tiny_chunks(chunks)

    def _split_long_paragraph(
        self,
        *,
        note_id: str,
        title: str,
        heading: Optional[str],
        paragraph: str,
        paragraph_start: int,
        tags: Optional[Sequence[str]],
        start_index: int,
    ) -> List[SearchTextChunk]:
        chunks: List[SearchTextChunk] = []
        current_parts: List[str] = []
        current_start: Optional[int] = None
        current_end = 0
        for local_start, local_end, sentence in self._sentence_spans(paragraph):
            text = sentence.strip()
            if not text:
                continue
            if current_start is None:
                current_start = local_start + self._leading_whitespace_len(sentence)
            proposed = " ".join([*current_parts, text]).strip()
            if current_parts and len(proposed) > self.target_chunk_chars:
                chunk_text = " ".join(current_parts).strip()
                chunks.append(
                    self._build_chunk(
                        note_id=note_id,
                        title=title,
                        heading=heading,
                        text=chunk_text,
                        start_offset=paragraph_start + (current_start or 0),
                        chunk_index=start_index + len(chunks),
                        tags=tags,
                    )
                )
                current_parts = [text]
                current_start = local_start + self._leading_whitespace_len(sentence)
            else:
                current_parts.append(text)
            current_end = local_end

        if current_parts:
            chunks.append(
                self._build_chunk(
                    note_id=note_id,
                    title=title,
                    heading=heading,
                    text=" ".join(current_parts).strip(),
                    start_offset=paragraph_start + (current_start or 0),
                    chunk_index=start_index + len(chunks),
                    tags=tags,
                )
            )
        return chunks

    def _build_chunk(
        self,
        *,
        note_id: str,
        title: str,
        heading: Optional[str],
        text: str,
        start_offset: int,
        chunk_index: int,
        tags: Optional[Sequence[str]],
    ) -> SearchTextChunk:
        clean = text.strip()
        end_offset = start_offset + len(clean)
        return SearchTextChunk(
            chunk_id=self.chunk_id_for_note_span(note_id, start_offset, end_offset),
            note_id=note_id,
            text=clean,
            start_offset=start_offset,
            end_offset=end_offset,
            chunk_index=chunk_index,
            heading=heading,
            metadata={
                "title": title,
                "heading": heading,
                "tags": list(tags or []),
                "source_offset_start": start_offset,
                "source_offset_end": end_offset,
            },
        )

    def _merge_tiny_chunks(self, chunks: List[SearchTextChunk]) -> List[SearchTextChunk]:
        if len(chunks) <= 1:
            return chunks

        merged: List[SearchTextChunk] = []
        pending: Optional[SearchTextChunk] = None
        for chunk in chunks:
            if pending is None:
                pending = chunk
                continue
            if len(pending.text) < self.min_chunk_chars and len(pending.text) + len(chunk.text) <= self.max_chunk_chars:
                pending = SearchTextChunk(
                    chunk_id=self.chunk_id_for_note_span(pending.note_id, pending.start_offset, chunk.end_offset),
                    note_id=pending.note_id,
                    text=f"{pending.text}\n\n{chunk.text}",
                    start_offset=pending.start_offset,
                    end_offset=chunk.end_offset,
                    chunk_index=pending.chunk_index,
                    heading=pending.heading or chunk.heading,
                    metadata={**pending.metadata, "source_offset_end": chunk.end_offset},
                )
                continue
            merged.append(pending)
            pending = chunk

        if pending is not None:
            merged.append(pending)

        for index, chunk in enumerate(merged):
            chunk.chunk_index = index
        return merged

    def _paragraph_spans(self, text: str) -> Iterable[tuple[int, int, str]]:
        pattern = re.compile(r"\S(?:.*?)(?=\n\s*\n|\Z)", re.DOTALL)
        for match in pattern.finditer(text):
            yield match.start(), match.end(), match.group(0)

    def _sentence_spans(self, text: str) -> List[tuple[int, int, str]]:
        spans: List[tuple[int, int, str]] = []
        for match in self._SENTENCE_RE.finditer(text or ""):
            sentence = match.group(0)
            if sentence.strip():
                spans.append((match.start(), match.end(), sentence))
        return spans

    def _query_terms(self, query: str) -> List[str]:
        intent = analyze_query_intent(query)
        terms = list(intent.content_terms or intent.token_set)
        if intent.quoted_terms:
            terms.extend(intent.quoted_terms)
        return sorted({term.lower() for term in terms if len(term) > 1}, key=len, reverse=True)

    def _matched_terms(self, query_terms: Sequence[str], text: str) -> List[str]:
        lowered = (text or "").lower()
        matched = []
        for term in query_terms:
            if term and term.lower() in lowered:
                matched.append(term)
        return matched[:8]

    def _chunk_semantic_score(
        self,
        chunk_id: str,
        *,
        query_embedding: Optional[List[float]],
        chunk_embeddings: Optional[Dict[str, List[float]]],
        fallback: float,
    ) -> float:
        if query_embedding and chunk_embeddings:
            vector = chunk_embeddings.get(chunk_id)
            if vector and len(vector) == len(query_embedding):
                return max(0.0, min((cosine_similarity(query_embedding, vector) + 1.0) / 2.0, 1.0))
        return max(0.0, min(fallback, 1.0))

    def _heading_bonus(self, query: str, heading: Optional[str]) -> float:
        if not heading:
            return 0.0
        terms = self._query_terms(query)
        if not terms:
            return 0.0
        heading_lower = heading.lower()
        hits = sum(1 for term in terms if term in heading_lower)
        return min(hits / max(len(terms), 1), 1.0) * 0.05

    def _confidence(self, score: float, evidence: float, semantic: float, off_topic: bool) -> str:
        if off_topic:
            return "low"
        blended = score * 0.45 + evidence * 0.30 + semantic * 0.25
        if blended >= 0.72:
            return "high"
        if blended >= 0.46:
            return "medium"
        return "low"

    def _passes_quality_floor(self, item: MatchedChunkEvidence, domain_specific: bool) -> bool:
        if item.off_topic:
            return False
        if not item.highlights:
            return False
        if domain_specific:
            return item.score >= 0.34 and (item.evidence_score >= 0.14 or item.semantic_score >= 0.66)
        return item.score >= 0.28 or item.semantic_score >= 0.62 or item.evidence_score >= 0.24

    def _trim_highlight_text(self, text: str, max_chars: int = 360) -> str:
        clean = " ".join((text or "").split())
        if len(clean) <= max_chars:
            return clean
        cutoff = clean.rfind(" ", 0, max_chars - 1)
        if cutoff < max_chars * 0.55:
            cutoff = max_chars
        return clean[:cutoff].rstrip() + "..."

    @staticmethod
    def _leading_whitespace_len(text: str) -> int:
        return len(text) - len(text.lstrip())

    @staticmethod
    def _ranges_overlap(left: range, right: range) -> bool:
        return left.start < right.stop and right.start < left.stop


def stable_content_fingerprint(values: Iterable[str]) -> str:
    digest = hashlib.sha256()
    for value in values:
        digest.update(str(value or "").encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()
