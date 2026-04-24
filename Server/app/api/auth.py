"""Authentication API routes."""
import logging
from datetime import timedelta
from typing import Optional, List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status, Request, BackgroundTasks
from fastapi.security import HTTPBearer
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, select

from app.database.session import bind_db_user_context, get_db
from app.database.models import User as DBUser, Workspace, WorkspaceMember, WorkspaceRole
from app.core.security import (
    verify_password,
    get_password_hash,
    create_access_token,
    decode_token,
    validate_password_strength,
)
from app.core.auth import get_current_user, get_current_active_user
from app.core.audit import AuditAction, AuditRequestContext, EntityType, audit, log_audit_event
from app.ingestion.vector_index import vector_index

router = APIRouter(prefix="/auth", tags=["Authentication"])
security = HTTPBearer()
logger = logging.getLogger(__name__)


def _initialize_workspace_vector_collection(workspace_id: str) -> None:
    """Initialize per-workspace vector storage without blocking auth flows."""
    try:
        vector_index.create_collection(workspace_id)
    except Exception:
        logger.warning(
            "Default workspace %s created but vector collection initialization failed",
            workspace_id,
            exc_info=True,
        )


async def get_user_workspaces(user: DBUser, db: AsyncSession) -> List[dict]:
    """Get all workspaces for a user with their roles.
    
    Args:
        user: User object
        db: Database session
        
    Returns:
        List of workspace info with roles
    """
    member_count_subquery = (
        select(
            WorkspaceMember.workspace_id.label("workspace_id"),
            func.count(WorkspaceMember.id).label("member_count"),
        )
        .group_by(WorkspaceMember.workspace_id)
        .subquery()
    )

    result = await db.execute(
        select(
            WorkspaceMember,
            Workspace,
            func.coalesce(member_count_subquery.c.member_count, 0).label("member_count"),
        )
        .join(Workspace, Workspace.id == WorkspaceMember.workspace_id)
        .outerjoin(member_count_subquery, member_count_subquery.c.workspace_id == Workspace.id)
        .where(WorkspaceMember.user_id == user.id)
        .order_by(Workspace.created_at.desc())
    )

    workspaces = []
    seen_workspace_ids: set[str] = set()

    for member, ws, member_count in result.all():
        workspace_id = str(ws.id)
        if workspace_id in seen_workspace_ids:
            continue
        seen_workspace_ids.add(workspace_id)
        workspaces.append({
            "id": workspace_id,
            "name": ws.name,
            "role": member.role.value if isinstance(member.role, WorkspaceRole) else member.role,
            "member_count": int(member_count or 0),
        })

    return workspaces


# Schemas
class UserRegister(BaseModel):
    """User registration schema."""
    email: EmailStr
    password: str = Field(..., min_length=10, max_length=128)
    full_name: Optional[str] = Field(None, max_length=255)


class UserLogin(BaseModel):
    """User login schema."""
    email: EmailStr
    password: str


class ChangePassword(BaseModel):
    """Change password schema."""
    old_password: str
    new_password: str = Field(..., min_length=10, max_length=128)


class TokenResponse(BaseModel):
    """Token response schema."""
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: dict
    workspaces: Optional[List[dict]] = None  # User's workspaces with roles


class UserResponse(BaseModel):
    """User response schema."""
    id: str
    email: str
    full_name: Optional[str]
    is_active: bool
    created_at: str


class WorkspaceInfo(BaseModel):
    """Workspace info with user's role."""
    id: str
    name: str
    role: str  # owner, admin, member, viewer
    member_count: int = 0


class UserWorkspacesResponse(BaseModel):
    """User workspaces response."""
    workspaces: List[WorkspaceInfo]


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(
    user_data: UserRegister,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    """Register a new user.
    
    Creates user account and a default workspace.
    
    Args:
        user_data: Registration data
        request: FastAPI request object
        db: Database session
        
    Returns:
        JWT token, user info, and workspaces
    """
    # Check if user already exists
    result = await db.execute(
        select(DBUser).where(DBUser.email == user_data.email)
    )
    existing_user = result.scalar_one_or_none()
    
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered"
        )
    
    # Create new user
    try:
        hashed_password = get_password_hash(user_data.password)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    
    user = DBUser(
        email=user_data.email,
        hashed_password=hashed_password,
        full_name=user_data.full_name,
        is_active=True  # Changed from integer to boolean
    )
    
    db.add(user)
    await db.flush()
    bind_db_user_context(db, user.id)

    # Create default workspace
    default_workspace = Workspace(
        name=f"{user_data.full_name or user_data.email}'s Workspace",
        owner_id=user.id
    )
    db.add(default_workspace)
    await db.flush()

    # Create WorkspaceMember record with OWNER role (RBAC)
    owner_member = WorkspaceMember(
        workspace_id=default_workspace.id,
        user_id=user.id,
        role=WorkspaceRole.OWNER
    )
    db.add(owner_member)
    await db.flush()
    await db.refresh(user)
    await db.refresh(default_workspace)

    background_tasks.add_task(_initialize_workspace_vector_collection, str(default_workspace.id))
    
    # Log audit event
    await log_audit_event(
        db=db,
        action=AuditAction.USER_REGISTERED,
        user_id=user.id,
        entity_type=EntityType.USER,
        entity_id=user.id,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent")
    )
    
    # Get workspaces
    workspaces = await get_user_workspaces(user, db)
    
    # Create access token
    access_token = create_access_token(
        data={"sub": str(user.id), "email": user.email}
    )
    
    return TokenResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in=3600 * 24,  # 24 hours
        user={
            "id": str(user.id),
            "email": user.email,
            "full_name": user.full_name
        },
        workspaces=workspaces
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    credentials: UserLogin,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """Login and get access token.
    
    Args:
        credentials: Login credentials
        request: FastAPI request object
        db: Database session
        
    Returns:
        JWT token, user info, and workspaces
    """
    # Find user by email
    result = await db.execute(
        select(DBUser).where(DBUser.email == credentials.email)
    )
    user = result.scalar_one_or_none()
    
    # Verify credentials
    if not user or not verify_password(credentials.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Check if user is active
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is disabled"
        )
    
    # Log audit event
    await log_audit_event(
        db=db,
        action=AuditAction.USER_LOGIN,
        user_id=user.id,
        entity_type=EntityType.USER,
        entity_id=user.id,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent")
    )
    
    # Get workspaces
    workspaces = await get_user_workspaces(user, db)
    
    # Create access token
    access_token = create_access_token(
        data={"sub": str(user.id), "email": user.email}
    )
    
    return TokenResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in=3600 * 24,  # 24 hours
        user={
            "id": str(user.id),
            "email": user.email,
            "full_name": user.full_name
        },
        workspaces=workspaces
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    request: Request,
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """Refresh access token.
    
    Args:
        request: FastAPI request object
        current_user: Authenticated user
        db: Database session
        
    Returns:
        JWT token and user info
    """
    # Create new access token
    access_token = create_access_token(
        data={"sub": str(current_user.id), "email": current_user.email}
    )

    workspaces = await get_user_workspaces(current_user, db)
    
    return TokenResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in=3600 * 24,  # 24 hours
        user={
            "id": str(current_user.id),
            "email": current_user.email,
            "full_name": current_user.full_name
        },
        workspaces=workspaces
    )


@router.get("/me", response_model=UserResponse)
async def get_me(
    current_user: DBUser = Depends(get_current_active_user)
):
    """Get current user info.
    
    Args:
        current_user: Authenticated user
        
    Returns:
        User info
    """
    return UserResponse(
        id=str(current_user.id),
        email=current_user.email,
        full_name=current_user.full_name,
        is_active=bool(current_user.is_active),
        created_at=current_user.created_at.isoformat() if current_user.created_at else None
    )


@router.post("/logout")
async def logout(
    request: Request,
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """Logout user (client should discard token).
    
    Args:
        request: FastAPI request object
        current_user: Authenticated user
        db: Database session
        
    Returns:
        Success message
    """
    # Log audit event
    await log_audit_event(
        db=db,
        action=AuditAction.USER_LOGOUT,
        user_id=current_user.id,
        entity_type=EntityType.USER,
        entity_id=current_user.id,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent")
    )
    
    return {"message": "Successfully logged out"}


@router.post("/change-password")
async def change_password(
    password_data: ChangePassword,
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """Change user password.
    
    Args:
        password_data: Old and new password
        current_user: Authenticated user
        db: Database session
        
    Returns:
        Success message
    """
    # Verify old password
    if not verify_password(password_data.old_password, current_user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Incorrect current password"
        )

    if password_data.old_password == password_data.new_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password must be different from the current password"
        )
    
    # Update password
    try:
        validate_password_strength(password_data.new_password)
        current_user.hashed_password = get_password_hash(password_data.new_password)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc)
        ) from exc
    await db.flush()
    
    # Log audit event
    await log_audit_event(
        db=db,
        action=AuditAction.PASSWORD_CHANGED,
        user_id=current_user.id,
        entity_type=EntityType.USER,
        entity_id=current_user.id
    )
    
    return {"message": "Password changed successfully"}


@router.get("/workspaces", response_model=UserWorkspacesResponse)
async def get_user_workspaces_endpoint(
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """Get all workspaces for current user.
    
    Args:
        current_user: Authenticated user
        db: Database session
        
    Returns:
        List of workspaces with user's role
    """
    workspaces = await get_user_workspaces(current_user, db)
    return UserWorkspacesResponse(workspaces=[
        WorkspaceInfo(**ws) for ws in workspaces
    ])

class InviteMemberRequest(BaseModel):
    email: EmailStr
    role: str = Field("member", pattern="^(owner|admin|member|viewer)$")


@router.post("/workspaces/{workspace_id}/invite")
async def invite_member(
    workspace_id: UUID,
    invite_data: InviteMemberRequest,
    request: Request,
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """Invite a member to workspace.
    
    Args:
        workspace_id: Target workspace
        invite_data: Email and role for new member
        current_user: Authenticated user (must be admin/owner)
        db: Database session
        
    Returns:
        Success message
    """
    from app.core.auth import get_workspace_context, WorkspaceRole
    
    # Verify workspace exists
    workspace_result = await db.execute(
        select(Workspace).where(Workspace.id == workspace_id)
    )
    workspace = workspace_result.scalar_one_or_none()
    
    if not workspace:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workspace not found"
        )
    
    # Check permissions (require admin or owner)
    context = await get_workspace_context(workspace_id, current_user, db)
    context.require_role(WorkspaceRole.ADMIN)
    
    # Check if user already exists
    user_result = await db.execute(
        select(DBUser).where(DBUser.email == invite_data.email)
    )
    existing_user = user_result.scalar_one_or_none()
    
    # Convert role string to enum
    try:
        role = WorkspaceRole[invite_data.role.upper()]
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid role: {invite_data.role}"
        )
    
    if existing_user:
        # Check if already a member
        member_result = await db.execute(
            select(WorkspaceMember).where(
                (WorkspaceMember.workspace_id == workspace_id) &
                (WorkspaceMember.user_id == existing_user.id)
            )
        )
        existing_member = member_result.scalar_one_or_none()
        
        if existing_member:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="User is already a member of this workspace"
            )
        
        # Add as workspace member
        member = WorkspaceMember(
            workspace_id=workspace_id,
            user_id=existing_user.id,
            role=role
        )
        db.add(member)
        await db.flush()
        await audit.member_invited(
            db,
            actor_user_id=current_user.id,
            workspace_id=workspace_id,
            target_user_id=existing_user.id,
            metadata={
                "invited_email": invite_data.email,
                "role": role.value,
                "source": "api.auth.invite_member",
            },
            context=AuditRequestContext.from_request(request, source="api.auth.invite_member"),
        )
        
        return {
            "message": f"User invited to workspace",
            "email": invite_data.email,
            "role": role.value
        }
    else:
        # Return invitation details for signup flow
        # In production, you'd send an email with invite link
        return {
            "message": "Invitation created (user needs to sign up first)",
            "email": invite_data.email,
            "role": role.value,
            "signup_url": f"https://app.anfinity.com/register?email={invite_data.email}&workspace={workspace.name}"
        }

