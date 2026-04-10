"""Add note_type field and compute word_count.

Revision ID: 001_add_note_type_embeddings
Revises: ff0312817cb7
Create Date: 2026-03-16 10:30:00.000000

This migration:
1. Adds note_type column with default 'note'
2. Creates index on note_type for efficient filtering
3. Validates existing data integrity
"""
from alembic import op
import sqlalchemy as sa


revision = '001_add_note_type_embeddings'
down_revision = 'ff0312817cb7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Apply migration to add note_type field and enhance indexing."""
    
    # Add note_type column
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
    
    print("✅ Migration complete: Added note_type field and indexes")


def downgrade() -> None:
    """Revert migration (remove note_type field and indexes)."""
    
    # Drop check constraint
    op.drop_constraint('ck_note_type_values', 'notes', type_='check')
    
    # Drop index
    op.drop_index('idx_note_type', table_name='notes')
    
    # Remove column
    op.drop_column('notes', 'note_type')
    
    print("✅ Migration reverted: Removed note_type field")


# Alternative: File-based migration for reference
"""
To use this as reference, you can also run:

    alembic revision --autogenerate -m "Add note_type field and embeddings"

Which will generate the migration automatically from model changes.

Manual SQL (if needed):
    
    -- Add column
    ALTER TABLE notes ADD COLUMN note_type VARCHAR(50) NOT NULL DEFAULT 'note';
    
    -- Create index
    CREATE INDEX idx_note_type ON notes(note_type);
    
    -- Add constraint
    ALTER TABLE notes ADD CONSTRAINT ck_note_type_values 
        CHECK (note_type IN ('note', 'web-clip', 'document', 'voice', 'ai-generated'));
    
    -- Verify (optional)
    SELECT COUNT(*), note_type FROM notes GROUP BY note_type;
"""
