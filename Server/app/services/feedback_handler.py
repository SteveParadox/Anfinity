"""Feedback processing for answer verification and chunk credibility tracking."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import Float, String, cast, func, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Answer, ChunkWeight, Feedback, SearchFeedback
from app.database.session import log_session_query_metrics

logger = logging.getLogger(__name__)


class FeedbackHandler:
    """
    Process answer feedback and adjust per-chunk credibility weights.

    The hot path used to perform a `SELECT` and `COMMIT` per cited chunk. This
    version keeps the logic explicit, but batches the chunk-weight work into one
    read and one PostgreSQL upsert so answer feedback stays transactionally
    coherent and avoids N+1 round trips.
    """

    def __init__(self) -> None:
        self.positive_multiplier = 1.1
        self.negative_multiplier = 0.9
        self.min_weight = 0.1
        self.max_weight = 2.0

    @staticmethod
    def _normalize_feedback_status(feedback_status: str) -> str:
        normalized = str(feedback_status or "").strip().lower()
        if normalized == "approved":
            return "verified"
        if normalized not in {"verified", "rejected"}:
            raise ValueError(f"Unsupported feedback status: {feedback_status}")
        return normalized

    @staticmethod
    def _extract_unique_sources(answer_sources: Any) -> list[tuple[str, str]]:
        unique_pairs: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()

        for source in list(answer_sources or []):
            chunk_id = str(source.get("chunk_id") or "").strip()
            document_id = str(source.get("document_id") or "").strip()
            if not chunk_id or not document_id:
                continue

            pair = (chunk_id, document_id)
            if pair in seen:
                continue

            seen.add(pair)
            unique_pairs.append(pair)

        return unique_pairs

    @staticmethod
    def feedback_signal_from_type(feedback_type: str, rating_value: Optional[int] = None) -> int:
        """
        Convert typed semantic-search feedback into a directional signal.

        Returns:
            1  => positive/correct signal
            0  => neutral/insufficient signal
            -1 => negative/incorrect signal
        """
        if rating_value is not None:
            if rating_value > 0:
                return 1
            if rating_value < 0:
                return -1
            return 0

        normalized = str(feedback_type or "").strip().lower()
        positive_types = {"correct"}
        neutral_types = {"partially_correct", "other"}
        negative_types = {
            "irrelevant",
            "missing_expected_result",
            "wrong_result",
            "bad_highlight",
            "low_quality_answer",
            "hallucinated_or_unsupported",
        }
        if normalized in positive_types:
            return 1
        if normalized in negative_types:
            return -1
        if normalized in neutral_types:
            return 0
        return 0

    async def process_answer_feedback(
        self,
        answer_id: UUID,
        feedback_status: str,
        comment: Optional[str],
        user_id: UUID,
        db: AsyncSession,
        answer_workspace_id: Optional[UUID] = None,
        answer_sources: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """
        Process feedback on an answer and update chunk weights in one transaction.
        """
        try:
            normalized_status = self._normalize_feedback_status(feedback_status)
            is_correct = normalized_status == "verified"

            workspace_id = answer_workspace_id
            sources = answer_sources
            if workspace_id is None or sources is None:
                result = await db.execute(
                    select(Answer.id, Answer.workspace_id, Answer.sources).where(Answer.id == answer_id)
                )
                answer_row = result.first()
                if not answer_row:
                    raise ValueError(f"Answer {answer_id} not found")
                workspace_id = answer_row.workspace_id
                sources = answer_row.sources

            source_pairs = self._extract_unique_sources(sources)

            logger.info(
                "Processing answer feedback: answer_id=%s status=%s user_id=%s cited_chunks=%s",
                answer_id,
                normalized_status,
                user_id,
                len(source_pairs),
            )

            chunks_updated = await self._bulk_update_chunk_weights(
                source_pairs=source_pairs,
                workspace_id=workspace_id,
                is_correct=is_correct,
                db=db,
            )

            db.add(
                Feedback(
                    answer_id=answer_id,
                    workspace_id=workspace_id,
                    user_id=user_id,
                    rating=5 if is_correct else 1,
                    comment=comment,
                )
            )

            await db.execute(
                update(Answer)
                .where(Answer.id == answer_id)
                .values(
                    verification_status=normalized_status,
                    verified_by=user_id,
                    verified_at=datetime.now(timezone.utc),
                    verification_comment=comment,
                )
            )

            await db.commit()
            log_session_query_metrics(db, "answers.feedback_handler")

            return {
                "answer_id": str(answer_id),
                "feedback_status": normalized_status,
                "chunks_updated": chunks_updated,
                "confidence_change": await self._calculate_confidence_impact(
                    chunks_updated,
                    is_correct,
                ),
            }
        except Exception as exc:
            logger.error("Error processing feedback for answer %s: %s", answer_id, exc, exc_info=True)
            raise

    async def _bulk_update_chunk_weights(
        self,
        source_pairs: list[tuple[str, str]],
        workspace_id: UUID,
        is_correct: bool,
        db: AsyncSession,
    ) -> List[Dict[str, Any]]:
        if not source_pairs:
            return []

        existing_result = await db.execute(
            select(ChunkWeight).where(
                ChunkWeight.workspace_id == workspace_id,
                tuple_(ChunkWeight.chunk_id, cast(ChunkWeight.document_id, String)).in_(source_pairs),
            )
        )
        existing_weights = existing_result.scalars().all()
        existing_by_pair = {
            (str(weight.chunk_id), str(weight.document_id)): weight
            for weight in existing_weights
        }

        positive_increment = 1 if is_correct else 0
        negative_increment = 0 if is_correct else 1
        multiplier = self.positive_multiplier if is_correct else self.negative_multiplier

        insert_values = [
            {
                "chunk_id": chunk_id,
                "document_id": document_id,
                "workspace_id": workspace_id,
                "credibility_score": 1.0,
                "positive_feedback_count": positive_increment,
                "negative_feedback_count": negative_increment,
                "total_uses": 1,
                "accuracy_rate": 1.0 if is_correct else 0.0,
            }
            for chunk_id, document_id in source_pairs
        ]

        insert_stmt = pg_insert(ChunkWeight).values(insert_values)
        total_feedback_expr = (
            ChunkWeight.positive_feedback_count
            + ChunkWeight.negative_feedback_count
            + insert_stmt.excluded.positive_feedback_count
            + insert_stmt.excluded.negative_feedback_count
        )
        upsert_stmt = (
            insert_stmt.on_conflict_do_update(
                constraint="uq_chunk_weight_scope",
                set_={
                    "positive_feedback_count": ChunkWeight.positive_feedback_count + insert_stmt.excluded.positive_feedback_count,
                    "negative_feedback_count": ChunkWeight.negative_feedback_count + insert_stmt.excluded.negative_feedback_count,
                    "total_uses": ChunkWeight.total_uses + insert_stmt.excluded.total_uses,
                    "accuracy_rate": cast(
                        ChunkWeight.positive_feedback_count + insert_stmt.excluded.positive_feedback_count,
                        Float,
                    ) / cast(func.nullif(total_feedback_expr, 0), Float),
                    "credibility_score": func.least(
                        self.max_weight,
                        func.greatest(self.min_weight, ChunkWeight.credibility_score * multiplier),
                    ),
                    "updated_at": datetime.now(timezone.utc),
                },
            )
            .returning(
                ChunkWeight.chunk_id,
                ChunkWeight.document_id,
                ChunkWeight.credibility_score,
                ChunkWeight.accuracy_rate,
                ChunkWeight.positive_feedback_count,
                ChunkWeight.negative_feedback_count,
                ChunkWeight.total_uses,
            )
        )

        updated_rows = (await db.execute(upsert_stmt)).all()
        updates_by_pair = {
            (str(row.chunk_id), str(row.document_id)): row
            for row in updated_rows
        }

        chunk_updates: list[dict[str, Any]] = []
        for chunk_id, document_id in source_pairs:
            row = updates_by_pair[(chunk_id, document_id)]
            existing = existing_by_pair.get((chunk_id, document_id))
            old_weight = float(existing.credibility_score) if existing is not None else 1.0
            chunk_updates.append(
                {
                    "chunk_id": chunk_id,
                    "document_id": document_id,
                    "old_weight": round(old_weight, 3),
                    "new_weight": round(float(row.credibility_score or 0.0), 3),
                    "accuracy": round(float(row.accuracy_rate or 0.0), 3),
                    "positive_count": int(row.positive_feedback_count or 0),
                    "negative_count": int(row.negative_feedback_count or 0),
                    "total_uses": int(row.total_uses or 0),
                }
            )

        return chunk_updates

    async def apply_feedback_delta(
        self,
        *,
        workspace_id: UUID,
        answer_sources: Any,
        previous_signal: int,
        new_signal: int,
        db: AsyncSession,
    ) -> List[Dict[str, Any]]:
        """
        Apply chunk-weight deltas when a user's feedback is created/updated.

        This keeps a single feedback record mutable while preserving aggregate
        chunk stats and credibility weights.
        """
        source_pairs = self._extract_unique_sources(answer_sources)
        if not source_pairs:
            return []

        previous_signal = int(max(-1, min(1, previous_signal)))
        new_signal = int(max(-1, min(1, new_signal)))
        if previous_signal == new_signal:
            return []

        positive_delta = int((1 if new_signal > 0 else 0) - (1 if previous_signal > 0 else 0))
        negative_delta = int((1 if new_signal < 0 else 0) - (1 if previous_signal < 0 else 0))
        total_delta = int((1 if new_signal != 0 else 0) - (1 if previous_signal != 0 else 0))

        previous_multiplier = self.positive_multiplier if previous_signal > 0 else self.negative_multiplier if previous_signal < 0 else 1.0
        new_multiplier = self.positive_multiplier if new_signal > 0 else self.negative_multiplier if new_signal < 0 else 1.0
        multiplier = float(new_multiplier / previous_multiplier) if previous_multiplier else 1.0

        existing_result = await db.execute(
            select(ChunkWeight).where(
                ChunkWeight.workspace_id == workspace_id,
                tuple_(ChunkWeight.chunk_id, cast(ChunkWeight.document_id, String)).in_(source_pairs),
            )
        )
        existing_weights = existing_result.scalars().all()
        existing_by_pair = {(str(weight.chunk_id), str(weight.document_id)): weight for weight in existing_weights}

        inserted_positive = max(positive_delta, 0)
        inserted_negative = max(negative_delta, 0)
        inserted_total = max(total_delta, 0)

        insert_values = [
            {
                "chunk_id": chunk_id,
                "document_id": document_id,
                "workspace_id": workspace_id,
                "credibility_score": 1.0,
                "positive_feedback_count": inserted_positive,
                "negative_feedback_count": inserted_negative,
                "total_uses": inserted_total,
                "accuracy_rate": 1.0 if inserted_positive > 0 and inserted_negative == 0 else 0.0,
            }
            for chunk_id, document_id in source_pairs
        ]

        insert_stmt = pg_insert(ChunkWeight).values(insert_values)
        new_positive_expr = func.greatest(0, ChunkWeight.positive_feedback_count + positive_delta)
        new_negative_expr = func.greatest(0, ChunkWeight.negative_feedback_count + negative_delta)
        new_feedback_total_expr = new_positive_expr + new_negative_expr
        accuracy_expr = cast(new_positive_expr, Float) / cast(func.nullif(new_feedback_total_expr, 0), Float)

        upsert_stmt = (
            insert_stmt.on_conflict_do_update(
                constraint="uq_chunk_weight_scope",
                set_={
                    "positive_feedback_count": new_positive_expr,
                    "negative_feedback_count": new_negative_expr,
                    "total_uses": func.greatest(0, ChunkWeight.total_uses + total_delta),
                    "accuracy_rate": func.coalesce(accuracy_expr, 0.0),
                    "credibility_score": func.least(
                        self.max_weight,
                        func.greatest(self.min_weight, ChunkWeight.credibility_score * multiplier),
                    ),
                    "updated_at": datetime.now(timezone.utc),
                },
            )
            .returning(
                ChunkWeight.chunk_id,
                ChunkWeight.document_id,
                ChunkWeight.credibility_score,
                ChunkWeight.accuracy_rate,
                ChunkWeight.positive_feedback_count,
                ChunkWeight.negative_feedback_count,
                ChunkWeight.total_uses,
            )
        )

        updated_rows = (await db.execute(upsert_stmt)).all()
        updates_by_pair = {(str(row.chunk_id), str(row.document_id)): row for row in updated_rows}

        chunk_updates: list[dict[str, Any]] = []
        for chunk_id, document_id in source_pairs:
            row = updates_by_pair[(chunk_id, document_id)]
            existing = existing_by_pair.get((chunk_id, document_id))
            old_weight = float(existing.credibility_score) if existing is not None else 1.0
            chunk_updates.append(
                {
                    "chunk_id": chunk_id,
                    "document_id": document_id,
                    "old_weight": round(old_weight, 3),
                    "new_weight": round(float(row.credibility_score or 0.0), 3),
                    "accuracy": round(float(row.accuracy_rate or 0.0), 3),
                    "positive_count": int(row.positive_feedback_count or 0),
                    "negative_count": int(row.negative_feedback_count or 0),
                    "total_uses": int(row.total_uses or 0),
                }
            )

        return chunk_updates

    async def _calculate_confidence_impact(
        self,
        chunks_updated: List[Dict[str, Any]],
        is_correct: bool,
    ) -> float:
        del is_correct
        if not chunks_updated:
            return 0.0

        weight_changes = [
            chunk["new_weight"] / chunk["old_weight"]
            for chunk in chunks_updated
            if float(chunk["old_weight"] or 0.0) > 0.0
        ]
        if not weight_changes:
            return 0.0

        avg_change = sum(weight_changes) / len(weight_changes)
        confidence_impact = (avg_change - 1.0) * 10
        return round(confidence_impact, 2)

    async def get_chunk_credibility_scores(
        self,
        workspace_id: UUID,
        db: AsyncSession,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        result = await db.execute(
            select(ChunkWeight)
            .where(ChunkWeight.workspace_id == workspace_id)
            .order_by(ChunkWeight.credibility_score.desc(), ChunkWeight.updated_at.desc())
            .limit(limit)
        )

        weights = result.scalars().all()
        return [
            {
                "chunk_id": w.chunk_id,
                "document_id": str(w.document_id),
                "credibility_score": round(w.credibility_score, 3),
                "accuracy_rate": round(w.accuracy_rate, 3),
                "positive_feedback": w.positive_feedback_count,
                "negative_feedback": w.negative_feedback_count,
                "total_uses": w.total_uses,
                "updated_at": w.updated_at.isoformat() if w.updated_at else None,
            }
            for w in weights
        ]

    async def get_model_evaluation_metrics(
        self,
        workspace_id: UUID,
        db: AsyncSession,
    ) -> Dict[str, Any]:
        search_feedback_result = await db.execute(
            select(SearchFeedback).where(
                SearchFeedback.workspace_id == workspace_id,
                SearchFeedback.target_kind == "answer",
            )
        )
        search_feedback_records = search_feedback_result.scalars().all()
        if search_feedback_records:
            total = len(search_feedback_records)
            approved = sum(
                1
                for f in search_feedback_records
                if self.feedback_signal_from_type(f.feedback_type, f.rating_value) > 0
            )
            rejected = sum(
                1
                for f in search_feedback_records
                if self.feedback_signal_from_type(f.feedback_type, f.rating_value) < 0
            )
            rated_values = [int(f.rating_value) for f in search_feedback_records if f.rating_value is not None]
            avg_rating = sum(rated_values) / len(rated_values) if rated_values else 0.0
            return {
                "total_feedback": total,
                "approved_count": approved,
                "rejected_count": rejected,
                "approval_rate": round(approved / total, 3) if total else 0.0,
                "rejection_rate": round(rejected / total, 3) if total else 0.0,
                "average_rating": round(avg_rating, 2),
            }

        # Legacy fallback for historical deployments that only persisted `feedback`.
        legacy_result = await db.execute(
            select(Feedback)
            .join(Answer, Feedback.answer_id == Answer.id)
            .where(Answer.workspace_id == workspace_id)
        )
        feedback_records = legacy_result.scalars().all()
        if not feedback_records:
            return {
                "total_feedback": 0,
                "approved_count": 0,
                "rejected_count": 0,
                "approval_rate": 0.0,
                "rejection_rate": 0.0,
                "average_rating": 0.0,
            }

        total = len(feedback_records)
        approved = sum(1 for f in feedback_records if f.rating >= 4)
        rejected = total - approved
        avg_rating = sum(f.rating for f in feedback_records) / total
        return {
            "total_feedback": total,
            "approved_count": approved,
            "rejected_count": rejected,
            "approval_rate": round(approved / total, 3),
            "rejection_rate": round(rejected / total, 3),
            "average_rating": round(avg_rating, 2),
        }


_feedback_handler: Optional[FeedbackHandler] = None


def get_feedback_handler() -> FeedbackHandler:
    """Get or create feedback handler instance."""
    global _feedback_handler
    if _feedback_handler is None:
        _feedback_handler = FeedbackHandler()
    return _feedback_handler
