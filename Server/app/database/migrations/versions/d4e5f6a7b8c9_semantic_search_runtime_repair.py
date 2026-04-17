"""Repair semantic-search runtime schema after migration drift.

Revision ID: d4e5f6a7b8c9
Revises: c1d2e3f4a5b6
Create Date: 2026-04-16
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "d4e5f6a7b8c9"
down_revision = "c1d2e3f4a5b6"
branch_labels = None
depends_on = None

EMBEDDING_DIM = 768


def _has_table(inspector: sa.Inspector, table_name: str) -> bool:
    return table_name in inspector.get_table_names()


def _has_index(inspector: sa.Inspector, table_name: str, index_name: str) -> bool:
    return any(index["name"] == index_name for index in inspector.get_indexes(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    with op.get_context().autocommit_block():
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    if _has_table(inspector, "notes"):
        op.execute("ALTER TABLE notes ADD COLUMN IF NOT EXISTS content_tsv tsvector;")
        op.execute(
            """
            UPDATE notes
            SET content_tsv = to_tsvector('english', COALESCE(title, '') || ' ' || COALESCE(content, ''))
            WHERE content_tsv IS NULL;
            """
        )
        op.execute(f"ALTER TABLE notes ADD COLUMN IF NOT EXISTS embedding_vector vector({EMBEDDING_DIM});")
        if not _has_index(inspector, "notes", "idx_notes_content_tsv"):
            op.create_index("idx_notes_content_tsv", "notes", ["content_tsv"], unique=False, postgresql_using="gin")
        op.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_notes_embedding_vector_ivf
            ON notes USING ivfflat (embedding_vector vector_cosine_ops)
            WITH (lists = 100);
            """
        )
        op.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_notes_workspace_embedding
            ON notes(workspace_id)
            WHERE embedding_vector IS NOT NULL;
            """
        )
        op.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_notes_workspace_tsvector
            ON notes(workspace_id)
            WHERE content_tsv IS NOT NULL;
            """
        )

    if not _has_table(inspector, "note_interactions"):
        op.create_table(
            "note_interactions",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
            sa.Column("note_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("notes.id", ondelete="CASCADE"), nullable=False),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("workspace_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
            sa.Column("interaction_type", sa.String(length=50), nullable=False),
            sa.Column("context", postgresql.JSONB, nullable=True, server_default=sa.text("'{}'::jsonb")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        )
        inspector = sa.inspect(bind)

    if _has_table(inspector, "note_interactions"):
        if not _has_index(inspector, "note_interactions", "idx_note_interactions_note"):
            op.create_index("idx_note_interactions_note", "note_interactions", ["note_id", "created_at"], unique=False)
        if not _has_index(inspector, "note_interactions", "idx_note_interactions_user"):
            op.create_index("idx_note_interactions_user", "note_interactions", ["user_id", "created_at"], unique=False)
        if not _has_index(inspector, "note_interactions", "idx_note_interactions_workspace"):
            op.create_index("idx_note_interactions_workspace", "note_interactions", ["workspace_id", "created_at"], unique=False)
        if not _has_index(inspector, "note_interactions", "idx_note_interactions_type"):
            op.create_index("idx_note_interactions_type", "note_interactions", ["interaction_type"], unique=False)
        if not _has_index(inspector, "note_interactions", "idx_note_interactions_compound"):
            op.create_index("idx_note_interactions_compound", "note_interactions", ["workspace_id", "interaction_type", "created_at"], unique=False)

    if _has_table(inspector, "search_queries"):
        op.execute(f"ALTER TABLE search_queries ADD COLUMN IF NOT EXISTS query_embedding_vector vector({EMBEDDING_DIM});")
        op.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_search_queries_embedding_vector_ivf
            ON search_queries USING ivfflat (query_embedding_vector vector_cosine_ops)
            WITH (lists = 100);
            """
        )

    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION hybrid_search(
            p_query_text VARCHAR,
            p_query_embedding vector({EMBEDDING_DIM}),
            p_workspace_id UUID,
            p_limit INT DEFAULT 10,
            p_similarity_weight FLOAT DEFAULT 0.60,
            p_recency_weight FLOAT DEFAULT 0.25,
            p_usage_weight FLOAT DEFAULT 0.15
        ) RETURNS TABLE (
            note_id UUID,
            title VARCHAR,
            content TEXT,
            note_type VARCHAR,
            workspace_id UUID,
            user_id UUID,
            created_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ,
            embedding_similarity FLOAT,
            text_score FLOAT,
            interaction_score FLOAT,
            final_score FLOAT,
            highlight TEXT
        ) AS $$
        WITH vector_search AS (
            SELECT
                n.id,
                n.title,
                n.content,
                n.note_type,
                n.workspace_id,
                n.user_id,
                n.created_at,
                n.updated_at,
                CASE
                    WHEN n.embedding_vector IS NULL THEN 0.0
                    ELSE 1.0 - ((n.embedding_vector <=> p_query_embedding) / 2.0)
                END AS similarity
            FROM notes n
            WHERE n.workspace_id = p_workspace_id
              AND n.embedding_vector IS NOT NULL
            ORDER BY n.embedding_vector <=> p_query_embedding
            LIMIT p_limit * 4
        ),
        text_search AS (
            SELECT
                n.id,
                LEAST(
                    1.0,
                    COALESCE(ts_rank(n.content_tsv, plainto_tsquery('english', p_query_text)), 0.0) / 0.25
                ) AS text_rank
            FROM notes n
            WHERE n.workspace_id = p_workspace_id
              AND n.content_tsv IS NOT NULL
              AND n.content_tsv @@ plainto_tsquery('english', p_query_text)
            LIMIT p_limit * 4
        ),
        interaction_tracking AS (
            SELECT
                note_id,
                CASE
                    WHEN COUNT(*) = 0 THEN 0.0
                    ELSE LEAST(1.0, LN(1.0 + COUNT(*)) / LN(11.0))
                END AS usage_score
            FROM note_interactions
            WHERE workspace_id = p_workspace_id
            GROUP BY note_id
        ),
        merged_results AS (
            SELECT DISTINCT ON (n.id)
                n.id AS note_id,
                n.title,
                n.content,
                n.note_type,
                n.workspace_id,
                n.user_id,
                n.created_at,
                n.updated_at,
                COALESCE(vs.similarity, 0.0) AS similarity,
                COALESCE(ts.text_rank, 0.0) AS text_score,
                COALESCE(it.usage_score, 0.0) AS usage_score
            FROM notes n
            LEFT JOIN vector_search vs ON vs.id = n.id
            LEFT JOIN text_search ts ON ts.id = n.id
            LEFT JOIN interaction_tracking it ON it.note_id = n.id
            WHERE n.workspace_id = p_workspace_id
              AND (COALESCE(vs.similarity, 0.0) > 0.0 OR COALESCE(ts.text_rank, 0.0) > 0.0)
        ),
        scored_results AS (
            SELECT
                note_id,
                title,
                content,
                note_type,
                workspace_id,
                user_id,
                created_at,
                updated_at,
                similarity,
                text_score,
                usage_score,
                POWER(0.5, EXTRACT(EPOCH FROM (NOW() - created_at)) / (28.0 * 86400.0)) AS recency_score,
                LEAST(1.0, (similarity * 0.7) + (text_score * 0.3)) AS semantic_score
            FROM merged_results
        )
        SELECT
            note_id,
            title,
            content,
            note_type,
            workspace_id,
            user_id,
            created_at,
            updated_at,
            ROUND(similarity::numeric, 4)::FLOAT AS embedding_similarity,
            ROUND(text_score::numeric, 4)::FLOAT AS text_score,
            ROUND(usage_score::numeric, 4)::FLOAT AS interaction_score,
            ROUND(
                (
                    p_similarity_weight * semantic_score +
                    p_recency_weight * recency_score +
                    p_usage_weight * usage_score
                )::numeric,
                4
            )::FLOAT AS final_score,
            CASE
                WHEN POSITION(LOWER(p_query_text) IN LOWER(content)) > 0 THEN
                    SUBSTRING(content FROM GREATEST(POSITION(LOWER(p_query_text) IN LOWER(content)) - 30, 1) FOR 180)
                ELSE SUBSTRING(content FROM 1 FOR 180)
            END AS highlight
        FROM scored_results
        ORDER BY final_score DESC, created_at DESC
        LIMIT p_limit;
        $$ LANGUAGE SQL STABLE;
        """
    )

    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION vector_search_only(
            p_query_embedding vector({EMBEDDING_DIM}),
            p_workspace_id UUID,
            p_limit INT DEFAULT 10,
            p_similarity_threshold FLOAT DEFAULT 0.6
        ) RETURNS TABLE (
            note_id UUID,
            title VARCHAR,
            content TEXT,
            similarity FLOAT
        ) AS $$
        SELECT
            n.id,
            n.title,
            n.content,
            1.0 - ((n.embedding_vector <=> p_query_embedding) / 2.0) AS similarity
        FROM notes n
        WHERE n.workspace_id = p_workspace_id
          AND n.embedding_vector IS NOT NULL
          AND 1.0 - ((n.embedding_vector <=> p_query_embedding) / 2.0) >= p_similarity_threshold
        ORDER BY n.embedding_vector <=> p_query_embedding
        LIMIT p_limit;
        $$ LANGUAGE SQL STABLE;
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION text_search_only(
            p_query_text VARCHAR,
            p_workspace_id UUID,
            p_limit INT DEFAULT 10
        ) RETURNS TABLE (
            note_id UUID,
            title VARCHAR,
            content TEXT,
            text_rank FLOAT
        ) AS $$
        SELECT
            n.id,
            n.title,
            n.content,
            ts_rank(n.content_tsv, plainto_tsquery('english', p_query_text)) / 10.0 AS text_rank
        FROM notes n
        WHERE n.workspace_id = p_workspace_id
          AND n.content_tsv IS NOT NULL
          AND n.content_tsv @@ plainto_tsquery('english', p_query_text)
        ORDER BY text_rank DESC, n.created_at DESC
        LIMIT p_limit;
        $$ LANGUAGE SQL STABLE;
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION get_interaction_stats(
            p_workspace_id UUID
        ) RETURNS TABLE (
            note_id UUID,
            interaction_count INT,
            last_interaction TIMESTAMPTZ,
            interaction_types TEXT[]
        ) AS $$
        SELECT
            note_id,
            COUNT(*) AS interaction_count,
            MAX(created_at) AS last_interaction,
            ARRAY_AGG(DISTINCT interaction_type) AS interaction_types
        FROM note_interactions
        WHERE workspace_id = p_workspace_id
        GROUP BY note_id;
        $$ LANGUAGE SQL STABLE;
        """
    )

    if _has_table(inspector, "notes"):
        op.execute("ANALYZE notes;")
    if _has_table(inspector, "search_queries"):
        op.execute("ANALYZE search_queries;")
    if _has_table(inspector, "note_interactions"):
        op.execute("ANALYZE note_interactions;")


def downgrade() -> None:
    # Forward repair only. We intentionally keep the restored runtime schema.
    pass
