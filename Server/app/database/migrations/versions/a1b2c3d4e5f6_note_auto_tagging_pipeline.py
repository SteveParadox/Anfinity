"""Add note capture events and enrichment step tracking.

Revision ID: a1b2c3d4e5f6
Revises: a0b1c2d3e4f5
Create Date: 2026-04-24 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "a1b2c3d4e5f6"
down_revision = "a0b1c2d3e4f5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "note_capture_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("correlation_id", sa.String(length=255), nullable=False),
        sa.Column("capture_source", sa.String(length=100), nullable=False),
        sa.Column("capture_path", sa.String(length=100), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("note_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="received"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["note_id"], ["notes.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("idempotency_key", name="uq_note_capture_events_idempotency_key"),
    )
    op.create_index("ix_note_capture_events_correlation_id", "note_capture_events", ["correlation_id"])
    op.create_index("ix_note_capture_events_capture_source", "note_capture_events", ["capture_source"])
    op.create_index("ix_note_capture_events_capture_path", "note_capture_events", ["capture_path"])
    op.create_index("ix_note_capture_events_workspace_id", "note_capture_events", ["workspace_id"])
    op.create_index("ix_note_capture_events_user_id", "note_capture_events", ["user_id"])
    op.create_index("ix_note_capture_events_note_id", "note_capture_events", ["note_id"])
    op.create_index("ix_note_capture_events_content_hash", "note_capture_events", ["content_hash"])
    op.create_index("ix_note_capture_events_status", "note_capture_events", ["status"])
    op.create_index("idx_note_capture_events_workspace_source", "note_capture_events", ["workspace_id", "capture_source", "created_at"])
    op.create_index("idx_note_capture_events_note_status", "note_capture_events", ["note_id", "status", "updated_at"])

    op.create_table(
        "note_enrichment_steps",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("note_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("capture_event_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("step", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("correlation_id", sa.String(length=255), nullable=True),
        sa.Column("task_id", sa.String(length=255), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["capture_event_id"], ["note_capture_events.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["note_id"], ["notes.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("note_id", "step", name="uq_note_enrichment_steps_note_step"),
    )
    op.create_index("ix_note_enrichment_steps_note_id", "note_enrichment_steps", ["note_id"])
    op.create_index("ix_note_enrichment_steps_capture_event_id", "note_enrichment_steps", ["capture_event_id"])
    op.create_index("ix_note_enrichment_steps_step", "note_enrichment_steps", ["step"])
    op.create_index("ix_note_enrichment_steps_status", "note_enrichment_steps", ["status"])
    op.create_index("ix_note_enrichment_steps_correlation_id", "note_enrichment_steps", ["correlation_id"])
    op.create_index("idx_note_enrichment_steps_event_status", "note_enrichment_steps", ["capture_event_id", "status", "updated_at"])
    op.create_index("idx_note_enrichment_steps_note_status", "note_enrichment_steps", ["note_id", "status", "updated_at"])


def downgrade() -> None:
    op.drop_index("idx_note_enrichment_steps_note_status", table_name="note_enrichment_steps")
    op.drop_index("idx_note_enrichment_steps_event_status", table_name="note_enrichment_steps")
    op.drop_index("ix_note_enrichment_steps_correlation_id", table_name="note_enrichment_steps")
    op.drop_index("ix_note_enrichment_steps_status", table_name="note_enrichment_steps")
    op.drop_index("ix_note_enrichment_steps_step", table_name="note_enrichment_steps")
    op.drop_index("ix_note_enrichment_steps_capture_event_id", table_name="note_enrichment_steps")
    op.drop_index("ix_note_enrichment_steps_note_id", table_name="note_enrichment_steps")
    op.drop_table("note_enrichment_steps")

    op.drop_index("idx_note_capture_events_note_status", table_name="note_capture_events")
    op.drop_index("idx_note_capture_events_workspace_source", table_name="note_capture_events")
    op.drop_index("ix_note_capture_events_status", table_name="note_capture_events")
    op.drop_index("ix_note_capture_events_content_hash", table_name="note_capture_events")
    op.drop_index("ix_note_capture_events_note_id", table_name="note_capture_events")
    op.drop_index("ix_note_capture_events_user_id", table_name="note_capture_events")
    op.drop_index("ix_note_capture_events_workspace_id", table_name="note_capture_events")
    op.drop_index("ix_note_capture_events_capture_path", table_name="note_capture_events")
    op.drop_index("ix_note_capture_events_capture_source", table_name="note_capture_events")
    op.drop_index("ix_note_capture_events_correlation_id", table_name="note_capture_events")
    op.drop_table("note_capture_events")
