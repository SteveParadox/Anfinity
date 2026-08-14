"""Add note lineage history snapshots.

Revision ID: e0f9d8c7b6a5
Revises: 008_semantic_search_hardening
Create Date: 2026-04-14
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "e0f9d8c7b6a5"
down_revision = "008_semantic_search_hardening"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "note_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("note_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("change_reason", sa.String(length=50), nullable=False, server_default="updated"),
        sa.Column("restored_from_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("tags", postgresql.JSONB(astext_type=sa.Text()), nullable=True, server_default=sa.text("'[]'::jsonb")),
        sa.Column("connections", postgresql.JSONB(astext_type=sa.Text()), nullable=True, server_default=sa.text("'[]'::jsonb")),
        sa.Column("note_type", sa.String(length=50), nullable=False, server_default="note"),
        sa.Column("source_url", sa.String(length=1000), nullable=True),
        sa.Column("word_count", sa.Integer(), nullable=True, server_default="0"),
        sa.Column("diff_segments", postgresql.JSONB(astext_type=sa.Text()), nullable=True, server_default=sa.text("'[]'::jsonb")),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["note_id"], ["notes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["restored_from_version_id"], ["note_versions.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("note_id", "version_number", name="uq_note_versions_note_version_number"),
    )
    op.create_index("idx_note_versions_note_created", "note_versions", ["note_id", "created_at"], unique=False)
    op.create_index("idx_note_versions_workspace_note", "note_versions", ["workspace_id", "note_id", "created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_note_versions_workspace_note", table_name="note_versions")
    op.drop_index("idx_note_versions_note_created", table_name="note_versions")
    op.drop_table("note_versions")
