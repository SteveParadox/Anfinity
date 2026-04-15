"""reconcile existing unstamped schema to current head

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-04-14
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "b8c9d0e1f2a3"
down_revision = "a7b8c9d0e1f2"
branch_labels = None
depends_on = None

EMBEDDING_DIM = 1536


def _has_table(inspector: sa.Inspector, table_name: str) -> bool:
    return table_name in inspector.get_table_names()


def _has_column(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))


def _has_index(inspector: sa.Inspector, table_name: str, index_name: str) -> bool:
    return any(index["name"] == index_name for index in inspector.get_indexes(table_name))


def _table_exists(bind: sa.Connection, table_name: str) -> bool:
    return bool(
        bind.execute(
            sa.text("SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = :table_name"),
            {"table_name": table_name},
        ).scalar()
    )


def _ensure_workspace_functions() -> None:
    op.execute("ALTER TYPE workspacesection ADD VALUE IF NOT EXISTS 'SETTINGS'")
    op.execute("ALTER TYPE workspacesection ADD VALUE IF NOT EXISTS 'MEMBERS'")
    op.execute("ALTER TYPE workspacesection ADD VALUE IF NOT EXISTS 'WORKFLOWS'")

    op.execute(
        """
        CREATE OR REPLACE FUNCTION get_user_workspace_role(target_workspace_id uuid, target_user_id uuid DEFAULT NULL)
        RETURNS text
        LANGUAGE plpgsql
        STABLE
        SECURITY DEFINER
        SET search_path = public
        AS $$
        DECLARE
            resolved_user_id uuid;
            resolved_role text;
        BEGIN
            IF current_setting('app.rls_bypass', true) = 'true' THEN
                RETURN 'owner';
            END IF;

            resolved_user_id := COALESCE(target_user_id, NULLIF(current_setting('app.current_user_id', true), '')::uuid);
            IF resolved_user_id IS NULL THEN
                RETURN NULL;
            END IF;

            SELECT wm.role::text
            INTO resolved_role
            FROM workspace_members wm
            WHERE wm.workspace_id = target_workspace_id
              AND wm.user_id = resolved_user_id
            LIMIT 1;

            IF resolved_role IS NOT NULL THEN
                RETURN resolved_role;
            END IF;

            SELECT 'owner'
            INTO resolved_role
            FROM workspaces w
            WHERE w.id = target_workspace_id
              AND w.owner_id = resolved_user_id
            LIMIT 1;

            RETURN resolved_role;
        END;
        $$;
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION has_workspace_permission(
            target_workspace_id uuid,
            target_section text,
            target_action text,
            target_user_id uuid DEFAULT NULL
        )
        RETURNS boolean
        LANGUAGE plpgsql
        STABLE
        SECURITY DEFINER
        SET search_path = public
        AS $$
        DECLARE
            resolved_user_id uuid;
            resolved_role text;
            normalized_section text;
            default_allowed boolean := false;
            override_view boolean;
            override_create boolean;
            override_update boolean;
            override_delete boolean;
            override_manage boolean;
        BEGIN
            IF current_setting('app.rls_bypass', true) = 'true' THEN
                RETURN true;
            END IF;

            resolved_user_id := COALESCE(target_user_id, NULLIF(current_setting('app.current_user_id', true), '')::uuid);
            IF resolved_user_id IS NULL THEN
                RETURN false;
            END IF;

            normalized_section := lower(target_section);
            IF normalized_section = 'workspace' THEN
                normalized_section := 'settings';
            END IF;

            resolved_role := get_user_workspace_role(target_workspace_id, resolved_user_id);
            IF resolved_role IS NULL THEN
                RETURN false;
            END IF;

            default_allowed := CASE resolved_role
                WHEN 'owner' THEN
                    CASE normalized_section
                        WHEN 'settings' THEN CASE target_action WHEN 'view' THEN true WHEN 'update' THEN true WHEN 'delete' THEN true WHEN 'manage' THEN true ELSE false END
                        WHEN 'members' THEN CASE target_action WHEN 'view' THEN true WHEN 'create' THEN true WHEN 'update' THEN true WHEN 'delete' THEN true WHEN 'manage' THEN true ELSE false END
                        WHEN 'documents' THEN CASE target_action WHEN 'view' THEN true WHEN 'create' THEN true WHEN 'update' THEN true WHEN 'delete' THEN true WHEN 'manage' THEN true ELSE false END
                        WHEN 'notes' THEN CASE target_action WHEN 'view' THEN true WHEN 'create' THEN true WHEN 'update' THEN true WHEN 'delete' THEN true WHEN 'manage' THEN true ELSE false END
                        WHEN 'search' THEN CASE target_action WHEN 'view' THEN true WHEN 'create' THEN true WHEN 'manage' THEN true ELSE false END
                        WHEN 'knowledge_graph' THEN CASE target_action WHEN 'view' THEN true WHEN 'update' THEN true WHEN 'manage' THEN true ELSE false END
                        WHEN 'chat' THEN CASE target_action WHEN 'view' THEN true WHEN 'create' THEN true WHEN 'manage' THEN true ELSE false END
                        WHEN 'workflows' THEN CASE target_action WHEN 'view' THEN true WHEN 'create' THEN true WHEN 'update' THEN true WHEN 'delete' THEN true WHEN 'manage' THEN true ELSE false END
                        ELSE false
                    END
                WHEN 'admin' THEN
                    CASE normalized_section
                        WHEN 'settings' THEN CASE target_action WHEN 'view' THEN true WHEN 'update' THEN true WHEN 'manage' THEN true ELSE false END
                        WHEN 'members' THEN CASE target_action WHEN 'view' THEN true WHEN 'create' THEN true WHEN 'update' THEN true WHEN 'delete' THEN true WHEN 'manage' THEN true ELSE false END
                        WHEN 'documents' THEN CASE target_action WHEN 'view' THEN true WHEN 'create' THEN true WHEN 'update' THEN true WHEN 'delete' THEN true WHEN 'manage' THEN true ELSE false END
                        WHEN 'notes' THEN CASE target_action WHEN 'view' THEN true WHEN 'create' THEN true WHEN 'update' THEN true WHEN 'delete' THEN true WHEN 'manage' THEN true ELSE false END
                        WHEN 'search' THEN CASE target_action WHEN 'view' THEN true WHEN 'create' THEN true WHEN 'manage' THEN true ELSE false END
                        WHEN 'knowledge_graph' THEN CASE target_action WHEN 'view' THEN true WHEN 'update' THEN true WHEN 'manage' THEN true ELSE false END
                        WHEN 'chat' THEN CASE target_action WHEN 'view' THEN true WHEN 'create' THEN true WHEN 'manage' THEN true ELSE false END
                        WHEN 'workflows' THEN CASE target_action WHEN 'view' THEN true WHEN 'create' THEN true WHEN 'update' THEN true WHEN 'manage' THEN true ELSE false END
                        ELSE false
                    END
                WHEN 'member' THEN
                    CASE normalized_section
                        WHEN 'settings' THEN CASE target_action WHEN 'view' THEN true ELSE false END
                        WHEN 'members' THEN CASE target_action WHEN 'view' THEN true ELSE false END
                        WHEN 'documents' THEN CASE target_action WHEN 'view' THEN true WHEN 'create' THEN true WHEN 'update' THEN true ELSE false END
                        WHEN 'notes' THEN CASE target_action WHEN 'view' THEN true WHEN 'create' THEN true WHEN 'update' THEN true WHEN 'delete' THEN true ELSE false END
                        WHEN 'search' THEN CASE target_action WHEN 'view' THEN true WHEN 'create' THEN true ELSE false END
                        WHEN 'knowledge_graph' THEN CASE target_action WHEN 'view' THEN true ELSE false END
                        WHEN 'chat' THEN CASE target_action WHEN 'view' THEN true WHEN 'create' THEN true ELSE false END
                        WHEN 'workflows' THEN CASE target_action WHEN 'view' THEN true WHEN 'create' THEN true WHEN 'update' THEN true ELSE false END
                        ELSE false
                    END
                WHEN 'viewer' THEN
                    CASE normalized_section
                        WHEN 'settings' THEN CASE target_action WHEN 'view' THEN true ELSE false END
                        WHEN 'members' THEN CASE target_action WHEN 'view' THEN true ELSE false END
                        WHEN 'documents' THEN CASE target_action WHEN 'view' THEN true ELSE false END
                        WHEN 'notes' THEN CASE target_action WHEN 'view' THEN true ELSE false END
                        WHEN 'search' THEN CASE target_action WHEN 'view' THEN true WHEN 'create' THEN true ELSE false END
                        WHEN 'knowledge_graph' THEN CASE target_action WHEN 'view' THEN true ELSE false END
                        WHEN 'chat' THEN CASE target_action WHEN 'view' THEN true WHEN 'create' THEN true ELSE false END
                        WHEN 'workflows' THEN CASE target_action WHEN 'view' THEN true ELSE false END
                        ELSE false
                    END
                ELSE false
            END;

            SELECT can_view, can_create, can_update, can_delete, can_manage
            INTO override_view, override_create, override_update, override_delete, override_manage
            FROM workspace_permission_overrides
            WHERE workspace_id = target_workspace_id
              AND user_id = resolved_user_id
              AND section::text = upper(normalized_section)
            LIMIT 1;

            IF FOUND THEN
                CASE target_action
                    WHEN 'view' THEN RETURN COALESCE(override_view, default_allowed);
                    WHEN 'create' THEN RETURN COALESCE(override_create, default_allowed);
                    WHEN 'update' THEN RETURN COALESCE(override_update, default_allowed);
                    WHEN 'delete' THEN RETURN COALESCE(override_delete, default_allowed);
                    WHEN 'manage' THEN RETURN COALESCE(override_manage, default_allowed);
                    ELSE RETURN false;
                END CASE;
            END IF;

            RETURN default_allowed;
        END;
        $$;
        """
    )


def _recreate_policy(table_name: str, action: str, statement: str) -> None:
    quoted_table = f'"{table_name}"'
    policy_name = f"{table_name}_{action}_policy"
    op.execute(f"DROP POLICY IF EXISTS {policy_name} ON {quoted_table}")
    op.execute(statement)


def _ensure_workspace_policies(bind: sa.Connection) -> None:
    tables = sa.inspect(bind).get_table_names()

    if "workspaces" in tables:
        op.execute("ALTER TABLE workspaces ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE workspaces FORCE ROW LEVEL SECURITY")
        _recreate_policy(
            "workspaces",
            "select",
            "CREATE POLICY workspaces_select_policy ON workspaces FOR SELECT USING (has_workspace_permission(id, 'settings', 'view'))",
        )
        _recreate_policy(
            "workspaces",
            "update",
            "CREATE POLICY workspaces_update_policy ON workspaces FOR UPDATE USING (has_workspace_permission(id, 'settings', 'update')) WITH CHECK (has_workspace_permission(id, 'settings', 'update'))",
        )
        _recreate_policy(
            "workspaces",
            "delete",
            "CREATE POLICY workspaces_delete_policy ON workspaces FOR DELETE USING (has_workspace_permission(id, 'settings', 'delete'))",
        )

    policy_targets = [
        ("workspace_members", "workspace_id", "members"),
        ("workspace_permission_overrides", "workspace_id", "members"),
        ("connectors", "workspace_id", "settings"),
        ("documents", "workspace_id", "documents"),
        ("notes", "workspace_id", "notes"),
        ("note_versions", "workspace_id", "notes"),
        ("note_connection_suggestions", "workspace_id", "notes"),
        ("search_queries", "workspace_id", "search"),
        ("search_logs", "workspace_id", "search"),
        ("queries", "workspace_id", "chat"),
        ("answers", "workspace_id", "chat"),
        ("graph_nodes", "workspace_id", "knowledge_graph"),
        ("graph_edges", "workspace_id", "knowledge_graph"),
        ("graph_clusters", "workspace_id", "knowledge_graph"),
        ("graph_cluster_memberships", "workspace_id", "knowledge_graph"),
    ]

    for table_name, workspace_expr, section in policy_targets:
        if table_name not in tables:
            continue
        quoted_table = f'"{table_name}"'
        op.execute(f"ALTER TABLE {quoted_table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {quoted_table} FORCE ROW LEVEL SECURITY")
        _recreate_policy(
            table_name,
            "select",
            f"CREATE POLICY {table_name}_select_policy ON {quoted_table} FOR SELECT USING (has_workspace_permission({workspace_expr}, '{section}', 'view'))",
        )
        _recreate_policy(
            table_name,
            "insert",
            f"CREATE POLICY {table_name}_insert_policy ON {quoted_table} FOR INSERT WITH CHECK (has_workspace_permission({workspace_expr}, '{section}', 'create'))",
        )
        _recreate_policy(
            table_name,
            "update",
            f"CREATE POLICY {table_name}_update_policy ON {quoted_table} FOR UPDATE USING (has_workspace_permission({workspace_expr}, '{section}', 'update')) WITH CHECK (has_workspace_permission({workspace_expr}, '{section}', 'update'))",
        )
        _recreate_policy(
            table_name,
            "delete",
            f"CREATE POLICY {table_name}_delete_policy ON {quoted_table} FOR DELETE USING (has_workspace_permission({workspace_expr}, '{section}', 'delete'))",
        )


def _ensure_semantic_search_objects(bind: sa.Connection) -> None:
    inspector = sa.inspect(bind)

    op.execute("CREATE EXTENSION IF NOT EXISTS vector;")

    if _has_table(inspector, "notes") and not _has_column(inspector, "notes", "content_tsv"):
        op.add_column("notes", sa.Column("content_tsv", postgresql.TSVECTOR, nullable=True))
    if _has_table(inspector, "notes"):
        op.execute(
            """
            UPDATE notes
            SET content_tsv = to_tsvector('english', COALESCE(title, '') || ' ' || COALESCE(content, ''))
            WHERE content_tsv IS NULL;
            """
        )
        if not _has_index(sa.inspect(bind), "notes", "idx_notes_content_tsv"):
            op.create_index("idx_notes_content_tsv", "notes", ["content_tsv"], postgresql_using="gin")
        op.execute("ALTER TABLE notes ADD COLUMN IF NOT EXISTS embedding_json text;")
        op.execute("UPDATE notes SET embedding_json = embedding WHERE embedding IS NOT NULL AND embedding_json IS NULL;")
        op.execute(f"ALTER TABLE notes ADD COLUMN IF NOT EXISTS embedding_vector vector({EMBEDDING_DIM});")
        op.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_notes_embedding_vector_ivf
            ON notes USING ivfflat (embedding_vector vector_cosine_ops)
            WITH (lists = 100);
            """
        )
        op.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_notes_workspace_type
            ON notes (workspace_id, note_type, created_at);
            """
        )
        op.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_notes_user_updated
            ON notes (user_id, updated_at);
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
        op.execute("ANALYZE notes;")

    if not _has_table(inspector, "note_interactions"):
        op.create_table(
            "note_interactions",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
            sa.Column("note_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("notes.id", ondelete="CASCADE"), nullable=False),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("workspace_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
            sa.Column("interaction_type", sa.String(50), nullable=False),
            sa.Column("context", postgresql.JSONB, default=dict, nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        )
    if _table_exists(bind, "note_interactions"):
        if not _has_index(sa.inspect(bind), "note_interactions", "idx_note_interactions_note"):
            op.create_index("idx_note_interactions_note", "note_interactions", ["note_id", "created_at"], unique=False)
        if not _has_index(sa.inspect(bind), "note_interactions", "idx_note_interactions_user"):
            op.create_index("idx_note_interactions_user", "note_interactions", ["user_id", "created_at"], unique=False)
        if not _has_index(sa.inspect(bind), "note_interactions", "idx_note_interactions_workspace"):
            op.create_index("idx_note_interactions_workspace", "note_interactions", ["workspace_id", "created_at"], unique=False)
        if not _has_index(sa.inspect(bind), "note_interactions", "idx_note_interactions_type"):
            op.create_index("idx_note_interactions_type", "note_interactions", ["interaction_type"], unique=False)
        if not _has_index(sa.inspect(bind), "note_interactions", "idx_note_interactions_compound"):
            op.create_index("idx_note_interactions_compound", "note_interactions", ["workspace_id", "interaction_type", "created_at"], unique=False)
        op.execute("ANALYZE note_interactions;")

    if _has_table(sa.inspect(bind), "search_queries"):
        op.execute(f"ALTER TABLE search_queries ADD COLUMN IF NOT EXISTS query_embedding_vector vector({EMBEDDING_DIM});")
        op.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_search_queries_embedding_vector_ivf
            ON search_queries USING ivfflat (query_embedding_vector vector_cosine_ops)
            WITH (lists = 100);
            """
        )
        op.execute("ANALYZE search_queries;")

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


def upgrade() -> None:
    bind = op.get_bind()
    _ensure_semantic_search_objects(bind)
    if _table_exists(bind, "workspace_permission_overrides"):
        _ensure_workspace_functions()
        _ensure_workspace_policies(bind)


def downgrade() -> None:
    # This reconciliation migration is intentionally non-destructive.
    pass
