"""Repair note contribution aggregate objects.

Revision ID: 4a5b6c7d8e9f
Revises: 3f4a5b6c7d8e
Create Date: 2026-08-14 13:30:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "4a5b6c7d8e9f"
down_revision = "3f4a5b6c7d8e"
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


def _index_exists(bind: sa.Connection, index_name: str) -> bool:
    return bool(
        bind.execute(
            sa.text("SELECT to_regclass(:index_name) IS NOT NULL"),
            {"index_name": f"public.{index_name}"},
        ).scalar()
    )


def _relation_kind(bind: sa.Connection, relation_name: str) -> str | None:
    return bind.execute(
        sa.text(
            """
            SELECT c.relkind
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public'
              AND c.relname = :relation_name
            """
        ),
        {"relation_name": relation_name},
    ).scalar()


def _create_audit_log_if_missing(bind: sa.Connection) -> None:
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
            sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column("request_id", sa.String(length=255), nullable=True),
            sa.Column("session_id", sa.String(length=255), nullable=True),
            sa.Column("source", sa.String(length=100), nullable=True),
            sa.Column("ip_address", sa.String(length=45), nullable=True),
            sa.Column("user_agent", sa.String(length=500), nullable=True),
        )
        return

    op.execute("ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()")
    op.execute("ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS actor_user_id UUID")
    op.execute("ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS workspace_id UUID")
    op.execute("ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS note_id UUID")
    op.execute("ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS target_user_id UUID")
    op.execute("ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS action_type VARCHAR(100) NOT NULL DEFAULT 'unknown'")
    op.execute("ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS entity_type VARCHAR(50) NOT NULL DEFAULT 'unknown'")
    op.execute("ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS entity_id VARCHAR(255)")
    op.execute("ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb")
    op.execute("ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS request_id VARCHAR(255)")
    op.execute("ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS session_id VARCHAR(255)")
    op.execute("ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS source VARCHAR(100)")
    op.execute("ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS ip_address VARCHAR(45)")
    op.execute("ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS user_agent VARCHAR(500)")
    op.execute("ALTER TABLE audit_log ALTER COLUMN action_type DROP DEFAULT")
    op.execute("ALTER TABLE audit_log ALTER COLUMN entity_type DROP DEFAULT")


def _create_audit_indexes() -> None:
    for index_name, columns in (
        ("ix_audit_log_created_at", "created_at"),
        ("ix_audit_log_actor_user_id", "actor_user_id"),
        ("ix_audit_log_workspace_id", "workspace_id"),
        ("ix_audit_log_note_id", "note_id"),
        ("ix_audit_log_target_user_id", "target_user_id"),
        ("ix_audit_log_action_type", "action_type"),
        ("ix_audit_log_entity_type", "entity_type"),
        ("ix_audit_log_entity_id", "entity_id"),
        ("ix_audit_log_request_id", "request_id"),
        ("ix_audit_log_session_id", "session_id"),
        ("ix_audit_log_source", "source"),
        ("idx_audit_log_workspace_created_at", "workspace_id, created_at"),
        ("idx_audit_log_actor_created_at", "actor_user_id, created_at"),
        ("idx_audit_log_note_created_at", "note_id, created_at"),
        ("idx_audit_log_action_created_at", "action_type, created_at"),
        ("idx_audit_log_entity_lookup", "entity_type, entity_id, created_at"),
    ):
        op.execute(f"CREATE INDEX IF NOT EXISTS {index_name} ON audit_log ({columns})")

    op.execute(
        f"""
        CREATE INDEX IF NOT EXISTS idx_audit_log_note_contribution_actions
        ON audit_log (note_id, actor_user_id, created_at DESC)
        WHERE note_id IS NOT NULL
          AND actor_user_id IS NOT NULL
          AND action_type IN {NOTE_CONTRIBUTION_ACTIONS}
        """
    )


def _create_audit_immutability_trigger() -> None:
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


def _create_note_contributions_if_missing(bind: sa.Connection) -> None:
    relation_kind = _relation_kind(bind, "note_contributions")
    if relation_kind in {"m", "r"}:
        return
    if relation_kind == "v":
        op.execute("DROP VIEW note_contributions")

    op.execute(
        """
        CREATE MATERIALIZED VIEW note_contributions AS
        SELECT
            al.note_id,
            (ARRAY_AGG(al.workspace_id ORDER BY al.created_at DESC NULLS LAST))[1] AS workspace_id,
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
        GROUP BY al.note_id, al.actor_user_id
        """
    )


def upgrade() -> None:
    bind = op.get_bind()
    _create_audit_log_if_missing(bind)
    _create_audit_indexes()
    _create_audit_immutability_trigger()
    _create_note_contributions_if_missing(bind)

    if not _index_exists(bind, "uq_note_contributions_note_user"):
        op.execute(
            """
            CREATE UNIQUE INDEX uq_note_contributions_note_user
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
