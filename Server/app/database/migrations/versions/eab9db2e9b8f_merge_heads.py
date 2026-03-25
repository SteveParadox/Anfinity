"""merge heads

Revision ID: eab9db2e9b8f
Revises: 001_add_note_type_embeddings, add_note_type_to_notes
Create Date: 2026-03-18 08:43:11.630113

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'eab9db2e9b8f'
down_revision = ('001_add_note_type_embeddings', 'add_note_type_to_notes')
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
