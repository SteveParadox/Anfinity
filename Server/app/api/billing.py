"""Billing and monetization API endpoints."""

from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.auth import get_current_active_user
from app.core.permissions import ensure_workspace_permission
from app.database.models import User as DBUser, WorkspaceSection
from app.database.session import get_db
from app.services.billing import (
    get_or_create_workspace_billing_profile,
    get_workspace_usage_dashboard,
    serialize_plan_catalog_for_api,
)


router = APIRouter(prefix="/billing", tags=["Billing"])


class PlanCatalogResponse(BaseModel):
    plans: list[dict[str, Any]]


class WorkspaceBillingResponse(BaseModel):
    workspace_id: str
    subscription: dict[str, Any]
    plan: dict[str, Any]


class BillingUsageResponse(BaseModel):
    workspace_id: str
    plan: dict[str, Any]
    subscription: dict[str, Any]
    usage_metrics: list[dict[str, Any]]
    projected_monthly_cost: dict[str, Any]


class PortalSessionRequest(BaseModel):
    workspace_id: UUID
    return_url: Optional[str] = None


class PortalSessionResponse(BaseModel):
    url: str


@router.get("/plans", response_model=PlanCatalogResponse)
async def get_plan_catalog() -> PlanCatalogResponse:
    """Public plan catalog used by pricing UI and backend-aligned comparisons."""
    return PlanCatalogResponse(plans=serialize_plan_catalog_for_api())


@router.get("/subscription", response_model=WorkspaceBillingResponse)
async def get_workspace_subscription(
    workspace_id: UUID,
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> WorkspaceBillingResponse:
    await ensure_workspace_permission(workspace_id, current_user, db, WorkspaceSection.SETTINGS, "view")
    usage_payload = await get_workspace_usage_dashboard(db, workspace_id=workspace_id)
    return WorkspaceBillingResponse(
        workspace_id=usage_payload["workspace_id"],
        subscription=usage_payload["subscription"],
        plan=usage_payload["plan"],
    )


@router.get("/usage", response_model=BillingUsageResponse)
async def get_workspace_usage(
    workspace_id: UUID,
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> BillingUsageResponse:
    await ensure_workspace_permission(workspace_id, current_user, db, WorkspaceSection.SETTINGS, "view")
    payload = await get_workspace_usage_dashboard(db, workspace_id=workspace_id)
    return BillingUsageResponse(**payload)


@router.post("/portal-session", response_model=PortalSessionResponse)
async def create_customer_portal_session(
    payload: PortalSessionRequest,
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> PortalSessionResponse:
    """Create a Stripe billing portal session for workspace self-serve billing."""
    await ensure_workspace_permission(payload.workspace_id, current_user, db, WorkspaceSection.SETTINGS, "manage")

    profile = await get_or_create_workspace_billing_profile(db, payload.workspace_id)
    if not profile.stripe_customer_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Stripe customer is not configured for this workspace",
        )

    if not settings.STRIPE_SECRET_KEY:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Stripe is not configured",
        )

    try:
        import stripe

        stripe.api_key = settings.STRIPE_SECRET_KEY
        session = stripe.billing_portal.Session.create(
            customer=profile.stripe_customer_id,
            return_url=payload.return_url or settings.STRIPE_PORTAL_RETURN_URL or settings.FRONTEND_URL,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Unable to create Stripe portal session: {exc}",
        ) from exc

    url = getattr(session, "url", None)
    if not url:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Stripe portal session did not return a URL",
        )
    return PortalSessionResponse(url=url)
