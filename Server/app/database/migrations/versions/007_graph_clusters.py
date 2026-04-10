"""Add persisted semantic graph clusters.

Revision ID: 007_graph_clusters
Revises: 006_connection_suggestions
Create Date: 2026-04-10 18:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "007_graph_clusters"
down_revision = "006_connection_suggestions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "graph_clusters",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("cluster_key", sa.String(length=120), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("importance", sa.Float(), nullable=False, server_default=sa.text("1.0")),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("workspace_id", "cluster_key", name="uq_graph_cluster_workspace_key"),
    )
    op.create_index("idx_graph_cluster_workspace_updated", "graph_clusters", ["workspace_id", "updated_at"])

    op.create_table(
        "graph_cluster_memberships",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("cluster_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("graph_clusters.id", ondelete="CASCADE"), nullable=False),
        sa.Column("node_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("graph_nodes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("membership_score", sa.Float(), nullable=False, server_default=sa.text("0.0")),
        sa.Column("cluster_rank", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("workspace_id", "node_id", name="uq_graph_cluster_membership_workspace_node"),
        sa.UniqueConstraint("cluster_id", "node_id", name="uq_graph_cluster_membership_cluster_node"),
    )
    op.create_index(
        "idx_graph_cluster_membership_cluster_rank",
        "graph_cluster_memberships",
        ["cluster_id", "cluster_rank"],
    )
    op.create_index(
        "idx_graph_cluster_membership_workspace_cluster",
        "graph_cluster_memberships",
        ["workspace_id", "cluster_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_graph_cluster_membership_workspace_cluster", table_name="graph_cluster_memberships")
    op.drop_index("idx_graph_cluster_membership_cluster_rank", table_name="graph_cluster_memberships")
    op.drop_table("graph_cluster_memberships")

    op.drop_index("idx_graph_cluster_workspace_updated", table_name="graph_clusters")
    op.drop_table("graph_clusters")
