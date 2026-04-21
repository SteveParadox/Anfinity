"""Shared query-intent and evidence-quality scoring for retrieval pipelines."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set


STOPWORDS: Set[str] = {
    "the", "a", "an", "and", "or", "to", "of", "for", "in", "on", "at",
    "with", "from", "by", "is", "are", "was", "were", "be", "this", "that",
    "it", "as", "about", "into", "than", "then", "what", "which", "who",
    "when", "where", "why", "how", "do", "does", "did", "can", "could",
    "should", "would", "will", "i", "you", "we", "they", "he", "she",
    "them", "their", "our", "your", "me", "my", "looking", "look", "find",
    "show", "tell", "give", "need", "want", "information",
}

GENERIC_QUERY_TERMS: Set[str] = {
    "book", "books", "guide", "guides", "manual", "manuals", "resource",
    "resources", "document", "documents", "material", "materials",
    "information", "topic", "topics",
}

BOOK_TERMS: Set[str] = {
    "book", "books", "textbook", "textbooks", "handbook", "handbooks",
    "manual", "manuals", "guide", "guides", "chapter", "chapters", "author",
    "authors", "edition", "editions", "publisher", "publishers",
}

DOCUMENT_SOURCE_TYPES: Set[str] = {
    "document", "pdf", "docx", "word", "file", "report", "note",
}

DOMAIN_KEYWORDS: Dict[str, Set[str]] = {
    "medical": {
        "medical", "medicine", "clinical", "patient", "patients", "symptom",
        "symptoms", "diagnosis", "diagnostic", "treatment", "therapy",
        "therapeutic", "neurology", "neurological", "healthcare", "health",
        "disorder", "disorders", "dyslexia", "assessment", "intervention",
    },
    "education": {
        "education", "educational", "teaching", "teacher", "teachers",
        "learning", "literacy", "reading", "classroom", "curriculum",
        "student", "students", "school", "schools", "pedagogy", "dyslexia",
        "instruction", "intervention",
    },
    "psychology": {
        "psychology", "psychological", "cognitive", "behavioral",
        "behavioural", "behavior", "developmental", "neurodevelopment",
        "neurodevelopmental", "dyslexia", "assessment", "therapy",
        "attention", "memory", "processing", "brain",
    },
    "programming": {
        "javascript", "typescript", "python", "java", "programming", "code",
        "coding", "function", "functions", "class", "classes", "algorithm",
        "algorithms", "recursion", "recursive", "dom", "event", "events",
        "listener", "listeners", "react", "node", "api", "apis", "snippet",
        "snippets", "bug", "debug", "compiler",
    },
    "crypto": {
        "crypto", "cryptocurrency", "bitcoin", "ethereum", "blockchain",
        "trading", "trader", "traders", "market", "markets", "token",
        "tokens", "wallet", "wallets", "defi", "exchange", "price", "prices",
        "volatility", "strategy", "strategies",
    },
}

DOMAIN_PHRASES: Dict[str, Set[str]] = {
    "medical": {"learning disorder", "medical book", "reading disorder"},
    "education": {"special education", "reading intervention", "learning support"},
    "psychology": {"cognitive psychology", "developmental psychology"},
    "programming": {"event handling", "dom event", "source code"},
    "crypto": {"trading strategy", "trading strategies"},
}

CONFLICTING_DOMAINS: Set[str] = {"medical", "education", "psychology", "programming", "crypto"}


@dataclass(frozen=True)
class QueryIntent:
    """Derived user intent for query-aware relevance scoring."""

    normalized_query: str
    tokens: List[str]
    token_set: Set[str]
    content_terms: Set[str]
    quoted_terms: List[str]
    domain_scores: Dict[str, float]
    primary_domains: Set[str]
    dominant_domain: Optional[str]
    book_intent: bool
    is_domain_specific: bool


@dataclass(frozen=True)
class ChunkRelevance:
    """Relevance evidence for a chunk against a specific query."""

    lexical_overlap: float
    exact_match_ratio: float
    domain_alignment: float
    domain_mismatch_score: float
    off_topic: bool
    book_alignment: float
    evidence_score: float
    chunk_domain_scores: Dict[str, float]


def _tokenize(text: str) -> List[str]:
    return [
        token for token in re.findall(r"[a-zA-Z0-9_:-]+", (text or "").lower())
        if len(token) > 1 and token not in STOPWORDS
    ]


def _keyword_hits(text: str, token_set: Set[str], keywords: Iterable[str], phrases: Iterable[str]) -> int:
    hits = sum(1 for keyword in keywords if keyword in token_set)
    lowered = text.lower()
    hits += sum(1 for phrase in phrases if phrase in lowered)
    return hits


def analyze_query_intent(query: str) -> QueryIntent:
    normalized_query = " ".join((query or "").split())
    tokens = _tokenize(normalized_query)
    token_set = set(tokens)
    content_terms = {
        token for token in token_set
        if token not in GENERIC_QUERY_TERMS and token not in BOOK_TERMS
    }
    quoted_terms = [term.strip().lower() for term in re.findall(r'"([^"]+)"', normalized_query) if term.strip()]

    domain_scores: Dict[str, float] = {}
    for domain, keywords in DOMAIN_KEYWORDS.items():
        hits = _keyword_hits(
            normalized_query,
            token_set,
            keywords,
            DOMAIN_PHRASES.get(domain, set()),
        )
        domain_scores[domain] = min(hits / 3.0, 1.0)

    dominant_domain: Optional[str] = None
    primary_domains: Set[str] = set()
    top_score = max(domain_scores.values(), default=0.0)
    if top_score > 0.0:
        dominant_domain = max(domain_scores, key=domain_scores.get)
        cutoff = max(0.34, top_score - 0.18)
        primary_domains = {
            domain for domain, score in domain_scores.items()
            if score >= cutoff and score > 0.0
        }

    book_intent = bool(token_set & BOOK_TERMS or any(term in normalized_query.lower() for term in BOOK_TERMS))
    is_domain_specific = top_score >= 0.34 or bool(primary_domains)

    return QueryIntent(
        normalized_query=normalized_query,
        tokens=tokens,
        token_set=token_set,
        content_terms=content_terms or token_set,
        quoted_terms=quoted_terms,
        domain_scores=domain_scores,
        primary_domains=primary_domains,
        dominant_domain=dominant_domain,
        book_intent=book_intent,
        is_domain_specific=is_domain_specific,
    )


def _collect_metadata_strings(metadata: Optional[Dict[str, Any]]) -> List[str]:
    if not metadata:
        return []

    collected: List[str] = []
    for key in ("tags", "topics", "subject", "category", "categories", "document_title", "title"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            collected.append(value)
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            collected.extend(str(item) for item in value if str(item).strip())
    return collected


def analyze_chunk_relevance(
    query: str,
    text: str,
    *,
    title: str = "",
    tags: Optional[Sequence[str]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    source_type: Optional[str] = None,
) -> ChunkRelevance:
    intent = analyze_query_intent(query)
    combined_parts: List[str] = [title or "", text or ""]
    if tags:
        combined_parts.extend(str(tag) for tag in tags if str(tag).strip())
    combined_parts.extend(_collect_metadata_strings(metadata))
    if source_type:
        combined_parts.append(str(source_type))
    combined_text = " ".join(part for part in combined_parts if part).lower()
    chunk_tokens = set(_tokenize(combined_text))

    content_terms = intent.content_terms or intent.token_set
    lexical_hits = content_terms & chunk_tokens
    lexical_overlap = 0.0
    if content_terms:
        lexical_overlap = min(len(lexical_hits) / max(min(len(content_terms), 4), 1), 1.0)

    exact_match_ratio = 0.0
    if content_terms:
        exact_match_ratio = min(len(lexical_hits) / max(min(len(content_terms), 3), 1), 1.0)
    if intent.quoted_terms:
        quote_hits = sum(1 for term in intent.quoted_terms if term in combined_text)
        exact_match_ratio = max(exact_match_ratio, min(quote_hits / len(intent.quoted_terms), 1.0))

    chunk_domain_scores: Dict[str, float] = {}
    for domain, keywords in DOMAIN_KEYWORDS.items():
        hits = _keyword_hits(
            combined_text,
            chunk_tokens,
            keywords,
            DOMAIN_PHRASES.get(domain, set()),
        )
        chunk_domain_scores[domain] = min(hits / 3.0, 1.0)

    primary_alignment = 0.0
    if intent.primary_domains:
        primary_alignment = max(chunk_domain_scores.get(domain, 0.0) for domain in intent.primary_domains)

    conflicting_alignment = 0.0
    if intent.primary_domains:
        conflicting_alignment = max(
            (
                score for domain, score in chunk_domain_scores.items()
                if domain in CONFLICTING_DOMAINS and domain not in intent.primary_domains
            ),
            default=0.0,
        )

    book_hits = 0
    if intent.book_intent:
        book_hits = _keyword_hits(combined_text, chunk_tokens, BOOK_TERMS, set())
        if str(source_type or "").lower() in DOCUMENT_SOURCE_TYPES:
            book_hits += 1
    book_alignment = min(book_hits / 2.0, 1.0) if intent.book_intent else 0.0

    domain_alignment = primary_alignment
    if intent.book_intent:
        domain_alignment = min(domain_alignment + (book_alignment * 0.2), 1.0)

    domain_mismatch_score = 0.0
    off_topic = False
    if intent.is_domain_specific:
        domain_mismatch_score = max(conflicting_alignment - primary_alignment, 0.0)
        off_topic = (
            primary_alignment < 0.15
            and conflicting_alignment >= 0.34
            and lexical_overlap < 0.15
        )

    evidence_score = (
        lexical_overlap * 0.40
        + exact_match_ratio * 0.15
        + domain_alignment * 0.35
        + book_alignment * 0.10
    )
    if intent.is_domain_specific and primary_alignment < 0.10 and lexical_overlap < 0.10:
        evidence_score *= 0.55
    if off_topic:
        evidence_score *= 0.25
    evidence_score = max(0.0, min(evidence_score, 1.0))

    return ChunkRelevance(
        lexical_overlap=lexical_overlap,
        exact_match_ratio=exact_match_ratio,
        domain_alignment=domain_alignment,
        domain_mismatch_score=domain_mismatch_score,
        off_topic=off_topic,
        book_alignment=book_alignment,
        evidence_score=evidence_score,
        chunk_domain_scores=chunk_domain_scores,
    )


def summarize_relevance(query: str, chunks: Sequence[Dict[str, Any]]) -> Dict[str, float]:
    """Aggregate evidence quality for a set of chunk-like dictionaries."""
    if not chunks:
        return {
            "avg_lexical_overlap": 0.0,
            "avg_domain_alignment": 0.0,
            "avg_evidence_score": 0.0,
            "top_evidence_score": 0.0,
            "off_topic_ratio": 0.0,
        }

    evidence = [
        analyze_chunk_relevance(
            query,
            item.get("text", "") or "",
            title=item.get("title", "") or "",
            tags=item.get("tags"),
            metadata=item.get("metadata"),
            source_type=item.get("source_type"),
        )
        for item in chunks
    ]
    count = len(evidence)
    return {
        "avg_lexical_overlap": sum(item.lexical_overlap for item in evidence) / count,
        "avg_domain_alignment": sum(item.domain_alignment for item in evidence) / count,
        "avg_evidence_score": sum(item.evidence_score for item in evidence) / count,
        "top_evidence_score": max(item.evidence_score for item in evidence),
        "off_topic_ratio": sum(1 for item in evidence if item.off_topic) / count,
    }
