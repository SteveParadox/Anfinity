"""Billing and monetization API endpoints."""

from __future__ import annotations

import logging
import time
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import inspect as sqlalchemy_inspect
from sqlalchemy.exc import NoInspectionAvailable
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.auth import get_current_active_user
from app.core.permissions import ensure_workspace_permission
from app.database.models import User as DBUser, WorkspaceSection
from app.database.session import get_db
from app.schemas.billing import (
    BillingUsageResponse,
    PlanCatalogResponse,
    WorkspaceBillingResponse,
)
from app.services.billing import (
    BillingStateError,
    get_or_create_workspace_billing_profile,
    get_workspace_usage_dashboard,
    serialize_plan_catalog_for_api,
)


router = APIRouter(prefix="/billing", tags=["Billing"])
logger = logging.getLogger(__name__)


class PortalSessionRequest(BaseModel):
    workspace_id: UUID
    return_url: Optional[str] = None


class PortalSessionResponse(BaseModel):
    url: str


def _orm_identity_id(instance: object) -> Optional[UUID]:
    """Read an ORM identity without triggering an async lazy refresh."""

    try:
        identity = sqlalchemy_inspect(instance).identity
    except NoInspectionAvailable:
        identity = None
    if identity:
        return identity[0]
    value = getattr(instance, "id", None)
    return value if isinstance(value, UUID) else None


def _billing_state_error_response(
    exc: BillingStateError,
    *,
    request: Request,
    user_id: Optional[UUID],
) -> JSONResponse:
    request_id = request.headers.get("x-request-id") or request.headers.get("x-correlation-id")
    logger.error(
        "billing_event=invalid_billing_state workspace_id=%s user_id=%s billing_profile_id=%s plan=%s request_id=%s",
        exc.workspace_id,
        user_id,
        exc.billing_profile_id,
        exc.plan,
        request_id,
    )
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content={
            "error": {
                "code": "BILLING_STATE_INVALID",
                "message": "Workspace billing state is invalid. Please contact support or run billing data migrations.",
                "timestamp": time.time(),
                "metadata": {
                    "workspace_id": str(exc.workspace_id),
                },
            }
        },
    )


@router.get("/plans", response_model=PlanCatalogResponse)
async def get_plan_catalog() -> PlanCatalogResponse:
    """Public plan catalog used by pricing UI and backend-aligned comparisons."""
    return PlanCatalogResponse(plans=serialize_plan_catalog_for_api())


@router.get("/subscription", response_model=WorkspaceBillingResponse)
async def get_workspace_subscription(
    workspace_id: UUID,
    request: Request,
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> WorkspaceBillingResponse | JSONResponse:
    user_id = _orm_identity_id(current_user)
    await ensure_workspace_permission(workspace_id, current_user, db, WorkspaceSection.SETTINGS, "view")
    try:
        usage_payload = await get_workspace_usage_dashboard(db, workspace_id=workspace_id)
    except BillingStateError as exc:
        await db.rollback()
        return _billing_state_error_response(exc, request=request, user_id=user_id)
    return WorkspaceBillingResponse(
        workspace_id=usage_payload["workspace_id"],
        subscription=usage_payload["subscription"],
        plan=usage_payload["plan"],
    )


@router.get("/usage", response_model=BillingUsageResponse)
async def get_workspace_usage(
    workspace_id: UUID,
    request: Request,
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> BillingUsageResponse | JSONResponse:
    user_id = _orm_identity_id(current_user)
    await ensure_workspace_permission(workspace_id, current_user, db, WorkspaceSection.SETTINGS, "view")
    try:
        payload = await get_workspace_usage_dashboard(db, workspace_id=workspace_id)
    except BillingStateError as exc:
        await db.rollback()
        return _billing_state_error_response(exc, request=request, user_id=user_id)
    return BillingUsageResponse(**payload)


@router.post("/portal-session", response_model=PortalSessionResponse)
async def create_customer_portal_session(
    payload: PortalSessionRequest,
    request: Request,
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> PortalSessionResponse | JSONResponse:
    """Create a Stripe billing portal session for workspace self-serve billing."""
    user_id = _orm_identity_id(current_user)
    await ensure_workspace_permission(payload.workspace_id, current_user, db, WorkspaceSection.SETTINGS, "manage")

    try:
        profile = await get_or_create_workspace_billing_profile(db, payload.workspace_id)
    except BillingStateError as exc:
        await db.rollback()
        return _billing_state_error_response(exc, request=request, user_id=user_id)
    billing_profile_id = profile.id
    stripe_customer_id = profile.stripe_customer_id
    if not stripe_customer_id:
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
            customer=stripe_customer_id,
            return_url=payload.return_url or settings.STRIPE_PORTAL_RETURN_URL or settings.FRONTEND_URL,
        )
    except Exception as exc:
        logger.exception(
            "billing_event=stripe_portal_session_failed workspace_id=%s user_id=%s billing_profile_id=%s",
            payload.workspace_id,
            user_id,
            billing_profile_id,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Unable to create Stripe portal session",
        ) from exc

    url = getattr(session, "url", None)
    if not url:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Stripe portal session did not return a URL",
        )
    return PortalSessionResponse(url=url)
