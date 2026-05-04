"""Canonical billing plan catalog and Stripe price mapping."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Optional

from app.config import settings


logger = logging.getLogger(__name__)

PUBLIC_BILLING_PLAN_VALUES: tuple[str, ...] = ("free", "pro", "team", "enterprise")
VALID_BILLING_PLAN_VALUES: tuple[str, ...] = PUBLIC_BILLING_PLAN_VALUES
LEGACY_PLAN_ALIASES: dict[str, str] = {
    "FREE": "free",
    "PRO": "pro",
    "TEAM": "team",
    "ENTERPRISE": "enterprise",
    # Business briefly existed as an internal tier. Preserve access by mapping
    # legacy rows to the next supported tier instead of dropping data.
    "business": "enterprise",
    "BUSINESS": "enterprise",
}

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
    supports_team_features: bool = False
    supports_admin_features: bool = False
    public: bool = True
    overage_rules: Optional[dict[str, str]] = None

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
            description="For solo testers validating a workspace-native AI knowledge system.",
            monthly_price_cents=0,
            annual_price_cents=0,
            features=[
                "1 workspace for personal knowledge capture",
                "Limited notes and semantic searches",
                "Basic dashboard and settings",
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
            public=True,
        ),
        "pro": PlanDefinition(
            key="pro",
            name="Pro",
            description="For solo power users, researchers, founders, and builders.",
            monthly_price_cents=1200,
            annual_price_cents=11520,
            features=[
                "Everything in Free",
                "Full AI Search and Ask Your Past Self",
                "Knowledge graph, source cards, and smart highlights",
                "Personal exports and higher monthly usage caps",
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
            cta_label="Upgrade to Pro",
            stripe_price_ids={
                "monthly": settings.STRIPE_PRICE_ID_PRO_MONTHLY,
                "annual": settings.STRIPE_PRICE_ID_PRO_ANNUAL,
            },
            public=True,
        ),
        "team": PlanDefinition(
            key="team",
            name="Team",
            description="For startups, agencies, product teams, and research teams.",
            monthly_price_cents=1800,
            annual_price_cents=17280,
            features=[
                "Everything in Pro",
                "Shared workspaces, comments, mentions, and note invites",
                "Approval workflows and team feedback analytics",
                "Slack, Notion, Google, and GitHub integration access",
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
            highlighted=True,
            cta_label="Start Team",
            stripe_price_ids={
                "monthly": settings.STRIPE_PRICE_ID_TEAM_MONTHLY,
                "annual": settings.STRIPE_PRICE_ID_TEAM_ANNUAL,
            },
            supports_team_features=True,
            supports_admin_features=True,
            public=True,
        ),
        "enterprise": PlanDefinition(
            key="enterprise",
            name="Enterprise",
            description="For larger organizations with security review and deployment needs.",
            monthly_price_cents=0,
            annual_price_cents=None,
            features=[
                "Everything in Team",
                "Unlimited usage envelopes",
                "SSO/SAML, SCIM, SOC2 support path, and custom retention",
                "Dedicated support, private deployment options, and custom integrations",
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
            supports_team_features=True,
            supports_admin_features=True,
            public=True,
        ),
    }


PLAN_ORDER: list[str] = ["free", "pro", "team", "enterprise"]


def coerce_billing_plan_key(plan_key: object, *, allow_legacy: bool = True) -> str:
    raw = str(getattr(plan_key, "value", plan_key) or "").strip()
    if allow_legacy and raw in LEGACY_PLAN_ALIASES:
        return LEGACY_PLAN_ALIASES[raw]
    normalized = raw.lower()
    if allow_legacy and normalized in LEGACY_PLAN_ALIASES:
        return LEGACY_PLAN_ALIASES[normalized]
    if normalized not in VALID_BILLING_PLAN_VALUES:
        raise ValueError(f"Unsupported billing plan: {raw or '<empty>'}")
    return normalized


def plan_definitions() -> dict[str, PlanDefinition]:
    return _plan_definitions()


def get_plan_definition(plan_key: object) -> PlanDefinition:
    plans = _plan_definitions()
    return plans[coerce_billing_plan_key(plan_key)]


def get_plan_key_for_stripe_price_id(price_id: Optional[str]) -> Optional[str]:
    if not price_id:
        return None
    for plan in _plan_definitions().values():
        for configured_price_id in (plan.stripe_price_ids or {}).values():
            if configured_price_id and configured_price_id == price_id:
                return plan.key
    logger.warning(
        "billing_event=unknown_stripe_price_id stripe_price_id=%s",
        price_id,
    )
    return None


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
        "supports_team_features": plan.supports_team_features,
        "supports_admin_features": plan.supports_admin_features,
        "public": plan.public,
        "overage_rules": plan.overage_rules or {},
    }


def serialize_plan_catalog_for_api() -> list[dict[str, Any]]:
    plans = _plan_definitions()
    return [serialize_plan_for_api(plans[key]) for key in PLAN_ORDER if key in plans]
