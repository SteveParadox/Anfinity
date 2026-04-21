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


def upgrade() -> None:
    NOTE_COLLABORATION_ROLE_ENUM.create(op.get_bind(), checkfirst=True)
    NOTE_INVITE_STATUS_ENUM.create(op.get_bind(), checkfirst=True)

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
    op.create_index("ix_note_collaborators_note_id", "note_collaborators", ["note_id"])
    op.create_index("ix_note_collaborators_user_id", "note_collaborators", ["user_id"])
    op.create_index("ix_note_collaborators_granted_by_user_id", "note_collaborators", ["granted_by_user_id"])
    op.create_index("ix_note_collaborators_created_at", "note_collaborators", ["created_at"])
    op.create_index("ix_note_collaborators_updated_at", "note_collaborators", ["updated_at"])
    op.create_index(
        "idx_note_collaborators_note_role",
        "note_collaborators",
        ["note_id", "role", "updated_at"],
    )
    op.create_index(
        "idx_note_collaborators_user_role",
        "note_collaborators",
        ["user_id", "role", "updated_at"],
    )

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
    op.create_index("ix_note_invites_note_id", "note_invites", ["note_id"])
    op.create_index("ix_note_invites_inviter_user_id", "note_invites", ["inviter_user_id"])
    op.create_index("ix_note_invites_invitee_email", "note_invites", ["invitee_email"])
    op.create_index("ix_note_invites_invitee_user_id", "note_invites", ["invitee_user_id"])
    op.create_index("ix_note_invites_status", "note_invites", ["status"])
    op.create_index("ix_note_invites_expires_at", "note_invites", ["expires_at"])
    op.create_index("ix_note_invites_created_at", "note_invites", ["created_at"])
    op.create_index("ix_note_invites_updated_at", "note_invites", ["updated_at"])
    op.create_index(
        "idx_note_invites_note_status",
        "note_invites",
        ["note_id", "status", "created_at"],
    )
    op.create_index(
        "idx_note_invites_email_status",
        "note_invites",
        ["invitee_email", "status", "expires_at"],
    )
    op.create_index(
        "idx_note_invites_user_status",
        "note_invites",
        ["invitee_user_id", "status", "expires_at"],
    )

    op.execute(
        """
        CREATE UNIQUE INDEX uq_note_invites_pending_email
        ON note_invites (note_id, lower(invitee_email))
        WHERE status = 'pending' AND invitee_email IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_note_invites_pending_user
        ON note_invites (note_id, invitee_user_id)
        WHERE status = 'pending' AND invitee_user_id IS NOT NULL
        """
    )


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
