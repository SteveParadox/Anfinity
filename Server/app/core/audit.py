"""Audit logging system for compliance and security."""
from enum import Enum
from datetime import datetime
from typing import Optional, Dict, Any, List
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc

from app.database.models import AuditLog


class AuditAction(str, Enum):
    """Audit action types."""
    # Auth actions
    USER_REGISTERED = "user_registered"
    USER_LOGIN = "user_login"
    USER_LOGOUT = "user_logout"
    PASSWORD_CHANGED = "password_changed"
    
    # Workspace actions
    WORKSPACE_CREATED = "workspace_created"
    WORKSPACE_UPDATED = "workspace_updated"
    WORKSPACE_DELETED = "workspace_deleted"
    MEMBER_INVITED = "member_invited"
    MEMBER_JOINED = "member_joined"
    MEMBER_REMOVED = "member_removed"
    ROLE_CHANGED = "role_changed"
    
    # Document actions
    DOCUMENT_UPLOADED = "document_uploaded"
    DOCUMENT_DELETED = "document_deleted"
    DOCUMENT_PROCESSED = "document_processed"
    DOCUMENT_FAILED = "document_failed"
    
    # Note actions
    NOTE_CREATED = "note_created"
    NOTE_UPDATED = "note_updated"
    NOTE_DELETED = "note_deleted"
    
    # Query actions
    QUERY_EXECUTED = "query_executed"
    ANSWER_GENERATED = "answer_generated"
    
    # Verification actions
    ANSWER_VERIFIED = "answer_verified"
    ANSWER_REJECTED = "answer_rejected"
    ANSWER_FAILED = "answer_failed"
    
    # Feedback actions
    FEEDBACK_SUBMITTED = "feedback_submitted"
    
    # Connector actions
    CONNECTOR_CREATED = "connector_created"
    CONNECTOR_UPDATED = "connector_updated"
    CONNECTOR_DELETED = "connector_deleted"
    CONNECTOR_SYNCED = "connector_synced"


class EntityType(str, Enum):
    """Entity types for audit logging."""
    USER = "user"
    WORKSPACE = "workspace"
    DOCUMENT = "document"
    NOTE = "note"
    CHUNK = "chunk"
    QUERY = "query"
    ANSWER = "answer"
    CONNECTOR = "connector"


async def log_audit_event(
    db: AsyncSession,
    action: AuditAction,
    user_id: Optional[UUID],
    workspace_id: Optional[UUID] = None,
    entity_type: Optional[EntityType] = None,
    entity_id: Optional[UUID] = None,
    metadata: Optional[Dict[str, Any]] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None
) -> AuditLog:
    """Log an audit event.
    
    Args:
        db: Database session
        action: Action type
        user_id: User who performed the action
        workspace_id: Workspace context
        entity_type: Type of entity affected
        entity_id: ID of entity affected
        metadata: Additional context
        ip_address: Client IP address
        user_agent: Client user agent
        
    Returns:
        Created audit log entry
    """
    audit_log = AuditLog(
        user_id=user_id,
        workspace_id=workspace_id,
        action=action.value,
        entity_type=entity_type.value if entity_type else None,
        entity_id=entity_id,
        audit_metadata=metadata or {},
        ip_address=ip_address,
        user_agent=user_agent
    )
    
    db.add(audit_log)
    await db.commit()
    await db.refresh(audit_log)
    
    return audit_log


async def get_audit_logs(
    db: AsyncSession,
    workspace_id: Optional[UUID] = None,
    user_id: Optional[UUID] = None,
    action: Optional[AuditAction] = None,
    entity_type: Optional[EntityType] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    limit: int = 100,
    offset: int = 0
) -> List[AuditLog]:
    """Get audit logs with filters.
    
    Args:
        db: Database session
        workspace_id: Filter by workspace
        user_id: Filter by user
        action: Filter by action type
        entity_type: Filter by entity type
        start_date: Filter by start date
        end_date: Filter by end date
        limit: Maximum results
        offset: Pagination offset
        
    Returns:
        List of audit log entries
    """
    query = select(AuditLog)
    
    if workspace_id:
        query = query.where(AuditLog.workspace_id == workspace_id)
    
    if user_id:
        query = query.where(AuditLog.user_id == user_id)
    
    if action:
        query = query.where(AuditLog.action == action.value)
    
    if entity_type:
        query = query.where(AuditLog.entity_type == entity_type.value)
    
    if start_date:
        query = query.where(AuditLog.created_at >= start_date)
    
    if end_date:
        query = query.where(AuditLog.created_at <= end_date)
    
    query = query.order_by(desc(AuditLog.created_at))
    query = query.offset(offset).limit(limit)
    
    result = await db.execute(query)
    return result.scalars().all()


async def get_recent_activity(
    db: AsyncSession,
    workspace_id: UUID,
    limit: int = 20
) -> List[AuditLog]:
    """Get recent activity for a workspace.
    
    Args:
        db: Database session
        workspace_id: Workspace ID
        limit: Maximum results
        
    Returns:
        List of recent audit logs
    """
    return await get_audit_logs(
        db=db,
        workspace_id=workspace_id,
        limit=limit
    )


class AuditLogger:
    """Helper class for structured audit logging."""
    
    def __init__(self, db: AsyncSession, user_id: Optional[UUID] = None):
        self.db = db
        self.user_id = user_id
        self.ip_address = None
        self.user_agent = None
    
    def with_request(self, request) -> "AuditLogger":
        """Attach request context."""
        self.ip_address = getattr(request, "client", None)
        if self.ip_address:
            self.ip_address = self.ip_address.host
        self.user_agent = request.headers.get("user-agent")
        return self
    
    async def log(
        self,
        action: AuditAction,
        workspace_id: Optional[UUID] = None,
        entity_type: Optional[EntityType] = None,
        entity_id: Optional[UUID] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> AuditLog:
        """Log an audit event."""
        return await log_audit_event(
            db=self.db,
            action=action,
            user_id=self.user_id,
            workspace_id=workspace_id,
            entity_type=entity_type,
            entity_id=entity_id,
            metadata=metadata,
            ip_address=self.ip_address,
            user_agent=self.user_agent
        )
    
    # Convenience methods
    async def log_upload(
        self,
        workspace_id: UUID,
        document_id: UUID,
        filename: str
    ) -> AuditLog:
        """Log document upload."""
        return await self.log(
            action=AuditAction.DOCUMENT_UPLOADED,
            workspace_id=workspace_id,
            entity_type=EntityType.DOCUMENT,
            entity_id=document_id,
            metadata={"filename": filename}
        )
    
    async def log_delete(
        self,
        workspace_id: UUID,
        document_id: UUID,
        title: str
    ) -> AuditLog:
        """Log document deletion."""
        return await self.log(
            action=AuditAction.DOCUMENT_DELETED,
            workspace_id=workspace_id,
            entity_type=EntityType.DOCUMENT,
            entity_id=document_id,
            metadata={"title": title}
        )
    
    async def log_query(
        self,
        workspace_id: UUID,
        query: str,
        result_count: int
    ) -> AuditLog:
        """Log search query."""
        return await self.log(
            action=AuditAction.QUERY_EXECUTED,
            workspace_id=workspace_id,
            entity_type=EntityType.QUERY,
            metadata={
                "query": query,
                "result_count": result_count
            }
        )
    
    async def log_verification(
        self,
        workspace_id: UUID,
        answer_id: UUID,
        verified: bool,
        comment: Optional[str] = None
    ) -> AuditLog:
        """Log answer verification."""
        return await self.log(
            action=AuditAction.ANSWER_VERIFIED if verified else AuditAction.ANSWER_REJECTED,
            workspace_id=workspace_id,
            entity_type=EntityType.ANSWER,
            entity_id=answer_id,
            metadata={
                "verified": verified,
                "comment": comment
            }
        )
