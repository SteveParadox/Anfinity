"""Add note comment notification type.

Revision ID: a0b1c2d3e4f5
Revises: 9d0e1f2a3b4c
Create Date: 2026-04-24 12:00:00.000000
"""

from alembic import op


revision = "a0b1c2d3e4f5"
down_revision = "9d0e1f2a3b4c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE usernotificationtype ADD VALUE IF NOT EXISTS 'note_comment'")


def downgrade() -> None:
    # PostgreSQL enum values cannot be removed safely without rebuilding the type.
    pass
