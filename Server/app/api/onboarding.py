"""Onboarding Accelerator API endpoints."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_active_user
from app.core.permissions import ensure_workspace_permission
from app.database.models import User as DBUser, WorkspaceSection
from app.database.session import get_db
from app.services.onboarding_accelerator import OnboardingCurriculum, get_onboarding_accelerator_service

router = APIRouter(prefix="/onboarding", tags=["onboarding"])


class OnboardingCurriculumRequest(BaseModel):
    workspace_id: UUID
    role: str = Field(..., min_length=1, max_length=120)


@router.post("/curriculum", response_model=OnboardingCurriculum)
async def generate_onboarding_curriculum(
    payload: OnboardingCurriculumRequest,
    current_user: Annotated[DBUser, Depends(get_current_active_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> OnboardingCurriculum:
    await ensure_workspace_permission(
        workspace_id=payload.workspace_id,
        user=current_user,
        db=db,
        section=WorkspaceSection.NOTES,
        action="view",
    )
    await ensure_workspace_permission(
        workspace_id=payload.workspace_id,
        user=current_user,
        db=db,
        section=WorkspaceSection.CHAT,
        action="create",
    )

    service = get_onboarding_accelerator_service()
    return await service.generate_curriculum(
        workspace_id=payload.workspace_id,
        user=current_user,
        role=payload.role,
        db=db,
    )
