"""Add chunk_status and updated_at columns to chunks table for idempotent embedding retry logic.

Revision ID: 001_add_chunk_status_updated_at
Revises: add_note_type_to_notes
Create Date: 2026-03-18 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = '001_add_chunk_status_updated_at'
down_revision = 'add_note_type_to_notes'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add chunk_status and updated_at columns to chunks table."""
    
    # Create ENUM type for ChunkStatus
    chunk_status_enum = postgresql.ENUM(
        'pending',
        'embedded',
        'failed',
        name='chunk_status_enum'
    )
    chunk_status_enum.create(op.get_bind(), checkfirst=True)
    
    # Add chunk_status column with default value
    op.add_column(
        'chunks',
        sa.Column(
            'chunk_status',
            postgresql.ENUM('pending', 'embedded', 'failed', name='chunk_status_enum'),
            nullable=False,
            server_default='pending'
        )
    )
    
    # Add updated_at column with default value and auto-update
    op.add_column(
        'chunks',
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now()
        )
    )
    
    # Create index for chunk_status for fast filtering during retries
    op.create_index(
        'idx_chunk_status',
        'chunks',
        ['chunk_status'],
        unique=False
    )
    
    # Create composite index for efficient document + status queries
    op.create_index(
        'idx_chunk_document_status',
        'chunks',
        ['document_id', 'chunk_status'],
        unique=False
    )


def downgrade() -> None:
    """Revert chunk_status and updated_at changes."""
    # Drop indexes
    op.drop_index('idx_chunk_document_status', table_name='chunks')
    op.drop_index('idx_chunk_status', table_name='chunks')
    
    # Drop columns
    op.drop_column('chunks', 'updated_at')
    op.drop_column('chunks', 'chunk_status')
    
    # Drop ENUM type
    chunk_status_enum = postgresql.ENUM(
        'pending',
        'embedded',
        'failed',
        name='chunk_status_enum'
    )
    chunk_status_enum.drop(op.get_bind(), checkfirst=True)
