"""Note-level classification and decay helpers for capture enrichment."""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timezone
from typing import Iterable

from app.ingestion.content_detection import classify_topics

INLINE_TAG_PATTERN = re.compile(r"#([a-zA-Z0-9_-]{2,50})")
WORD_PATTERN = re.compile(r"\b[a-zA-Z][a-zA-Z0-9_-]{2,}\b")
NON_TAG_CHARS = re.compile(r"[^a-z0-9_-]+")
STOPWORDS = {
    "about", "after", "again", "also", "because", "been", "being", "between", "both", "could",
    "does", "each", "from", "have", "into", "just", "like", "more", "most", "note", "notes",
    "other", "over", "same", "such", "than", "that", "their", "them", "then", "there", "these",
    "they", "this", "through", "using", "very", "what", "when", "where", "which", "with", "would",
    "your", "will", "shall", "should", "therefore", "however",
}


def normalize_tag(value: object) -> str:
    tag = NON_TAG_CHARS.sub("-", str(value or "").strip().lower()).strip("-_")
    return tag[:50]


def dedupe_tags(values: Iterable[object]) -> list[str]:
    seen: set[str] = set()
    tags: list[str] = []
    for value in values:
        tag = normalize_tag(value)
        if not tag or tag in seen:
            continue
        seen.add(tag)
        tags.append(tag)
    return tags


def classify_note_tags(title: str, content: str, existing_tags: Iterable[object] | None = None) -> dict[str, object]:
    text = f"{title or ''}\n\n{content or ''}".strip()
    inline_tags = INLINE_TAG_PATTERN.findall(text)
    topic_tags = classify_topics(text)
    if not topic_tags:
        counts = Counter(
            word.lower()
            for word in WORD_PATTERN.findall(text)
            if word.lower() not in STOPWORDS
        )
        topic_tags = [word for word, _ in counts.most_common(5)]

    suggested_tags = dedupe_tags([*inline_tags, *topic_tags])[:8]
    existing = dedupe_tags(existing_tags or [])
    merged = dedupe_tags([*existing, *suggested_tags])[:20]

    confidence = 0.35
    if inline_tags:
        confidence += 0.25
    if topic_tags:
        confidence += min(0.3, 0.06 * len(topic_tags))
    if len(text) > 400:
        confidence += 0.1

    return {
        "tags": merged,
        "suggested_tags": suggested_tags,
        "preserved_tags": existing,
        "confidence": round(min(confidence, 0.95), 2),
        "mode": "additive",
    }


def classify_decay(note_created_at: datetime | None, content: str, tags: Iterable[object] | None = None) -> dict[str, object]:
    now = datetime.now(timezone.utc)
    created_at = note_created_at or now
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    age_days = max(0, (now - created_at).days)
    tag_set = {normalize_tag(tag) for tag in (tags or [])}
    content_lower = (content or "").lower()

    durable_signals = {"reference", "evergreen", "decision", "architecture", "policy", "runbook"}
    volatile_signals = {"todo", "draft", "temporary", "meeting", "calendar", "daily", "standup"}

    durable_hits = sorted(tag_set & durable_signals)
    volatile_hits = sorted(tag_set & volatile_signals)
    if any(word in content_lower for word in ("todo", "follow up", "draft", "temporary")):
        volatile_hits.append("content-signal")
    if any(word in content_lower for word in ("decision", "architecture", "policy", "runbook")):
        durable_hits.append("content-signal")

    if durable_hits:
        decay_class = "durable"
        review_after_days = 180
    elif volatile_hits:
        decay_class = "volatile"
        review_after_days = 14
    elif age_days >= 90:
        decay_class = "stale_candidate"
        review_after_days = 30
    else:
        decay_class = "standard"
        review_after_days = 60

    return {
        "decay_class": decay_class,
        "age_days": age_days,
        "review_after_days": review_after_days,
        "durable_signals": durable_hits[:5],
        "volatile_signals": volatile_hits[:5],
    }
