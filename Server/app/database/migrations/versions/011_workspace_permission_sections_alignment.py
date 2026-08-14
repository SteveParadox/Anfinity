"""align workspace permission sections with settings/member/workflow scopes

Revision ID: a7b8c9d0e1f2
Revises: f1a2b3c4d5e6
Create Date: 2026-04-14
"""

from alembic import op


revision = "a7b8c9d0e1f2"
down_revision = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None


def _recreate_policy(table_name: str, action: str, statement: str) -> None:
    quoted_table = f'"{table_name}"'
    policy_name = f"{table_name}_{action}_policy"
    op.execute(f"DROP POLICY IF EXISTS {policy_name} ON {quoted_table}")
    op.execute(statement)


def upgrade() -> None:
    op.execute("ALTER TYPE workspacesection ADD VALUE IF NOT EXISTS 'SETTINGS'")
    op.execute("ALTER TYPE workspacesection ADD VALUE IF NOT EXISTS 'MEMBERS'")
    op.execute("ALTER TYPE workspacesection ADD VALUE IF NOT EXISTS 'WORKFLOWS'")

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

    _recreate_policy(
        "workspace_members",
        "select",
        "CREATE POLICY workspace_members_select_policy ON \"workspace_members\" FOR SELECT USING (has_workspace_permission(workspace_id, 'members', 'view'))",
    )
    _recreate_policy(
        "workspace_members",
        "insert",
        "CREATE POLICY workspace_members_insert_policy ON \"workspace_members\" FOR INSERT WITH CHECK (has_workspace_permission(workspace_id, 'members', 'create'))",
    )
    _recreate_policy(
        "workspace_members",
        "update",
        "CREATE POLICY workspace_members_update_policy ON \"workspace_members\" FOR UPDATE USING (has_workspace_permission(workspace_id, 'members', 'update')) WITH CHECK (has_workspace_permission(workspace_id, 'members', 'update'))",
    )
    _recreate_policy(
        "workspace_members",
        "delete",
        "CREATE POLICY workspace_members_delete_policy ON \"workspace_members\" FOR DELETE USING (has_workspace_permission(workspace_id, 'members', 'delete'))",
    )

    _recreate_policy(
        "workspace_permission_overrides",
        "select",
        "CREATE POLICY workspace_permission_overrides_select_policy ON \"workspace_permission_overrides\" FOR SELECT USING (has_workspace_permission(workspace_id, 'members', 'view'))",
    )
    _recreate_policy(
        "workspace_permission_overrides",
        "insert",
        "CREATE POLICY workspace_permission_overrides_insert_policy ON \"workspace_permission_overrides\" FOR INSERT WITH CHECK (has_workspace_permission(workspace_id, 'members', 'create'))",
    )
    _recreate_policy(
        "workspace_permission_overrides",
        "update",
        "CREATE POLICY workspace_permission_overrides_update_policy ON \"workspace_permission_overrides\" FOR UPDATE USING (has_workspace_permission(workspace_id, 'members', 'update')) WITH CHECK (has_workspace_permission(workspace_id, 'members', 'update'))",
    )
    _recreate_policy(
        "workspace_permission_overrides",
        "delete",
        "CREATE POLICY workspace_permission_overrides_delete_policy ON \"workspace_permission_overrides\" FOR DELETE USING (has_workspace_permission(workspace_id, 'members', 'delete'))",
    )

    _recreate_policy(
        "connectors",
        "select",
        "CREATE POLICY connectors_select_policy ON \"connectors\" FOR SELECT USING (has_workspace_permission(workspace_id, 'settings', 'view'))",
    )
    _recreate_policy(
        "connectors",
        "insert",
        "CREATE POLICY connectors_insert_policy ON \"connectors\" FOR INSERT WITH CHECK (has_workspace_permission(workspace_id, 'settings', 'create'))",
    )
    _recreate_policy(
        "connectors",
        "update",
        "CREATE POLICY connectors_update_policy ON \"connectors\" FOR UPDATE USING (has_workspace_permission(workspace_id, 'settings', 'update')) WITH CHECK (has_workspace_permission(workspace_id, 'settings', 'update'))",
    )
    _recreate_policy(
        "connectors",
        "delete",
        "CREATE POLICY connectors_delete_policy ON \"connectors\" FOR DELETE USING (has_workspace_permission(workspace_id, 'settings', 'delete'))",
    )


def downgrade() -> None:
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

    _recreate_policy(
        "workspaces",
        "select",
        "CREATE POLICY workspaces_select_policy ON workspaces FOR SELECT USING (has_workspace_permission(id, 'workspace', 'view'))",
    )
    _recreate_policy(
        "workspaces",
        "update",
        "CREATE POLICY workspaces_update_policy ON workspaces FOR UPDATE USING (has_workspace_permission(id, 'workspace', 'update')) WITH CHECK (has_workspace_permission(id, 'workspace', 'update'))",
    )
    _recreate_policy(
        "workspaces",
        "delete",
        "CREATE POLICY workspaces_delete_policy ON workspaces FOR DELETE USING (has_workspace_permission(id, 'workspace', 'delete'))",
    )

    _recreate_policy(
        "workspace_members",
        "select",
        "CREATE POLICY workspace_members_select_policy ON \"workspace_members\" FOR SELECT USING (has_workspace_permission(workspace_id, 'workspace', 'view'))",
    )
    _recreate_policy(
        "workspace_members",
        "insert",
        "CREATE POLICY workspace_members_insert_policy ON \"workspace_members\" FOR INSERT WITH CHECK (has_workspace_permission(workspace_id, 'workspace', 'create'))",
    )
    _recreate_policy(
        "workspace_members",
        "update",
        "CREATE POLICY workspace_members_update_policy ON \"workspace_members\" FOR UPDATE USING (has_workspace_permission(workspace_id, 'workspace', 'update')) WITH CHECK (has_workspace_permission(workspace_id, 'workspace', 'update'))",
    )
    _recreate_policy(
        "workspace_members",
        "delete",
        "CREATE POLICY workspace_members_delete_policy ON \"workspace_members\" FOR DELETE USING (has_workspace_permission(workspace_id, 'workspace', 'delete'))",
    )

    _recreate_policy(
        "workspace_permission_overrides",
        "select",
        "CREATE POLICY workspace_permission_overrides_select_policy ON \"workspace_permission_overrides\" FOR SELECT USING (has_workspace_permission(workspace_id, 'workspace', 'view'))",
    )
    _recreate_policy(
        "workspace_permission_overrides",
        "insert",
        "CREATE POLICY workspace_permission_overrides_insert_policy ON \"workspace_permission_overrides\" FOR INSERT WITH CHECK (has_workspace_permission(workspace_id, 'workspace', 'create'))",
    )
    _recreate_policy(
        "workspace_permission_overrides",
        "update",
        "CREATE POLICY workspace_permission_overrides_update_policy ON \"workspace_permission_overrides\" FOR UPDATE USING (has_workspace_permission(workspace_id, 'workspace', 'update')) WITH CHECK (has_workspace_permission(workspace_id, 'workspace', 'update'))",
    )
    _recreate_policy(
        "workspace_permission_overrides",
        "delete",
        "CREATE POLICY workspace_permission_overrides_delete_policy ON \"workspace_permission_overrides\" FOR DELETE USING (has_workspace_permission(workspace_id, 'workspace', 'delete'))",
    )

    _recreate_policy(
        "connectors",
        "select",
        "CREATE POLICY connectors_select_policy ON \"connectors\" FOR SELECT USING (has_workspace_permission(workspace_id, 'workspace', 'view'))",
    )
    _recreate_policy(
        "connectors",
        "insert",
        "CREATE POLICY connectors_insert_policy ON \"connectors\" FOR INSERT WITH CHECK (has_workspace_permission(workspace_id, 'workspace', 'create'))",
    )
    _recreate_policy(
        "connectors",
        "update",
        "CREATE POLICY connectors_update_policy ON \"connectors\" FOR UPDATE USING (has_workspace_permission(workspace_id, 'workspace', 'update')) WITH CHECK (has_workspace_permission(workspace_id, 'workspace', 'update'))",
    )
    _recreate_policy(
        "connectors",
        "delete",
        "CREATE POLICY connectors_delete_policy ON \"connectors\" FOR DELETE USING (has_workspace_permission(workspace_id, 'workspace', 'delete'))",
    )
