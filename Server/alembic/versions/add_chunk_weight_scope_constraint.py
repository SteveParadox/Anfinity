"""Add unique constraint on chunk_weights for workspace/document/chunk scope.

Revision ID: add_chunk_weight_scope_constraint
Revises: 001_add_chunk_status_updated_at
Create Date: 2026-04-21 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'add_chunk_weight_scope_constraint'
down_revision = '001_add_chunk_status_updated_at'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add unique constraint on chunk_weights table for workspace/document/chunk scope."""
    # Add the unique constraint that was defined in the model but missing from the database
    op.create_unique_constraint(
        "uq_chunk_weight_scope",
        "chunk_weights",
        ["workspace_id", "document_id", "chunk_id"],
    )


def downgrade() -> None:
    """Remove the unique constraint."""
    op.drop_constraint(
        "uq_chunk_weight_scope",
        "chunk_weights",
        type_="unique",
    )
