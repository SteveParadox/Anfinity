"""
PHASE 4: POSTGRESQL HYBRID SEARCH SQL FUNCTION

This migration creates a PostgreSQL stored function that performs hybrid search:
1. Vector similarity search (semantic)
2. Full-text search (keyword matching) 
3. Interaction tracking (user engagement score)

The function combines all three signals into a unified score for ranking.

Revision ID: b3e2d7a9f812
Revises: 002_semantic_phase1
Create Date: 2026-03-25 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = 'b3e2d7a9f812'
down_revision = '002_semantic_phase1'
branch_labels = None
depends_on = None

EMBEDDING_DIM = 1536


def upgrade() -> None:
    """Create hybrid search function."""
    
    # ───────────────────────────────────────────────────────────────────
    # Step 1: Create search_results type for function output
    # ───────────────────────────────────────────────────────────────────
    op.execute("""
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_type WHERE typname = 'search_result_type'
        ) THEN
            CREATE TYPE search_result_type AS (
                note_id UUID,
                title VARCHAR,
                content TEXT,
                note_type VARCHAR,
                workspace_id UUID,
                user_id UUID,
                created_at TIMESTAMPTZ,
                updated_at TIMESTAMPTZ,
                embedding_similarity FLOAT,
                keyword_score FLOAT,
                interaction_score FLOAT,
                final_score FLOAT,
                highlight TEXT
            );
        END IF;
    END$$;
    """)


    # ───────────────────────────────────────────────────────────────────
    # Step 2: Create main hybrid_search function
    # ───────────────────────────────────────────────────────────────────
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
        -- A. Vector semantic similarity search
        SELECT 
            n.id,
            n.title,
            n.content,
            n.note_type,
            n.workspace_id,
            n.user_id,
            n.created_at,
            n.updated_at,
            -- Normalize cosine distance (0.0 = identical, 2.0 = opposite)
            -- Convert to similarity (1.0 = identical, 0.0 = no similarity)
            CASE 
                WHEN n.embedding_vector IS NULL THEN 0.0
                ELSE 1.0 - ((n.embedding_vector <=> p_query_embedding) / 2.0)
            END AS similarity,
            0.0 AS text_rank  -- Will be filled by text search
        FROM notes n
        WHERE n.workspace_id = p_workspace_id
          AND n.embedding_vector IS NOT NULL
        ORDER BY n.embedding_vector <=> p_query_embedding
        LIMIT p_limit * 3  -- Return 3x to allow for hybrid filtering
    ),
    
    text_search AS (
        -- B. Full-text search on title + content
        SELECT 
            n.id,
            n.title,
            n.content,
            n.note_type,
            n.workspace_id,
            n.user_id,
            n.created_at,
            n.updated_at,
            0.0 AS similarity,  -- Will be filled from vector_search
            -- ts_rank: normalized rank weighted by query term frequency
            CASE 
                WHEN n.content_tsv IS NULL THEN 0.0
                ELSE ts_rank(n.content_tsv, plainto_tsquery('english', p_query_text)) / 10.0
            END AS text_rank
        FROM notes n
        WHERE n.workspace_id = p_workspace_id
          AND n.content_tsv @@ plainto_tsquery('english', p_query_text)
        LIMIT p_limit * 3
    ),
    
    interaction_tracking AS (
        -- C. Count user interactions for engagement boost
        SELECT 
            note_id,
            COUNT(*) as interaction_count,
            -- Normalize: log(1 + count) / log(1 + max_possible)
            CASE 
                WHEN COUNT(*) = 0 THEN 0.0
                ELSE LN(1.0 + COUNT(*)) / LN(11.0)  -- Max 10 interactions = 1.0 score
            END AS usage_score
        FROM note_interactions
        GROUP BY note_id
    ),
    
    merged_results AS (
        -- Combine all signals: vector + text + interaction
        SELECT DISTINCT ON (COALESCE(vs.id, ts.id))
            COALESCE(vs.id, ts.id) as note_id,
            COALESCE(vs.title, ts.title) as title,
            COALESCE(vs.content, ts.content) as content,
            COALESCE(vs.note_type, ts.note_type) as note_type,
            COALESCE(vs.workspace_id, ts.workspace_id) as workspace_id,
            COALESCE(vs.user_id, ts.user_id) as user_id,
            COALESCE(vs.created_at, ts.created_at) as created_at,
            COALESCE(vs.updated_at, ts.updated_at) as updated_at,
            COALESCE(vs.similarity, 0.0) as similarity,
            COALESCE(ts.text_rank, 0.0) as text_score,
            COALESCE(it.usage_score, 0.0) as usage_score
        FROM vector_search vs
        FULL OUTER JOIN text_search ts ON vs.id = ts.id
        LEFT JOIN interaction_tracking it ON COALESCE(vs.id, ts.id) = it.note_id
    ),
    
    scored_results AS (
        -- Calculate composite score with recency boost
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
            -- Recency: exponential decay with 4-week half-life
            -- POWER(0.5, days_since_creation / 28) gives 0.5 at 28 days
            POWER(0.5, EXTRACT(EPOCH FROM (NOW() - created_at)) / (28.0 * 86400.0)) AS recency_score,
            -- Final composite score
            (
                p_similarity_weight * similarity +
                p_usage_weight * usage_score +
                p_recency_weight * POWER(0.5, EXTRACT(EPOCH FROM (NOW() - created_at)) / (28.0 * 86400.0))
            ) AS final_score
        FROM merged_results
        WHERE similarity > 0.0 OR text_score > 0.0  -- Only include results with some match
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
        ROUND(similarity::numeric, 4)::FLOAT as embedding_similarity,
        ROUND(text_score::numeric, 4)::FLOAT as text_score,
        ROUND(usage_score::numeric, 4)::FLOAT as interaction_score,
        ROUND(final_score::numeric, 4)::FLOAT as final_score,
        -- Extract highlight (first 150 chars containing query word)
        SUBSTRING(
            content,
            POSITION(LOWER(p_query_text) IN LOWER(content)) - 30,
            150
        ) as highlight
    FROM scored_results
    ORDER BY final_score DESC, created_at DESC
    LIMIT p_limit;
    $$ LANGUAGE SQL STABLE;
    """)


    # ───────────────────────────────────────────────────────────────────
    # Step 3: Create helper function for simple vector search
    # ───────────────────────────────────────────────────────────────────
    op.execute(f"""
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
        1.0 - ((n.embedding_vector <=> p_query_embedding) / 2.0) as similarity
    FROM notes n
    WHERE n.workspace_id = p_workspace_id
      AND n.embedding_vector IS NOT NULL
      AND 1.0 - ((n.embedding_vector <=> p_query_embedding) / 2.0) >= p_similarity_threshold
    ORDER BY n.embedding_vector <=> p_query_embedding
    LIMIT p_limit;
    $$ LANGUAGE SQL STABLE;
    """)


    # ───────────────────────────────────────────────────────────────────
    # Step 4: Create helper function for text search only
    # ───────────────────────────────────────────────────────────────────
    op.execute(f"""
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
        ts_rank(n.content_tsv, plainto_tsquery('english', p_query_text)) / 10.0 as text_rank
    FROM notes n
    WHERE n.workspace_id = p_workspace_id
      AND n.content_tsv @@ plainto_tsquery('english', p_query_text)
    ORDER BY text_rank DESC, n.created_at DESC
    LIMIT p_limit;
    $$ LANGUAGE SQL STABLE;
    """)


    # ───────────────────────────────────────────────────────────────────
    # Step 5: Create function for interaction analytics
    # ───────────────────────────────────────────────────────────────────
    op.execute(f"""
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
        COUNT(*) as interaction_count,
        MAX(created_at) as last_interaction,
        ARRAY_AGG(DISTINCT interaction_type) as interaction_types
    FROM note_interactions
    WHERE workspace_id = p_workspace_id
    GROUP BY note_id;
    $$ LANGUAGE SQL STABLE;
    """)


    # ───────────────────────────────────────────────────────────────────
    # Step 6: Create index for the helper functions
    # ───────────────────────────────────────────────────────────────────
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_notes_workspace_embedding
        ON notes(workspace_id) 
        WHERE embedding_vector IS NOT NULL;
    """)
    
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_notes_workspace_tsvector
        ON notes(workspace_id) 
        WHERE content_tsv IS NOT NULL;
    """)


def downgrade() -> None:
    """Remove hybrid search function."""
    
    op.execute("DROP FUNCTION IF EXISTS hybrid_search(VARCHAR, vector, UUID, INT, FLOAT, FLOAT, FLOAT) CASCADE;")
    op.execute("DROP FUNCTION IF EXISTS vector_search_only(vector, UUID, INT, FLOAT) CASCADE;")
    op.execute("DROP FUNCTION IF EXISTS text_search_only(VARCHAR, UUID, INT) CASCADE;")
    op.execute("DROP FUNCTION IF EXISTS get_interaction_stats(UUID) CASCADE;")
    op.execute("DROP TYPE IF EXISTS search_result_type CASCADE;")
    op.execute("DROP INDEX IF EXISTS idx_notes_workspace_embedding;")
    op.execute("DROP INDEX IF EXISTS idx_notes_workspace_tsvector;")
