"""Add note_type column to notes table.

Revision ID: add_note_type_to_notes
Revises: ff0312817cb7
Create Date: 2026-03-17 14:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'add_note_type_to_notes'
down_revision = 'ff0312817cb7'
branch_labels = None
depends_on = None


def _has_column(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))


def _has_index(inspector: sa.Inspector, table_name: str, index_name: str) -> bool:
    return any(index["name"] == index_name for index in inspector.get_indexes(table_name))


def _has_check_constraint(inspector: sa.Inspector, table_name: str, constraint_name: str) -> bool:
    return any(
        constraint["name"] == constraint_name
        for constraint in inspector.get_check_constraints(table_name)
    )


def upgrade() -> None:
    """Add note_type field and related constraints."""
    inspector = sa.inspect(op.get_bind())

    if not _has_column(inspector, 'notes', 'note_type'):
        op.add_column(
            'notes',
            sa.Column(
                'note_type',
                sa.String(50),
                nullable=False,
                server_default='note'
            )
        )

    inspector = sa.inspect(op.get_bind())
    if not _has_index(inspector, 'notes', 'idx_note_type'):
        op.create_index(
            'idx_note_type',
            'notes',
            ['note_type'],
            unique=False
        )

    inspector = sa.inspect(op.get_bind())
    if not _has_check_constraint(inspector, 'notes', 'ck_note_type_values'):
        op.create_check_constraint(
            'ck_note_type_values',
            'notes',
            "note_type IN ('note', 'web-clip', 'document', 'voice', 'ai-generated')"
        )


def downgrade() -> None:
    """Revert note_type field changes."""
    inspector = sa.inspect(op.get_bind())

    if _has_check_constraint(inspector, 'notes', 'ck_note_type_values'):
        op.drop_constraint('ck_note_type_values', 'notes', type_='check')

    inspector = sa.inspect(op.get_bind())
    if _has_index(inspector, 'notes', 'idx_note_type'):
        op.drop_index('idx_note_type', table_name='notes')

    inspector = sa.inspect(op.get_bind())
    if _has_column(inspector, 'notes', 'note_type'):
        op.drop_column('notes', 'note_type')
