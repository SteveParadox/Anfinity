"""Add Business billing plan enum value.

Revision ID: 0a1b2c3d4e5f
Revises: f3b4c5d6e7f8
Create Date: 2026-04-29 00:00:00.000000
"""

from alembic import op


revision = "0a1b2c3d4e5f"
down_revision = "f3b4c5d6e7f8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE billingplan ADD VALUE IF NOT EXISTS 'business'")


def downgrade() -> None:
    # PostgreSQL cannot safely remove an enum value without rewriting dependent
    # columns. Leaving the value in place keeps existing billing rows readable.
    pass
