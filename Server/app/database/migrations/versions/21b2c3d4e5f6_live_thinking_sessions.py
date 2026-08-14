"""Add live thinking session state, contributions, votes, and synthesis runs.

Revision ID: 21b2c3d4e5f6
Revises: 13a1b2c3d4e5
Create Date: 2026-04-21 15:10:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "21b2c3d4e5f6"
down_revision = "13a1b2c3d4e5"
branch_labels = None
depends_on = None


THINKING_SESSION_PHASE_ENUM = postgresql.ENUM(
    "waiting",
    "gathering",
    "synthesizing",
    "refining",
    "completed",
    name="thinkingsessionphase",
    create_type=False,
)

THINKING_SYNTHESIS_STATUS_ENUM = postgresql.ENUM(
    "pending",
    "streaming",
    "completed",
    "failed",
    "cancelled",
    name="thinkingsynthesisstatus",
    create_type=False,
)


def _table_exists(bind: sa.Connection, table_name: str) -> bool:
    return bool(
        bind.execute(
            sa.text("SELECT to_regclass(:table_name) IS NOT NULL"),
            {"table_name": f"public.{table_name}"},
        ).scalar()
    )


def _constraint_exists(bind: sa.Connection, table_name: str, constraint_name: str) -> bool:
    return bool(
        bind.execute(
            sa.text(
                """
                SELECT 1
                FROM information_schema.table_constraints
                WHERE table_schema = 'public'
                  AND table_name = :table_name
                  AND constraint_name = :constraint_name
                """
            ),
            {"table_name": table_name, "constraint_name": constraint_name},
        ).scalar()
    )


def _ensure_unique_constraint(table_name: str, constraint_name: str, columns: tuple[str, ...]) -> None:
    bind = op.get_bind()
    if not _constraint_exists(bind, table_name, constraint_name):
        op.create_unique_constraint(constraint_name, table_name, list(columns))


def _create_index_if_missing(index_name: str, table_name: str, columns: tuple[str, ...]) -> None:
    op.execute(f"CREATE INDEX IF NOT EXISTS {index_name} ON {table_name} ({', '.join(columns)})")


def upgrade() -> None:
    bind = op.get_bind()
    THINKING_SESSION_PHASE_ENUM.create(bind, checkfirst=True)
    THINKING_SYNTHESIS_STATUS_ENUM.create(bind, checkfirst=True)

    if not _table_exists(bind, "thinking_sessions"):
        op.create_table(
            "thinking_sessions",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column(
                "workspace_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "note_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("notes.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("room_id", sa.String(length=255), nullable=False),
            sa.Column("title", sa.String(length=255), nullable=False),
            sa.Column("prompt_context", sa.Text(), nullable=True),
            sa.Column(
                "created_by_user_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "host_user_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "phase",
                THINKING_SESSION_PHASE_ENUM,
                nullable=False,
                server_default=sa.text("'waiting'"),
            ),
            sa.Column("phase_entered_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("waiting_started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("gathering_started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("synthesizing_started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("refining_started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("active_synthesis_run_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("synthesis_output", sa.Text(), nullable=True),
            sa.Column("refined_output", sa.Text(), nullable=True),
            sa.Column("final_output", sa.Text(), nullable=True),
            sa.Column(
                "last_refined_by_user_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.UniqueConstraint("room_id", name="uq_thinking_sessions_room_id"),
        )
    else:
        _ensure_unique_constraint("thinking_sessions", "uq_thinking_sessions_room_id", ("room_id",))

    op.execute("ALTER TABLE thinking_sessions ALTER COLUMN phase SET DEFAULT 'waiting'")
    op.execute("ALTER TABLE thinking_sessions ALTER COLUMN phase_entered_at SET DEFAULT now()")
    op.execute("ALTER TABLE thinking_sessions ALTER COLUMN waiting_started_at SET DEFAULT now()")
    op.execute("ALTER TABLE thinking_sessions ALTER COLUMN created_at SET DEFAULT now()")
    op.execute("ALTER TABLE thinking_sessions ALTER COLUMN updated_at SET DEFAULT now()")
    _create_index_if_missing("ix_thinking_sessions_workspace_id", "thinking_sessions", ("workspace_id",))
    _create_index_if_missing("ix_thinking_sessions_note_id", "thinking_sessions", ("note_id",))
    _create_index_if_missing("ix_thinking_sessions_room_id", "thinking_sessions", ("room_id",))
    _create_index_if_missing("ix_thinking_sessions_created_by_user_id", "thinking_sessions", ("created_by_user_id",))
    _create_index_if_missing("ix_thinking_sessions_host_user_id", "thinking_sessions", ("host_user_id",))
    _create_index_if_missing("ix_thinking_sessions_phase", "thinking_sessions", ("phase",))
    _create_index_if_missing("ix_thinking_sessions_active_synthesis_run_id", "thinking_sessions", ("active_synthesis_run_id",))
    _create_index_if_missing("ix_thinking_sessions_last_refined_by_user_id", "thinking_sessions", ("last_refined_by_user_id",))
    _create_index_if_missing("ix_thinking_sessions_created_at", "thinking_sessions", ("created_at",))
    _create_index_if_missing("ix_thinking_sessions_updated_at", "thinking_sessions", ("updated_at",))
    _create_index_if_missing("idx_thinking_sessions_workspace_phase", "thinking_sessions", ("workspace_id", "phase", "updated_at"))
    _create_index_if_missing("idx_thinking_sessions_host_phase", "thinking_sessions", ("host_user_id", "phase", "updated_at"))

    if not _table_exists(bind, "thinking_session_participants"):
        op.create_table(
            "thinking_session_participants",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column(
                "session_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("thinking_sessions.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "user_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.UniqueConstraint("session_id", "user_id", name="uq_thinking_session_participants_session_user"),
        )
    else:
        _ensure_unique_constraint(
            "thinking_session_participants",
            "uq_thinking_session_participants_session_user",
            ("session_id", "user_id"),
        )

    op.execute("ALTER TABLE thinking_session_participants ALTER COLUMN joined_at SET DEFAULT now()")
    op.execute("ALTER TABLE thinking_session_participants ALTER COLUMN last_seen_at SET DEFAULT now()")
    _create_index_if_missing(
        "ix_thinking_session_participants_session_id",
        "thinking_session_participants",
        ("session_id",),
    )
    _create_index_if_missing("ix_thinking_session_participants_user_id", "thinking_session_participants", ("user_id",))
    _create_index_if_missing("ix_thinking_session_participants_joined_at", "thinking_session_participants", ("joined_at",))
    _create_index_if_missing(
        "ix_thinking_session_participants_last_seen_at",
        "thinking_session_participants",
        ("last_seen_at",),
    )
    _create_index_if_missing(
        "idx_thinking_session_participants_last_seen",
        "thinking_session_participants",
        ("session_id", "last_seen_at"),
    )

    if not _table_exists(bind, "thinking_session_contributions"):
        op.create_table(
            "thinking_session_contributions",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column(
                "session_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("thinking_sessions.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "author_user_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column(
                "created_phase",
                THINKING_SESSION_PHASE_ENUM,
                nullable=False,
                server_default=sa.text("'gathering'"),
            ),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        )

    op.execute("ALTER TABLE thinking_session_contributions ALTER COLUMN created_phase SET DEFAULT 'gathering'")
    op.execute("ALTER TABLE thinking_session_contributions ALTER COLUMN created_at SET DEFAULT now()")
    op.execute("ALTER TABLE thinking_session_contributions ALTER COLUMN updated_at SET DEFAULT now()")
    _create_index_if_missing(
        "ix_thinking_session_contributions_session_id",
        "thinking_session_contributions",
        ("session_id",),
    )
    _create_index_if_missing(
        "ix_thinking_session_contributions_author_user_id",
        "thinking_session_contributions",
        ("author_user_id",),
    )
    _create_index_if_missing(
        "ix_thinking_session_contributions_created_at",
        "thinking_session_contributions",
        ("created_at",),
    )
    _create_index_if_missing(
        "ix_thinking_session_contributions_updated_at",
        "thinking_session_contributions",
        ("updated_at",),
    )
    _create_index_if_missing(
        "idx_thinking_session_contributions_session_created",
        "thinking_session_contributions",
        ("session_id", "created_at"),
    )

    if not _table_exists(bind, "thinking_session_votes"):
        op.create_table(
            "thinking_session_votes",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column(
                "session_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("thinking_sessions.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "contribution_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("thinking_session_contributions.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "user_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.UniqueConstraint("contribution_id", "user_id", name="uq_thinking_session_votes_contribution_user"),
        )
    else:
        _ensure_unique_constraint(
            "thinking_session_votes",
            "uq_thinking_session_votes_contribution_user",
            ("contribution_id", "user_id"),
        )

    op.execute("ALTER TABLE thinking_session_votes ALTER COLUMN created_at SET DEFAULT now()")
    _create_index_if_missing("ix_thinking_session_votes_session_id", "thinking_session_votes", ("session_id",))
    _create_index_if_missing(
        "ix_thinking_session_votes_contribution_id",
        "thinking_session_votes",
        ("contribution_id",),
    )
    _create_index_if_missing("ix_thinking_session_votes_user_id", "thinking_session_votes", ("user_id",))
    _create_index_if_missing("ix_thinking_session_votes_created_at", "thinking_session_votes", ("created_at",))
    _create_index_if_missing(
        "idx_thinking_session_votes_session_contribution",
        "thinking_session_votes",
        ("session_id", "contribution_id"),
    )

    if not _table_exists(bind, "thinking_session_synthesis_runs"):
        op.create_table(
            "thinking_session_synthesis_runs",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column(
                "session_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("thinking_sessions.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "triggered_by_user_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "status",
                THINKING_SYNTHESIS_STATUS_ENUM,
                nullable=False,
                server_default=sa.text("'pending'"),
            ),
            sa.Column("model", sa.String(length=100), nullable=False, server_default=sa.text("'gpt-4o'")),
            sa.Column(
                "snapshot_payload",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'{}'::jsonb"),
            ),
            sa.Column("facilitation_prompt", sa.Text(), nullable=True),
            sa.Column("output_text", sa.Text(), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        )

    op.execute("ALTER TABLE thinking_session_synthesis_runs ALTER COLUMN status SET DEFAULT 'pending'")
    op.execute("ALTER TABLE thinking_session_synthesis_runs ALTER COLUMN model SET DEFAULT 'gpt-4o'")
    op.execute("ALTER TABLE thinking_session_synthesis_runs ALTER COLUMN snapshot_payload SET DEFAULT '{}'::jsonb")
    op.execute("ALTER TABLE thinking_session_synthesis_runs ALTER COLUMN created_at SET DEFAULT now()")
    op.execute("ALTER TABLE thinking_session_synthesis_runs ALTER COLUMN updated_at SET DEFAULT now()")
    _create_index_if_missing(
        "ix_thinking_session_synthesis_runs_session_id",
        "thinking_session_synthesis_runs",
        ("session_id",),
    )
    _create_index_if_missing(
        "ix_thinking_session_synthesis_runs_triggered_by_user_id",
        "thinking_session_synthesis_runs",
        ("triggered_by_user_id",),
    )
    _create_index_if_missing(
        "ix_thinking_session_synthesis_runs_status",
        "thinking_session_synthesis_runs",
        ("status",),
    )
    _create_index_if_missing(
        "ix_thinking_session_synthesis_runs_created_at",
        "thinking_session_synthesis_runs",
        ("created_at",),
    )
    _create_index_if_missing(
        "ix_thinking_session_synthesis_runs_updated_at",
        "thinking_session_synthesis_runs",
        ("updated_at",),
    )
    _create_index_if_missing(
        "idx_thinking_session_synthesis_runs_session_status",
        "thinking_session_synthesis_runs",
        ("session_id", "status", "created_at"),
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_thinking_session_single_streaming_run
        ON thinking_session_synthesis_runs (session_id)
        WHERE status = 'streaming'
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_thinking_session_single_streaming_run")

    op.drop_index("idx_thinking_session_synthesis_runs_session_status", table_name="thinking_session_synthesis_runs")
    op.drop_index("ix_thinking_session_synthesis_runs_updated_at", table_name="thinking_session_synthesis_runs")
    op.drop_index("ix_thinking_session_synthesis_runs_created_at", table_name="thinking_session_synthesis_runs")
    op.drop_index("ix_thinking_session_synthesis_runs_status", table_name="thinking_session_synthesis_runs")
    op.drop_index("ix_thinking_session_synthesis_runs_triggered_by_user_id", table_name="thinking_session_synthesis_runs")
    op.drop_index("ix_thinking_session_synthesis_runs_session_id", table_name="thinking_session_synthesis_runs")
    op.drop_table("thinking_session_synthesis_runs")

    op.drop_index("idx_thinking_session_votes_session_contribution", table_name="thinking_session_votes")
    op.drop_index("ix_thinking_session_votes_created_at", table_name="thinking_session_votes")
    op.drop_index("ix_thinking_session_votes_user_id", table_name="thinking_session_votes")
    op.drop_index("ix_thinking_session_votes_contribution_id", table_name="thinking_session_votes")
    op.drop_index("ix_thinking_session_votes_session_id", table_name="thinking_session_votes")
    op.drop_table("thinking_session_votes")

    op.drop_index("idx_thinking_session_contributions_session_created", table_name="thinking_session_contributions")
    op.drop_index("ix_thinking_session_contributions_updated_at", table_name="thinking_session_contributions")
    op.drop_index("ix_thinking_session_contributions_created_at", table_name="thinking_session_contributions")
    op.drop_index("ix_thinking_session_contributions_author_user_id", table_name="thinking_session_contributions")
    op.drop_index("ix_thinking_session_contributions_session_id", table_name="thinking_session_contributions")
    op.drop_table("thinking_session_contributions")

    op.drop_index("idx_thinking_session_participants_last_seen", table_name="thinking_session_participants")
    op.drop_index("ix_thinking_session_participants_last_seen_at", table_name="thinking_session_participants")
    op.drop_index("ix_thinking_session_participants_joined_at", table_name="thinking_session_participants")
    op.drop_index("ix_thinking_session_participants_user_id", table_name="thinking_session_participants")
    op.drop_index("ix_thinking_session_participants_session_id", table_name="thinking_session_participants")
    op.drop_table("thinking_session_participants")

    op.drop_index("idx_thinking_sessions_host_phase", table_name="thinking_sessions")
    op.drop_index("idx_thinking_sessions_workspace_phase", table_name="thinking_sessions")
    op.drop_index("ix_thinking_sessions_updated_at", table_name="thinking_sessions")
    op.drop_index("ix_thinking_sessions_created_at", table_name="thinking_sessions")
    op.drop_index("ix_thinking_sessions_last_refined_by_user_id", table_name="thinking_sessions")
    op.drop_index("ix_thinking_sessions_active_synthesis_run_id", table_name="thinking_sessions")
    op.drop_index("ix_thinking_sessions_phase", table_name="thinking_sessions")
    op.drop_index("ix_thinking_sessions_host_user_id", table_name="thinking_sessions")
    op.drop_index("ix_thinking_sessions_created_by_user_id", table_name="thinking_sessions")
    op.drop_index("ix_thinking_sessions_room_id", table_name="thinking_sessions")
    op.drop_index("ix_thinking_sessions_note_id", table_name="thinking_sessions")
    op.drop_index("ix_thinking_sessions_workspace_id", table_name="thinking_sessions")
    op.drop_table("thinking_sessions")

    THINKING_SYNTHESIS_STATUS_ENUM.drop(op.get_bind(), checkfirst=True)
    THINKING_SESSION_PHASE_ENUM.drop(op.get_bind(), checkfirst=True)
