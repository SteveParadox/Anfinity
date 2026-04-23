"""Add automation trigger/action system.

Revision ID: 7b8c9d0e1f2a
Revises: 6a7b8c9d0e1f
Create Date: 2026-04-23 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "7b8c9d0e1f2a"
down_revision = "6a7b8c9d0e1f"
branch_labels = None
depends_on = None


def _table_exists(bind: sa.Connection, table_name: str) -> bool:
    return bool(
        bind.execute(
            sa.text("SELECT to_regclass(:table_name) IS NOT NULL"),
            {"table_name": f"public.{table_name}"},
        ).scalar()
    )


def _column_exists(bind: sa.Connection, table_name: str, column_name: str) -> bool:
    return bool(
        bind.execute(
            sa.text(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = :table_name
                  AND column_name = :column_name
                """
            ),
            {"table_name": table_name, "column_name": column_name},
        ).scalar()
    )


def _index_exists(bind: sa.Connection, index_name: str) -> bool:
    return bool(
        bind.execute(
            sa.text("SELECT to_regclass(:index_name) IS NOT NULL"),
            {"index_name": f"public.{index_name}"},
        ).scalar()
    )


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    bind = op.get_bind()
    if not _column_exists(bind, table_name, column.name):
        op.add_column(table_name, column)


def _create_index_if_missing(index_name: str, table_name: str, columns: tuple[str, ...], *, unique: bool = False) -> None:
    bind = op.get_bind()
    if not _index_exists(bind, index_name):
        op.create_index(index_name, table_name, list(columns), unique=unique)


def upgrade() -> None:
    bind = op.get_bind()

    op.execute("ALTER TYPE usernotificationtype ADD VALUE IF NOT EXISTS 'automation'")

    _add_column_if_missing("workspaces", sa.Column("slug", sa.String(length=255), nullable=True))
    op.execute(
        """
        WITH slugged AS (
            SELECT
                id,
                COALESCE(
                    NULLIF(lower(regexp_replace(regexp_replace(name, '[^a-zA-Z0-9]+', '-', 'g'), '(^-|-$)', '', 'g')), ''),
                    'workspace'
                ) AS base_slug
            FROM workspaces
            WHERE slug IS NULL OR slug = ''
        ),
        ranked AS (
            SELECT
                id,
                base_slug,
                row_number() OVER (PARTITION BY base_slug ORDER BY id) AS slug_rank
            FROM slugged
        )
        UPDATE workspaces
        SET slug = CASE
            WHEN ranked.slug_rank = 1 THEN ranked.base_slug
            ELSE ranked.base_slug || '-' || ranked.slug_rank::text
        END
        FROM ranked
        WHERE workspaces.id = ranked.id
        """
    )
    _create_index_if_missing("ix_workspaces_slug", "workspaces", ("slug",), unique=True)

    if not _table_exists(bind, "automations"):
        op.create_table(
            "automations",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("trigger_type", sa.String(length=100), nullable=False),
            sa.Column("conditions", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
            sa.Column("actions", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        )

    _create_index_if_missing("ix_automations_workspace_id", "automations", ("workspace_id",))
    _create_index_if_missing("ix_automations_created_by_user_id", "automations", ("created_by_user_id",))
    _create_index_if_missing("ix_automations_trigger_type", "automations", ("trigger_type",))
    _create_index_if_missing("ix_automations_enabled", "automations", ("enabled",))
    _create_index_if_missing("ix_automations_created_at", "automations", ("created_at",))
    _create_index_if_missing("ix_automations_updated_at", "automations", ("updated_at",))
    _create_index_if_missing(
        "idx_automations_workspace_trigger_enabled",
        "automations",
        ("workspace_id", "trigger_type", "enabled"),
    )
    _create_index_if_missing("idx_automations_workspace_updated", "automations", ("workspace_id", "updated_at"))


def downgrade() -> None:
    op.drop_index("idx_automations_workspace_updated", table_name="automations")
    op.drop_index("idx_automations_workspace_trigger_enabled", table_name="automations")
    op.drop_index("ix_automations_updated_at", table_name="automations")
    op.drop_index("ix_automations_created_at", table_name="automations")
    op.drop_index("ix_automations_enabled", table_name="automations")
    op.drop_index("ix_automations_trigger_type", table_name="automations")
    op.drop_index("ix_automations_created_by_user_id", table_name="automations")
    op.drop_index("ix_automations_workspace_id", table_name="automations")
    op.drop_table("automations")
    op.drop_index("ix_workspaces_slug", table_name="workspaces")
    op.drop_column("workspaces", "slug")
