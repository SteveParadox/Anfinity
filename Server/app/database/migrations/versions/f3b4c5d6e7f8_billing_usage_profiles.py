"""Add workspace billing profiles and usage counters.

Revision ID: f3b4c5d6e7f8
Revises: eab9db2e9b8f
Create Date: 2026-04-28 19:20:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "f3b4c5d6e7f8"
down_revision = "eab9db2e9b8f"
branch_labels = None
depends_on = None


# Define enums with create_type=False to prevent duplicate creation errors
billing_plan_enum = postgresql.ENUM(
    "free",
    "pro",
    "team",
    "enterprise",
    name="billingplan",
    create_type=False,
)

billing_interval_enum = postgresql.ENUM(
    "monthly",
    "annual",
    name="billinginterval",
    create_type=False,
)

billing_status_enum = postgresql.ENUM(
    "active",
    "trialing",
    "past_due",
    "canceled",
    "incomplete",
    name="billingstatus",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    billing_plan_enum.create(bind, checkfirst=True)
    billing_interval_enum.create(bind, checkfirst=True)
    billing_status_enum.create(bind, checkfirst=True)

    op.create_table(
        "workspace_billing_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("plan", billing_plan_enum, nullable=False, server_default="free"),
        sa.Column("billing_interval", billing_interval_enum, nullable=False, server_default="monthly"),
        sa.Column("status", billing_status_enum, nullable=False, server_default="active"),
        sa.Column("stripe_customer_id", sa.String(length=255), nullable=True),
        sa.Column("stripe_subscription_id", sa.String(length=255), nullable=True),
        sa.Column("stripe_price_id", sa.String(length=255), nullable=True),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default="usd"),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_at_period_end", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id"),
    )
    op.create_index("ix_workspace_billing_profiles_workspace_id", "workspace_billing_profiles", ["workspace_id"], unique=False)
    op.create_index("ix_workspace_billing_profiles_plan", "workspace_billing_profiles", ["plan"], unique=False)
    op.create_index("ix_workspace_billing_profiles_billing_interval", "workspace_billing_profiles", ["billing_interval"], unique=False)
    op.create_index("ix_workspace_billing_profiles_status", "workspace_billing_profiles", ["status"], unique=False)
    op.create_index("ix_workspace_billing_profiles_stripe_customer_id", "workspace_billing_profiles", ["stripe_customer_id"], unique=False)
    op.create_index("ix_workspace_billing_profiles_stripe_subscription_id", "workspace_billing_profiles", ["stripe_subscription_id"], unique=False)
    op.create_index("ix_workspace_billing_profiles_stripe_price_id", "workspace_billing_profiles", ["stripe_price_id"], unique=False)
    op.create_index("ix_workspace_billing_profiles_created_at", "workspace_billing_profiles", ["created_at"], unique=False)
    op.create_index("ix_workspace_billing_profiles_updated_at", "workspace_billing_profiles", ["updated_at"], unique=False)

    op.create_table(
        "usage_counters",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("metric_key", sa.String(length=120), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("usage_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("included_limit", sa.Integer(), nullable=True),
        sa.Column("overage_rate_cents", sa.Integer(), nullable=True),
        sa.Column("unit_label", sa.String(length=64), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "metric_key",
            "period_start",
            "period_end",
            name="uq_usage_counter_workspace_metric_period",
        ),
    )
    op.create_index("ix_usage_counters_workspace_id", "usage_counters", ["workspace_id"], unique=False)
    op.create_index("ix_usage_counters_metric_key", "usage_counters", ["metric_key"], unique=False)
    op.create_index("ix_usage_counters_period_start", "usage_counters", ["period_start"], unique=False)
    op.create_index("ix_usage_counters_period_end", "usage_counters", ["period_end"], unique=False)
    op.create_index("ix_usage_counters_created_at", "usage_counters", ["created_at"], unique=False)
    op.create_index("ix_usage_counters_updated_at", "usage_counters", ["updated_at"], unique=False)
    op.create_index("idx_usage_counters_workspace_metric", "usage_counters", ["workspace_id", "metric_key"], unique=False)
    op.create_index("idx_usage_counters_workspace_period", "usage_counters", ["workspace_id", "period_start", "period_end"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_usage_counters_workspace_period", table_name="usage_counters")
    op.drop_index("idx_usage_counters_workspace_metric", table_name="usage_counters")
    op.drop_index("ix_usage_counters_updated_at", table_name="usage_counters")
    op.drop_index("ix_usage_counters_created_at", table_name="usage_counters")
    op.drop_index("ix_usage_counters_period_end", table_name="usage_counters")
    op.drop_index("ix_usage_counters_period_start", table_name="usage_counters")
    op.drop_index("ix_usage_counters_metric_key", table_name="usage_counters")
    op.drop_index("ix_usage_counters_workspace_id", table_name="usage_counters")
    op.drop_table("usage_counters")

    op.drop_index("ix_workspace_billing_profiles_updated_at", table_name="workspace_billing_profiles")
    op.drop_index("ix_workspace_billing_profiles_created_at", table_name="workspace_billing_profiles")
    op.drop_index("ix_workspace_billing_profiles_stripe_price_id", table_name="workspace_billing_profiles")
    op.drop_index("ix_workspace_billing_profiles_stripe_subscription_id", table_name="workspace_billing_profiles")
    op.drop_index("ix_workspace_billing_profiles_stripe_customer_id", table_name="workspace_billing_profiles")
    op.drop_index("ix_workspace_billing_profiles_status", table_name="workspace_billing_profiles")
    op.drop_index("ix_workspace_billing_profiles_billing_interval", table_name="workspace_billing_profiles")
    op.drop_index("ix_workspace_billing_profiles_plan", table_name="workspace_billing_profiles")
    op.drop_index("ix_workspace_billing_profiles_workspace_id", table_name="workspace_billing_profiles")
    op.drop_table("workspace_billing_profiles")

    bind = op.get_bind()
    billing_status_enum.drop(bind, checkfirst=True)
    billing_interval_enum.drop(bind, checkfirst=True)
    billing_plan_enum.drop(bind, checkfirst=True)
