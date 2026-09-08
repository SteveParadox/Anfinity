from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from starlette.requests import Request

from app.api import billing as billing_api
from app.billing import plans
from app.core.entitlements import EntitlementRequiredError
from app.database.models import BillingInterval, BillingPlan, BillingStatus, UsageCounter, WorkspaceBillingProfile
from app.services import billing


class _ScalarResult:
    def __init__(self, value=None, *, rowcount: int = 0):
        self.value = value
        self.rowcount = rowcount

    def scalar_one_or_none(self):
        return self.value

    def scalar_one(self):
        return self.value

    def mappings(self):
        return self

    def first(self):
        return self.value


class _FakeAsyncSession:
    def __init__(self, *results):
        self.results = list(results)
        self.execute_calls = []

    async def execute(self, statement, params=None):
        self.execute_calls.append((statement, params))
        if not self.results:
            raise AssertionError(f"No fake result queued for statement: {statement}")
        return self.results.pop(0)


class _RollbackTrackingSession:
    def __init__(self, user):
        self.user = user
        self.rolled_back = False

    async def rollback(self):
        self.rolled_back = True
        self.user.expired = True


class _UserThatRaisesAfterRollback:
    def __init__(self):
        self._id = uuid4()
        self.expired = False
        self.id_reads = 0

    @property
    def id(self):
        self.id_reads += 1
        if self.expired:
            raise AssertionError("current_user.id was accessed after rollback")
        return self._id


def _profile(workspace_id, plan=BillingPlan.free) -> WorkspaceBillingProfile:
    return WorkspaceBillingProfile(
        id=uuid4(),
        workspace_id=workspace_id,
        plan=plan,
        billing_interval=BillingInterval.monthly,
        status=BillingStatus.active,
        period_start=datetime(2026, 4, 1, tzinfo=timezone.utc),
        period_end=datetime(2026, 5, 1, tzinfo=timezone.utc),
        currency="usd",
    )


def _counter(workspace_id, metric_key: str, *, usage_count: int = 0, limit: int | None = 100) -> UsageCounter:
    return UsageCounter(
        id=uuid4(),
        workspace_id=workspace_id,
        metric_key=metric_key,
        period_start=datetime(2026, 4, 1, tzinfo=timezone.utc),
        period_end=datetime(2026, 5, 1, tzinfo=timezone.utc),
        usage_count=usage_count,
        included_limit=limit,
        overage_rate_cents=None,
        unit_label="units",
    )


@pytest.mark.asyncio
async def test_missing_billing_profile_is_created_as_free_with_conflict_safe_insert() -> None:
    workspace_id = uuid4()
    profile = _profile(workspace_id)
    db = _FakeAsyncSession(
        _ScalarResult(rowcount=0),
        _ScalarResult(None),
        _ScalarResult(profile.id),
        _ScalarResult(profile),
    )

    resolved = await billing.get_or_create_workspace_billing_profile(db, workspace_id)

    assert resolved is profile
    assert resolved.plan == BillingPlan.free
    assert len(db.execute_calls) == 4
    assert "ON CONFLICT" in str(db.execute_calls[2][0])


@pytest.mark.asyncio
async def test_existing_billing_profile_is_reused() -> None:
    workspace_id = uuid4()
    profile = _profile(workspace_id, BillingPlan.team)
    db = _FakeAsyncSession(_ScalarResult(rowcount=0), _ScalarResult(profile))

    resolved = await billing.get_or_create_workspace_billing_profile(db, workspace_id)

    assert resolved is profile
    assert len(db.execute_calls) == 2


@pytest.mark.asyncio
async def test_atomic_usage_increment_uses_postgres_upsert() -> None:
    workspace_id = uuid4()
    profile = _profile(workspace_id, BillingPlan.pro)
    counter = _counter(workspace_id, "notes_created_monthly", usage_count=8, limit=2000)
    db = _FakeAsyncSession(_ScalarResult(counter.id), _ScalarResult(counter))

    resolved = await billing.increment_usage_counter(
        db,
        workspace_id=workspace_id,
        metric_key="notes_created_monthly",
        amount=3,
        profile=profile,
    )

    assert resolved is counter
    statement_sql = str(db.execute_calls[0][0])
    assert "ON CONFLICT" in statement_sql
    assert "usage_count" in statement_sql


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("plan", "expected_limit"),
    [
        (BillingPlan.free, 200),
        (BillingPlan.pro, 5000),
        (BillingPlan.team, 25000),
        (BillingPlan.enterprise, None),
    ],
)
async def test_usage_dashboard_returns_plan_limits_and_zero_missing_counters(monkeypatch, plan, expected_limit) -> None:
    workspace_id = uuid4()
    profile = _profile(workspace_id, plan)
    plan_definition = billing.get_plan_definition(plan)

    async def fake_profile(_db, _workspace_id):
        return profile

    async def fake_counter(_db, *, workspace_id, metric_key, profile, now=None):
        del now
        metric = plan_definition.metrics[metric_key]
        return _counter(workspace_id, metric_key, usage_count=0, limit=metric.limit)

    monkeypatch.setattr(billing, "get_or_create_workspace_billing_profile", fake_profile)
    monkeypatch.setattr(billing, "get_or_create_usage_counter", fake_counter)

    payload = await billing.get_workspace_usage_dashboard(SimpleNamespace(), workspace_id=workspace_id)

    semantic_metric = next(
        metric for metric in payload["usage_metrics"]
        if metric["metric_key"] == "semantic_search_runs_monthly"
    )
    assert payload["subscription"]["plan_key"] == plan.value
    assert "stripe_customer_id" not in payload["subscription"]
    assert semantic_metric["current_usage"] == 0
    assert semantic_metric["limit"] == expected_limit


@pytest.mark.asyncio
async def test_entitlement_over_limit_raises_typed_402_metadata(monkeypatch) -> None:
    workspace_id = uuid4()
    profile = _profile(workspace_id, BillingPlan.free)

    async def fake_profile(_db, _workspace_id):
        return profile

    async def fake_counter(_db, *, workspace_id, metric_key, profile, now=None):
        del metric_key, profile, now
        return _counter(workspace_id, "notes_created_monthly", usage_count=100, limit=100)

    monkeypatch.setattr(billing, "get_or_create_workspace_billing_profile", fake_profile)
    monkeypatch.setattr(billing, "get_or_create_usage_counter", fake_counter)

    with pytest.raises(EntitlementRequiredError) as exc_info:
        await billing.enforce_entitlement_limit(
            SimpleNamespace(),
            workspace_id=workspace_id,
            metric_key="notes_created_monthly",
            increment=1,
        )

    metadata = exc_info.value.metadata.to_dict()
    assert metadata["code"] == "ENTITLEMENT_LIMIT_REACHED"
    assert metadata["current_plan"] == "free"
    assert metadata["required_plan"] == "pro"
    assert metadata["usage"] == 100


@pytest.mark.asyncio
async def test_usage_endpoint_does_not_read_expired_user_after_billing_rollback(monkeypatch) -> None:
    workspace_id = uuid4()
    user = _UserThatRaisesAfterRollback()
    db = _RollbackTrackingSession(user)

    async def fake_permission(*args, **kwargs):
        return None

    async def fake_dashboard(_db, *, workspace_id):
        raise billing.BillingStateError(
            "bad billing state",
            workspace_id=workspace_id,
            billing_profile_id=uuid4(),
            plan="FREE",
        )

    monkeypatch.setattr(billing_api, "ensure_workspace_permission", fake_permission)
    monkeypatch.setattr(billing_api, "get_workspace_usage_dashboard", fake_dashboard)

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/billing/usage",
            "headers": [(b"x-request-id", b"req-test")],
        }
    )

    response = await billing_api.get_workspace_usage(
        workspace_id,
        request,
        user,
        db,
    )

    assert db.rolled_back is True
    assert user.id_reads == 1
    assert response.status_code == 409
    payload = json.loads(response.body)
    assert payload["error"]["code"] == "BILLING_STATE_INVALID"
    assert payload["error"]["metadata"]["workspace_id"] == str(workspace_id)


def test_stripe_price_mapping_is_lowercase_and_unknown_is_rejected(monkeypatch) -> None:
    monkeypatch.setattr(plans.settings, "STRIPE_PRICE_ID_PRO_MONTHLY", "price_pro_monthly")

    assert billing.get_plan_key_for_stripe_price_id("price_pro_monthly") == "pro"
    assert billing.get_plan_key_for_stripe_price_id("price_unknown") is None


def test_billing_migration_normalizes_uppercase_plans_and_adds_checks() -> None:
    migration = Path("app/database/migrations/versions/1b2c3d4e5f6a_normalize_billing_plan_values.py")
    source = migration.read_text(encoding="utf-8")

    assert "FREE" in source
    assert "lower({column_name}::text)" in source
    assert 'PLAN_VALUES = ("free", "pro", "team", "enterprise")' in source
    assert "CHECK ({expression}) NOT VALID" in source
    assert "jsonb_set" in source
