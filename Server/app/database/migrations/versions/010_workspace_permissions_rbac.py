"""workspace permissions rbac

Revision ID: 010_workspace_permissions_rbac
Revises: 009_note_lineage_history
Create Date: 2026-04-14
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "010_workspace_permissions_rbac"
down_revision = "009_note_lineage_history"
branch_labels = None
depends_on = None


WORKSPACE_PERMISSION_SECTIONS = (
    "workspace",
    "documents",
    "notes",
    "search",
    "knowledge_graph",
    "chat",
)


def _create_workspace_scoped_policies(table_name: str, workspace_expr: str, section: str) -> None:
    quoted_table = f'"{table_name}"'
    statements = [
        f"ALTER TABLE {quoted_table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {quoted_table} FORCE ROW LEVEL SECURITY",
        f"DROP POLICY IF EXISTS {table_name}_select_policy ON {quoted_table}",
        f"DROP POLICY IF EXISTS {table_name}_insert_policy ON {quoted_table}",
        f"DROP POLICY IF EXISTS {table_name}_update_policy ON {quoted_table}",
        f"DROP POLICY IF EXISTS {table_name}_delete_policy ON {quoted_table}",
        (
            f"CREATE POLICY {table_name}_select_policy ON {quoted_table} "
            f"FOR SELECT USING (has_workspace_permission({workspace_expr}, '{section}', 'view'))"
        ),
        (
            f"CREATE POLICY {table_name}_insert_policy ON {quoted_table} "
            f"FOR INSERT WITH CHECK (has_workspace_permission({workspace_expr}, '{section}', 'create'))"
        ),
        (
            f"CREATE POLICY {table_name}_update_policy ON {quoted_table} "
            f"FOR UPDATE USING (has_workspace_permission({workspace_expr}, '{section}', 'update')) "
            f"WITH CHECK (has_workspace_permission({workspace_expr}, '{section}', 'update'))"
        ),
        (
            f"CREATE POLICY {table_name}_delete_policy ON {quoted_table} "
            f"FOR DELETE USING (has_workspace_permission({workspace_expr}, '{section}', 'delete'))"
        ),
    ]
    for statement in statements:
        op.execute(statement)


def _drop_workspace_scoped_policies(table_name: str) -> None:
    quoted_table = f'"{table_name}"'
    for policy_name in (
        f"{table_name}_select_policy",
        f"{table_name}_insert_policy",
        f"{table_name}_update_policy",
        f"{table_name}_delete_policy",
    ):
        op.execute(f"DROP POLICY IF EXISTS {policy_name} ON {quoted_table}")
    op.execute(f"ALTER TABLE {quoted_table} NO FORCE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {quoted_table} DISABLE ROW LEVEL SECURITY")


def upgrade() -> None:
    workspacesection_enum = postgresql.ENUM(
        *[section.upper() for section in WORKSPACE_PERMISSION_SECTIONS],
        name="workspacesection",
    )
    workspacesection_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "workspace_permission_overrides",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("section", sa.Enum(*[section.upper() for section in WORKSPACE_PERMISSION_SECTIONS], name="workspacesection"), nullable=False),
        sa.Column("can_view", sa.Boolean(), nullable=True),
        sa.Column("can_create", sa.Boolean(), nullable=True),
        sa.Column("can_update", sa.Boolean(), nullable=True),
        sa.Column("can_delete", sa.Boolean(), nullable=True),
        sa.Column("can_manage", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("workspace_id", "user_id", "section", name="uq_workspace_permission_override_scope"),
    )
    op.create_index(
        "idx_workspace_permission_override_lookup",
        "workspace_permission_overrides",
        ["workspace_id", "user_id", "section"],
    )

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

            resolved_role := get_user_workspace_role(target_workspace_id, resolved_user_id);
            IF resolved_role IS NULL THEN
                RETURN false;
            END IF;

            default_allowed := CASE resolved_role
                WHEN 'owner' THEN
                    CASE target_section
                        WHEN 'workspace' THEN CASE target_action WHEN 'view' THEN true WHEN 'update' THEN true WHEN 'delete' THEN true WHEN 'manage' THEN true ELSE false END
                        WHEN 'documents' THEN CASE target_action WHEN 'view' THEN true WHEN 'create' THEN true WHEN 'update' THEN true WHEN 'delete' THEN true WHEN 'manage' THEN true ELSE false END
                        WHEN 'notes' THEN CASE target_action WHEN 'view' THEN true WHEN 'create' THEN true WHEN 'update' THEN true WHEN 'delete' THEN true WHEN 'manage' THEN true ELSE false END
                        WHEN 'search' THEN CASE target_action WHEN 'view' THEN true WHEN 'create' THEN true WHEN 'manage' THEN true ELSE false END
                        WHEN 'knowledge_graph' THEN CASE target_action WHEN 'view' THEN true WHEN 'update' THEN true WHEN 'manage' THEN true ELSE false END
                        WHEN 'chat' THEN CASE target_action WHEN 'view' THEN true WHEN 'create' THEN true WHEN 'manage' THEN true ELSE false END
                        ELSE false
                    END
                WHEN 'admin' THEN
                    CASE target_section
                        WHEN 'workspace' THEN CASE target_action WHEN 'view' THEN true WHEN 'update' THEN true WHEN 'manage' THEN true ELSE false END
                        WHEN 'documents' THEN CASE target_action WHEN 'view' THEN true WHEN 'create' THEN true WHEN 'update' THEN true WHEN 'delete' THEN true WHEN 'manage' THEN true ELSE false END
                        WHEN 'notes' THEN CASE target_action WHEN 'view' THEN true WHEN 'create' THEN true WHEN 'update' THEN true WHEN 'delete' THEN true WHEN 'manage' THEN true ELSE false END
                        WHEN 'search' THEN CASE target_action WHEN 'view' THEN true WHEN 'create' THEN true WHEN 'manage' THEN true ELSE false END
                        WHEN 'knowledge_graph' THEN CASE target_action WHEN 'view' THEN true WHEN 'update' THEN true WHEN 'manage' THEN true ELSE false END
                        WHEN 'chat' THEN CASE target_action WHEN 'view' THEN true WHEN 'create' THEN true WHEN 'manage' THEN true ELSE false END
                        ELSE false
                    END
                WHEN 'member' THEN
                    CASE target_section
                        WHEN 'workspace' THEN CASE target_action WHEN 'view' THEN true ELSE false END
                        WHEN 'documents' THEN CASE target_action WHEN 'view' THEN true WHEN 'create' THEN true WHEN 'update' THEN true ELSE false END
                        WHEN 'notes' THEN CASE target_action WHEN 'view' THEN true WHEN 'create' THEN true WHEN 'update' THEN true WHEN 'delete' THEN true ELSE false END
                        WHEN 'search' THEN CASE target_action WHEN 'view' THEN true WHEN 'create' THEN true ELSE false END
                        WHEN 'knowledge_graph' THEN CASE target_action WHEN 'view' THEN true ELSE false END
                        WHEN 'chat' THEN CASE target_action WHEN 'view' THEN true WHEN 'create' THEN true ELSE false END
                        ELSE false
                    END
                WHEN 'viewer' THEN
                    CASE target_section
                        WHEN 'workspace' THEN CASE target_action WHEN 'view' THEN true ELSE false END
                        WHEN 'documents' THEN CASE target_action WHEN 'view' THEN true ELSE false END
                        WHEN 'notes' THEN CASE target_action WHEN 'view' THEN true ELSE false END
                        WHEN 'search' THEN CASE target_action WHEN 'view' THEN true WHEN 'create' THEN true ELSE false END
                        WHEN 'knowledge_graph' THEN CASE target_action WHEN 'view' THEN true ELSE false END
                        WHEN 'chat' THEN CASE target_action WHEN 'view' THEN true WHEN 'create' THEN true ELSE false END
                        ELSE false
                    END
                ELSE false
            END;

            SELECT can_view, can_create, can_update, can_delete, can_manage
            INTO override_view, override_create, override_update, override_delete, override_manage
            FROM workspace_permission_overrides
            WHERE workspace_id = target_workspace_id
              AND user_id = resolved_user_id
              AND section::text = upper(target_section)
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

    op.execute(
        """
        ALTER TABLE workspaces ENABLE ROW LEVEL SECURITY;
        ALTER TABLE workspaces FORCE ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS workspaces_select_policy ON workspaces;
        DROP POLICY IF EXISTS workspaces_insert_policy ON workspaces;
        DROP POLICY IF EXISTS workspaces_update_policy ON workspaces;
        DROP POLICY IF EXISTS workspaces_delete_policy ON workspaces;
        CREATE POLICY workspaces_select_policy ON workspaces
            FOR SELECT USING (has_workspace_permission(id, 'workspace', 'view'));
        CREATE POLICY workspaces_insert_policy ON workspaces
            FOR INSERT WITH CHECK (
                current_setting('app.rls_bypass', true) = 'true'
                OR owner_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid
            );
        CREATE POLICY workspaces_update_policy ON workspaces
            FOR UPDATE USING (has_workspace_permission(id, 'workspace', 'update'))
            WITH CHECK (has_workspace_permission(id, 'workspace', 'update'));
        CREATE POLICY workspaces_delete_policy ON workspaces
            FOR DELETE USING (has_workspace_permission(id, 'workspace', 'delete'));
        """
    )

    _create_workspace_scoped_policies("workspace_members", "workspace_id", "workspace")
    _create_workspace_scoped_policies("workspace_permission_overrides", "workspace_id", "workspace")
    _create_workspace_scoped_policies("connectors", "workspace_id", "workspace")
    _create_workspace_scoped_policies("documents", "workspace_id", "documents")
    _create_workspace_scoped_policies("notes", "workspace_id", "notes")
    _create_workspace_scoped_policies("note_versions", "workspace_id", "notes")
    _create_workspace_scoped_policies("note_connection_suggestions", "workspace_id", "notes")
    _create_workspace_scoped_policies("search_queries", "workspace_id", "search")
    _create_workspace_scoped_policies("search_logs", "workspace_id", "search")
    _create_workspace_scoped_policies("queries", "workspace_id", "chat")
    _create_workspace_scoped_policies("answers", "workspace_id", "chat")
    _create_workspace_scoped_policies("graph_nodes", "workspace_id", "knowledge_graph")
    _create_workspace_scoped_policies("graph_edges", "workspace_id", "knowledge_graph")
    _create_workspace_scoped_policies("graph_clusters", "workspace_id", "knowledge_graph")
    _create_workspace_scoped_policies("graph_cluster_memberships", "workspace_id", "knowledge_graph")


def downgrade() -> None:
    for table_name in (
        "graph_cluster_memberships",
        "graph_clusters",
        "graph_edges",
        "graph_nodes",
        "answers",
        "queries",
        "search_logs",
        "search_queries",
        "note_connection_suggestions",
        "note_versions",
        "notes",
        "documents",
        "connectors",
        "workspace_permission_overrides",
        "workspace_members",
    ):
        _drop_workspace_scoped_policies(table_name)

    op.execute("DROP POLICY IF EXISTS workspaces_select_policy ON workspaces")
    op.execute("DROP POLICY IF EXISTS workspaces_insert_policy ON workspaces")
    op.execute("DROP POLICY IF EXISTS workspaces_update_policy ON workspaces")
    op.execute("DROP POLICY IF EXISTS workspaces_delete_policy ON workspaces")
    op.execute("ALTER TABLE workspaces NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE workspaces DISABLE ROW LEVEL SECURITY")

    op.execute("DROP FUNCTION IF EXISTS has_workspace_permission(uuid, text, text, uuid)")
    op.execute("DROP FUNCTION IF EXISTS get_user_workspace_role(uuid, uuid)")

    op.drop_index("idx_workspace_permission_override_lookup", table_name="workspace_permission_overrides")
    op.drop_table("workspace_permission_overrides")

    workspacesection_enum = postgresql.ENUM(
        *[section.upper() for section in WORKSPACE_PERMISSION_SECTIONS],
        name="workspacesection",
    )
    workspacesection_enum.drop(op.get_bind(), checkfirst=True)
