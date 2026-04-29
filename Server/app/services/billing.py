"""Centralized billing plans, entitlement checks, usage accounting, and projections."""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.entitlements import EntitlementErrorMetadata, EntitlementRequiredError
from app.database.models import (
    BillingInterval,
    BillingPlan,
    BillingStatus,
    UsageCounter,
    WorkspaceBillingProfile,
)


BILLING_METRICS: dict[str, dict[str, str]] = {
    "notes_created_monthly": {
        "label": "Notes created",
        "description": "New notes captured in the current billing month.",
        "unit_label": "notes",
    },
    "semantic_search_runs_monthly": {
        "label": "Semantic search runs",
        "description": "AI-powered semantic searches executed this month.",
        "unit_label": "runs",
    },
    "thinking_sessions_created_monthly": {
        "label": "Thinking sessions created",
        "description": "Collaborative thinking sessions started this month.",
        "unit_label": "sessions",
    },
    "automations_created_monthly": {
        "label": "Automations created",
        "description": "Workflow automations created this month.",
        "unit_label": "automations",
    },
}


@dataclass(frozen=True)
class PlanMetric:
    limit: Optional[int]
    overage_rate_cents: Optional[int] = None


@dataclass(frozen=True)
class PlanDefinition:
    key: str
    name: str
    description: str
    monthly_price_cents: int
    annual_price_cents: Optional[int]
    features: list[str]
    entitlement_keys: list[str]
    metrics: dict[str, PlanMetric]
    highlighted: bool = False
    cta_label: str = "Choose plan"
    stripe_price_ids: Optional[dict[str, Optional[str]]] = None

    @property
    def annual_per_month_cents(self) -> Optional[int]:
        if self.annual_price_cents is None:
            return None
        return int(Decimal(self.annual_price_cents / 12).quantize(0, rounding=ROUND_HALF_UP))

    @property
    def annual_savings_cents(self) -> Optional[int]:
        if self.annual_price_cents is None:
            return None
        return max((self.monthly_price_cents * 12) - self.annual_price_cents, 0)


def _plan_definitions() -> dict[str, PlanDefinition]:
    return {
        "free": PlanDefinition(
            key="free",
            name="Free",
            description="For individuals getting started with lightweight AI knowledge work.",
            monthly_price_cents=0,
            annual_price_cents=0,
            features=[
                "Core notes and workspace navigation",
                "Basic semantic search",
                "Lightweight collaboration",
                "Community support",
            ],
            entitlement_keys=[
                "notes.create",
                "search.semantic",
                "thinking_sessions.create",
                "automations.create",
            ],
            metrics={
                "notes_created_monthly": PlanMetric(limit=100),
                "semantic_search_runs_monthly": PlanMetric(limit=200),
                "thinking_sessions_created_monthly": PlanMetric(limit=10),
                "automations_created_monthly": PlanMetric(limit=2),
            },
            cta_label="Start free",
            stripe_price_ids={"monthly": None, "annual": None},
        ),
        "pro": PlanDefinition(
            key="pro",
            name="Pro",
            description="For power users who need more search depth and automation capacity.",
            monthly_price_cents=1200,
            annual_price_cents=11520,
            features=[
                "Everything in Free",
                "Higher monthly usage caps",
                "AI-heavy personal workflows",
                "Priority support",
            ],
            entitlement_keys=[
                "notes.create",
                "search.semantic",
                "thinking_sessions.create",
                "automations.create",
            ],
            metrics={
                "notes_created_monthly": PlanMetric(limit=2000, overage_rate_cents=2),
                "semantic_search_runs_monthly": PlanMetric(limit=5000, overage_rate_cents=1),
                "thinking_sessions_created_monthly": PlanMetric(limit=100, overage_rate_cents=25),
                "automations_created_monthly": PlanMetric(limit=25, overage_rate_cents=50),
            },
            highlighted=True,
            cta_label="Upgrade to Pro",
            stripe_price_ids={
                "monthly": settings.STRIPE_PRICE_ID_PRO_MONTHLY,
                "annual": settings.STRIPE_PRICE_ID_PRO_ANNUAL,
            },
        ),
        "team": PlanDefinition(
            key="team",
            name="Team",
            description="For collaborative teams with heavier search and automation throughput.",
            monthly_price_cents=2500,
            annual_price_cents=24000,
            features=[
                "Everything in Pro",
                "Team-scale usage envelopes",
                "Advanced collaboration controls",
                "Admin-level visibility",
            ],
            entitlement_keys=[
                "notes.create",
                "search.semantic",
                "thinking_sessions.create",
                "automations.create",
            ],
            metrics={
                "notes_created_monthly": PlanMetric(limit=10000, overage_rate_cents=1),
                "semantic_search_runs_monthly": PlanMetric(limit=25000, overage_rate_cents=1),
                "thinking_sessions_created_monthly": PlanMetric(limit=500, overage_rate_cents=10),
                "automations_created_monthly": PlanMetric(limit=200, overage_rate_cents=25),
            },
            cta_label="Start Team",
            stripe_price_ids={
                "monthly": settings.STRIPE_PRICE_ID_TEAM_MONTHLY,
                "annual": settings.STRIPE_PRICE_ID_TEAM_ANNUAL,
            },
        ),
        "enterprise": PlanDefinition(
            key="enterprise",
            name="Enterprise",
            description="For organizations that need scale, governance, and custom support.",
            monthly_price_cents=0,
            annual_price_cents=None,
            features=[
                "Everything in Team",
                "Unlimited usage envelopes",
                "Custom security and compliance options",
                "Dedicated success management",
            ],
            entitlement_keys=[
                "notes.create",
                "search.semantic",
                "thinking_sessions.create",
                "automations.create",
            ],
            metrics={
                "notes_created_monthly": PlanMetric(limit=None),
                "semantic_search_runs_monthly": PlanMetric(limit=None),
                "thinking_sessions_created_monthly": PlanMetric(limit=None),
                "automations_created_monthly": PlanMetric(limit=None),
            },
            cta_label="Contact sales",
            stripe_price_ids={
                "monthly": settings.STRIPE_PRICE_ID_ENTERPRISE_MONTHLY,
                "annual": settings.STRIPE_PRICE_ID_ENTERPRISE_ANNUAL,
            },
        ),
    }


PLAN_ORDER = ["free", "pro", "team", "enterprise"]


def plan_definitions() -> dict[str, PlanDefinition]:
    return _plan_definitions()


def get_plan_definition(plan_key: str) -> PlanDefinition:
    plans = _plan_definitions()
    resolved = plans.get((plan_key or "").lower())
    if resolved is None:
        return plans["free"]
    return resolved


def _month_period_bounds(now: Optional[datetime] = None) -> tuple[datetime, datetime]:
    dt = now.astimezone(timezone.utc) if now else datetime.now(timezone.utc)
    start = datetime(dt.year, dt.month, 1, tzinfo=timezone.utc)
    days_in_month = calendar.monthrange(dt.year, dt.month)[1]
    end = start + timedelta(days=days_in_month)
    return start, end


def _next_required_plan(metric_key: str, current_plan: str, usage: int) -> Optional[str]:
    plans = _plan_definitions()
    try:
        current_index = PLAN_ORDER.index(current_plan)
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
    profile = (
        await db.execute(
            select(WorkspaceBillingProfile).where(WorkspaceBillingProfile.workspace_id == workspace_id)
        )
    ).scalar_one_or_none()
    if profile is not None:
        return profile

    period_start, period_end = _month_period_bounds()
    profile = WorkspaceBillingProfile(
        workspace_id=workspace_id,
        plan=BillingPlan.FREE,
        billing_interval=BillingInterval.MONTHLY,
        status=BillingStatus.ACTIVE,
        period_start=period_start,
        period_end=period_end,
    )
    db.add(profile)
    await db.flush()
    await db.refresh(profile)
    return profile


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
    plan = get_plan_definition(profile.plan.value if isinstance(profile.plan, BillingPlan) else str(profile.plan))
    metric_limits = plan.metrics.get(metric_key)
    if metric_limits is None:
        metric_limits = PlanMetric(limit=None)

    counter = (
        await db.execute(
            select(UsageCounter).where(
                and_(
                    UsageCounter.workspace_id == workspace_id,
                    UsageCounter.metric_key == metric_key,
                    UsageCounter.period_start == period_start,
                    UsageCounter.period_end == period_end,
                )
            )
        )
    ).scalar_one_or_none()
    if counter is not None:
        if counter.included_limit != metric_limits.limit or counter.overage_rate_cents != metric_limits.overage_rate_cents:
            counter.included_limit = metric_limits.limit
            counter.overage_rate_cents = metric_limits.overage_rate_cents
            counter.unit_label = BILLING_METRICS[metric_key]["unit_label"]
            await db.flush()
        return counter

    counter = UsageCounter(
        workspace_id=workspace_id,
        metric_key=metric_key,
        period_start=period_start,
        period_end=period_end,
        usage_count=0,
        included_limit=metric_limits.limit,
        overage_rate_cents=metric_limits.overage_rate_cents,
        unit_label=BILLING_METRICS[metric_key]["unit_label"],
        counter_metadata={"plan": plan.key},
    )
    db.add(counter)
    await db.flush()
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
    counter = await get_or_create_usage_counter(
        db,
        workspace_id=workspace_id,
        metric_key=metric_key,
        profile=profile,
    )
    counter.usage_count = int(counter.usage_count or 0) + amount
    await db.flush()
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
    plan_key = profile.plan.value if isinstance(profile.plan, BillingPlan) else str(profile.plan or "free")
    plan = get_plan_definition(plan_key)
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


def serialize_plan_for_api(plan: PlanDefinition) -> dict[str, Any]:
    return {
        "key": plan.key,
        "name": plan.name,
        "description": plan.description,
        "monthly_price_cents": plan.monthly_price_cents,
        "annual_price_cents": plan.annual_price_cents,
        "annual_per_month_cents": plan.annual_per_month_cents,
        "annual_savings_cents": plan.annual_savings_cents,
        "features": plan.features,
        "entitlement_keys": plan.entitlement_keys,
        "limits": {
            metric_key: {
                "limit": metric.limit,
                "overage_rate_cents": metric.overage_rate_cents,
                "label": BILLING_METRICS.get(metric_key, {}).get("label", metric_key),
                "unit_label": BILLING_METRICS.get(metric_key, {}).get("unit_label", "units"),
            }
            for metric_key, metric in plan.metrics.items()
        },
        "highlighted": plan.highlighted,
        "cta_label": plan.cta_label,
        "stripe_price_ids": plan.stripe_price_ids or {"monthly": None, "annual": None},
    }


def serialize_plan_catalog_for_api() -> list[dict[str, Any]]:
    plans = _plan_definitions()
    return [serialize_plan_for_api(plans[key]) for key in PLAN_ORDER if key in plans]


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
    if interval == BillingInterval.ANNUAL.value and plan.annual_per_month_cents is not None:
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
    plan_key = profile.plan.value if isinstance(profile.plan, BillingPlan) else str(profile.plan or "free")
    plan = get_plan_definition(plan_key)

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
    status = profile.status.value if isinstance(profile.status, BillingStatus) else str(profile.status or "active")
    return {
        "workspace_id": str(workspace_id),
        "plan": serialize_plan_for_api(plan),
        "subscription": {
            "plan_key": plan.key,
            "billing_interval": interval,
            "status": status,
            "stripe_customer_id": profile.stripe_customer_id,
            "stripe_subscription_id": profile.stripe_subscription_id,
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
