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


def upgrade() -> None:
    """Add note_type field and related constraints."""
    # Add note_type column with default value
    op.add_column(
        'notes',
        sa.Column(
            'note_type',
            sa.String(50),
            nullable=False,
            server_default='note'
        )
    )
    
    # Create index for efficient type filtering
    op.create_index(
        'idx_note_type',
        'notes',
        ['note_type'],
        unique=False
    )
    
    # Add check constraint to validate note_type values
    op.create_check_constraint(
        'ck_note_type_values',
        'notes',
        "note_type IN ('note', 'web-clip', 'document', 'voice', 'ai-generated')"
    )


def downgrade() -> None:
    """Revert note_type field changes."""
    op.drop_constraint('ck_note_type_values', 'notes')
    op.drop_index('idx_note_type')
    op.drop_column('notes', 'note_type')
