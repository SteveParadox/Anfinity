"""Add persisted knowledge-graph nodes and edges.

Revision ID: 005_graph_data_model
Revises: c4d3e8b0a923
Create Date: 2026-04-10 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import ENUM


revision = "005_graph_data_model"
down_revision = "c4d3e8b0a923"
branch_labels = None
depends_on = None


graph_node_type = ENUM(
    "workspace",
    "note",
    "entity",
    "tag",
    name="graphnodetype",
    create_type=False,
)

graph_edge_type = ENUM(
    "workspace_contains_note",
    "note_mentions_entity",
    "note_has_tag",
    "note_links_note",
    "note_related_note",
    "entity_co_occurs_with_entity",
    "tag_co_occurs_with_tag",
    name="graphedgetype",
    create_type=False,
)


def upgrade() -> None:
    # Create enum types safely only if they do not already exist
    op.execute("""
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_type WHERE typname = 'graphnodetype'
        ) THEN
            CREATE TYPE graphnodetype AS ENUM (
                'workspace',
                'note',
                'entity',
                'tag'
            );
        END IF;
    END$$;
    """)

    op.execute("""
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_type WHERE typname = 'graphedgetype'
        ) THEN
            CREATE TYPE graphedgetype AS ENUM (
                'workspace_contains_note',
                'note_mentions_entity',
                'note_has_tag',
                'note_links_note',
                'note_related_note',
                'entity_co_occurs_with_entity',
                'tag_co_occurs_with_tag'
            );
        END IF;
    END$$;
    """)

    op.create_table(
        "graph_nodes",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("node_type", graph_node_type, nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=False),
        sa.Column("label", sa.String(length=500), nullable=False),
        sa.Column("normalized_label", sa.String(length=500), nullable=False),
        sa.Column("weight", sa.Float(), nullable=False, server_default=sa.text("1.0")),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "node_type",
            "external_id",
            name="uq_graph_node_workspace_type_external",
        ),
    )
    op.create_index(
        "idx_graph_node_workspace_type",
        "graph_nodes",
        ["workspace_id", "node_type"],
    )
    op.create_index(
        "idx_graph_node_workspace_label",
        "graph_nodes",
        ["workspace_id", "normalized_label"],
    )

    op.create_table(
        "graph_edges",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("edge_type", graph_edge_type, nullable=False),
        sa.Column(
            "source_node_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("graph_nodes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "target_node_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("graph_nodes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("weight", sa.Float(), nullable=False, server_default=sa.text("1.0")),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "edge_type",
            "source_node_id",
            "target_node_id",
            name="uq_graph_edge_workspace_type_pair",
        ),
    )
    op.create_index(
        "idx_graph_edge_workspace_type",
        "graph_edges",
        ["workspace_id", "edge_type"],
    )
    op.create_index(
        "idx_graph_edge_workspace_source",
        "graph_edges",
        ["workspace_id", "source_node_id"],
    )
    op.create_index(
        "idx_graph_edge_workspace_target",
        "graph_edges",
        ["workspace_id", "target_node_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_graph_edge_workspace_target", table_name="graph_edges")
    op.drop_index("idx_graph_edge_workspace_source", table_name="graph_edges")
    op.drop_index("idx_graph_edge_workspace_type", table_name="graph_edges")
    op.drop_table("graph_edges")

    op.drop_index("idx_graph_node_workspace_label", table_name="graph_nodes")
    op.drop_index("idx_graph_node_workspace_type", table_name="graph_nodes")
    op.drop_table("graph_nodes")

    op.execute("DROP TYPE IF EXISTS graphedgetype;")
    op.execute("DROP TYPE IF EXISTS graphnodetype;")