"""Merge semantic search and billing heads."""

from alembic import op

# revision identifiers, used by Alembic.
revision = "3f4a5b6c7d8e"
down_revision = (
    "002_semantic_phase1",
    "2c3d4e5f6a7b",
)
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    raise NotImplementedError("Downgrade not supported for merge migration.")
