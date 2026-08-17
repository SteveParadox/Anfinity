"""Billing profile management, entitlement checks, usage accounting, and projections."""

from __future__ import annotations

import calendar
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Optional
from uuid import UUID, uuid4

from sqlalchemy import and_, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError, StatementError
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing.plans import (
    BILLING_METRICS,
    PLAN_ORDER,
    VALID_BILLING_PLAN_VALUES,
    PlanDefinition,
    PlanMetric,
    coerce_billing_plan_key,
    get_plan_definition,
    get_plan_key_for_stripe_price_id,
    plan_definitions,
    serialize_plan_catalog_for_api,
    serialize_plan_for_api,
)
from app.core.entitlements import EntitlementErrorMetadata, EntitlementRequiredError
from app.database.models import (
    BillingInterval,
    BillingPlan,
    BillingStatus,
    UsageCounter,
    WorkspaceBillingProfile,
)


logger = logging.getLogger(__name__)


class BillingStateError(Exception):
    """Raised when persisted billing state cannot be trusted."""

    def __init__(
        self,
        message: str,
        *,
        workspace_id: UUID,
        billing_profile_id: Optional[UUID] = None,
        plan: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.workspace_id = workspace_id
        self.billing_profile_id = billing_profile_id
        self.plan = plan


def _log_billing_event(level: int, event: str, **fields: object) -> None:
    clean_fields = {
        key: value
        for key, value in fields.items()
        if value is not None
    }
    suffix = " ".join(f"{key}={value}" for key, value in clean_fields.items())
    logger.log(level, "billing_event=%s%s%s", event, " " if suffix else "", suffix)


def _month_period_bounds(now: Optional[datetime] = None) -> tuple[datetime, datetime]:
    dt = now.astimezone(timezone.utc) if now else datetime.now(timezone.utc)
    start = datetime(dt.year, dt.month, 1, tzinfo=timezone.utc)
    days_in_month = calendar.monthrange(dt.year, dt.month)[1]
    end = start + timedelta(days=days_in_month)
    return start, end


def _profile_plan_key(profile: WorkspaceBillingProfile) -> str:
    try:
        return coerce_billing_plan_key(profile.plan)
    except ValueError as exc:
        raise BillingStateError(
            "Workspace billing profile has an unsupported plan",
            workspace_id=profile.workspace_id,
            billing_profile_id=profile.id,
            plan=str(getattr(profile.plan, "value", profile.plan)),
        ) from exc


async def _fetch_billing_profile_snapshot(
    db: AsyncSession,
    workspace_id: UUID,
) -> Optional[dict[str, Any]]:
    result = await db.execute(
        text(
            """
            SELECT id::text AS id, plan::text AS plan
            FROM workspace_billing_profiles
            WHERE workspace_id = :workspace_id
            LIMIT 1
            """
        ),
        {"workspace_id": str(workspace_id)},
    )
    row = result.mappings().first()
    if not row:
        return None
    return dict(row)


async def _normalize_known_legacy_billing_values(
    db: AsyncSession,
    workspace_id: UUID,
) -> None:
    """Repair known legacy casing before ORM enum deserialization runs."""

    try:
        result = await db.execute(
            text(
                """
                UPDATE workspace_billing_profiles
                SET plan = CASE
                    WHEN lower(plan::text) = 'business' THEN 'enterprise'
                    ELSE lower(plan::text)
                END
                WHERE workspace_id = :workspace_id
                  AND plan::text IN ('FREE', 'PRO', 'TEAM', 'ENTERPRISE', 'business', 'BUSINESS')
                """
            ),
            {"workspace_id": str(workspace_id)},
        )
        if getattr(result, "rowcount", 0):
            _log_billing_event(
                logging.WARNING,
                "legacy_plan_normalized",
                workspace_id=workspace_id,
                rows=result.rowcount,
            )
    except SQLAlchemyError as exc:
        _log_billing_event(
            logging.ERROR,
            "legacy_plan_normalization_failed",
            workspace_id=workspace_id,
            error=exc.__class__.__name__,
        )
        raise BillingStateError(
            "Workspace billing profile has invalid plan data",
            workspace_id=workspace_id,
        ) from exc


async def _select_workspace_billing_profile(
    db: AsyncSession,
    workspace_id: UUID,
) -> Optional[WorkspaceBillingProfile]:
    try:
        return (
            await db.execute(
                select(WorkspaceBillingProfile).where(
                    WorkspaceBillingProfile.workspace_id == workspace_id
                )
            )
        ).scalar_one_or_none()
    except (LookupError, StatementError) as exc:
        snapshot = await _fetch_billing_profile_snapshot(db, workspace_id)
        _log_billing_event(
            logging.ERROR,
            "invalid_billing_plan_detected",
            workspace_id=workspace_id,
            billing_profile_id=snapshot.get("id") if snapshot else None,
            plan=snapshot.get("plan") if snapshot else None,
        )
        raise BillingStateError(
            "Workspace billing profile has an unsupported plan",
            workspace_id=workspace_id,
            billing_profile_id=UUID(snapshot["id"]) if snapshot and snapshot.get("id") else None,
            plan=snapshot.get("plan") if snapshot else None,
        ) from exc


def _next_required_plan(metric_key: str, current_plan: str, usage: int) -> Optional[str]:
    plans = plan_definitions()
    try:
        current_index = PLAN_ORDER.index(coerce_billing_plan_key(current_plan))
    except ValueError:
        current_index = 0
    for candidate_key in PLAN_ORDER[current_index + 1 :]:
        metric = plans[candidate_key].metrics.get(metric_key)
        if metric is None:
            continue
        if metric.limit is None or usage <= metric.limit:
            return candidate_key
    return None


async def get_or_create_workspace_billing_profile(
    db: AsyncSession,
    workspace_id: UUID,
) -> WorkspaceBillingProfile:
    await _normalize_known_legacy_billing_values(db, workspace_id)

    profile = await _select_workspace_billing_profile(db, workspace_id)
    if profile is not None:
        plan_key = _profile_plan_key(profile)
        _log_billing_event(
            logging.INFO,
            "billing_profile_fetched",
            workspace_id=workspace_id,
            billing_profile_id=profile.id,
            plan=plan_key,
            subscription_status=getattr(profile.status, "value", profile.status),
        )
        return profile

    period_start, period_end = _month_period_bounds()
    profile_id = uuid4()
    insert_stmt = (
        pg_insert(WorkspaceBillingProfile)
        .values(
            id=profile_id,
            workspace_id=workspace_id,
            plan=BillingPlan.free,
            billing_interval=BillingInterval.monthly,
            status=BillingStatus.active,
            period_start=period_start,
            period_end=period_end,
        )
        .on_conflict_do_nothing(index_elements=["workspace_id"])
        .returning(WorkspaceBillingProfile.id)
    )
    result = await db.execute(insert_stmt)
    inserted_profile_id = result.scalar_one_or_none()

    profile = await _select_workspace_billing_profile(db, workspace_id)
    if profile is None:
        raise BillingStateError(
            "Workspace billing profile could not be created",
            workspace_id=workspace_id,
        )

    _log_billing_event(
        logging.INFO,
        "billing_profile_created" if inserted_profile_id else "billing_profile_fetched",
        workspace_id=workspace_id,
        billing_profile_id=profile.id,
        plan=_profile_plan_key(profile),
        subscription_status=getattr(profile.status, "value", profile.status),
    )
    return profile


def _metric_limits_for_profile(profile: WorkspaceBillingProfile, metric_key: str) -> tuple[PlanDefinition, PlanMetric]:
    plan = get_plan_definition(_profile_plan_key(profile))
    return plan, plan.metrics.get(metric_key) or PlanMetric(limit=None)


async def get_or_create_usage_counter(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    metric_key: str,
    profile: Optional[WorkspaceBillingProfile] = None,
    now: Optional[datetime] = None,
) -> UsageCounter:
    if metric_key not in BILLING_METRICS:
        raise ValueError(f"Unsupported metric key: {metric_key}")

    profile = profile or await get_or_create_workspace_billing_profile(db, workspace_id)
    period_start, period_end = _month_period_bounds(now)
    plan, metric_limits = _metric_limits_for_profile(profile, metric_key)
    metric_meta = BILLING_METRICS[metric_key]
    counter_id = uuid4()

    insert_stmt = (
        pg_insert(UsageCounter)
        .values(
            id=counter_id,
            workspace_id=workspace_id,
            metric_key=metric_key,
            period_start=period_start,
            period_end=period_end,
            usage_count=0,
            included_limit=metric_limits.limit,
            overage_rate_cents=metric_limits.overage_rate_cents,
            unit_label=metric_meta["unit_label"],
            counter_metadata={"plan": plan.key, "period": "monthly"},
        )
        .on_conflict_do_update(
            constraint="uq_usage_counter_workspace_metric_period",
            set_={
                "included_limit": metric_limits.limit,
                "overage_rate_cents": metric_limits.overage_rate_cents,
                "unit_label": metric_meta["unit_label"],
                "updated_at": func.now(),
            },
        )
        .returning(UsageCounter.id)
    )
    result = await db.execute(insert_stmt)
    resolved_counter_id = result.scalar_one()
    counter = (
        await db.execute(
            select(UsageCounter).where(UsageCounter.id == resolved_counter_id)
        )
    ).scalar_one()
    return counter


async def increment_usage_counter(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    metric_key: str,
    amount: int = 1,
    profile: Optional[WorkspaceBillingProfile] = None,
) -> UsageCounter:
    if amount <= 0:
        raise ValueError("Usage increment must be a positive integer")
    if metric_key not in BILLING_METRICS:
        raise ValueError(f"Unsupported metric key: {metric_key}")

    profile = profile or await get_or_create_workspace_billing_profile(db, workspace_id)
    period_start, period_end = _month_period_bounds()
    plan, metric_limits = _metric_limits_for_profile(profile, metric_key)
    metric_meta = BILLING_METRICS[metric_key]
    counter_id = uuid4()

    insert_stmt = (
        pg_insert(UsageCounter)
        .values(
            id=counter_id,
            workspace_id=workspace_id,
            metric_key=metric_key,
            period_start=period_start,
            period_end=period_end,
            usage_count=amount,
            included_limit=metric_limits.limit,
            overage_rate_cents=metric_limits.overage_rate_cents,
            unit_label=metric_meta["unit_label"],
            counter_metadata={"plan": plan.key, "period": "monthly"},
        )
        .on_conflict_do_update(
            constraint="uq_usage_counter_workspace_metric_period",
            set_={
                "usage_count": UsageCounter.usage_count + amount,
                "included_limit": metric_limits.limit,
                "overage_rate_cents": metric_limits.overage_rate_cents,
                "unit_label": metric_meta["unit_label"],
                "updated_at": func.now(),
            },
        )
        .returning(UsageCounter.id)
    )
    result = await db.execute(insert_stmt)
    resolved_counter_id = result.scalar_one()
    counter = (
        await db.execute(
            select(UsageCounter).where(UsageCounter.id == resolved_counter_id)
        )
    ).scalar_one()
    _log_billing_event(
        logging.INFO,
        "usage_counter_incremented",
        workspace_id=workspace_id,
        billing_profile_id=profile.id,
        plan=plan.key,
        usage_key=metric_key,
    )
    return counter


async def enforce_entitlement_limit(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    metric_key: str,
    increment: int = 1,
    upgrade_url: Optional[str] = None,
) -> WorkspaceBillingProfile:
    if increment <= 0:
        raise ValueError("Entitlement increment must be a positive integer")
    profile = await get_or_create_workspace_billing_profile(db, workspace_id)
    plan = get_plan_definition(_profile_plan_key(profile))
    metric = plan.metrics.get(metric_key)
    if metric is None:
        return profile

    counter = await get_or_create_usage_counter(
        db,
        workspace_id=workspace_id,
        metric_key=metric_key,
        profile=profile,
    )
    current_usage = int(counter.usage_count or 0)
    next_usage = current_usage + increment
    if metric.limit is None or next_usage <= metric.limit:
        return profile

    required_plan = _next_required_plan(metric_key, plan.key, next_usage)
    metric_meta = BILLING_METRICS.get(metric_key, {})
    _log_billing_event(
        logging.WARNING,
        "usage_limit_reached",
        workspace_id=workspace_id,
        billing_profile_id=profile.id,
        plan=plan.key,
        usage_key=metric_key,
        usage=current_usage,
        limit=metric.limit,
        required_plan=required_plan,
    )
    raise EntitlementRequiredError(
        EntitlementErrorMetadata(
            code="ENTITLEMENT_LIMIT_REACHED",
            message=f"{metric_meta.get('label', 'Usage limit')} has reached this plan's monthly limit.",
            feature_key=metric_key,
            required_plan=required_plan,
            current_plan=plan.key,
            limit=metric.limit,
            usage=current_usage,
            upgrade_url=upgrade_url,
        )
    )


def _safe_percentage(current: int, limit: Optional[int]) -> Optional[float]:
    if limit is None or limit <= 0:
        return None
    return float((Decimal(current) / Decimal(limit) * Decimal(100)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _project_usage(current: int, period_start: datetime, period_end: datetime, now: Optional[datetime] = None) -> int:
    dt = now.astimezone(timezone.utc) if now else datetime.now(timezone.utc)
    elapsed_seconds = max((dt - period_start).total_seconds(), 1.0)
    total_seconds = max((period_end - period_start).total_seconds(), elapsed_seconds)
    projected = Decimal(current) / Decimal(elapsed_seconds) * Decimal(total_seconds)
    return int(projected.quantize(0, rounding=ROUND_HALF_UP))


def _compute_projected_monthly_cost_cents(
    *,
    profile: WorkspaceBillingProfile,
    plan: PlanDefinition,
    counters: list[UsageCounter],
) -> dict[str, Any]:
    interval = profile.billing_interval.value if isinstance(profile.billing_interval, BillingInterval) else str(profile.billing_interval or "monthly")
    if interval == BillingInterval.annual.value and plan.annual_per_month_cents is not None:
        base_monthly_cents = plan.annual_per_month_cents
    else:
        base_monthly_cents = plan.monthly_price_cents

    total_overage_cents = 0
    metric_breakdown: list[dict[str, Any]] = []
    for counter in counters:
        metric_key = str(counter.metric_key)
        current_usage = int(counter.usage_count or 0)
        projected_usage = _project_usage(current_usage, counter.period_start, counter.period_end)
        included_limit = counter.included_limit
        overage_rate = counter.overage_rate_cents
        projected_overage_units = 0
        projected_overage_cents = 0
        if included_limit is not None and overage_rate is not None:
            projected_overage_units = max(projected_usage - included_limit, 0)
            projected_overage_cents = projected_overage_units * overage_rate
            total_overage_cents += projected_overage_cents
        metric_breakdown.append(
            {
                "metric_key": metric_key,
                "projected_usage": projected_usage,
                "projected_overage_units": projected_overage_units,
                "projected_overage_cents": projected_overage_cents,
            }
        )

    return {
        "base_monthly_cents": base_monthly_cents,
        "projected_overage_cents": total_overage_cents,
        "projected_total_monthly_cents": base_monthly_cents + total_overage_cents,
        "metric_breakdown": metric_breakdown,
    }


async def get_workspace_usage_dashboard(
    db: AsyncSession,
    *,
    workspace_id: UUID,
) -> dict[str, Any]:
    profile = await get_or_create_workspace_billing_profile(db, workspace_id)
    plan = get_plan_definition(_profile_plan_key(profile))

    counters: list[UsageCounter] = []
    for metric_key in BILLING_METRICS.keys():
        counter = await get_or_create_usage_counter(
            db,
            workspace_id=workspace_id,
            metric_key=metric_key,
            profile=profile,
        )
        counters.append(counter)

    cost_projection = _compute_projected_monthly_cost_cents(profile=profile, plan=plan, counters=counters)
    projection_by_key = {
        item["metric_key"]: item
        for item in cost_projection["metric_breakdown"]
    }

    usage_metrics: list[dict[str, Any]] = []
    for counter in counters:
        metric_key = str(counter.metric_key)
        meta = BILLING_METRICS.get(metric_key, {})
        current_usage = int(counter.usage_count or 0)
        included_limit = counter.included_limit
        pct_used = _safe_percentage(current_usage, included_limit)
        projection_item = projection_by_key.get(metric_key, {})
        usage_metrics.append(
            {
                "metric_key": metric_key,
                "label": meta.get("label", metric_key),
                "description": meta.get("description", ""),
                "unit_label": counter.unit_label or meta.get("unit_label", "units"),
                "current_usage": current_usage,
                "limit": included_limit,
                "percentage_used": pct_used,
                "period_start": counter.period_start.isoformat(),
                "period_end": counter.period_end.isoformat(),
                "projected_usage": projection_item.get("projected_usage", current_usage),
                "projected_overage_units": projection_item.get("projected_overage_units", 0),
                "projected_overage_cents": projection_item.get("projected_overage_cents", 0),
                "overage_rate_cents": counter.overage_rate_cents,
            }
        )

    interval = profile.billing_interval.value if isinstance(profile.billing_interval, BillingInterval) else str(profile.billing_interval or "monthly")
    subscription_status = profile.status.value if isinstance(profile.status, BillingStatus) else str(profile.status or "active")
    _log_billing_event(
        logging.INFO,
        "usage_dashboard_loaded",
        workspace_id=workspace_id,
        billing_profile_id=profile.id,
        plan=plan.key,
        subscription_status=subscription_status,
    )
    return {
        "workspace_id": str(workspace_id),
        "plan": serialize_plan_for_api(plan),
        "subscription": {
            "plan_key": plan.key,
            "billing_interval": interval,
            "status": subscription_status,
            "period_start": profile.period_start.isoformat() if profile.period_start else None,
            "period_end": profile.period_end.isoformat() if profile.period_end else None,
            "cancel_at_period_end": bool(profile.cancel_at_period_end),
            "currency": profile.currency,
        },
        "usage_metrics": usage_metrics,
        "projected_monthly_cost": {
            "base_monthly_cents": cost_projection["base_monthly_cents"],
            "projected_overage_cents": cost_projection["projected_overage_cents"],
            "projected_total_monthly_cents": cost_projection["projected_total_monthly_cents"],
        },
    }


__all__ = [
    "BILLING_METRICS",
    "BillingStateError",
    "PlanDefinition",
    "PlanMetric",
    "VALID_BILLING_PLAN_VALUES",
    "_next_required_plan",
    "enforce_entitlement_limit",
    "get_or_create_usage_counter",
    "get_or_create_workspace_billing_profile",
    "get_plan_definition",
    "get_plan_key_for_stripe_price_id",
    "get_workspace_usage_dashboard",
    "increment_usage_counter",
    "plan_definitions",
    "serialize_plan_catalog_for_api",
    "serialize_plan_for_api",
]
