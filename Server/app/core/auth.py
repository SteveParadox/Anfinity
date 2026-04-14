"""Authentication and authorization dependencies."""
from typing import Annotated, Optional
from uuid import UUID

from fastapi import Depends, HTTPException, status, WebSocket
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from starlette.websockets import WebSocketState
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database.session import bind_db_user_context, get_db
from app.database.models import User as DBUser, Workspace, WorkspaceMember, WorkspaceRole
from app.core.security import get_token_payload

# Security scheme
security = HTTPBearer(auto_error=False)


def _can_close_websocket(websocket: WebSocket) -> bool:
    """Return True when the socket is still in a closeable state."""
    return (
        websocket.client_state != WebSocketState.DISCONNECTED
        and websocket.application_state != WebSocketState.DISCONNECTED
    )


# Role hierarchy for permission checking
ROLE_HIERARCHY = {
    WorkspaceRole.OWNER: 4,
    WorkspaceRole.ADMIN: 3,
    WorkspaceRole.MEMBER: 2,
    WorkspaceRole.VIEWER: 1,
}


def has_required_role(user_role: WorkspaceRole, required_role: WorkspaceRole) -> bool:
    """Check if user role meets required role level.
    
    Args:
        user_role: User's actual role
        required_role: Minimum required role
        
    Returns:
        True if user has sufficient permissions
    """
    return ROLE_HIERARCHY.get(user_role, 0) >= ROLE_HIERARCHY.get(required_role, 0)


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: AsyncSession = Depends(get_db)
) -> DBUser:
    """Get current authenticated user from JWT token.
    
    Args:
        credentials: HTTP Authorization header with Bearer token
        db: Database session
        
    Returns:
        Authenticated User object
        
    Raises:
        HTTPException: If authentication fails
    """
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    payload = get_token_payload(credentials.credentials)
    user_id = payload.get("sub")
    
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Get user from database
    try:
        parsed_user_id = UUID(str(user_id))
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
            headers={"WWW-Authenticate": "Bearer"},
        )

    result = await db.execute(
        select(DBUser).where(DBUser.id == parsed_user_id)
    )
    user = result.scalar_one_or_none()
    
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is disabled",
        )

    bind_db_user_context(db, user.id)
    
    return user


async def get_current_active_user(
    current_user: DBUser = Depends(get_current_user)
) -> DBUser:
    """Verify user is active.
    
    Args:
        current_user: Authenticated user
        
    Returns:
        Active user
    """
    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Inactive user"
        )
    return current_user


async def get_websocket_user(
    websocket: WebSocket,
    db: AsyncSession
) -> DBUser:
    """Get authenticated user from WebSocket connection.
    
    WebSocket clients pass the token via query parameter:
    - ws://host/path?token=JWT_TOKEN
    
    Args:
        websocket: WebSocket connection
        db: Database session
        
    Returns:
        Authenticated user
        
    Raises:
        Exception: If authentication fails
    """
    # Get token from query parameters
    token = websocket.query_params.get("token")
    
    if not token:
        if _can_close_websocket(websocket):
            await websocket.close(code=1008, reason="Unauthorized")
        raise Exception("Missing authentication token")
    
    try:
        payload = get_token_payload(token)
        user_id = payload.get("sub")
        
        if not user_id:
            if _can_close_websocket(websocket):
                await websocket.close(code=1008, reason="Unauthorized")
            raise Exception("Invalid token payload")

        try:
            parsed_user_id = UUID(str(user_id))
        except (TypeError, ValueError):
            if _can_close_websocket(websocket):
                await websocket.close(code=1008, reason="Unauthorized")
            raise Exception("Invalid token payload")
        
        # Get user from database
        result = await db.execute(
            select(DBUser).where(DBUser.id == parsed_user_id)
        )
        user = result.scalar_one_or_none()
        
        if not user:
            if _can_close_websocket(websocket):
                await websocket.close(code=1008, reason="Unauthorized")
            raise Exception("User not found")
        
        if not user.is_active:
            if _can_close_websocket(websocket):
                await websocket.close(code=1008, reason="Unauthorized")
            raise Exception("User account is disabled")
        
        return user
    except Exception as e:
        if _can_close_websocket(websocket):
            await websocket.close(code=1008, reason="Unauthorized")
        raise


class WorkspaceContext:
    """Context object for workspace membership."""
    
    def __init__(
        self,
        workspace_id: UUID,
        user: DBUser,
        role: WorkspaceRole,
        member_record: WorkspaceMember
    ):
        self.workspace_id = workspace_id
        self.user = user
        self.role = role
        self.member_record = member_record
    
    def require_role(self, required_role: WorkspaceRole) -> None:
        """Require minimum role level.
        
        Args:
            required_role: Minimum required role
            
        Raises:
            HTTPException: If user doesn't have required role
        """
        if not has_required_role(self.role, required_role):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Insufficient permissions. Required: {required_role.value}"
            )


async def get_workspace_context(
    workspace_id: UUID,
    user: Annotated[DBUser, Depends(get_current_active_user)],
    db: Annotated[AsyncSession, Depends(get_db)]
) -> WorkspaceContext:
    """Get workspace context for user.
    
    Args:
        workspace_id: Workspace UUID
        user: Authenticated user
        db: Database session
        
    Returns:
        WorkspaceContext with membership info
        
    Raises:
        HTTPException: If user is not a workspace member
    """
    workspace_result = await db.execute(
        select(Workspace.id).where(Workspace.id == workspace_id)
    )
    workspace_exists = workspace_result.scalar_one_or_none()

    if workspace_exists is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workspace not found"
        )

    result = await db.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user.id
        )
    )
    member = result.scalar_one_or_none()
    
    if member is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not a member of this workspace"
        )
    
    return WorkspaceContext(
        workspace_id=workspace_id,
        user=user,
        role=WorkspaceRole(member.role),
        member_record=member
    )


def require_role(required_role: WorkspaceRole):
    """Dependency factory for role-based access control.
    
    Args:
        required_role: Minimum required role
        
    Returns:
        Dependency function
    """
    async def role_checker(
        workspace_id: UUID,
        current_user: DBUser = Depends(get_current_active_user),
        db: AsyncSession = Depends(get_db)
    ) -> WorkspaceContext:
        context = await get_workspace_context(workspace_id, current_user, db)
        context.require_role(required_role)
        return context
    
    return role_checker


# Pre-defined role requirements
require_owner = require_role(WorkspaceRole.OWNER)
require_admin = require_role(WorkspaceRole.ADMIN)
require_member = require_role(WorkspaceRole.MEMBER)
require_viewer = require_role(WorkspaceRole.VIEWER)


class WorkspacePermission:
    """Permission checker for workspace operations."""
    
    def __init__(self, context: WorkspaceContext):
        self.context = context
    
    def can_upload(self) -> bool:
        """Check if user can upload documents."""
        return has_required_role(self.context.role, WorkspaceRole.MEMBER)
    
    def can_delete(self) -> bool:
        """Check if user can delete documents."""
        return has_required_role(self.context.role, WorkspaceRole.ADMIN)
    
    def can_invite(self) -> bool:
        """Check if user can invite members."""
        return has_required_role(self.context.role, WorkspaceRole.ADMIN)
    
    def can_manage_settings(self) -> bool:
        """Check if user can manage workspace settings."""
        return has_required_role(self.context.role, WorkspaceRole.ADMIN)
    
    def can_verify(self) -> bool:
        """Check if user can verify answers."""
        return has_required_role(self.context.role, WorkspaceRole.MEMBER)
