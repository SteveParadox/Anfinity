"""Asynchronous note-connection suggestion generation."""

from __future__ import annotations

import json
import logging
import math
import re
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Iterable, Optional
from uuid import UUID

from sqlalchemy import select, text

from app.celery_app import celery_app
from app.database.models import Note, NoteConnectionSuggestion
from app.database.session import SyncSessionLocal
from app.services.embeddings import get_embedding_service
from app.services.graph_service import extract_entities_from_note

logger = logging.getLogger(__name__)

INLINE_TAG_PATTERN = re.compile(r"#([a-zA-Z0-9_-]{2,50})")
WORD_PATTERN = re.compile(r"\b[a-zA-Z][a-zA-Z0-9_-]{2,}\b")
STOPWORDS = {
    "about", "after", "again", "also", "because", "been", "being", "between", "both", "could",
    "does", "each", "from", "have", "into", "just", "like", "more", "most", "note", "notes",
    "other", "over", "same", "such", "than", "that", "their", "them", "then", "there", "these",
    "they", "this", "through", "using", "very", "what", "when", "where", "which", "with", "would",
    "your",
}


def _parse_uuid(value: str, field: str) -> UUID | None:
    try:
        return UUID(value)
    except (TypeError, ValueError, AttributeError):
        logger.error("Invalid %s UUID: %r", field, value)
        return None


def _build_embedding_text(note: Note) -> str:
    parts = [note.title or "", note.content or ""]
    if note.tags:
        parts.append(" ".join(str(tag) for tag in note.tags if str(tag).strip()))
    return "\n".join(part for part in parts if part.strip())


def _parse_embedding_value(value: Any) -> list[float]:
    if isinstance(value, list):
        return [float(item) for item in value]
    if isinstance(value, str) and value.strip():
        return [float(item) for item in json.loads(value)]
    return []


def _ensure_note_embedding(db, note: Note) -> list[float]:
    existing_embedding = _parse_embedding_value(note.embedding)
    if existing_embedding:
        return existing_embedding

    embedding_service = get_embedding_service()
    embedding_vector = embedding_service.embed_text(_build_embedding_text(note))
    if not embedding_vector:
        raise ValueError(f"Embedding service returned an empty vector for note {note.id}")

    note.embedding = json.dumps(embedding_vector)
    note.updated_at = datetime.now(timezone.utc)
    try:
        with db.begin_nested():
            db.execute(
                text(
                    f"""
                    UPDATE notes
                    SET embedding_vector = CAST(:embedding AS vector({len(embedding_vector)}))
                    WHERE id = :note_id
                    """
                ),
                {
                    "embedding": f"[{','.join(map(str, embedding_vector))}]",
                    "note_id": note.id,
                },
            )
    except Exception as exc:
        logger.warning(
            "Skipping embedding_vector sync for connection suggestions on note %s (dim=%d): %s",
            note.id,
            len(embedding_vector),
            exc,
        )
    db.flush()
    return embedding_vector


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return max(0.0, min(dot / (left_norm * right_norm), 1.0))


def _extract_tags(tags: Iterable[str] | None, content: str) -> list[str]:
    explicit_tags = [str(tag).strip() for tag in (tags or []) if str(tag).strip()]
    inline_tags = [match.strip() for match in INLINE_TAG_PATTERN.findall(content or "")]
    merged = []
    seen = set()
    for tag in [*explicit_tags, *inline_tags]:
        normalized = tag.lower()
        if normalized and normalized not in seen:
            seen.add(normalized)
            merged.append(tag)
    return merged


def _extract_keywords(title: str, content: str) -> list[str]:
    counts = Counter(
        word.lower()
        for word in WORD_PATTERN.findall(f"{title} {content}")
        if word.lower() not in STOPWORDS
    )
    return [word for word, _ in counts.most_common(6)]


def _build_reason(source_note: Note, target_title: str, target_content: str, target_tags: list[str], similarity: float) -> tuple[str, dict[str, Any]]:
    source_tags = {tag.lower(): tag for tag in _extract_tags(source_note.tags, source_note.content or "")}
    target_tag_map = {tag.lower(): tag for tag in _extract_tags(target_tags, target_content)}
    shared_tags = [source_tags[key] for key in source_tags.keys() & target_tag_map.keys()]

    source_entities = {entity.lower(): entity for entity in extract_entities_from_note(source_note.title or "", source_note.content or "")}
    target_entities = {entity.lower(): entity for entity in extract_entities_from_note(target_title or "", target_content or "")}
    shared_entities = [source_entities[key] for key in source_entities.keys() & target_entities.keys()]

    source_keywords = set(_extract_keywords(source_note.title or "", source_note.content or ""))
    target_keywords = _extract_keywords(target_title or "", target_content or "")
    shared_keywords = [keyword for keyword in target_keywords if keyword in source_keywords][:3]

    similarity_pct = max(55, round(similarity * 100))

    if shared_tags and shared_entities:
        reason = f"These notes both center on {', '.join(shared_tags[:2])} and mention {', '.join(shared_entities[:2])}, which makes them a strong related connection."
    elif shared_tags:
        reason = f"These notes overlap around {', '.join(shared_tags[:2])}, and their content is semantically aligned enough to suggest a useful connection."
    elif shared_entities:
        reason = f"These notes both reference {', '.join(shared_entities[:2])}, suggesting they capture related ideas worth linking."
    elif shared_keywords:
        reason = f"These notes use similar language around {', '.join(shared_keywords[:2])}, making them a likely related thread in your knowledge base."
    else:
        reason = f"These notes are about {similarity_pct}% semantically similar and appear to cover closely related ideas."

    return reason, {
        "shared_tags": shared_tags[:5],
        "shared_entities": shared_entities[:5],
        "shared_keywords": shared_keywords[:5],
        "similarity_percentage": similarity_pct,
    }


def _find_candidates_via_pgvector(db, note: Note, threshold: float, limit: int) -> list[dict[str, Any]]:
    result = db.execute(
        text(
            """
            WITH source_note AS (
                SELECT id, workspace_id, embedding_vector
                FROM notes
                WHERE id = :note_id
            )
            SELECT
                n.id,
                n.title,
                n.content,
                n.tags,
                n.created_at,
                GREATEST(
                    0.0,
                    LEAST(
                        1.0,
                        1.0 - ((n.embedding_vector <=> s.embedding_vector) / 2.0)
                    )
                )::FLOAT AS similarity
            FROM notes n
            CROSS JOIN source_note s
            WHERE n.workspace_id = s.workspace_id
              AND n.id <> s.id
              AND s.embedding_vector IS NOT NULL
              AND n.embedding_vector IS NOT NULL
            ORDER BY n.embedding_vector <=> s.embedding_vector
            LIMIT :limit
            """
        ),
        {"note_id": note.id, "limit": max(limit * 4, 20)},
    )
    rows = []
    for row in result:
        mapped = dict(row._mapping)
        similarity = float(mapped.get("similarity", 0.0) or 0.0)
        if similarity >= threshold:
            rows.append(mapped)
    return rows


def _find_candidates_fallback(db, note: Note, threshold: float, limit: int) -> list[dict[str, Any]]:
    source_embedding = _ensure_note_embedding(db, note)
    other_notes = db.execute(
        select(Note).where(
            Note.workspace_id == note.workspace_id,
            Note.id != note.id,
        )
    ).scalars().all()

    scored = []
    for other_note in other_notes:
        other_embedding = _parse_embedding_value(other_note.embedding)
        if not other_embedding:
            continue
        similarity = _cosine_similarity(source_embedding, other_embedding)
        if similarity < threshold:
            continue
        scored.append(
            {
                "id": other_note.id,
                "title": other_note.title,
                "content": other_note.content,
                "tags": other_note.tags or [],
                "created_at": other_note.created_at,
                "similarity": similarity,
            }
        )

    return sorted(scored, key=lambda row: float(row["similarity"]), reverse=True)[: max(limit * 4, 20)]


@celery_app.task(
    bind=True,
    max_retries=2,
    name="generate_connection_suggestions",
    acks_late=True,
)
def generate_connection_suggestions(
    self,
    note_id: str,
    threshold: float = 0.55,
    limit: int = 5,
) -> dict[str, Any]:
    """Refresh persisted connection suggestions for a note."""
    note_uuid = _parse_uuid(note_id, "note_id")
    if note_uuid is None:
        return {"status": "error", "note_id": note_id, "message": "Invalid UUID"}

    db = SyncSessionLocal()
    try:
        note = db.execute(select(Note).where(Note.id == note_uuid)).scalar_one_or_none()
        if note is None:
            return {"status": "not_found", "note_id": note_id, "message": "Note does not exist"}
        if note.workspace_id is None:
            return {"status": "skipped", "note_id": note_id, "message": "Note has no workspace"}

        _ensure_note_embedding(db, note)

        try:
            candidates = _find_candidates_via_pgvector(db, note, threshold, limit)
        except Exception as exc:
            logger.warning("Connection suggestions falling back to Python similarity for note %s: %s", note_id, exc)
            candidates = _find_candidates_fallback(db, note, threshold, limit)

        connected_ids = set()
        for connection_id in note.connections or []:
            try:
                connected_ids.add(UUID(str(connection_id)))
            except (TypeError, ValueError):
                logger.debug("Skipping invalid connection id %s on note %s", connection_id, note.id)

        existing_rows = db.execute(
            select(NoteConnectionSuggestion).where(NoteConnectionSuggestion.source_note_id == note.id)
        ).scalars().all()
        existing_by_target = {row.suggested_note_id: row for row in existing_rows}

        active_candidate_ids: set[UUID] = set()
        created_count = 0
        updated_count = 0
        skipped_count = 0

        for candidate in candidates:
            candidate_id = candidate["id"]
            if not isinstance(candidate_id, UUID):
                candidate_id = UUID(str(candidate_id))

            if candidate_id in connected_ids:
                pending_row = existing_by_target.get(candidate_id)
                if pending_row and pending_row.status == "pending":
                    db.delete(pending_row)
                continue

            existing_row = existing_by_target.get(candidate_id)
            if existing_row and existing_row.status in {"dismissed", "confirmed"}:
                skipped_count += 1
                continue

            reason, metadata = _build_reason(
                source_note=note,
                target_title=str(candidate.get("title") or ""),
                target_content=str(candidate.get("content") or ""),
                target_tags=list(candidate.get("tags") or []),
                similarity=float(candidate.get("similarity", 0.0) or 0.0),
            )

            if existing_row is None:
                db.add(
                    NoteConnectionSuggestion(
                        workspace_id=note.workspace_id,
                        source_note_id=note.id,
                        suggested_note_id=candidate_id,
                        similarity_score=float(candidate["similarity"]),
                        reason=reason,
                        status="pending",
                        suggestion_metadata=metadata,
                    )
                )
                created_count += 1
            else:
                existing_row.similarity_score = float(candidate["similarity"])
                existing_row.reason = reason
                existing_row.suggestion_metadata = metadata
                existing_row.updated_at = datetime.now(timezone.utc)
                updated_count += 1

            active_candidate_ids.add(candidate_id)
            if len(active_candidate_ids) >= limit:
                break

        for row in existing_rows:
            if row.status == "pending" and row.suggested_note_id not in active_candidate_ids:
                db.delete(row)

        db.commit()
        return {
            "status": "success",
            "note_id": note_id,
            "pending_suggestions": len(active_candidate_ids),
            "created": created_count,
            "updated": updated_count,
            "skipped_preserved": skipped_count,
        }

    except Exception as exc:
        db.rollback()
        logger.error(
            "Failed to generate connection suggestions for note %s (attempt %d): %s",
            note_id,
            self.request.retries + 1,
            exc,
            exc_info=True,
        )
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc, countdown=120 * (2 ** self.request.retries))
        return {
            "status": "failed",
            "note_id": note_id,
            "error": str(exc),
            "retries_exceeded": True,
        }
    finally:
        db.close()
