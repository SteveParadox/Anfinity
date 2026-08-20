"""Strict note-only retrieval and citation helpers for Ask Your Past Self."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Note, NoteCollaborator, User as DBUser
from app.services.retrieval_relevance import analyze_chunk_relevance, analyze_query_intent
from app.services.search_highlights import SearchHighlightExtractor, SearchTextChunk

logger = logging.getLogger(__name__)

REFUSAL_TEXT = "I couldn't find enough in your notes to answer that."

MIN_SUPPORTED_SCORE = 0.46
MIN_PARTIAL_SCORE = 0.38
MAX_NOTES_SCANNED = 500

_CITATION_ID_RE = re.compile(r"\[(S\d+)\]")
_PERSONAL_ANCHOR_RE = re.compile(
    r"\b(i|me|my|mine|we|our|ours|you wrote|your notes|did i|do i|have i|what did i|what have i)\b",
    re.IGNORECASE,
)
_GENERAL_KNOWLEDGE_RE = re.compile(
    r"^\s*(what|who|where|when|why|how)\s+(is|are|was|were|does|do|did|can|should|would|will)\b|"
    r"^\s*(explain|define|describe)\b",
    re.IGNORECASE,
)
_DIRECT_ANSWER_RE = re.compile(
    r"\b(is|are|was|were|means|mean|refers to|defined as|described as|because|when|where|how)\b|:",
    re.IGNORECASE,
)


@dataclass(slots=True)
class StrictRAGSource:
    """A source card and prompt citation backed by a live note chunk."""

    source_id: str
    note_id: str
    title: str
    excerpt: str
    created_at: str
    similarity: float
    similarity_percent: int
    citation: str
    chunk_id: str
    chunk_index: int
    support_level: str
    score_components: Dict[str, float] = field(default_factory=dict)

    def to_api_dict(self) -> Dict[str, Any]:
        return {
            "sourceId": self.source_id,
            "noteId": self.note_id,
            "title": self.title,
            "excerpt": self.excerpt,
            "createdAt": self.created_at,
            "similarity": self.similarity,
            "similarityPercent": self.similarity_percent,
            "citation": self.citation,
            "chunkId": self.chunk_id,
            "chunkIndex": self.chunk_index,
            "supportLevel": self.support_level,
        }


@dataclass(slots=True)
class StrictRAGResult:
    """Retrieved note evidence plus the server-side answerability decision."""

    sources: List[StrictRAGSource]
    answer_status: str
    confidence: str
    refusal_reason: Optional[str] = None
    diagnostics: Dict[str, Any] = field(default_factory=dict)

    @property
    def can_answer(self) -> bool:
        return self.answer_status in {"supported", "partial"} and bool(self.sources)


def calibrate_similarity_percent(score: float) -> int:
    """Convert an internal evidence score into a coarse UI percentage."""

    bounded = max(0.0, min(float(score or 0.0), 1.0))
    if bounded <= 0:
        return 0
    coarse = int(round((bounded * 100) / 5.0) * 5)
    return max(5, min(coarse, 95))


def _isoformat(value: Optional[datetime]) -> str:
    if value is None:
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _clean_title(title: Optional[str]) -> str:
    clean = " ".join(str(title or "").split())
    return clean or "Untitled Note"


def _coerce_tags(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return []


def _best_excerpt(extractor: SearchHighlightExtractor, query: str, chunk: SearchTextChunk) -> str:
    highlights = extractor.extract_highlights(query=query, chunk=chunk, source_type="note", max_highlights=1)
    if highlights:
        return highlights[0].text
    return " ".join((chunk.text or "").split())[:360]


def _looks_like_general_knowledge_query(query: str) -> bool:
    """Detect broad questions that need explicit note evidence before answering."""
    normalized = " ".join((query or "").split())
    if not normalized:
        return False
    if _PERSONAL_ANCHOR_RE.search(normalized):
        return False
    return bool(_GENERAL_KNOWLEDGE_RE.search(normalized))


def _direct_answer_support(query: str, text: str) -> float:
    """Score whether a chunk directly answers a broad/general question."""
    if not _looks_like_general_knowledge_query(query):
        return 1.0

    relevance = analyze_chunk_relevance(query, text, source_type="note")
    if not _DIRECT_ANSWER_RE.search(text or ""):
        return 0.0
    if relevance.exact_match_ratio >= 0.66 or relevance.lexical_overlap >= 0.66:
        return 1.0
    if relevance.exact_match_ratio >= 0.34 and relevance.lexical_overlap >= 0.34:
        return 0.6
    return 0.0


def _score_chunk(query: str, chunk: SearchTextChunk) -> tuple[float, Dict[str, float], bool]:
    relevance = analyze_chunk_relevance(
        query,
        chunk.text,
        title=str(chunk.metadata.get("title") or ""),
        tags=chunk.metadata.get("tags") or [],
        metadata=chunk.metadata,
        source_type="note",
    )
    intent = analyze_query_intent(query)
    lowered_query = intent.normalized_query.lower()
    lowered_chunk = " ".join(
        [
            str(chunk.metadata.get("title") or ""),
            chunk.text or "",
            " ".join(str(tag) for tag in (chunk.metadata.get("tags") or [])),
        ]
    ).lower()
    exact_phrase_bonus = 0.06 if lowered_query and lowered_query in lowered_chunk else 0.0
    title_hits = sum(
        1
        for term in intent.content_terms
        if term and term in str(chunk.metadata.get("title") or "").lower()
    )
    title_bonus = min(title_hits / max(len(intent.content_terms), 1), 1.0) * 0.08
    score = (
        relevance.evidence_score * 0.52
        + relevance.lexical_overlap * 0.28
        + relevance.exact_match_ratio * 0.10
        + relevance.domain_alignment * 0.04
        + title_bonus
        + exact_phrase_bonus
    )
    answerability = _direct_answer_support(query, chunk.text)
    if relevance.off_topic:
        score *= 0.2
    if answerability <= 0.0:
        score *= 0.35
    elif answerability < 1.0:
        score *= 0.70
    score = max(0.0, min(score, 1.0))
    components = {
        "score": round(score, 4),
        "evidence": round(relevance.evidence_score, 4),
        "lexical": round(relevance.lexical_overlap, 4),
        "exact": round(relevance.exact_match_ratio, 4),
        "domain": round(relevance.domain_alignment, 4),
        "answerability": round(answerability, 4),
    }
    return score, components, relevance.off_topic


def _dedupe_sources_by_note(candidates: Iterable[StrictRAGSource], limit: int) -> List[StrictRAGSource]:
    best_by_note: Dict[str, StrictRAGSource] = {}
    for source in candidates:
        current = best_by_note.get(source.note_id)
        if current is None or source.similarity > current.similarity:
            best_by_note[source.note_id] = source

    ranked = sorted(best_by_note.values(), key=lambda item: item.similarity, reverse=True)[:limit]
    renumbered: List[StrictRAGSource] = []
    for index, source in enumerate(ranked, start=1):
        source_id = f"S{index}"
        citation = f"{source.title} | {source.created_at[:10] or 'Unknown Date'} | {source.similarity_percent}%"
        renumbered.append(
            StrictRAGSource(
                source_id=source_id,
                note_id=source.note_id,
                title=source.title,
                excerpt=source.excerpt,
                created_at=source.created_at,
                similarity=source.similarity,
                similarity_percent=source.similarity_percent,
                citation=citation,
                chunk_id=source.chunk_id,
                chunk_index=source.chunk_index,
                support_level=source.support_level,
                score_components=source.score_components,
            )
        )
    return renumbered


def evaluate_sources(
    sources: List[StrictRAGSource],
    *,
    query: str = "",
    min_score: float = MIN_SUPPORTED_SCORE,
) -> StrictRAGResult:
    """Gate model generation before any LLM call."""

    if not sources:
        return StrictRAGResult(
            sources=[],
            answer_status="refusal",
            confidence="not_found",
            refusal_reason="no_note_evidence",
            diagnostics={"top_score": 0.0, "source_count": 0},
        )

    top_score = sources[0].similarity
    medium_sources = [source for source in sources if source.similarity >= MIN_PARTIAL_SCORE]
    if _looks_like_general_knowledge_query(query):
        directly_supported = [
            source
            for source in sources
            if float(source.score_components.get("answerability", 0.0) or 0.0) >= 0.6
        ]
        if not directly_supported:
            return StrictRAGResult(
                sources=[],
                answer_status="refusal",
                confidence="not_found",
                refusal_reason="general_knowledge_not_supported_by_notes",
                diagnostics={"top_score": round(top_score, 4), "source_count": len(sources)},
            )

    if top_score >= min_score and len(medium_sources) >= 1:
        confidence = "high" if top_score >= 0.70 else "medium"
        return StrictRAGResult(
            sources=sources,
            answer_status="supported",
            confidence=confidence,
            diagnostics={"top_score": round(top_score, 4), "source_count": len(sources)},
        )

    if top_score >= MIN_PARTIAL_SCORE and len(medium_sources) >= 2:
        return StrictRAGResult(
            sources=sources,
            answer_status="partial",
            confidence="low",
            refusal_reason="limited_note_evidence",
            diagnostics={"top_score": round(top_score, 4), "source_count": len(sources)},
        )

    return StrictRAGResult(
        sources=[],
        answer_status="refusal",
        confidence="not_found",
        refusal_reason="weak_or_indirect_note_evidence",
        diagnostics={"top_score": round(top_score, 4), "source_count": len(sources)},
    )


async def retrieve_strict_note_context(
    *,
    query: str,
    workspace_id: UUID,
    user: DBUser,
    db: AsyncSession,
    limit: int = 6,
    min_score: float = MIN_SUPPORTED_SCORE,
) -> StrictRAGResult:
    """Retrieve answerable evidence from live note rows in one workspace."""
    started_at = time.perf_counter()
    logger.info(
        "ask_past_self_retrieval_started",
        extra={
            "workspace_id": str(workspace_id),
            "user_id": str(getattr(user, "id", "")),
            "query_length": len(query or ""),
            "requested_limit": limit,
            "min_score": min_score,
            "max_notes_scanned": MAX_NOTES_SCANNED,
            "is_superuser": bool(getattr(user, "is_superuser", False)),
        },
    )

    query_stmt = (
        select(Note)
        .outerjoin(
            NoteCollaborator,
            and_(
                NoteCollaborator.note_id == Note.id,
                NoteCollaborator.user_id == user.id,
            ),
        )
        .where(Note.workspace_id == workspace_id)
        .order_by(Note.updated_at.desc().nullslast(), Note.created_at.desc().nullslast())
        .distinct()
        .limit(MAX_NOTES_SCANNED)
    )
    if not getattr(user, "is_superuser", False):
        query_stmt = query_stmt.where(
            or_(
                Note.user_id == user.id,
                NoteCollaborator.user_id == user.id,
            )
        )

    result = await db.execute(query_stmt)
    notes = result.scalars().all()
    logger.info(
        "ask_past_self_notes_loaded",
        extra={
            "workspace_id": str(workspace_id),
            "user_id": str(getattr(user, "id", "")),
            "note_count": len(notes),
        },
    )
    extractor = SearchHighlightExtractor()
    candidates: List[StrictRAGSource] = []
    notes_with_content = 0
    chunks_scanned = 0
    chunks_rejected = 0

    for note in notes:
        content = str(getattr(note, "content", "") or "")
        if not content.strip():
            continue
        notes_with_content += 1

        title = _clean_title(getattr(note, "title", None))
        tags = _coerce_tags(getattr(note, "tags", None))
        chunks = extractor.chunk_note(
            note_id=str(note.id),
            title=title,
            content=content,
            tags=tags,
        )
        chunks_scanned += len(chunks)
        for chunk in chunks:
            score, components, off_topic = _score_chunk(query, chunk)
            if off_topic or score < MIN_PARTIAL_SCORE:
                chunks_rejected += 1
                continue
            similarity_percent = calibrate_similarity_percent(score)
            created_at = _isoformat(getattr(note, "created_at", None) or getattr(note, "updated_at", None))
            candidates.append(
                StrictRAGSource(
                    source_id="",
                    note_id=str(note.id),
                    title=title,
                    excerpt=_best_excerpt(extractor, query, chunk),
                    created_at=created_at,
                    similarity=round(score, 4),
                    similarity_percent=similarity_percent,
                    citation="",
                    chunk_id=chunk.chunk_id,
                    chunk_index=chunk.chunk_index,
                    support_level="supported" if score >= min_score else "partial",
                    score_components=components,
                )
            )

    logger.info(
        "ask_past_self_scoring_complete",
        extra={
            "workspace_id": str(workspace_id),
            "user_id": str(getattr(user, "id", "")),
            "notes_with_content": notes_with_content,
            "chunks_scanned": chunks_scanned,
            "chunks_rejected": chunks_rejected,
            "candidate_count": len(candidates),
            "elapsed_ms": round((time.perf_counter() - started_at) * 1000, 2),
        },
    )

    sources = _dedupe_sources_by_note(candidates, limit)
    logger.info(
        "ask_past_self_sources_deduplicated",
        extra={
            "workspace_id": str(workspace_id),
            "user_id": str(getattr(user, "id", "")),
            "candidate_count": len(candidates),
            "source_count": len(sources),
            "top_scores": [source.similarity for source in sources[:3]],
        },
    )
    gated = evaluate_sources(sources, query=query, min_score=min_score)
    logger.info(
        "ask_past_self_retrieval_complete",
        extra={
            "workspace_id": str(workspace_id),
            "user_id": str(getattr(user, "id", "")),
            "answer_status": gated.answer_status,
            "confidence": gated.confidence,
            "refusal_reason": gated.refusal_reason,
            **gated.diagnostics,
        },
    )
    return gated


def citation_map(sources: Iterable[StrictRAGSource]) -> Dict[str, StrictRAGSource]:
    return {source.source_id: source for source in sources}


def replace_source_markers(text: str, sources: Iterable[StrictRAGSource]) -> str:
    """Replace model-facing [S1] markers with display citations."""

    by_id = citation_map(sources)

    def _replace(match: re.Match[str]) -> str:
        source = by_id.get(match.group(1))
        if source is None:
            return ""
        return f"[{source.citation}]"

    return _CITATION_ID_RE.sub(_replace, text or "")


def cited_sources_from_answer(answer: str, sources: Iterable[StrictRAGSource]) -> List[StrictRAGSource]:
    by_id = citation_map(sources)
    seen: set[str] = set()
    cited: List[StrictRAGSource] = []
    for source_id in _CITATION_ID_RE.findall(answer or ""):
        if source_id in seen:
            continue
        source = by_id.get(source_id)
        if source is not None:
            seen.add(source_id)
            cited.append(source)
    return cited


class GroundedAnswerStreamGuard:
    """Buffer model text and only release sentences with valid note citations."""

    def __init__(self, sources: Iterable[StrictRAGSource]) -> None:
        self.sources = list(sources)
        self._valid_ids = set(citation_map(self.sources))
        self._buffer = ""
        self._released_any = False

    @property
    def released_any(self) -> bool:
        return self._released_any

    def feed(self, text: str) -> str:
        self._buffer += text or ""
        releasable, self._buffer = self._split_releasable(self._buffer)
        return self._filter_releasable(releasable)

    def flush(self) -> str:
        pending = self._buffer
        self._buffer = ""
        return self._filter_releasable(pending)

    def _filter_releasable(self, value: str) -> str:
        if not value:
            return ""
        if REFUSAL_TEXT in value:
            self._released_any = True
            return REFUSAL_TEXT

        pieces = re.split(r"(?<=[.!?])\s+", value)
        kept: List[str] = []
        for piece in pieces:
            candidate = piece.strip()
            if not candidate:
                continue
            cited_ids = set(_CITATION_ID_RE.findall(candidate))
            if cited_ids & self._valid_ids:
                kept.append(candidate)
        if kept:
            self._released_any = True
        return " ".join(kept)

    @staticmethod
    def _split_releasable(value: str) -> tuple[str, str]:
        last_boundary = max(value.rfind(". "), value.rfind("? "), value.rfind("! "), value.rfind("\n"))
        if last_boundary < 0:
            return "", value
        cutoff = last_boundary + 1
        return value[:cutoff], value[cutoff:]


class CitationStreamTransformer:
    """Streaming-safe citation marker replacement for [S1]-style tokens."""

    def __init__(self, sources: Iterable[StrictRAGSource]) -> None:
        self.sources = list(sources)
        self._buffer = ""

    def feed(self, text: str) -> str:
        self._buffer += text or ""
        keep = self._trailing_partial_marker(self._buffer)
        emit = self._buffer[: len(self._buffer) - keep] if keep else self._buffer
        self._buffer = self._buffer[len(emit) :]
        return replace_source_markers(emit, self.sources)

    def flush(self) -> str:
        pending = self._buffer
        self._buffer = ""
        return replace_source_markers(pending, self.sources)

    @staticmethod
    def _trailing_partial_marker(value: str) -> int:
        for length in range(min(8, len(value)), 0, -1):
            suffix = value[-length:]
            if re.fullmatch(r"\[S?\d{0,3}", suffix):
                return length
        return 0
