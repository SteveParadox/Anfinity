"""Pydantic schemas for public billing API contracts."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


BillingPlanKey = Literal["free", "pro", "team", "enterprise"]
BillingIntervalValue = Literal["monthly", "annual"]
BillingStatusValue = Literal["active", "trialing", "past_due", "canceled", "unpaid", "incomplete"]


class BillingPlanLimitResponse(BaseModel):
    limit: Optional[int] = None
    overage_rate_cents: Optional[int] = None
    label: str
    unit_label: str


class BillingPlanDefinitionResponse(BaseModel):
    key: BillingPlanKey
    name: str
    description: str
    monthly_price_cents: int
    annual_price_cents: Optional[int] = None
    annual_per_month_cents: Optional[int] = None
    annual_savings_cents: Optional[int] = None
    features: list[str]
    entitlement_keys: list[str]
    limits: dict[str, BillingPlanLimitResponse]
    highlighted: bool = False
    cta_label: str
    stripe_price_ids: dict[BillingIntervalValue, Optional[str]]
    supports_team_features: bool = False
    supports_admin_features: bool = False
    public: bool = True
    overage_rules: dict[str, str] = Field(default_factory=dict)


class PlanCatalogResponse(BaseModel):
    plans: list[BillingPlanDefinitionResponse]


class BillingSubscriptionResponse(BaseModel):
    plan_key: BillingPlanKey
    billing_interval: BillingIntervalValue
    status: BillingStatusValue
    period_start: Optional[str] = None
    period_end: Optional[str] = None
    cancel_at_period_end: bool = False
    currency: str = "usd"


class WorkspaceBillingResponse(BaseModel):
    workspace_id: str
    subscription: BillingSubscriptionResponse
    plan: BillingPlanDefinitionResponse


class BillingUsageMetricResponse(BaseModel):
    metric_key: str
    label: str
    description: str
    unit_label: str
    current_usage: int = Field(ge=0)
    limit: Optional[int] = None
    percentage_used: Optional[float] = None
    period_start: str
    period_end: str
    projected_usage: int = Field(ge=0)
    projected_overage_units: int = Field(ge=0)
    projected_overage_cents: int = Field(ge=0)
    overage_rate_cents: Optional[int] = None


class ProjectedMonthlyCostResponse(BaseModel):
    base_monthly_cents: int = Field(ge=0)
    projected_overage_cents: int = Field(ge=0)
    projected_total_monthly_cents: int = Field(ge=0)


class BillingUsageResponse(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    workspace_id: str
    plan: BillingPlanDefinitionResponse
    subscription: BillingSubscriptionResponse
    usage_metrics: list[BillingUsageMetricResponse]
    projected_monthly_cost: ProjectedMonthlyCostResponse
