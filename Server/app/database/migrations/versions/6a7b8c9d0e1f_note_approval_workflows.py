"""Add note approval workflow state, history, and notifications.

Revision ID: 6a7b8c9d0e1f
Revises: 5b7c8d9e0f1a
Create Date: 2026-04-22 10:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "6a7b8c9d0e1f"
down_revision = "5b7c8d9e0f1a"
branch_labels = None
depends_on = None


APPROVAL_WORKFLOW_STATUS_ENUM = postgresql.ENUM(
    "draft",
    "submitted",
    "needs_changes",
    "approved",
    "rejected",
    "cancelled",
    name="approvalworkflowstatus",
    create_type=False,
)

APPROVAL_WORKFLOW_PRIORITY_ENUM = postgresql.ENUM(
    "low",
    "normal",
    "high",
    "critical",
    name="approvalworkflowpriority",
    create_type=False,
)


def _table_exists(bind: sa.Connection, table_name: str) -> bool:
    return bool(
        bind.execute(
            sa.text("SELECT to_regclass(:table_name) IS NOT NULL"),
            {"table_name": f"public.{table_name}"},
        ).scalar()
    )


def _column_exists(bind: sa.Connection, table_name: str, column_name: str) -> bool:
    return bool(
        bind.execute(
            sa.text(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = :table_name
                  AND column_name = :column_name
                """
            ),
            {"table_name": table_name, "column_name": column_name},
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


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    bind = op.get_bind()
    if not _column_exists(bind, table_name, column.name):
        op.add_column(table_name, column)


def _create_foreign_key_if_missing(
    constraint_name: str,
    source_table: str,
    referent_table: str,
    local_cols: tuple[str, ...],
    remote_cols: tuple[str, ...],
) -> None:
    bind = op.get_bind()
    if not _constraint_exists(bind, source_table, constraint_name):
        op.create_foreign_key(
            constraint_name,
            source_table,
            referent_table,
            list(local_cols),
            list(remote_cols),
        )


def _create_index_if_missing(index_name: str, table_name: str, columns: tuple[str, ...]) -> None:
    op.execute(f"CREATE INDEX IF NOT EXISTS {index_name} ON {table_name} ({', '.join(columns)})")


def upgrade() -> None:
    bind = op.get_bind()
    APPROVAL_WORKFLOW_STATUS_ENUM.create(bind, checkfirst=True)
    APPROVAL_WORKFLOW_PRIORITY_ENUM.create(bind, checkfirst=True)

    op.execute("ALTER TYPE usernotificationtype ADD VALUE IF NOT EXISTS 'approval_submitted'")
    op.execute("ALTER TYPE usernotificationtype ADD VALUE IF NOT EXISTS 'approval_approved'")
    op.execute("ALTER TYPE usernotificationtype ADD VALUE IF NOT EXISTS 'approval_rejected'")
    op.execute("ALTER TYPE usernotificationtype ADD VALUE IF NOT EXISTS 'approval_needs_changes'")

    _add_column_if_missing(
        "notes",
        sa.Column(
            "approval_status",
            APPROVAL_WORKFLOW_STATUS_ENUM,
            nullable=False,
            server_default=sa.text("'draft'"),
        ),
    )
    _add_column_if_missing(
        "notes",
        sa.Column(
            "approval_priority",
            APPROVAL_WORKFLOW_PRIORITY_ENUM,
            nullable=False,
            server_default=sa.text("'normal'"),
        ),
    )
    _add_column_if_missing("notes", sa.Column("approval_due_at", sa.DateTime(timezone=True), nullable=True))
    _add_column_if_missing("notes", sa.Column("approval_submitted_at", sa.DateTime(timezone=True), nullable=True))
    _add_column_if_missing("notes", sa.Column("approval_submitted_by_user_id", postgresql.UUID(as_uuid=True), nullable=True))
    _add_column_if_missing("notes", sa.Column("approval_decided_at", sa.DateTime(timezone=True), nullable=True))
    _add_column_if_missing("notes", sa.Column("approval_decided_by_user_id", postgresql.UUID(as_uuid=True), nullable=True))

    op.execute("ALTER TABLE notes ALTER COLUMN approval_status SET DEFAULT 'draft'")
    op.execute("ALTER TABLE notes ALTER COLUMN approval_priority SET DEFAULT 'normal'")
    _create_foreign_key_if_missing(
        "fk_notes_approval_submitted_by_user_id_users",
        "notes",
        "users",
        ("approval_submitted_by_user_id",),
        ("id",),
    )
    _create_foreign_key_if_missing(
        "fk_notes_approval_decided_by_user_id_users",
        "notes",
        "users",
        ("approval_decided_by_user_id",),
        ("id",),
    )

    _create_index_if_missing("ix_notes_approval_status", "notes", ("approval_status",))
    _create_index_if_missing("ix_notes_approval_priority", "notes", ("approval_priority",))
    _create_index_if_missing("ix_notes_approval_due_at", "notes", ("approval_due_at",))
    _create_index_if_missing("ix_notes_approval_submitted_at", "notes", ("approval_submitted_at",))
    _create_index_if_missing("ix_notes_approval_submitted_by_user_id", "notes", ("approval_submitted_by_user_id",))
    _create_index_if_missing("ix_notes_approval_decided_at", "notes", ("approval_decided_at",))
    _create_index_if_missing("ix_notes_approval_decided_by_user_id", "notes", ("approval_decided_by_user_id",))
    _create_index_if_missing(
        "idx_note_approval_dashboard",
        "notes",
        ("workspace_id", "approval_status", "approval_due_at", "approval_priority"),
    )
    _create_index_if_missing(
        "idx_note_approval_submitter",
        "notes",
        ("approval_submitted_by_user_id", "approval_status", "approval_submitted_at"),
    )

    if not _table_exists(bind, "note_approval_transitions"):
        op.create_table(
            "note_approval_transitions",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column("note_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("from_status", APPROVAL_WORKFLOW_STATUS_ENUM, nullable=False),
            sa.Column("to_status", APPROVAL_WORKFLOW_STATUS_ENUM, nullable=False),
            sa.Column("comment", sa.Text(), nullable=True),
            sa.Column("due_at_snapshot", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "priority_snapshot",
                APPROVAL_WORKFLOW_PRIORITY_ENUM,
                nullable=False,
                server_default=sa.text("'normal'"),
            ),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.ForeignKeyConstraint(["note_id"], ["notes.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        )

    op.execute("ALTER TABLE note_approval_transitions ALTER COLUMN priority_snapshot SET DEFAULT 'normal'")
    op.execute("ALTER TABLE note_approval_transitions ALTER COLUMN created_at SET DEFAULT now()")
    _create_index_if_missing("ix_note_approval_transitions_note_id", "note_approval_transitions", ("note_id",))
    _create_index_if_missing("ix_note_approval_transitions_workspace_id", "note_approval_transitions", ("workspace_id",))
    _create_index_if_missing("ix_note_approval_transitions_actor_user_id", "note_approval_transitions", ("actor_user_id",))
    _create_index_if_missing("ix_note_approval_transitions_to_status", "note_approval_transitions", ("to_status",))
    _create_index_if_missing("ix_note_approval_transitions_created_at", "note_approval_transitions", ("created_at",))
    _create_index_if_missing(
        "idx_note_approval_transitions_note_created",
        "note_approval_transitions",
        ("note_id", "created_at"),
    )
    _create_index_if_missing(
        "idx_note_approval_transitions_workspace_status",
        "note_approval_transitions",
        ("workspace_id", "to_status", "created_at"),
    )


def downgrade() -> None:
    op.drop_index("idx_note_approval_transitions_workspace_status", table_name="note_approval_transitions")
    op.drop_index("idx_note_approval_transitions_note_created", table_name="note_approval_transitions")
    op.drop_index("ix_note_approval_transitions_created_at", table_name="note_approval_transitions")
    op.drop_index("ix_note_approval_transitions_to_status", table_name="note_approval_transitions")
    op.drop_index("ix_note_approval_transitions_actor_user_id", table_name="note_approval_transitions")
    op.drop_index("ix_note_approval_transitions_workspace_id", table_name="note_approval_transitions")
    op.drop_index("ix_note_approval_transitions_note_id", table_name="note_approval_transitions")
    op.drop_table("note_approval_transitions")

    op.drop_index("idx_note_approval_submitter", table_name="notes")
    op.drop_index("idx_note_approval_dashboard", table_name="notes")
    op.drop_index("ix_notes_approval_decided_by_user_id", table_name="notes")
    op.drop_index("ix_notes_approval_decided_at", table_name="notes")
    op.drop_index("ix_notes_approval_submitted_by_user_id", table_name="notes")
    op.drop_index("ix_notes_approval_submitted_at", table_name="notes")
    op.drop_index("ix_notes_approval_due_at", table_name="notes")
    op.drop_index("ix_notes_approval_priority", table_name="notes")
    op.drop_index("ix_notes_approval_status", table_name="notes")

    op.drop_constraint("fk_notes_approval_decided_by_user_id_users", "notes", type_="foreignkey")
    op.drop_constraint("fk_notes_approval_submitted_by_user_id_users", "notes", type_="foreignkey")

    op.drop_column("notes", "approval_decided_by_user_id")
    op.drop_column("notes", "approval_decided_at")
    op.drop_column("notes", "approval_submitted_by_user_id")
    op.drop_column("notes", "approval_submitted_at")
    op.drop_column("notes", "approval_due_at")
    op.drop_column("notes", "approval_priority")
    op.drop_column("notes", "approval_status")

    bind = op.get_bind()
    APPROVAL_WORKFLOW_PRIORITY_ENUM.drop(bind, checkfirst=True)
    APPROVAL_WORKFLOW_STATUS_ENUM.drop(bind, checkfirst=True)
