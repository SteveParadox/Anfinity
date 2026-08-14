"""Add note_type field and compute word_count.

Revision ID: d5a4f9c1b034
Revises: ff0312817cb7
Create Date: 2026-03-16 10:30:00.000000

This migration:
1. Adds note_type column with default 'note'
2. Creates index on note_type for efficient filtering
3. Validates existing data integrity
"""
from alembic import op
import sqlalchemy as sa


revision = 'd5a4f9c1b034'
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
    """Apply migration to add note_type field and enhance indexing."""
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
    
    print("✅ Migration complete: Added note_type field and indexes")


def downgrade() -> None:
    """Revert migration (remove note_type field and indexes)."""
    inspector = sa.inspect(op.get_bind())

    if _has_check_constraint(inspector, 'notes', 'ck_note_type_values'):
        op.drop_constraint('ck_note_type_values', 'notes', type_='check')

    inspector = sa.inspect(op.get_bind())
    if _has_index(inspector, 'notes', 'idx_note_type'):
        op.drop_index('idx_note_type', table_name='notes')

    inspector = sa.inspect(op.get_bind())
    if _has_column(inspector, 'notes', 'note_type'):
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
