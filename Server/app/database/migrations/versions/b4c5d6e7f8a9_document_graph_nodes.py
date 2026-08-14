"""Add document-backed knowledge graph node and edge types.

Revision ID: b4c5d6e7f8a9
Revises: c1d2e3f4a5b6
Create Date: 2026-04-16 16:20:00.000000
"""

from alembic import op


revision = "b4c5d6e7f8a9"
down_revision = "c1d2e3f4a5b6"
branch_labels = None
depends_on = None


def _add_enum_value(enum_name: str, value: str) -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_enum
                WHERE enumlabel = '{value}'
                  AND enumtypid = '{enum_name}'::regtype
            ) THEN
                ALTER TYPE {enum_name} ADD VALUE '{value}';
            END IF;
        END$$;
        """
    )


def upgrade() -> None:
    _add_enum_value("graphnodetype", "document")
    _add_enum_value("graphedgetype", "workspace_contains_document")
    _add_enum_value("graphedgetype", "document_mentions_entity")
    _add_enum_value("graphedgetype", "document_has_tag")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values safely in-place.
    pass
