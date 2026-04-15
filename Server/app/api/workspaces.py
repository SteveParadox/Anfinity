"""Workspace API routes."""
from __future__ import annotations
import logging
from typing import Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status, Request, BackgroundTasks
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_

from app.database.session import get_db
from app.database.models import Workspace, WorkspaceMember, WorkspacePermissionOverride, WorkspaceSection, User as DBUser
from app.core.auth import (
    get_current_active_user,
    WorkspaceContext,
    get_workspace_context,
    WorkspaceRole,
    has_required_role
)
from app.core.permissions import (
    WORKSPACE_PERMISSION_ACTIONS,
    ensure_workspace_permission,
    get_bulk_workspace_permissions_for_user,
    get_workspace_permissions_for_user,
    require_permission,
)
from app.core.audit import log_audit_event, AuditAction, EntityType, AuditLogger
from app.ingestion.vector_index import vector_index

router = APIRouter(prefix="/workspaces", tags=["Workspaces"])
logger = logging.getLogger(__name__)


def _initialize_workspace_vector_collection(workspace_id: str) -> None:
    """Initialize per-workspace vector storage without blocking the API response."""
    try:
        vector_index.create_collection(workspace_id)
    except Exception:
        logger.warning(
            "Workspace %s created but vector collection initialization failed",
            workspace_id,
            exc_info=True,
        )


def _serialize_permission_map(payload: Dict[str, Dict[str, bool]]) -> Dict[str, PermissionStateResponse]:
    return {
        section: PermissionStateResponse(**{
            action: bool(values.get(action, False))
            for action in WORKSPACE_PERMISSION_ACTIONS
        })
        for section, values in payload.items()
    }


# Schemas
class WorkspaceCreate(BaseModel):
    """Workspace creation schema."""
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=1000)


class WorkspaceUpdate(BaseModel):
    """Workspace update schema."""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=1000)


class WorkspaceInvite(BaseModel):
    """Workspace invite schema."""
    email: str
    role: WorkspaceRole = WorkspaceRole.MEMBER


class WorkspaceMemberResponse(BaseModel):
    """Workspace member response."""
    user_id: str
    email: str
    full_name: Optional[str]
    role: str
    joined_at: str


class WorkspaceResponse(BaseModel):
    """Workspace response."""
    id: str
    name: str
    description: Optional[str]
    owner_id: str
    role: str
    created_at: str
    updated_at: Optional[str]
    member_count: int
    document_count: Optional[int] = 0


class WorkspaceStatsResponse(BaseModel):
    """Workspace statistics response."""
    documents: dict = Field(..., description="Document statistics")
    vectors: int = Field(..., description="Total vector embeddings")


class PermissionStateResponse(BaseModel):
    view: bool
    create: bool
    update: bool
    delete: bool
    manage: bool


class WorkspacePermissionsResponse(BaseModel):
    workspace_id: str
    role: str
    permissions: Dict[str, PermissionStateResponse]


class WorkspacePermissionOverrideUpdate(BaseModel):
    section: WorkspaceSection
    can_view: Optional[bool] = None
    can_create: Optional[bool] = None
    can_update: Optional[bool] = None
    can_delete: Optional[bool] = None
    can_manage: Optional[bool] = None


class WorkspacePermissionOverrideResponse(BaseModel):
    workspace_id: str
    user_id: str
    section: str
    can_view: Optional[bool] = None
    can_create: Optional[bool] = None
    can_update: Optional[bool] = None
    can_delete: Optional[bool] = None
    can_manage: Optional[bool] = None


class WorkspaceInviteResponse(BaseModel):
    message: str
    user_id: str
    role: str


@router.post("", response_model=WorkspaceResponse, status_code=status.HTTP_201_CREATED)
async def create_workspace(
    workspace_data: WorkspaceCreate,
    request: Request,
    background_tasks: BackgroundTasks,
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """Create a new workspace.
    
    Args:
        workspace_data: Workspace creation data
        request: FastAPI request object
        current_user: Authenticated user
        db: Database session
        
    Returns:
        Created workspace
    """
    # Create workspace
    workspace = Workspace(
        name=workspace_data.name,
        description=workspace_data.description,
        owner_id=current_user.id,
        settings={}
    )
    
    db.add(workspace)
    await db.flush()

    # Add owner as member in the same transaction
    member = WorkspaceMember(
        workspace_id=workspace.id,
        user_id=current_user.id,
        role=WorkspaceRole.OWNER
    )
    db.add(member)
    await db.commit()
    await db.refresh(workspace)
    
    # Initialize vector storage after the response so Qdrant latency/outages do
    # not slow down the control-plane create request.
    background_tasks.add_task(_initialize_workspace_vector_collection, str(workspace.id))
    
    # Log audit event
    logger = AuditLogger(db, current_user.id).with_request(request)
    await logger.log(
        action=AuditAction.WORKSPACE_CREATED,
        workspace_id=workspace.id,
        entity_type=EntityType.WORKSPACE,
        entity_id=workspace.id,
        metadata={"name": workspace.name}
    )
    
    return WorkspaceResponse(
        id=str(workspace.id),
        name=workspace.name,
        description=workspace.description,
        owner_id=str(workspace.owner_id),
        role=WorkspaceRole.OWNER.value,
        created_at=workspace.created_at.isoformat(),
        updated_at=workspace.updated_at.isoformat() if workspace.updated_at else None,
        member_count=1
    )


@router.get("", response_model=List[WorkspaceResponse])
async def list_workspaces(
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """List user's workspaces.
    
    Args:
        current_user: Authenticated user
        db: Database session
        
    Returns:
        List of workspaces
    """
    # Get workspaces where user is a member
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
            Workspace,
            WorkspaceMember.role.label("current_role"),
            func.coalesce(member_count_subquery.c.member_count, 0).label("member_count"),
        )
        .join(
            WorkspaceMember,
            and_(
                Workspace.id == WorkspaceMember.workspace_id,
                WorkspaceMember.user_id == current_user.id,
            ),
        )
        .outerjoin(member_count_subquery, member_count_subquery.c.workspace_id == Workspace.id)
        .order_by(Workspace.created_at.desc())
    )
    
    workspaces = []
    for row in result.all():
        workspace = row[0]
        role = row[1]
        member_count = row[2]
        
        workspaces.append(WorkspaceResponse(
            id=str(workspace.id),
            name=workspace.name,
            description=workspace.description,
            owner_id=str(workspace.owner_id),
            role=role.value if isinstance(role, WorkspaceRole) else str(role),
            created_at=workspace.created_at.isoformat(),
            updated_at=workspace.updated_at.isoformat() if workspace.updated_at else None,
            member_count=member_count
        ))
    
    return workspaces


@router.get("/permissions/bulk", response_model=Dict[str, WorkspacePermissionsResponse])
async def get_user_permissions(
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    permission_map = await get_bulk_workspace_permissions_for_user(db, current_user)
    return {
        workspace_id: WorkspacePermissionsResponse(
            workspace_id=workspace_id,
            role=str(payload["role"]),
            permissions=_serialize_permission_map(payload["permissions"]),  # type: ignore[arg-type]
        )
        for workspace_id, payload in permission_map.items()
    }


@router.get("/{workspace_id}/permissions", response_model=WorkspacePermissionsResponse)
async def get_workspace_permissions(
    workspace_id: UUID,
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    context = await get_workspace_context(workspace_id, current_user, db)
    permissions = await get_workspace_permissions_for_user(db, workspace_id, current_user, context)
    return WorkspacePermissionsResponse(
        workspace_id=str(workspace_id),
        role=context.role.value if isinstance(context.role, WorkspaceRole) else str(context.role),
        permissions=_serialize_permission_map(permissions),
    )


@router.get("/{workspace_id}", response_model=WorkspaceResponse)
async def get_workspace(
    workspace_id: UUID,
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """Get workspace by ID.
    
    Args:
        workspace_id: Workspace UUID
        current_user: Authenticated user
        db: Database session
        
    Returns:
        Workspace details
    """
    await ensure_workspace_permission(
        workspace_id=workspace_id,
        user=current_user,
        db=db,
        section=WorkspaceSection.SETTINGS,
        action="view",
    )
    
    # Get workspace with member count
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
            Workspace,
            WorkspaceMember.role.label("current_role"),
            func.coalesce(member_count_subquery.c.member_count, 0).label("member_count"),
        )
        .join(
            WorkspaceMember,
            and_(
                Workspace.id == WorkspaceMember.workspace_id,
                WorkspaceMember.user_id == current_user.id,
            ),
        )
        .outerjoin(member_count_subquery, member_count_subquery.c.workspace_id == Workspace.id)
        .where(Workspace.id == workspace_id)
    )
    
    row = result.first()
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workspace not found"
        )
    
    workspace, role, member_count = row
    
    return WorkspaceResponse(
        id=str(workspace.id),
        name=workspace.name,
        description=workspace.description,
        owner_id=str(workspace.owner_id),
        role=role.value if isinstance(role, WorkspaceRole) else str(role),
        created_at=workspace.created_at.isoformat(),
        updated_at=workspace.updated_at.isoformat() if workspace.updated_at else None,
        member_count=member_count
    )


@router.get("/{workspace_id}/stats", response_model=WorkspaceStatsResponse)
async def get_workspace_stats(
    workspace_id: UUID,
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """Get workspace statistics.
    
    Args:
        workspace_id: Workspace UUID
        current_user: Authenticated user
        db: Database session
        
    Returns:
        Workspace statistics
    """
    await ensure_workspace_permission(
        workspace_id=workspace_id,
        user=current_user,
        db=db,
        section=WorkspaceSection.SETTINGS,
        action="view",
    )
    
    from app.database.models import Document, DocumentStatus
    
    # Get document statistics
    total_result = await db.execute(
        select(func.count(Document.id))
        .where(Document.workspace_id == workspace_id)
    )
    total_docs = total_result.scalar() or 0
    
    indexed_result = await db.execute(
        select(func.count(Document.id))
        .where(Document.workspace_id == workspace_id)
        .where(Document.status == DocumentStatus.INDEXED)
    )
    indexed_docs = indexed_result.scalar() or 0
    
    processing_result = await db.execute(
        select(func.count(Document.id))
        .where(Document.workspace_id == workspace_id)
        .where(Document.status == DocumentStatus.PROCESSING)
    )
    processing_docs = processing_result.scalar() or 0
    
    # Get vector count (this is a simplified count - in reality you'd count from vector DB)
    # For now, we'll estimate based on indexed documents
    vectors = indexed_docs * 5  # Rough estimate: 5 vectors per document
    
    return WorkspaceStatsResponse(
        documents={
            "total": total_docs,
            "indexed": indexed_docs,
            "processing": processing_docs
        },
        vectors=vectors
    )


@router.patch("/{workspace_id}", response_model=WorkspaceResponse)
async def update_workspace(
    workspace_id: UUID,
    workspace_data: WorkspaceUpdate,
    request: Request,
    context: WorkspaceContext = Depends(require_permission(WorkspaceSection.SETTINGS, "update")),
    db: AsyncSession = Depends(get_db)
):
    """Update workspace.
    
    Args:
        workspace_id: Workspace UUID
        workspace_data: Update data
        request: FastAPI request object
        context: Workspace context with membership
        db: Database session
        
    Returns:
        Updated workspace
    """
    # Get workspace
    result = await db.execute(
        select(Workspace).where(Workspace.id == workspace_id)
    )
    workspace = result.scalar_one_or_none()
    
    if not workspace:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workspace not found"
        )
    
    # Update fields
    if workspace_data.name is not None:
        workspace.name = workspace_data.name
    if workspace_data.description is not None:
        workspace.description = workspace_data.description
    
    await db.commit()
    await db.refresh(workspace)
    
    # Log audit event
    logger = AuditLogger(db, context.user.id).with_request(request)
    await logger.log(
        action=AuditAction.WORKSPACE_UPDATED,
        workspace_id=workspace_id,
        entity_type=EntityType.WORKSPACE,
        entity_id=workspace_id,
        metadata={
            "name": workspace.name,
            "updated_fields": list(workspace_data.dict(exclude_unset=True).keys())
        }
    )
    
    # Get member count
    result = await db.execute(
        select(func.count(WorkspaceMember.id))
        .where(WorkspaceMember.workspace_id == workspace_id)
    )
    member_count = result.scalar()
    
    return WorkspaceResponse(
        id=str(workspace.id),
        name=workspace.name,
        description=workspace.description,
        owner_id=str(workspace.owner_id),
        role=context.role.value if isinstance(context.role, WorkspaceRole) else str(context.role),
        created_at=workspace.created_at.isoformat(),
        updated_at=workspace.updated_at.isoformat() if workspace.updated_at else None,
        member_count=member_count
    )


@router.put(
    "/{workspace_id}/permissions/{user_id}",
    response_model=WorkspacePermissionOverrideResponse,
)
async def upsert_workspace_permission_override(
    workspace_id: UUID,
    user_id: UUID,
    override_data: WorkspacePermissionOverrideUpdate,
    request: Request,
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    context = await ensure_workspace_permission(
        workspace_id=workspace_id,
        user=current_user,
        db=db,
        section=WorkspaceSection.MEMBERS,
        action="manage",
    )

    membership_result = await db.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user_id,
        )
    )
    membership = membership_result.scalar_one_or_none()
    if membership is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace member not found")

    override_result = await db.execute(
        select(WorkspacePermissionOverride).where(
            WorkspacePermissionOverride.workspace_id == workspace_id,
            WorkspacePermissionOverride.user_id == user_id,
            WorkspacePermissionOverride.section == override_data.section,
        )
    )
    override = override_result.scalar_one_or_none()

    if override is None:
        override = WorkspacePermissionOverride(
            workspace_id=workspace_id,
            user_id=user_id,
            section=override_data.section,
        )
        db.add(override)

    override.can_view = override_data.can_view
    override.can_create = override_data.can_create
    override.can_update = override_data.can_update
    override.can_delete = override_data.can_delete
    override.can_manage = override_data.can_manage

    await db.commit()
    await db.refresh(override)

    logger = AuditLogger(db, context.user.id).with_request(request)
    await logger.log(
        action=AuditAction.WORKSPACE_UPDATED,
        workspace_id=workspace_id,
        entity_type=EntityType.USER,
        entity_id=user_id,
        metadata={
            "permission_override_section": override_data.section.value,
            "can_view": override.can_view,
            "can_create": override.can_create,
            "can_update": override.can_update,
            "can_delete": override.can_delete,
            "can_manage": override.can_manage,
        },
    )

    return WorkspacePermissionOverrideResponse(
        workspace_id=str(workspace_id),
        user_id=str(user_id),
        section=override.section.value if isinstance(override.section, WorkspaceSection) else str(override.section),
        can_view=override.can_view,
        can_create=override.can_create,
        can_update=override.can_update,
        can_delete=override.can_delete,
        can_manage=override.can_manage,
    )


@router.post("/{workspace_id}/invite", response_model=WorkspaceInviteResponse)
async def invite_member(
    workspace_id: UUID,
    invite_data: WorkspaceInvite,
    request: Request,
    context: WorkspaceContext = Depends(require_permission(WorkspaceSection.MEMBERS, "manage")),
    db: AsyncSession = Depends(get_db)
):
    """Invite a user to workspace.
    
    Args:
        workspace_id: Workspace UUID
        invite_data: Invite data (email and role)
        request: FastAPI request object
        context: Workspace context with membership
        db: Database session
        
    Returns:
        Invite result
    """
    if invite_data.role == WorkspaceRole.OWNER:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Inviting another owner is not supported"
        )

    # Find user by email
    result = await db.execute(
        select(DBUser).where(DBUser.email == invite_data.email)
    )
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    # Check if already a member
    result = await db.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user.id
        )
    )
    existing = result.scalar_one_or_none()
    
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User is already a member of this workspace"
        )
    
    # Cannot invite with higher role than self
    if not has_required_role(context.role, invite_data.role):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot invite with higher role than yourself"
        )
    
    # Add member
    member = WorkspaceMember(
        workspace_id=workspace_id,
        user_id=user.id,
        role=invite_data.role
    )
    db.add(member)
    await db.commit()
    
    # Log audit event
    logger = AuditLogger(db, context.user.id).with_request(request)
    await logger.log(
        action=AuditAction.MEMBER_INVITED,
        workspace_id=workspace_id,
        entity_type=EntityType.USER,
        entity_id=user.id,
        metadata={
            "invited_email": invite_data.email,
            "role": invite_data.role.value
        }
    )
    
    return WorkspaceInviteResponse(
        message=f"Invited {invite_data.email} as {invite_data.role.value}",
        user_id=str(user.id),
        role=invite_data.role.value
    )


@router.get("/{workspace_id}/members", response_model=List[WorkspaceMemberResponse])
async def list_members(
    workspace_id: UUID,
    context: WorkspaceContext = Depends(require_permission(WorkspaceSection.MEMBERS, "view")),
    db: AsyncSession = Depends(get_db)
):
    """List workspace members.
    
    Args:
        workspace_id: Workspace UUID
        context: Workspace context with membership
        db: Database session
        
    Returns:
        List of members
    """
    result = await db.execute(
        select(WorkspaceMember, DBUser)
        .join(DBUser, WorkspaceMember.user_id == DBUser.id)
        .where(WorkspaceMember.workspace_id == workspace_id)
        .order_by(WorkspaceMember.joined_at)
    )
    
    members = []
    for member, user in result.all():
        members.append(WorkspaceMemberResponse(
            user_id=str(user.id),
            email=user.email,
            full_name=user.full_name,
            role=member.role,
            joined_at=member.joined_at.isoformat()
        ))
    
    return members


@router.delete("/{workspace_id}/members/{user_id}")
async def remove_member(
    workspace_id: UUID,
    user_id: UUID,
    request: Request,
    context: WorkspaceContext = Depends(require_permission(WorkspaceSection.MEMBERS, "manage")),
    db: AsyncSession = Depends(get_db)
):
    """Remove a member from workspace.
    
    Args:
        workspace_id: Workspace UUID
        user_id: User to remove
        request: FastAPI request object
        context: Workspace context with membership
        db: Database session
        
    Returns:
        Success message
    """
    # Cannot remove owner
    result = await db.execute(
        select(Workspace).where(Workspace.id == workspace_id)
    )
    workspace = result.scalar_one()
    
    if str(workspace.owner_id) == str(user_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot remove workspace owner"
        )
    
    # Get member record
    result = await db.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user_id
        )
    )
    member = result.scalar_one_or_none()
    
    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member not found"
        )
    
    # Cannot remove someone with higher or equal role
    if has_required_role(WorkspaceRole(member.role), context.role):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot remove member with equal or higher role"
        )
    
    await db.delete(member)
    await db.commit()
    
    # Log audit event
    logger = AuditLogger(db, context.user.id).with_request(request)
    await logger.log(
        action=AuditAction.MEMBER_REMOVED,
        workspace_id=workspace_id,
        entity_type=EntityType.USER,
        entity_id=user_id
    )
    
    return {"message": "Member removed successfully"}


@router.delete("/{workspace_id}")
async def delete_workspace(
    workspace_id: UUID,
    request: Request,
    context: WorkspaceContext = Depends(require_permission(WorkspaceSection.SETTINGS, "delete")),
    db: AsyncSession = Depends(get_db)
):
    """Delete workspace.
    
    Args:
        workspace_id: Workspace UUID
        request: FastAPI request object
        context: Workspace context with membership
        db: Database session
        
    Returns:
        Success message
    """
    # Get workspace
    result = await db.execute(
        select(Workspace).where(Workspace.id == workspace_id)
    )
    workspace = result.scalar_one_or_none()
    
    if not workspace:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workspace not found"
        )
    
    # Delete vector collection best-effort so storage cleanup problems do not
    # block the source-of-truth workspace deletion.
    try:
        vector_index.delete_collection(str(workspace_id))
    except Exception:
        logger.warning("Workspace %s deleted but vector collection cleanup failed", workspace_id, exc_info=True)
    
    # Delete workspace (cascades to members, documents, etc.)
    await db.delete(workspace)
    await db.commit()
    
    # Log audit event
    logger = AuditLogger(db, context.user.id).with_request(request)
    await logger.log(
        action=AuditAction.WORKSPACE_DELETED,
        workspace_id=workspace_id,
        entity_type=EntityType.WORKSPACE,
        entity_id=workspace_id,
        metadata={"name": workspace.name}
    )
    
    return {"message": "Workspace deleted successfully"}
