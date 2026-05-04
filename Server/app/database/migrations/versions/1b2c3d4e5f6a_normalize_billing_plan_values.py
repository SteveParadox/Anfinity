"""Normalize billing plan values and protect billing columns.

Revision ID: 1b2c3d4e5f6a
Revises: 0a1b2c3d4e5f
Create Date: 2026-04-30 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "1b2c3d4e5f6a"
down_revision = "0a1b2c3d4e5f"
branch_labels = None
depends_on = None


PLAN_VALUES = ("free", "pro", "team", "enterprise")
PLAN_LEGACY_VALUES = ("FREE", "PRO", "TEAM", "ENTERPRISE", "business", "BUSINESS")
INTERVAL_VALUES = ("monthly", "annual")
STATUS_VALUES = ("active", "trialing", "past_due", "canceled", "unpaid", "incomplete")


def _has_table(table_name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(table_name)


def _has_column(table_name: str, column_name: str) -> bool:
    if not _has_table(table_name):
        return False
    columns = sa.inspect(op.get_bind()).get_columns(table_name)
    return any(column["name"] == column_name for column in columns)


def _quoted_csv(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _add_check_constraint_if_missing(table_name: str, constraint_name: str, expression: str) -> None:
    op.execute(
        sa.text(
            f"""
            DO $$
            BEGIN
                IF to_regclass('{table_name}') IS NOT NULL
                   AND NOT EXISTS (
                        SELECT 1
                        FROM pg_constraint
                        WHERE conname = '{constraint_name}'
                          AND conrelid = to_regclass('{table_name}')
                   ) THEN
                    ALTER TABLE {table_name}
                    ADD CONSTRAINT {constraint_name}
                    CHECK ({expression}) NOT VALID;
                END IF;
            END $$;
            """
        )
    )


def _normalize_plan_column(table_name: str, column_name: str = "plan") -> None:
    if not _has_column(table_name, column_name):
        return
    op.execute(sa.text(f"ALTER TABLE {table_name} ALTER COLUMN {column_name} DROP DEFAULT"))
    op.execute(
        sa.text(
            f"""
            ALTER TABLE {table_name}
            ALTER COLUMN {column_name} TYPE varchar(32)
            USING CASE
                WHEN lower({column_name}::text) = 'business' THEN 'enterprise'
                ELSE lower({column_name}::text)
            END
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            UPDATE {table_name}
            SET {column_name} = CASE
                WHEN lower({column_name}::text) = 'business' THEN 'enterprise'
                ELSE lower({column_name}::text)
            END
            WHERE {column_name}::text IN ({_quoted_csv(PLAN_LEGACY_VALUES)})
            """
        )
    )
    if table_name == "workspace_billing_profiles" and column_name == "plan":
        op.execute(sa.text("ALTER TABLE workspace_billing_profiles ALTER COLUMN plan SET DEFAULT 'free'"))
    _add_check_constraint_if_missing(
        table_name,
        f"ck_{table_name}_{column_name}",
        f"{column_name} IN ({_quoted_csv(PLAN_VALUES)})",
    )


def _normalize_workspace_billing_profile_enums() -> None:
    if not _has_table("workspace_billing_profiles"):
        return

    _normalize_plan_column("workspace_billing_profiles", "plan")

    if _has_column("workspace_billing_profiles", "billing_interval"):
        op.execute(sa.text("ALTER TABLE workspace_billing_profiles ALTER COLUMN billing_interval DROP DEFAULT"))
        op.execute(
            sa.text(
                """
                ALTER TABLE workspace_billing_profiles
                ALTER COLUMN billing_interval TYPE varchar(16)
                USING lower(billing_interval::text)
                """
            )
        )
        op.execute(
            sa.text(
                """
                UPDATE workspace_billing_profiles
                SET billing_interval = lower(billing_interval::text)
                WHERE billing_interval::text IN ('MONTHLY', 'ANNUAL')
                """
            )
        )
        op.execute(sa.text("ALTER TABLE workspace_billing_profiles ALTER COLUMN billing_interval SET DEFAULT 'monthly'"))
        _add_check_constraint_if_missing(
            "workspace_billing_profiles",
            "ck_workspace_billing_profiles_billing_interval",
            f"billing_interval IN ({_quoted_csv(INTERVAL_VALUES)})",
        )

    if _has_column("workspace_billing_profiles", "status"):
        op.execute(sa.text("ALTER TABLE workspace_billing_profiles ALTER COLUMN status DROP DEFAULT"))
        op.execute(
            sa.text(
                """
                ALTER TABLE workspace_billing_profiles
                ALTER COLUMN status TYPE varchar(32)
                USING lower(status::text)
                """
            )
        )
        op.execute(
            sa.text(
                """
                UPDATE workspace_billing_profiles
                SET status = lower(status::text)
                WHERE status::text IN ('ACTIVE', 'TRIALING', 'PAST_DUE', 'CANCELED', 'UNPAID', 'INCOMPLETE')
                """
            )
        )
        op.execute(sa.text("ALTER TABLE workspace_billing_profiles ALTER COLUMN status SET DEFAULT 'active'"))
        _add_check_constraint_if_missing(
            "workspace_billing_profiles",
            "ck_workspace_billing_profiles_status",
            f"status IN ({_quoted_csv(STATUS_VALUES)})",
        )


def _normalize_usage_counter_plan_metadata() -> None:
    if not _has_column("usage_counters", "metadata"):
        return
    op.execute(
        sa.text(
            """
            UPDATE usage_counters
            SET metadata = jsonb_set(
                COALESCE(metadata, '{}'::jsonb),
                '{plan}',
                to_jsonb((
                    CASE
                        WHEN lower(metadata->>'plan') = 'business' THEN 'enterprise'
                        ELSE lower(metadata->>'plan')
                    END
                )::text),
                true
            )
            WHERE metadata ? 'plan'
              AND metadata->>'plan' IN ('FREE', 'PRO', 'TEAM', 'ENTERPRISE', 'business', 'BUSINESS')
            """
        )
    )


def _drop_legacy_enum_type_if_unused(type_name: str) -> None:
    op.execute(
        sa.text(
            f"""
            DO $$
            BEGIN
                IF EXISTS (SELECT 1 FROM pg_type WHERE typname = '{type_name}')
                   AND NOT EXISTS (
                        SELECT 1
                        FROM pg_attribute a
                        JOIN pg_type t ON a.atttypid = t.oid
                        WHERE t.typname = '{type_name}'
                          AND a.attnum > 0
                          AND NOT a.attisdropped
                   ) THEN
                    DROP TYPE {type_name};
                END IF;
            END $$;
            """
        )
    )


def upgrade() -> None:
    _normalize_workspace_billing_profile_enums()
    _normalize_usage_counter_plan_metadata()

    # Defensive normalization/checks for related billing tables if they exist in
    # deployed databases ahead of the open-source schema.
    for table_name in (
        "subscriptions",
        "invoices",
        "entitlements",
        "workspace_billing_settings",
    ):
        _normalize_plan_column(table_name, "plan")
        _normalize_plan_column(table_name, "plan_key")

    _drop_legacy_enum_type_if_unused("billingplan")
    _drop_legacy_enum_type_if_unused("billinginterval")
    _drop_legacy_enum_type_if_unused("billingstatus")


def downgrade() -> None:
    # Reintroducing native PostgreSQL enum columns or uppercase legacy values
    # would risk corrupting live billing rows, so this migration is intentionally
    # not reversible.
    pass
