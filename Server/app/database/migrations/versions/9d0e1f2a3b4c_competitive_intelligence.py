"""Add competitive intelligence monitoring state.

Revision ID: 9d0e1f2a3b4c
Revises: 8c9d0e1f2a3b
Create Date: 2026-04-24 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "9d0e1f2a3b4c"
down_revision = "8c9d0e1f2a3b"
branch_labels = None
depends_on = None


def _table_exists(bind: sa.Connection, table_name: str) -> bool:
    return bool(
        bind.execute(
            sa.text("SELECT to_regclass(:table_name) IS NOT NULL"),
            {"table_name": f"public.{table_name}"},
        ).scalar()
    )


def _index_exists(bind: sa.Connection, index_name: str) -> bool:
    return bool(
        bind.execute(
            sa.text("SELECT to_regclass(:index_name) IS NOT NULL"),
            {"index_name": f"public.{index_name}"},
        ).scalar()
    )


def _create_index_if_missing(index_name: str, table_name: str, columns: tuple[str, ...], *, unique: bool = False) -> None:
    if not _index_exists(op.get_bind(), index_name):
        op.create_index(index_name, table_name, list(columns), unique=unique)


def upgrade() -> None:
    bind = op.get_bind()

    if not _table_exists(bind, "competitive_sources"):
        op.create_table(
            "competitive_sources",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("url", sa.String(length=1000), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("check_interval_minutes", sa.Integer(), nullable=False, server_default="1440"),
            sa.Column("last_content_hash", sa.String(length=64), nullable=True),
            sa.Column("last_processed_hash", sa.String(length=64), nullable=True),
            sa.Column("last_successful_fetch_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_changed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("run_status", sa.String(length=50), nullable=False, server_default="idle"),
            sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
            sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
            sa.UniqueConstraint("workspace_id", "url", name="uq_competitive_sources_workspace_url"),
        )

    if not _table_exists(bind, "competitive_snapshots"):
        op.create_table(
            "competitive_snapshots",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("url", sa.String(length=1000), nullable=False),
            sa.Column("reader_url", sa.String(length=1500), nullable=True),
            sa.Column("content_hash", sa.String(length=64), nullable=True),
            sa.Column("extraction_status", sa.String(length=50), nullable=False),
            sa.Column("is_changed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("normalized_content", sa.Text(), nullable=True),
            sa.Column("content_length", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.ForeignKeyConstraint(["source_id"], ["competitive_sources.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        )

    if not _table_exists(bind, "competitive_analyses"):
        op.create_table(
            "competitive_analyses",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("previous_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("content_hash", sa.String(length=64), nullable=False),
            sa.Column("previous_content_hash", sa.String(length=64), nullable=True),
            sa.Column("diff_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column("analysis_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column("findings", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
            sa.Column("headline_summary", sa.Text(), nullable=True),
            sa.Column("overall_urgency", sa.Float(), nullable=False, server_default="0"),
            sa.Column("urgency_label", sa.String(length=50), nullable=False, server_default="low"),
            sa.Column("should_trigger_immediate_workflow", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("workflow_dispatch_status", sa.String(length=50), nullable=False, server_default="not_required"),
            sa.Column("slack_dispatch_status", sa.String(length=50), nullable=False, server_default="not_required"),
            sa.Column("alert_dedupe_key", sa.String(length=128), nullable=True),
            sa.Column("model_used", sa.String(length=100), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.ForeignKeyConstraint(["source_id"], ["competitive_sources.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["snapshot_id"], ["competitive_snapshots.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["previous_snapshot_id"], ["competitive_snapshots.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("source_id", "content_hash", name="uq_competitive_analyses_source_hash"),
        )

    for table, columns in {
        "competitive_sources": (
            ("ix_competitive_sources_workspace_id", ("workspace_id",)),
            ("ix_competitive_sources_created_by_user_id", ("created_by_user_id",)),
            ("ix_competitive_sources_is_active", ("is_active",)),
            ("ix_competitive_sources_last_content_hash", ("last_content_hash",)),
            ("ix_competitive_sources_last_processed_hash", ("last_processed_hash",)),
            ("ix_competitive_sources_last_successful_fetch_at", ("last_successful_fetch_at",)),
            ("ix_competitive_sources_last_checked_at", ("last_checked_at",)),
            ("ix_competitive_sources_last_changed_at", ("last_changed_at",)),
            ("ix_competitive_sources_run_status", ("run_status",)),
            ("ix_competitive_sources_lease_until", ("lease_until",)),
            ("ix_competitive_sources_created_at", ("created_at",)),
            ("ix_competitive_sources_updated_at", ("updated_at",)),
            ("idx_competitive_sources_due", ("is_active", "last_checked_at")),
            ("idx_competitive_sources_workspace_active", ("workspace_id", "is_active")),
        ),
        "competitive_snapshots": (
            ("ix_competitive_snapshots_source_id", ("source_id",)),
            ("ix_competitive_snapshots_workspace_id", ("workspace_id",)),
            ("ix_competitive_snapshots_content_hash", ("content_hash",)),
            ("ix_competitive_snapshots_extraction_status", ("extraction_status",)),
            ("ix_competitive_snapshots_is_changed", ("is_changed",)),
            ("ix_competitive_snapshots_created_at", ("created_at",)),
            ("idx_competitive_snapshots_source_hash", ("source_id", "content_hash")),
            ("idx_competitive_snapshots_source_created", ("source_id", "created_at")),
        ),
        "competitive_analyses": (
            ("ix_competitive_analyses_source_id", ("source_id",)),
            ("ix_competitive_analyses_snapshot_id", ("snapshot_id",)),
            ("ix_competitive_analyses_previous_snapshot_id", ("previous_snapshot_id",)),
            ("ix_competitive_analyses_workspace_id", ("workspace_id",)),
            ("ix_competitive_analyses_content_hash", ("content_hash",)),
            ("ix_competitive_analyses_previous_content_hash", ("previous_content_hash",)),
            ("ix_competitive_analyses_overall_urgency", ("overall_urgency",)),
            ("ix_competitive_analyses_urgency_label", ("urgency_label",)),
            ("ix_competitive_analyses_should_trigger_immediate_workflow", ("should_trigger_immediate_workflow",)),
            ("ix_competitive_analyses_workflow_dispatch_status", ("workflow_dispatch_status",)),
            ("ix_competitive_analyses_slack_dispatch_status", ("slack_dispatch_status",)),
            ("ix_competitive_analyses_alert_dedupe_key", ("alert_dedupe_key",)),
            ("ix_competitive_analyses_created_at", ("created_at",)),
            ("ix_competitive_analyses_updated_at", ("updated_at",)),
            ("idx_competitive_analyses_workspace_created", ("workspace_id", "created_at")),
            ("idx_competitive_analyses_source_urgency", ("source_id", "overall_urgency")),
        ),
    }.items():
        for index_name, column_names in columns:
            _create_index_if_missing(index_name, table, column_names, unique=index_name == "ix_competitive_analyses_alert_dedupe_key")


def downgrade() -> None:
    for table in ("competitive_analyses", "competitive_snapshots", "competitive_sources"):
        if _table_exists(op.get_bind(), table):
            op.drop_table(table)
