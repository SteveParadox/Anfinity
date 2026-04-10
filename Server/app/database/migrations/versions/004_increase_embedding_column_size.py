"""Increase embedding column size for 1536D vectors.

Revision ID: 004_increase_embedding_column_size
Revises: 003_hybrid_search_function
Create Date: 2026-03-26 21:40:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '004_increase_embedding_column_size'
down_revision = '003_hybrid_search_function'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Increase embedding column sizes to accommodate 1536D vectors.
    
    Issue: 1536-dimensional embeddings serialized to JSON exceed VARCHAR(10000)
    - A 1536D float vector as JSON is ~20-25KB when serialized
    - Solution: Use TEXT type (effectively unlimited) instead of VARCHAR(10000)
    
    Affected columns:
    - notes.embedding
    - search_queries.query_embedding
    """
    
    # Modify notes.embedding column
    op.alter_column(
        'notes',
        'embedding',
        existing_type=sa.String(10000),
        type_=sa.Text(),
        existing_nullable=True,
        nullable=True,
        comment='JSON-serialized embedding vector for semantic search (1536D)'
    )
    
    # Modify search_queries.query_embedding column
    op.alter_column(
        'search_queries',
        'query_embedding',
        existing_type=sa.String(10000),
        type_=sa.Text(),
        existing_nullable=True,
        nullable=True,
        comment='JSON-serialized query embedding vector (1536D)'
    )


def downgrade() -> None:
    """Revert embedding column sizes back to VARCHAR(10000).
    
    WARNING: This will TRUNCATE any embeddings larger than 10000 characters!
    Only apply if rolling back to a version that doesn't support large embeddings.
    """
    
    # Revert notes.embedding column
    op.alter_column(
        'notes',
        'embedding',
        existing_type=sa.Text(),
        type_=sa.String(10000),
        existing_nullable=True,
        nullable=True,
        comment='Store as text for pgvector compatibility'
    )
    
    # Revert search_queries.query_embedding column
    op.alter_column(
        'search_queries',
        'query_embedding',
        existing_type=sa.Text(),
        type_=sa.String(10000),
        existing_nullable=True,
        nullable=True,
        comment='Store as text for pgvector compatibility'
    )
