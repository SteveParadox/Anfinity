"""Add persisted note connection suggestions.

Revision ID: 006_connection_suggestions
Revises: 005_graph_data_model
Create Date: 2026-04-10 14:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "006_connection_suggestions"
down_revision = "005_graph_data_model"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "note_connection_suggestions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_note_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("notes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("suggested_note_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("notes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("similarity_score", sa.Float(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True, server_default=sa.text("'{}'::jsonb")),
        sa.Column("responded_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("workspace_id", "source_note_id", "suggested_note_id", name="uq_note_connection_suggestion_pair"),
    )
    op.create_index("idx_note_connection_suggestions_source_status", "note_connection_suggestions", ["source_note_id", "status", "updated_at"])
    op.create_index("idx_note_connection_suggestions_workspace_status", "note_connection_suggestions", ["workspace_id", "status", "created_at"])


def downgrade() -> None:
    op.drop_index("idx_note_connection_suggestions_workspace_status", table_name="note_connection_suggestions")
    op.drop_index("idx_note_connection_suggestions_source_status", table_name="note_connection_suggestions")
    op.drop_table("note_connection_suggestions")
