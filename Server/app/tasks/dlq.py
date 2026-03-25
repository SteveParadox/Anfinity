"""Dead Letter Queue (DLQ) handler for failed Celery tasks.

Manages tasks that have exhausted all retries. Provides:
- Persistent DLQ storage in database
- Monitoring and alerting
- Manual retry capabilities
- Analysis of failure patterns
"""

import logging
from datetime import datetime
from typing import Optional, List
from uuid import UUID
from enum import Enum

from sqlalchemy import Column, String, DateTime, Integer, Text, Enum as SQLEnum, select
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, JSONB
from app.database.session import SyncSessionLocal, AsyncSessionLocal
from app.database.models import Base
from app.celery_app import celery_app

logger = logging.getLogger(__name__)


class DLQStatus(str, Enum):
    """Status of a dead letter queue item."""
    PENDING = "pending"  # New, not yet reviewed
    REVIEWED = "reviewed"  # Admin reviewed, awaiting action
    IN_RETRY = "in_retry"  # Being retried
    RESOLVED = "resolved"  # Successfully resolved
    ARCHIVED = "archived"  # Archived without resolution
    POISONED = "poisoned"  # Permanently failed, cannot be fixed


class DeadLetter(Base):
    """Model for storing failed tasks."""
    __tablename__ = "dead_letters"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=lambda: UUID(int=0))
    task_name = Column(String(255), nullable=False, index=True)
    task_id = Column(String(255), nullable=False, index=True)
    document_id = Column(PG_UUID(as_uuid=True), nullable=True, index=True)
    workspace_id = Column(PG_UUID(as_uuid=True), nullable=True, index=True)
    
    # Error details
    error_type = Column(String(255), nullable=False)
    error_message = Column(Text, nullable=False)
    traceback = Column(Text, nullable=True)
    
    # Task details
    args = Column(JSONB, nullable=True)  # Task arguments
    kwargs = Column(JSONB, nullable=True)  # Task keyword arguments
    
    # Status tracking
    status = Column(SQLEnum(DLQStatus), default=DLQStatus.PENDING, index=True)
    retry_count = Column(Integer, default=0)
    
    # Timestamps
    failed_at = Column(DateTime, default=datetime.utcnow, index=True)
    reviewed_at = Column(DateTime, nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    
    # Admin notes
    admin_notes = Column(Text, nullable=True)
    resolution_notes = Column(Text, nullable=True)


class DLQManager:
    """Manages the Dead Letter Queue."""

    @staticmethod
    def add_failed_task(
        task_name: str,
        task_id: str,
        error_type: str,
        error_message: str,
        traceback: Optional[str] = None,
        document_id: Optional[UUID] = None,
        workspace_id: Optional[UUID] = None,
        args: Optional[dict] = None,
        kwargs: Optional[dict] = None,
    ) -> DeadLetter:
        """Add a failed task to the DLQ.
        
        Args:
            task_name: Name of the task (e.g., 'process_document')
            task_id: Celery task ID
            error_type: Type of error (e.g., 'ValueError')
            error_message: Error message
            traceback: Full traceback (optional)
            document_id: Associated document ID (optional)
            workspace_id: Associated workspace ID (optional)
            args: Task positional arguments
            kwargs: Task keyword arguments
        
        Returns:
            The created DeadLetter record
        """
        db = SyncSessionLocal()
        try:
            dead_letter = DeadLetter(
                task_name=task_name,
                task_id=task_id,
                document_id=document_id,
                workspace_id=workspace_id,
                error_type=error_type,
                error_message=error_message,
                traceback=traceback,
                args=args or {},
                kwargs=kwargs or {},
                status=DLQStatus.PENDING,
            )
            db.add(dead_letter)
            db.commit()
            logger.warning(
                f"📨 Task {task_name}[{task_id}] added to DLQ: {error_message}"
            )
            return dead_letter
        finally:
            db.close()

    @staticmethod
    def get_pending_items(limit: int = 100) -> List[DeadLetter]:
        """Get pending DLQ items awaiting review.
        
        Args:
            limit: Maximum number of items to return
        
        Returns:
            List of pending dead letters
        """
        db = SyncSessionLocal()
        try:
            items = db.query(DeadLetter)\
                .filter(DeadLetter.status == DLQStatus.PENDING)\
                .order_by(DeadLetter.failed_at.desc())\
                .limit(limit)\
                .all()
            return items
        finally:
            db.close()

    @staticmethod
    def get_by_document(document_id: UUID, limit: int = 50) -> List[DeadLetter]:
        """Get all DLQ items for a specific document.
        
        Args:
            document_id: Document UUID
            limit: Maximum number of items to return
        
        Returns:
            List of dead letters for the document
        """
        db = SyncSessionLocal()
        try:
            items = db.query(DeadLetter)\
                .filter(DeadLetter.document_id == document_id)\
                .order_by(DeadLetter.failed_at.desc())\
                .limit(limit)\
                .all()
            return items
        finally:
            db.close()

    @staticmethod
    def mark_reviewed(dlq_id: UUID, admin_notes: str = ""):
        """Mark a DLQ item as reviewed.
        
        Args:
            dlq_id: DeadLetter record ID
            admin_notes: Notes from admin
        """
        db = SyncSessionLocal()
        try:
            item = db.query(DeadLetter).filter(DeadLetter.id == dlq_id).first()
            if item:
                item.status = DLQStatus.REVIEWED
                item.reviewed_at = datetime.utcnow()
                item.admin_notes = admin_notes
                db.commit()
                logger.info(f"DLQ item {dlq_id} marked as reviewed")
        finally:
            db.close()

    @staticmethod
    def mark_resolved(dlq_id: UUID, resolution_notes: str = ""):
        """Mark a DLQ item as resolved.
        
        Args:
            dlq_id: DeadLetter record ID
            resolution_notes: Notes on how it was resolved
        """
        db = SyncSessionLocal()
        try:
            item = db.query(DeadLetter).filter(DeadLetter.id == dlq_id).first()
            if item:
                item.status = DLQStatus.RESOLVED
                item.resolved_at = datetime.utcnow()
                item.resolution_notes = resolution_notes
                db.commit()
                logger.info(f"DLQ item {dlq_id} marked as resolved")
        finally:
            db.close()

    @staticmethod
    def get_failure_stats() -> dict:
        """Get statistics on DLQ failures.
        
        Returns:
            Dictionary with failure statistics
        """
        db = SyncSessionLocal()
        try:
            from sqlalchemy import func
            
            total = db.query(func.count(DeadLetter.id)).scalar()
            by_status = {}
            for status in DLQStatus:
                count = db.query(func.count(DeadLetter.id))\
                    .filter(DeadLetter.status == status).scalar()
                by_status[status.value] = count
            
            # Most common errors
            most_common = db.query(
                DeadLetter.error_type,
                func.count(DeadLetter.id).label('count')
            ).group_by(DeadLetter.error_type)\
                .order_by(func.count(DeadLetter.id).desc())\
                .limit(5)\
                .all()
            
            # Most affected tasks
            most_affected_tasks = db.query(
                DeadLetter.task_name,
                func.count(DeadLetter.id).label('count')
            ).group_by(DeadLetter.task_name)\
                .order_by(func.count(DeadLetter.id).desc())\
                .limit(5)\
                .all()
            
            return {
                "total_failed": total,
                "by_status": by_status,
                "most_common_errors": [
                    {"error_type": et, "count": c}
                    for et, c in most_common
                ],
                "most_affected_tasks": [
                    {"task_name": tn, "count": c}
                    for tn, c in most_affected_tasks
                ],
            }
        finally:
            db.close()


@celery_app.task
def move_to_dlq(
    task_name: str,
    task_id: str,
    error_type: str,
    error_message: str,
    traceback: Optional[str] = None,
    document_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
):
    """Celery task to add a failed task to the DLQ.
    
    Called when a task has exhausted all retries.
    """
    logger.info("💀 [TASK START] move_to_dlq - Task: %s (ID: %s), Error Type: %s, Document: %s", task_name, task_id, error_type, document_id or "N/A")
    
    try:
        logger.debug("📝 [DLQ ENTRY] Creating DLQ entry for failed task")
        logger.debug("📋 [ERROR DETAILS] Error Type: %s, Message: %s", error_type, error_message[:100])
        
        result = DLQManager.add_failed_task(
            task_name=task_name,
            task_id=task_id,
            error_type=error_type,
            error_message=error_message,
            traceback=traceback,
            document_id=UUID(document_id) if document_id else None,
            workspace_id=UUID(workspace_id) if workspace_id else None,
        )
        
        logger.info("✅ [TASK SUCCESS] move_to_dlq completed - Task: %s, DLQ Entry ID: %s", task_name, result.id if result else "Unknown")
        return {"status": "success", "dlq_entry_id": str(result.id) if result else None}
    except Exception as exc:
        logger.error("❌ [TASK ERROR] move_to_dlq failed - Task: %s (ID: %s) - Error: %s", task_name, task_id, exc, exc_info=True)
        return {"status": "failed", "task_id": task_id, "error": str(exc)}
