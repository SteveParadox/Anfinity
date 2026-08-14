"""Add note collaboration collaborators and invites.

Revision ID: 13a1b2c3d4e5
Revises: e5f6a7b8c9d0
Create Date: 2026-04-21 10:30:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "13a1b2c3d4e5"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None


NOTE_COLLABORATION_ROLE_ENUM = postgresql.ENUM(
    "viewer",
    "editor",
    name="notecollaborationrole",
    create_type=False,
)

NOTE_INVITE_STATUS_ENUM = postgresql.ENUM(
    "pending",
    "accepted",
    "revoked",
    "expired",
    name="noteinvitestatus",
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


def _ensure_unique_constraint(table_name: str, constraint_name: str, columns: tuple[str, ...]) -> None:
    bind = op.get_bind()
    if not _constraint_exists(bind, table_name, constraint_name):
        op.create_unique_constraint(constraint_name, table_name, list(columns))


def _ensure_check_constraint(table_name: str, constraint_name: str, condition: str) -> None:
    bind = op.get_bind()
    if not _constraint_exists(bind, table_name, constraint_name):
        op.create_check_constraint(constraint_name, table_name, condition)


def _ensure_note_collaborators_table(bind: sa.Connection) -> None:
    if not _table_exists(bind, "note_collaborators"):
        op.create_table(
            "note_collaborators",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column(
                "note_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("notes.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "user_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("role", NOTE_COLLABORATION_ROLE_ENUM, nullable=False, server_default=sa.text("'viewer'")),
            sa.Column(
                "granted_by_user_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.UniqueConstraint("note_id", "user_id", name="uq_note_collaborators_note_user"),
        )
    else:
        _add_column_if_missing("note_collaborators", sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False))
        _add_column_if_missing("note_collaborators", sa.Column("note_id", postgresql.UUID(as_uuid=True), nullable=False))
        _add_column_if_missing("note_collaborators", sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False))
        _add_column_if_missing(
            "note_collaborators",
            sa.Column("role", NOTE_COLLABORATION_ROLE_ENUM, nullable=False, server_default=sa.text("'viewer'")),
        )
        _add_column_if_missing("note_collaborators", sa.Column("granted_by_user_id", postgresql.UUID(as_uuid=True), nullable=True))
        _add_column_if_missing(
            "note_collaborators",
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        )
        _add_column_if_missing(
            "note_collaborators",
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        )
        op.execute("ALTER TABLE note_collaborators ALTER COLUMN role SET DEFAULT 'viewer'")
        op.execute("ALTER TABLE note_collaborators ALTER COLUMN created_at SET DEFAULT now()")
        op.execute("ALTER TABLE note_collaborators ALTER COLUMN updated_at SET DEFAULT now()")
        _ensure_unique_constraint(
            "note_collaborators",
            "uq_note_collaborators_note_user",
            ("note_id", "user_id"),
        )

    op.execute("CREATE INDEX IF NOT EXISTS ix_note_collaborators_note_id ON note_collaborators (note_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_note_collaborators_user_id ON note_collaborators (user_id)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_note_collaborators_granted_by_user_id ON note_collaborators (granted_by_user_id)"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_note_collaborators_created_at ON note_collaborators (created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_note_collaborators_updated_at ON note_collaborators (updated_at)")
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_note_collaborators_note_role
        ON note_collaborators (note_id, role, updated_at)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_note_collaborators_user_role
        ON note_collaborators (user_id, role, updated_at)
        """
    )


def _ensure_note_invites_table(bind: sa.Connection) -> None:
    if not _table_exists(bind, "note_invites"):
        op.create_table(
            "note_invites",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column(
                "note_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("notes.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "inviter_user_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("invitee_email", sa.String(length=255), nullable=True),
            sa.Column(
                "invitee_user_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("role", NOTE_COLLABORATION_ROLE_ENUM, nullable=False, server_default=sa.text("'viewer'")),
            sa.Column("status", NOTE_INVITE_STATUS_ENUM, nullable=False, server_default=sa.text("'pending'")),
            sa.Column("token_hash", sa.String(length=128), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("message", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.UniqueConstraint("token_hash", name="uq_note_invites_token_hash"),
            sa.CheckConstraint(
                "(invitee_email IS NOT NULL) OR (invitee_user_id IS NOT NULL)",
                name="ck_note_invites_target_present",
            ),
        )
    else:
        _add_column_if_missing("note_invites", sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False))
        _add_column_if_missing("note_invites", sa.Column("note_id", postgresql.UUID(as_uuid=True), nullable=False))
        _add_column_if_missing("note_invites", sa.Column("inviter_user_id", postgresql.UUID(as_uuid=True), nullable=True))
        _add_column_if_missing("note_invites", sa.Column("invitee_email", sa.String(length=255), nullable=True))
        _add_column_if_missing("note_invites", sa.Column("invitee_user_id", postgresql.UUID(as_uuid=True), nullable=True))
        _add_column_if_missing(
            "note_invites",
            sa.Column("role", NOTE_COLLABORATION_ROLE_ENUM, nullable=False, server_default=sa.text("'viewer'")),
        )
        _add_column_if_missing(
            "note_invites",
            sa.Column("status", NOTE_INVITE_STATUS_ENUM, nullable=False, server_default=sa.text("'pending'")),
        )
        _add_column_if_missing("note_invites", sa.Column("token_hash", sa.String(length=128), nullable=False))
        _add_column_if_missing("note_invites", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False))
        _add_column_if_missing("note_invites", sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True))
        _add_column_if_missing("note_invites", sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True))
        _add_column_if_missing("note_invites", sa.Column("message", sa.Text(), nullable=True))
        _add_column_if_missing(
            "note_invites",
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        )
        _add_column_if_missing(
            "note_invites",
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        )
        op.execute("ALTER TABLE note_invites ALTER COLUMN role SET DEFAULT 'viewer'")
        op.execute("ALTER TABLE note_invites ALTER COLUMN status SET DEFAULT 'pending'")
        op.execute("ALTER TABLE note_invites ALTER COLUMN created_at SET DEFAULT now()")
        op.execute("ALTER TABLE note_invites ALTER COLUMN updated_at SET DEFAULT now()")
        _ensure_unique_constraint("note_invites", "uq_note_invites_token_hash", ("token_hash",))
        _ensure_check_constraint(
            "note_invites",
            "ck_note_invites_target_present",
            "(invitee_email IS NOT NULL) OR (invitee_user_id IS NOT NULL)",
        )

    op.execute("CREATE INDEX IF NOT EXISTS ix_note_invites_note_id ON note_invites (note_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_note_invites_inviter_user_id ON note_invites (inviter_user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_note_invites_invitee_email ON note_invites (invitee_email)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_note_invites_invitee_user_id ON note_invites (invitee_user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_note_invites_status ON note_invites (status)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_note_invites_expires_at ON note_invites (expires_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_note_invites_created_at ON note_invites (created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_note_invites_updated_at ON note_invites (updated_at)")
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_note_invites_note_status
        ON note_invites (note_id, status, created_at)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_note_invites_email_status
        ON note_invites (invitee_email, status, expires_at)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_note_invites_user_status
        ON note_invites (invitee_user_id, status, expires_at)
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_note_invites_pending_email
        ON note_invites (note_id, lower(invitee_email))
        WHERE status = 'pending' AND invitee_email IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_note_invites_pending_user
        ON note_invites (note_id, invitee_user_id)
        WHERE status = 'pending' AND invitee_user_id IS NOT NULL
        """
    )


def upgrade() -> None:
    bind = op.get_bind()
    NOTE_COLLABORATION_ROLE_ENUM.create(bind, checkfirst=True)
    NOTE_INVITE_STATUS_ENUM.create(bind, checkfirst=True)
    _ensure_note_collaborators_table(bind)
    _ensure_note_invites_table(bind)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_note_invites_pending_user")
    op.execute("DROP INDEX IF EXISTS uq_note_invites_pending_email")

    op.drop_index("idx_note_invites_user_status", table_name="note_invites")
    op.drop_index("idx_note_invites_email_status", table_name="note_invites")
    op.drop_index("idx_note_invites_note_status", table_name="note_invites")
    op.drop_index("ix_note_invites_updated_at", table_name="note_invites")
    op.drop_index("ix_note_invites_created_at", table_name="note_invites")
    op.drop_index("ix_note_invites_expires_at", table_name="note_invites")
    op.drop_index("ix_note_invites_status", table_name="note_invites")
    op.drop_index("ix_note_invites_invitee_user_id", table_name="note_invites")
    op.drop_index("ix_note_invites_invitee_email", table_name="note_invites")
    op.drop_index("ix_note_invites_inviter_user_id", table_name="note_invites")
    op.drop_index("ix_note_invites_note_id", table_name="note_invites")
    op.drop_table("note_invites")

    op.drop_index("idx_note_collaborators_user_role", table_name="note_collaborators")
    op.drop_index("idx_note_collaborators_note_role", table_name="note_collaborators")
    op.drop_index("ix_note_collaborators_updated_at", table_name="note_collaborators")
    op.drop_index("ix_note_collaborators_created_at", table_name="note_collaborators")
    op.drop_index("ix_note_collaborators_granted_by_user_id", table_name="note_collaborators")
    op.drop_index("ix_note_collaborators_user_id", table_name="note_collaborators")
    op.drop_index("ix_note_collaborators_note_id", table_name="note_collaborators")
    op.drop_table("note_collaborators")

    NOTE_INVITE_STATUS_ENUM.drop(op.get_bind(), checkfirst=True)
    NOTE_COLLABORATION_ROLE_ENUM.drop(op.get_bind(), checkfirst=True)
