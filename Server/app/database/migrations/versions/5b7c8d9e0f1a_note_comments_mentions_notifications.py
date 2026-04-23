"""Add note comments, mentions, reactions, and notifications.

Revision ID: 5b7c8d9e0f1a
Revises: 4f7a9c0d1e2f
Create Date: 2026-04-21 20:10:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "5b7c8d9e0f1a"
down_revision = "4f7a9c0d1e2f"
branch_labels = None
depends_on = None


note_comment_reaction_type = sa.Enum(
    "thumbs_up",
    "heart",
    "laugh",
    "hooray",
    "eyes",
    "rocket",
    name="notecommentreactiontype",
)

user_notification_type = sa.Enum(
    "comment_mention",
    "comment_reply",
    name="usernotificationtype",
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


def _ensure_check_constraint(table_name: str, constraint_name: str, condition: str) -> None:
    bind = op.get_bind()
    if not _constraint_exists(bind, table_name, constraint_name):
        op.create_check_constraint(constraint_name, table_name, condition)


def _create_index_if_missing(index_name: str, table_name: str, columns: tuple[str, ...]) -> None:
    op.execute(f"CREATE INDEX IF NOT EXISTS {index_name} ON {table_name} ({', '.join(columns)})")


def upgrade() -> None:
    bind = op.get_bind()
    note_comment_reaction_type.create(bind, checkfirst=True)
    user_notification_type.create(bind, checkfirst=True)

    if not _table_exists(bind, "note_comments"):
        op.create_table(
            "note_comments",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column("note_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("author_user_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("parent_comment_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("depth", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("body", sa.Text(), nullable=False),
            sa.Column("is_resolved", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("resolved_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["note_id"], ["notes.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["author_user_id"], ["users.id"]),
            sa.ForeignKeyConstraint(["parent_comment_id"], ["note_comments.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["resolved_by_user_id"], ["users.id"]),
            sa.CheckConstraint("depth >= 0", name="ck_note_comments_depth_non_negative"),
            sa.CheckConstraint("char_length(btrim(body)) > 0", name="ck_note_comments_body_not_blank"),
        )
    else:
        _ensure_check_constraint("note_comments", "ck_note_comments_depth_non_negative", "depth >= 0")
        _ensure_check_constraint("note_comments", "ck_note_comments_body_not_blank", "char_length(btrim(body)) > 0")

    op.execute("ALTER TABLE note_comments ALTER COLUMN depth SET DEFAULT 0")
    op.execute("ALTER TABLE note_comments ALTER COLUMN is_resolved SET DEFAULT false")
    op.execute("ALTER TABLE note_comments ALTER COLUMN created_at SET DEFAULT now()")
    op.execute("ALTER TABLE note_comments ALTER COLUMN updated_at SET DEFAULT now()")
    _create_index_if_missing("ix_note_comments_note_id", "note_comments", ("note_id",))
    _create_index_if_missing("ix_note_comments_author_user_id", "note_comments", ("author_user_id",))
    _create_index_if_missing("ix_note_comments_parent_comment_id", "note_comments", ("parent_comment_id",))
    _create_index_if_missing("ix_note_comments_is_resolved", "note_comments", ("is_resolved",))
    _create_index_if_missing("ix_note_comments_resolved_by_user_id", "note_comments", ("resolved_by_user_id",))
    _create_index_if_missing("ix_note_comments_created_at", "note_comments", ("created_at",))
    _create_index_if_missing("ix_note_comments_updated_at", "note_comments", ("updated_at",))
    _create_index_if_missing("ix_note_comments_deleted_at", "note_comments", ("deleted_at",))
    _create_index_if_missing("idx_note_comments_note_thread", "note_comments", ("note_id", "parent_comment_id", "created_at"))
    _create_index_if_missing("idx_note_comments_note_resolved", "note_comments", ("note_id", "is_resolved", "updated_at"))
    _create_index_if_missing("idx_note_comments_parent_created", "note_comments", ("parent_comment_id", "created_at"))

    if not _table_exists(bind, "note_comment_mentions"):
        op.create_table(
            "note_comment_mentions",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column("comment_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("mentioned_user_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("mention_token", sa.String(length=255), nullable=False),
            sa.Column("start_offset", sa.Integer(), nullable=False),
            sa.Column("end_offset", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.ForeignKeyConstraint(["comment_id"], ["note_comments.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["mentioned_user_id"], ["users.id"]),
            sa.UniqueConstraint("comment_id", "mentioned_user_id", name="uq_note_comment_mentions_comment_user"),
            sa.CheckConstraint("start_offset >= 0", name="ck_note_comment_mentions_start_non_negative"),
            sa.CheckConstraint("end_offset > start_offset", name="ck_note_comment_mentions_offsets_valid"),
        )
    else:
        _ensure_unique_constraint(
            "note_comment_mentions",
            "uq_note_comment_mentions_comment_user",
            ("comment_id", "mentioned_user_id"),
        )
        _ensure_check_constraint("note_comment_mentions", "ck_note_comment_mentions_start_non_negative", "start_offset >= 0")
        _ensure_check_constraint("note_comment_mentions", "ck_note_comment_mentions_offsets_valid", "end_offset > start_offset")

    op.execute("ALTER TABLE note_comment_mentions ALTER COLUMN created_at SET DEFAULT now()")
    _create_index_if_missing("ix_note_comment_mentions_comment_id", "note_comment_mentions", ("comment_id",))
    _create_index_if_missing("ix_note_comment_mentions_mentioned_user_id", "note_comment_mentions", ("mentioned_user_id",))
    _create_index_if_missing("ix_note_comment_mentions_created_at", "note_comment_mentions", ("created_at",))
    _create_index_if_missing(
        "idx_note_comment_mentions_user_created",
        "note_comment_mentions",
        ("mentioned_user_id", "created_at"),
    )

    if not _table_exists(bind, "note_comment_reactions"):
        op.create_table(
            "note_comment_reactions",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column("comment_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("emoji", note_comment_reaction_type, nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.ForeignKeyConstraint(["comment_id"], ["note_comments.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("comment_id", "user_id", "emoji", name="uq_note_comment_reactions_comment_user_emoji"),
        )
    else:
        _ensure_unique_constraint(
            "note_comment_reactions",
            "uq_note_comment_reactions_comment_user_emoji",
            ("comment_id", "user_id", "emoji"),
        )

    op.execute("ALTER TABLE note_comment_reactions ALTER COLUMN created_at SET DEFAULT now()")
    _create_index_if_missing("ix_note_comment_reactions_comment_id", "note_comment_reactions", ("comment_id",))
    _create_index_if_missing("ix_note_comment_reactions_user_id", "note_comment_reactions", ("user_id",))
    _create_index_if_missing("ix_note_comment_reactions_emoji", "note_comment_reactions", ("emoji",))
    _create_index_if_missing("ix_note_comment_reactions_created_at", "note_comment_reactions", ("created_at",))
    _create_index_if_missing(
        "idx_note_comment_reactions_comment_emoji",
        "note_comment_reactions",
        ("comment_id", "emoji", "created_at"),
    )

    if not _table_exists(bind, "user_notifications"):
        op.create_table(
            "user_notifications",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("note_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("comment_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("notification_type", user_notification_type, nullable=False),
            sa.Column(
                "payload",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'{}'::jsonb"),
            ),
            sa.Column("is_read", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["note_id"], ["notes.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["comment_id"], ["note_comments.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("user_id", "comment_id", "notification_type", name="uq_user_notifications_comment_type"),
        )
    else:
        _ensure_unique_constraint(
            "user_notifications",
            "uq_user_notifications_comment_type",
            ("user_id", "comment_id", "notification_type"),
        )

    op.execute("ALTER TABLE user_notifications ALTER COLUMN payload SET DEFAULT '{}'::jsonb")
    op.execute("ALTER TABLE user_notifications ALTER COLUMN is_read SET DEFAULT false")
    op.execute("ALTER TABLE user_notifications ALTER COLUMN created_at SET DEFAULT now()")
    _create_index_if_missing("ix_user_notifications_user_id", "user_notifications", ("user_id",))
    _create_index_if_missing("ix_user_notifications_actor_user_id", "user_notifications", ("actor_user_id",))
    _create_index_if_missing("ix_user_notifications_workspace_id", "user_notifications", ("workspace_id",))
    _create_index_if_missing("ix_user_notifications_note_id", "user_notifications", ("note_id",))
    _create_index_if_missing("ix_user_notifications_comment_id", "user_notifications", ("comment_id",))
    _create_index_if_missing("ix_user_notifications_notification_type", "user_notifications", ("notification_type",))
    _create_index_if_missing("ix_user_notifications_is_read", "user_notifications", ("is_read",))
    _create_index_if_missing("ix_user_notifications_created_at", "user_notifications", ("created_at",))
    _create_index_if_missing(
        "idx_user_notifications_user_read_created",
        "user_notifications",
        ("user_id", "is_read", "created_at"),
    )
    _create_index_if_missing("idx_user_notifications_note_created", "user_notifications", ("note_id", "created_at"))


def downgrade() -> None:
    op.drop_index("idx_user_notifications_note_created", table_name="user_notifications")
    op.drop_index("idx_user_notifications_user_read_created", table_name="user_notifications")
    op.drop_index("ix_user_notifications_created_at", table_name="user_notifications")
    op.drop_index("ix_user_notifications_is_read", table_name="user_notifications")
    op.drop_index("ix_user_notifications_notification_type", table_name="user_notifications")
    op.drop_index("ix_user_notifications_comment_id", table_name="user_notifications")
    op.drop_index("ix_user_notifications_note_id", table_name="user_notifications")
    op.drop_index("ix_user_notifications_workspace_id", table_name="user_notifications")
    op.drop_index("ix_user_notifications_actor_user_id", table_name="user_notifications")
    op.drop_index("ix_user_notifications_user_id", table_name="user_notifications")
    op.drop_table("user_notifications")

    op.drop_index("idx_note_comment_reactions_comment_emoji", table_name="note_comment_reactions")
    op.drop_index("ix_note_comment_reactions_created_at", table_name="note_comment_reactions")
    op.drop_index("ix_note_comment_reactions_emoji", table_name="note_comment_reactions")
    op.drop_index("ix_note_comment_reactions_user_id", table_name="note_comment_reactions")
    op.drop_index("ix_note_comment_reactions_comment_id", table_name="note_comment_reactions")
    op.drop_table("note_comment_reactions")

    op.drop_index("idx_note_comment_mentions_user_created", table_name="note_comment_mentions")
    op.drop_index("ix_note_comment_mentions_created_at", table_name="note_comment_mentions")
    op.drop_index("ix_note_comment_mentions_mentioned_user_id", table_name="note_comment_mentions")
    op.drop_index("ix_note_comment_mentions_comment_id", table_name="note_comment_mentions")
    op.drop_table("note_comment_mentions")

    op.drop_index("idx_note_comments_parent_created", table_name="note_comments")
    op.drop_index("idx_note_comments_note_resolved", table_name="note_comments")
    op.drop_index("idx_note_comments_note_thread", table_name="note_comments")
    op.drop_index("ix_note_comments_deleted_at", table_name="note_comments")
    op.drop_index("ix_note_comments_updated_at", table_name="note_comments")
    op.drop_index("ix_note_comments_created_at", table_name="note_comments")
    op.drop_index("ix_note_comments_resolved_by_user_id", table_name="note_comments")
    op.drop_index("ix_note_comments_is_resolved", table_name="note_comments")
    op.drop_index("ix_note_comments_parent_comment_id", table_name="note_comments")
    op.drop_index("ix_note_comments_author_user_id", table_name="note_comments")
    op.drop_index("ix_note_comments_note_id", table_name="note_comments")
    op.drop_table("note_comments")

    bind = op.get_bind()
    user_notification_type.drop(bind, checkfirst=True)
    note_comment_reaction_type.drop(bind, checkfirst=True)
