"""STEP 8: Feedback Loop — Chunk weight tracking and confidence recalibration."""

import logging
from typing import List, Dict, Any, Optional
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update

from app.database.models import (
    Answer, Feedback, ChunkWeight, Document
)

logger = logging.getLogger(__name__)


class FeedbackHandler:
    """
    STEP 8: Processes user feedback and updates chunk credibility scores.
    
    Feedback Loop:
    1. User marks answer as verified/rejected
    2. Extract source chunks
    3. Update chunk weights based on correctness
    4. Recalibrate confidence for future answers
    5. Log for model evaluation
    """
    
    def __init__(self):
        """Initialize feedback handler."""
        self.positive_multiplier = 1.1  # Increase weight by 10% for correct
        self.negative_multiplier = 0.9  # Decrease weight by 10% for incorrect
        self.min_weight = 0.1  # Minimum credibility score
        self.max_weight = 2.0  # Maximum credibility score
    
    async def process_answer_feedback(
        self,
        answer_id: UUID,
        feedback_status: str,  # "verified" or "rejected"
        comment: Optional[str],
        user_id: UUID,
        db: AsyncSession
    ) -> Dict[str, Any]:
        """
        Process feedback on an answer and update chunk weights.
        
        Args:
            answer_id: Answer to provide feedback on
            feedback_status: "verified" (approved) or "rejected"
            comment: Optional user comment
            user_id: User providing feedback
            db: Database session
            
        Returns:
            Feedback processing result with chunk updates
        """
        
        try:
            # Get answer with sources
            result = await db.execute(
                select(Answer).where(Answer.id == answer_id)
            )
            answer = result.scalar_one_or_none()
            
            if not answer:
                raise ValueError(f"Answer {answer_id} not found")
            
            logger.info(
                f"STEP 8: Processing feedback for answer {answer_id}: "
                f"status={feedback_status}, user={user_id}"
            )
            
            # Extract chunks from answer sources
            chunks_updated = []
            if answer.sources:
                is_correct = feedback_status == "verified"
                
                for source in answer.sources:
                    chunk_update = await self._update_chunk_weight(
                        chunk_id=source.get("chunk_id"),
                        document_id=source.get("document_id"),
                        workspace_id=answer.workspace_id,
                        is_correct=is_correct,
                        db=db
                    )
                    chunks_updated.append(chunk_update)
            
            # Store feedback record
            feedback = Feedback(
                answer_id=answer_id,
                workspace_id=answer.workspace_id,
                user_id=user_id,
                rating=5 if feedback_status == "verified" else 1,  # 5 stars for verified, 1 for rejected
                comment=comment
            )
            db.add(feedback)
            
            # Update answer verification status
            answer.verification_status = feedback_status
            answer.verified_by = user_id
            answer.verification_comment = comment
            
            await db.commit()
            
            logger.info(
                f"STEP 8: Feedback processed. Updated {len(chunks_updated)} chunks. "
                f"Status: {feedback_status}"
            )
            
            return {
                "answer_id": str(answer_id),
                "feedback_status": feedback_status,
                "chunks_updated": chunks_updated,
                "confidence_change": await self._calculate_confidence_impact(
                    chunks_updated, is_correct
                )
            }
            
        except Exception as e:
            logger.error(f"Error processing feedback for answer {answer_id}: {str(e)}", exc_info=True)
            raise
    
    async def _update_chunk_weight(
        self,
        chunk_id: str,
        document_id: str,
        workspace_id: UUID,
        is_correct: bool,
        db: AsyncSession
    ) -> Dict[str, Any]:
        """
        Update chunk weight based on feedback.
        
        Args:
            chunk_id: Chunk identifier
            document_id: Document identifier
            workspace_id: Workspace identifier
            is_correct: True if feedback was positive
            db: Database session
            
        Returns:
            Update details with old/new weights
        """
        
        # Try to get existing weight
        result = await db.execute(
            select(ChunkWeight).where(
                (ChunkWeight.chunk_id == chunk_id) &
                (ChunkWeight.document_id == document_id) &
                (ChunkWeight.workspace_id == workspace_id)
            )
        )
        chunk_weight = result.scalar_one_or_none()
        
        if not chunk_weight:
            # Create new weight record
            chunk_weight = ChunkWeight(
                chunk_id=chunk_id,
                document_id=document_id,
                workspace_id=workspace_id,
                credibility_score=1.0,
                positive_feedback_count=1 if is_correct else 0,
                negative_feedback_count=0 if is_correct else 1,
                total_uses=1,
                accuracy_rate=1.0 if is_correct else 0.0
            )
            db.add(chunk_weight)
            
            logger.debug(f"Created new chunk weight for {chunk_id}")
            
            old_weight = 1.0
            await db.flush()
        else:
            # Update existing weight
            old_weight = chunk_weight.credibility_score
            
            # Update counters
            if is_correct:
                chunk_weight.positive_feedback_count += 1
            else:
                chunk_weight.negative_feedback_count += 1
            
            chunk_weight.total_uses += 1
            
            # Recalculate accuracy
            total_feedback = chunk_weight.positive_feedback_count + chunk_weight.negative_feedback_count
            chunk_weight.accuracy_rate = chunk_weight.positive_feedback_count / total_feedback if total_feedback > 0 else 0.5
            
            # Update credibility score based on feedback
            multiplier = self.positive_multiplier if is_correct else self.negative_multiplier
            new_weight = chunk_weight.credibility_score * multiplier
            
            # Clamp to bounds
            new_weight = max(self.min_weight, min(new_weight, self.max_weight))
            chunk_weight.credibility_score = new_weight
        
        await db.commit()
        await db.refresh(chunk_weight)
        
        logger.debug(
            f"Updated chunk weight {chunk_id}: "
            f"{old_weight:.3f} → {chunk_weight.credibility_score:.3f}, "
            f"accuracy: {chunk_weight.accuracy_rate:.1%}"
        )
        
        return {
            "chunk_id": chunk_id,
            "document_id": str(document_id),
            "old_weight": round(old_weight, 3),
            "new_weight": round(chunk_weight.credibility_score, 3),
            "accuracy": round(chunk_weight.accuracy_rate, 3),
            "positive_count": chunk_weight.positive_feedback_count,
            "negative_count": chunk_weight.negative_feedback_count,
            "total_uses": chunk_weight.total_uses
        }
    
    async def _calculate_confidence_impact(
        self,
        chunks_updated: List[Dict[str, Any]],
        is_correct: bool
    ) -> float:
        """
        Calculate confidence change impact from chunk updates.
        
        Args:
            chunks_updated: List of updated chunks
            is_correct: Whether feedback was positive
            
        Returns:
            Estimated confidence change percentage
        """
        
        if not chunks_updated:
            return 0.0
        
        # Average weight change across all chunks
        weight_changes = [
            chunk["new_weight"] / chunk["old_weight"]
            for chunk in chunks_updated
        ]
        avg_change = sum(weight_changes) / len(weight_changes)
        
        # Confidence change is proportional to weight change
        confidence_impact = (avg_change - 1.0) * 10  # Scale to percentage
        
        return round(confidence_impact, 2)
    
    async def get_chunk_credibility_scores(
        self,
        workspace_id: UUID,
        db: AsyncSession,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Get chunks sorted by credibility score.
        
        Args:
            workspace_id: Workspace identifier
            db: Database session
            limit: Number of results to return
            
        Returns:
            List of chunks with credibility scores
        """
        
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
                "updated_at": w.updated_at.isoformat() if w.updated_at else None
            }
            for w in weights
        ]
    
    async def get_model_evaluation_metrics(
        self,
        workspace_id: UUID,
        db: AsyncSession
    ) -> Dict[str, Any]:
        """
        Get model evaluation metrics from feedback.
        
        Args:
            workspace_id: Workspace identifier
            db: Database session
            
        Returns:
            Evaluation metrics for model performance
        """
        
        # Get all feedback for workspace
        result = await db.execute(
            select(Feedback)
            .join(Answer, Feedback.answer_id == Answer.id)
            .where(Answer.workspace_id == workspace_id)
        )
        
        feedback_records = result.scalars().all()
        
        if not feedback_records:
            return {
                "total_feedback": 0,
                "approval_rate": 0.0,
                "rejection_rate": 0.0,
                "average_rating": 0.0
            }
        
        # Calculate metrics
        total = len(feedback_records)
        approved = sum(1 for f in feedback_records if f.rating >= 4)  # 4-5 stars
        rejected = total - approved
        avg_rating = sum(f.rating for f in feedback_records) / total
        
        return {
            "total_feedback": total,
            "approved_count": approved,
            "rejected_count": rejected,
            "approval_rate": round(approved / total, 3),
            "rejection_rate": round(rejected / total, 3),
            "average_rating": round(avg_rating, 2)
        }


# Singleton instance
_feedback_handler: Optional[FeedbackHandler] = None


def get_feedback_handler() -> FeedbackHandler:
    """Get or create feedback handler instance."""
    global _feedback_handler
    if _feedback_handler is None:
        _feedback_handler = FeedbackHandler()
    return _feedback_handler
