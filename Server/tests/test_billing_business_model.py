from __future__ import annotations

import pytest
from sqlalchemy.dialects.postgresql import dialect as postgres_dialect

from app.billing.plans import coerce_billing_plan_key
from app.database.models import BillingPlan, WorkspaceBillingProfile
from app.services import billing


def test_plan_catalog_uses_canonical_public_plan_ids() -> None:
    catalog = billing.serialize_plan_catalog_for_api()
    by_key = {plan["key"]: plan for plan in catalog}

    assert [plan["key"] for plan in catalog] == ["free", "pro", "team", "enterprise"]
    assert by_key["free"]["limits"]["notes_created_monthly"]["limit"] == 100
    assert by_key["pro"]["monthly_price_cents"] == 1200
    assert by_key["team"]["monthly_price_cents"] == 1800
    assert by_key["team"]["annual_per_month_cents"] == 1440
    assert by_key["team"]["highlighted"] is True
    assert by_key["enterprise"]["annual_price_cents"] is None
    assert by_key["enterprise"]["limits"]["semantic_search_runs_monthly"]["limit"] is None


def test_required_plan_escalates_from_team_to_enterprise() -> None:
    assert billing._next_required_plan("semantic_search_runs_monthly", "free", 201) == "pro"
    assert billing._next_required_plan("semantic_search_runs_monthly", "team", 25001) == "enterprise"
    assert billing._next_required_plan("semantic_search_runs_monthly", "enterprise", 999999) is None


def test_billing_plan_enum_persists_lowercase_values_and_rejects_invalid_strings() -> None:
    plan_column = WorkspaceBillingProfile.__table__.c.plan
    bind_processor = plan_column.type.bind_processor(postgres_dialect())

    assert BillingPlan.free.value == "free"
    assert plan_column.type.enums == ["free", "pro", "team", "enterprise"]
    assert plan_column.type.native_enum is False
    assert "FREE" not in plan_column.type.enums
    assert bind_processor is not None
    assert bind_processor(BillingPlan.free) == "free"
    with pytest.raises(LookupError):
        bind_processor("FREE")


def test_legacy_business_plan_is_mapped_forward_without_being_public() -> None:
    assert "business" not in [plan["key"] for plan in billing.serialize_plan_catalog_for_api()]
    assert coerce_billing_plan_key("business") == "enterprise"


def test_unknown_plan_is_rejected_explicitly() -> None:
    with pytest.raises(ValueError):
        coerce_billing_plan_key("starter")
