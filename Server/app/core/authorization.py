"""Authorization enforcement utilities for workspace and resource access control."""
from typing import Optional, Type, TypeVar
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_

from app.database.models import User as DBUser, WorkspaceMember, WorkspaceRole
from app.core.auth import WorkspaceContext, get_workspace_context

T = TypeVar("T")  # Generic model type


async def verify_resource_workspace_access(
    resource,
    user: DBUser,
    db: AsyncSession,
    required_role: WorkspaceRole = WorkspaceRole.MEMBER
) -> WorkspaceContext:
    """
    Verify user has access to a resource's workspace.
    
    This enforces the pattern:
    1. Verify workspace_id exists on resource
    2. Check user is a workspace member or owner
    3. Verify role meets minimum requirement
    
    Args:
        resource: Database model with workspace_id attribute
        user: Authenticated user
        db: Database session
        required_role: Minimum role required
        
    Returns:
        WorkspaceContext for further operations
        
    Raises:
        HTTPException(403): If user not in workspace or insufficient role
    """
    if not hasattr(resource, "workspace_id"):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Resource does not have workspace_id attribute"
        )
    
    context = await get_workspace_context(resource.workspace_id, user, db)
    context.require_role(required_role)
    return context


async def verify_workspace_query_isolation(
    workspace_id: UUID,
    user: DBUser,
    db: AsyncSession
) -> UUID:
    """
    Verify user has access to workspace and return workspace UUID for safe querying.
    
    Prevents:
    - User A querying User B's workspace
    - Invalid workspace IDs leaking information
    - Cross-workspace data access
    
    Args:
        workspace_id: Workspace UUID to verify
        user: Authenticated user
        db: Database session
        
    Returns:
        Verified workspace UUID for use in WHERE clauses
        
    Raises:
        HTTPException(403): If user not authorized for workspace
    """
    context = await get_workspace_context(workspace_id, user, db)
    return context.workspace_id


async def get_user_workspace_ids(user: DBUser, db: AsyncSession) -> list[UUID]:
    """
    Get all workspace IDs a user has access to.
    
    This includes:
    - Workspaces user owns (Workspace.owner_id = user.id)
    - Workspaces user is member of (WorkspaceMember.user_id = user.id)
    
    Args:
        user: Authenticated user
        db: Database session
        
    Returns:
        List of workspace UUIDs
    """
    # Get owned workspaces
    from app.database.models import Workspace
    
    owned_result = await db.execute(
        select(Workspace.id).where(Workspace.owner_id == user.id)
    )
    owned_ids = [row[0] for row in owned_result.all()]
    
    # Get member workspaces
    member_result = await db.execute(
        select(WorkspaceMember.workspace_id).where(WorkspaceMember.user_id == user.id)
    )
    member_ids = [row[0] for row in member_result.all()]
    
    return list(set(owned_ids + member_ids))


async def require_workspace_role(
    workspace_id: UUID,
    user: DBUser,
    required_role: WorkspaceRole,
    db: AsyncSession
) -> None:
    """
    Require user to have specific role in workspace.
    
    Raises HTTPException with appropriate status:
    - 403 Forbidden if user not in workspace
    - 403 Forbidden if insufficient role
    
    Args:
        workspace_id: Workspace UUID
        user: Authenticated user
        required_role: Minimum required role
        db: Database session
        
    Raises:
        HTTPException(403): If not authorized
    """
    context = await get_workspace_context(workspace_id, user, db)
    context.require_role(required_role)


def enforce_workspace_filter_in_query(
    query_filter_clause,
    workspace_id: UUID,
    model_class: Type[T]
) -> any:
    """
    Build a safe WHERE clause that always includes workspace_id filter.
    
    Prevents accidental queries that forget workspace filtering.
    
    Example:
        # Before (DANGEROUS):
        query = select(Document).where(Document.title.contains("secret"))
        
        # After (SAFE):
        filters = enforce_workspace_filter_in_query(
            Document.title.contains("secret"),
            workspace_id,
            Document
        )
        query = select(Document).where(filters)
    
    Args:
        query_filter_clause: Original WHERE clause
        workspace_id: Workspace to filter by
        model_class: Database model class
        
    Returns:
        Combined filter with workspace_id included
    """
    if not hasattr(model_class, "workspace_id"):
        raise ValueError(f"{model_class.__name__} does not have workspace_id attribute")
    
    return and_(
        query_filter_clause,
        model_class.workspace_id == workspace_id
    )
