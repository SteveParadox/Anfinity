"""Conflict detection database functions, migrations, and utilities.

All SQL here is idempotent (safe to re-run) and pgvector-aware.
"""

# ---------------------------------------------------------------------------
# Configurable embedding dimension — must match your embedding model.
# text-embedding-3-small = 1536, text-embedding-3-large = 3072, etc.
# ---------------------------------------------------------------------------
EMBEDDING_DIM = 1536


# ---------------------------------------------------------------------------
# FIX 1 & 4: Enable pgvector extension before any vector operations.
# CREATE EXTENSION is idempotent with IF NOT EXISTS.
# ---------------------------------------------------------------------------
ENABLE_PGVECTOR = """
CREATE EXTENSION IF NOT EXISTS vector;
"""


# ---------------------------------------------------------------------------
# FIX 1: Use the proper pgvector `vector(N)` column type instead of
# VARCHAR(10000).  VARCHAR silently stores garbage strings; vector(N)
# enforces dimensionality and enables cosine/dot/L2 operators.
#
# FIX 3 & 5: Add a composite index on (workspace_id) for document lookups
# and a partial index that only covers rows eligible for conflict detection
# (word_count > 30 AND embedding IS NOT NULL) to keep index small and fast.
#
# FIX 2: Enable the HNSW index for approximate nearest-neighbour search.
# HNSW is preferred over IVFFlat — no training step needed and better
# recall at low ef_construction values.  Built CONCURRENTLY so it does
# not lock the table during index creation.
# ---------------------------------------------------------------------------
NOTES_TABLE_MIGRATION = f"""
-- Ensure pgvector is available
CREATE EXTENSION IF NOT EXISTS vector;

-- Add word_count column (safe if already present)
ALTER TABLE notes ADD COLUMN IF NOT EXISTS word_count INTEGER DEFAULT 0;

-- FIX 1: Add a proper vector column.
-- If you previously had a VARCHAR embedding column, drop it first:
--   ALTER TABLE notes DROP COLUMN IF EXISTS embedding;
ALTER TABLE notes ADD COLUMN IF NOT EXISTS embedding vector({EMBEDDING_DIM});

-- FIX 3: Index for workspace-scoped lookups
CREATE INDEX IF NOT EXISTS idx_notes_workspace
    ON notes (workspace_id);

-- FIX 3 & 5: Partial composite index — only rows that can participate
-- in conflict detection are indexed, keeping the index lean.
CREATE INDEX IF NOT EXISTS idx_notes_conflict_candidates
    ON notes (workspace_id, word_count DESC)
    WHERE embedding IS NOT NULL AND word_count > 30;

-- FIX 2: HNSW approximate nearest-neighbour index for cosine distance.
-- m=16 and ef_construction=64 are good defaults; tune for your dataset.
-- Built CONCURRENTLY so it never takes an exclusive table lock.
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_notes_embedding_hnsw
    ON notes USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- Reclaim space after migration
VACUUM ANALYZE notes;
"""


# ---------------------------------------------------------------------------
# FIX 2 & 3: The similarity search now benefits from the HNSW index above.
# Additional robustness improvements:
#   - Filter a.id < b.id in SQL (not post-hoc) to avoid symmetric duplicates.
#   - Cast similarity to FLOAT4 to avoid numeric precision edge cases.
#   - Both workspace_id guards use the index via (workspace_id, word_count).
# ---------------------------------------------------------------------------
FIND_CONFLICT_CANDIDATES_FUNCTION = """
CREATE OR REPLACE FUNCTION find_conflict_candidates(
    p_workspace_id    UUID,
    p_min_similarity  FLOAT  DEFAULT 0.70,
    p_max_similarity  FLOAT  DEFAULT 0.96,
    p_limit           INTEGER DEFAULT 50
)
RETURNS TABLE (
    note_a_id      UUID,
    note_a_title   TEXT,
    note_a_content TEXT,
    note_a_date    TIMESTAMPTZ,
    note_b_id      UUID,
    note_b_title   TEXT,
    note_b_content TEXT,
    note_b_date    TIMESTAMPTZ,
    similarity     FLOAT
)
LANGUAGE plpgsql
STABLE   -- marks the function as read-only so the planner can cache results
AS $$
BEGIN
    RETURN QUERY
    SELECT
        a.id          AS note_a_id,
        a.title       AS note_a_title,
        a.content     AS note_a_content,
        a.created_at  AS note_a_date,
        b.id          AS note_b_id,
        b.title       AS note_b_title,
        b.content     AS note_b_content,
        b.created_at  AS note_b_date,
        -- Cast to FLOAT to avoid numeric type mismatch in callers
        (1.0 - (a.embedding <=> b.embedding))::FLOAT AS similarity
    FROM notes a
    JOIN notes b
         ON  a.id < b.id   -- canonical ordering eliminates symmetric duplicates
         AND b.workspace_id = p_workspace_id
         AND b.embedding IS NOT NULL
         AND b.word_count > 30
    WHERE
        a.workspace_id = p_workspace_id
        AND a.embedding IS NOT NULL
        AND a.word_count > 30
        -- Exclude notes created at the exact same instant (likely duplicates)
        AND a.created_at <> b.created_at
        -- Apply similarity window
        AND (1.0 - (a.embedding <=> b.embedding)) BETWEEN p_min_similarity AND p_max_similarity
    ORDER BY similarity DESC
    LIMIT p_limit;
END;
$$;
"""


# ---------------------------------------------------------------------------
# Helper: apply all migrations in order within a single transaction.
# Intended to be called from Alembic or a startup script.
# ---------------------------------------------------------------------------
def apply_migrations(engine) -> None:  # type: ignore[type-arg]
    """Run all DB migrations and create/replace the conflict function.

    Safe to call on every app start — every statement is idempotent.
    Skips pgvector extension if not available (requires PostgreSQL server config).

    Args:
        engine: SQLAlchemy Engine (sync).
    """
    from sqlalchemy import text
    import logging
    log = logging.getLogger(__name__)

    steps = [
        ("enable_pgvector",              ENABLE_PGVECTOR),
        ("notes_table_migration",        NOTES_TABLE_MIGRATION),
        ("find_conflict_candidates_fn",  FIND_CONFLICT_CANDIDATES_FUNCTION),
    ]

    for name, sql in steps:
        try:
            with engine.begin() as conn:
                conn.execute(text(sql))  # type: ignore[arg-type]
            log.info("Migration applied: %s", name)
        except Exception as exc:
            error_str = str(exc).lower()
            if name == "enable_pgvector" and "vector" in error_str:
                log.warning(
                    "pgvector extension not available on PostgreSQL server. "
                    "Install it with: CREATE EXTENSION vector; "
                    "OR via system package: apt install postgresql-15-pgvector"
                )
                continue  # Skip pgvector and continue with other migrations
            elif name == "notes_table_migration" and "vector" in error_str:
                # Pgvector not available — use simpler migration without vector types
                log.warning(
                    "pgvector extension not available. Using VARCHAR for embeddings instead. "
                    "For production, install pgvector: apt install postgresql-15-pgvector"
                )
                try:
                    with engine.begin() as conn:
                        # Just add the basic columns/indices without vector type
                        conn.execute(text("""
                            ALTER TABLE notes ADD COLUMN IF NOT EXISTS word_count INTEGER DEFAULT 0;
                            ALTER TABLE notes ADD COLUMN IF NOT EXISTS embedding VARCHAR(10000);
                            CREATE INDEX IF NOT EXISTS idx_notes_workspace ON notes (workspace_id);
                            CREATE INDEX IF NOT EXISTS idx_notes_conflict_candidates 
                                ON notes (workspace_id, word_count DESC) 
                                WHERE embedding IS NOT NULL AND word_count > 30;
                            VACUUM ANALYZE notes;
                        """))
                    log.info("Migration applied: %s (fallback - no pgvector)", name)
                except Exception as inner_exc:
                    log.error("Fallback migration failed for %s: %s", name, inner_exc)
                    raise
            else:
                log.error("Migration failed at step '%s': %s", name, exc)
                raise