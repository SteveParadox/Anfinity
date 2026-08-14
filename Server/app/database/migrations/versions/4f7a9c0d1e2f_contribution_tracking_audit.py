"""Add immutable audit_log infrastructure and note contribution aggregates.

Revision ID: 4f7a9c0d1e2f
Revises: 21b2c3d4e5f6
Create Date: 2026-04-21 17:20:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "4f7a9c0d1e2f"
down_revision = "21b2c3d4e5f6"
branch_labels = None
depends_on = None


NOTE_CONTRIBUTION_ACTIONS = (
    "note.created",
    "note.updated",
    "note.restored",
    "thinking_session.contribution_submitted",
    "thinking_session.vote_cast",
)


def _relation_exists(bind: sa.Connection, relation_name: str) -> bool:
    return bool(
        bind.execute(
            sa.text("SELECT to_regclass(:relation_name) IS NOT NULL"),
            {"relation_name": f"public.{relation_name}"},
        ).scalar()
    )


def _create_index_if_missing(index_name: str, table_name: str, columns: tuple[str, ...]) -> None:
    op.execute(f"CREATE INDEX IF NOT EXISTS {index_name} ON {table_name} ({', '.join(columns)})")


def upgrade() -> None:
    bind = op.get_bind()
    if not _relation_exists(bind, "audit_log"):
        op.create_table(
            "audit_log",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("note_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("target_user_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("action_type", sa.String(length=100), nullable=False),
            sa.Column("entity_type", sa.String(length=50), nullable=False),
            sa.Column("entity_id", sa.String(length=255), nullable=True),
            sa.Column(
                "metadata_json",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'{}'::jsonb"),
            ),
            sa.Column("request_id", sa.String(length=255), nullable=True),
            sa.Column("session_id", sa.String(length=255), nullable=True),
            sa.Column("source", sa.String(length=100), nullable=True),
            sa.Column("ip_address", sa.String(length=45), nullable=True),
            sa.Column("user_agent", sa.String(length=500), nullable=True),
        )

    op.execute("ALTER TABLE audit_log ALTER COLUMN created_at SET DEFAULT now()")
    op.execute("ALTER TABLE audit_log ALTER COLUMN metadata_json SET DEFAULT '{}'::jsonb")
    _create_index_if_missing("ix_audit_log_created_at", "audit_log", ("created_at",))
    _create_index_if_missing("ix_audit_log_actor_user_id", "audit_log", ("actor_user_id",))
    _create_index_if_missing("ix_audit_log_workspace_id", "audit_log", ("workspace_id",))
    _create_index_if_missing("ix_audit_log_note_id", "audit_log", ("note_id",))
    _create_index_if_missing("ix_audit_log_target_user_id", "audit_log", ("target_user_id",))
    _create_index_if_missing("ix_audit_log_action_type", "audit_log", ("action_type",))
    _create_index_if_missing("ix_audit_log_entity_type", "audit_log", ("entity_type",))
    _create_index_if_missing("ix_audit_log_entity_id", "audit_log", ("entity_id",))
    _create_index_if_missing("ix_audit_log_request_id", "audit_log", ("request_id",))
    _create_index_if_missing("ix_audit_log_session_id", "audit_log", ("session_id",))
    _create_index_if_missing("ix_audit_log_source", "audit_log", ("source",))
    _create_index_if_missing("idx_audit_log_workspace_created_at", "audit_log", ("workspace_id", "created_at"))
    _create_index_if_missing("idx_audit_log_actor_created_at", "audit_log", ("actor_user_id", "created_at"))
    _create_index_if_missing("idx_audit_log_note_created_at", "audit_log", ("note_id", "created_at"))
    _create_index_if_missing("idx_audit_log_action_created_at", "audit_log", ("action_type", "created_at"))
    _create_index_if_missing("idx_audit_log_entity_lookup", "audit_log", ("entity_type", "entity_id", "created_at"))

    op.execute(
        f"""
        CREATE INDEX IF NOT EXISTS idx_audit_log_note_contribution_actions
        ON audit_log (note_id, actor_user_id, created_at DESC)
        WHERE note_id IS NOT NULL
          AND actor_user_id IS NOT NULL
          AND action_type IN {NOTE_CONTRIBUTION_ACTIONS}
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_audit_log_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'audit_log is append-only and does not support % operations', TG_OP;
        END;
        $$;
        """
    )
    op.execute(
        """
        DROP TRIGGER IF EXISTS trg_prevent_audit_log_mutation ON audit_log;

        CREATE TRIGGER trg_prevent_audit_log_mutation
        BEFORE UPDATE OR DELETE ON audit_log
        FOR EACH ROW
        EXECUTE FUNCTION prevent_audit_log_mutation()
        """
    )

    if not _relation_exists(bind, "note_contributions"):
        op.execute(
            """
            CREATE MATERIALIZED VIEW note_contributions AS
            SELECT
                al.note_id,
                al.workspace_id,
                al.actor_user_id AS contributor_user_id,
                COUNT(*)::bigint AS contribution_count,
                COUNT(*) FILTER (WHERE al.action_type = 'note.created')::bigint AS note_create_count,
                COUNT(*) FILTER (WHERE al.action_type = 'note.updated')::bigint AS note_update_count,
                COUNT(*) FILTER (WHERE al.action_type = 'note.restored')::bigint AS note_restore_count,
                COUNT(*) FILTER (
                    WHERE al.action_type = 'thinking_session.contribution_submitted'
                )::bigint AS thinking_contribution_count,
                COUNT(*) FILTER (WHERE al.action_type = 'thinking_session.vote_cast')::bigint AS vote_cast_count,
                MIN(al.created_at) AS first_contribution_at,
                MAX(al.created_at) AS last_contribution_at
            FROM audit_log al
            WHERE al.note_id IS NOT NULL
              AND al.actor_user_id IS NOT NULL
              AND al.action_type IN (
                  'note.created',
                  'note.updated',
                  'note.restored',
                  'thinking_session.contribution_submitted',
                  'thinking_session.vote_cast'
              )
            GROUP BY al.note_id, al.workspace_id, al.actor_user_id
            """
        )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_note_contributions_note_user
        ON note_contributions (note_id, contributor_user_id)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_note_contributions_note_rank
        ON note_contributions (note_id, contribution_count DESC, last_contribution_at DESC)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_note_contributions_note_rank")
    op.execute("DROP INDEX IF EXISTS uq_note_contributions_note_user")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS note_contributions")

    op.execute("DROP TRIGGER IF EXISTS trg_prevent_audit_log_mutation ON audit_log")
    op.execute("DROP FUNCTION IF EXISTS prevent_audit_log_mutation()")

    op.execute("DROP INDEX IF EXISTS idx_audit_log_note_contribution_actions")
    op.drop_index("idx_audit_log_entity_lookup", table_name="audit_log")
    op.drop_index("idx_audit_log_action_created_at", table_name="audit_log")
    op.drop_index("idx_audit_log_note_created_at", table_name="audit_log")
    op.drop_index("idx_audit_log_actor_created_at", table_name="audit_log")
    op.drop_index("idx_audit_log_workspace_created_at", table_name="audit_log")
    op.drop_index("ix_audit_log_source", table_name="audit_log")
    op.drop_index("ix_audit_log_session_id", table_name="audit_log")
    op.drop_index("ix_audit_log_request_id", table_name="audit_log")
    op.drop_index("ix_audit_log_entity_id", table_name="audit_log")
    op.drop_index("ix_audit_log_entity_type", table_name="audit_log")
    op.drop_index("ix_audit_log_action_type", table_name="audit_log")
    op.drop_index("ix_audit_log_target_user_id", table_name="audit_log")
    op.drop_index("ix_audit_log_note_id", table_name="audit_log")
    op.drop_index("ix_audit_log_workspace_id", table_name="audit_log")
    op.drop_index("ix_audit_log_actor_user_id", table_name="audit_log")
    op.drop_index("ix_audit_log_created_at", table_name="audit_log")
    op.drop_table("audit_log")
