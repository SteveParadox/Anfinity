from alembic import op


revision = "5b6c7d8e9f0a"
down_revision = "4a5b6c7d8e9f"
branch_labels = None
depends_on = None

EMBEDDING_DIM = 2048


def upgrade() -> None:
    op.execute(f"""
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
                ELSE 1.0 -
                    ((n.embedding_vector <=> p_query_embedding) / 2.0)
            END AS similarity
        FROM notes n
        WHERE n.workspace_id = p_workspace_id
          AND n.embedding_vector IS NOT NULL
        ORDER BY n.embedding_vector <=> p_query_embedding
        LIMIT p_limit * 3
    ),

    text_search AS (
        SELECT
            n.id,
            n.title,
            n.content,
            n.note_type,
            n.workspace_id,
            n.user_id,
            n.created_at,
            n.updated_at,
            0.0 AS similarity,
            CASE
                WHEN n.content_tsv IS NULL THEN 0.0
                ELSE ts_rank(
                    n.content_tsv,
                    plainto_tsquery('english', p_query_text)
                ) / 10.0
            END AS text_rank
        FROM notes n
        WHERE n.workspace_id = p_workspace_id
          AND n.content_tsv IS NOT NULL
          AND n.content_tsv @@ plainto_tsquery(
              'english',
              p_query_text
          )
        LIMIT p_limit * 3
    ),

    interaction_tracking AS (
        SELECT
            note_id,
            CASE
                WHEN COUNT(*) = 0 THEN 0.0
                ELSE LN(1.0 + COUNT(*)) / LN(11.0)
            END AS usage_score
        FROM note_interactions
        WHERE workspace_id = p_workspace_id
        GROUP BY note_id
    ),

    merged_results AS (
        SELECT DISTINCT ON (COALESCE(vs.id, ts.id))
            COALESCE(vs.id, ts.id) AS note_id,
            COALESCE(vs.title, ts.title) AS title,
            COALESCE(vs.content, ts.content) AS content,
            COALESCE(vs.note_type, ts.note_type) AS note_type,
            COALESCE(vs.workspace_id, ts.workspace_id) AS workspace_id,
            COALESCE(vs.user_id, ts.user_id) AS user_id,
            COALESCE(vs.created_at, ts.created_at) AS created_at,
            COALESCE(vs.updated_at, ts.updated_at) AS updated_at,
            COALESCE(vs.similarity, 0.0) AS similarity,
            COALESCE(ts.text_rank, 0.0) AS text_score,
            COALESCE(it.usage_score, 0.0) AS usage_score
        FROM vector_search vs
        FULL OUTER JOIN text_search ts
            ON vs.id = ts.id
        LEFT JOIN interaction_tracking it
            ON COALESCE(vs.id, ts.id) = it.note_id
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
            POWER(
                0.5,
                EXTRACT(EPOCH FROM (NOW() - created_at))
                / (28.0 * 86400.0)
            ) AS recency_score,

            (
                p_similarity_weight * similarity +
                p_usage_weight * usage_score +
                p_recency_weight * POWER(
                    0.5,
                    EXTRACT(EPOCH FROM (NOW() - created_at))
                    / (28.0 * 86400.0)
                )
            ) AS final_score

        FROM merged_results
        WHERE similarity > 0.0
           OR text_score > 0.0
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
        ROUND(similarity::numeric, 4)::FLOAT,
        ROUND(text_score::numeric, 4)::FLOAT,
        ROUND(usage_score::numeric, 4)::FLOAT,
        ROUND(final_score::numeric, 4)::FLOAT,
        SUBSTRING(content, 1, 150)
    FROM scored_results
    ORDER BY final_score DESC, created_at DESC
    LIMIT p_limit;

    $$ LANGUAGE SQL STABLE;
    """)


def downgrade() -> None:
    op.execute("""
        DROP FUNCTION IF EXISTS hybrid_search(
            VARCHAR,
            vector(2048),
            UUID,
            INT,
            FLOAT,
            FLOAT,
            FLOAT
        );
    """)