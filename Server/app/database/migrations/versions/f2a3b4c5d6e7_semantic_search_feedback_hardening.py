"""semantic search feedback hardening

Revision ID: f2a3b4c5d6e7
Revises: 206aa02452b6
Create Date: 2026-04-28 11:10:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "f2a3b4c5d6e7"
down_revision = "206aa02452b6"
branch_labels = None
depends_on = None


def _inspector():
    return sa.inspect(op.get_bind())


def _has_table(table_name: str) -> bool:
    return table_name in _inspector().get_table_names()


def _has_column(table_name: str, column_name: str) -> bool:
    if not _has_table(table_name):
        return False
    return column_name in {column["name"] for column in _inspector().get_columns(table_name)}


def _has_index(table_name: str, index_name: str) -> bool:
    if not _has_table(table_name):
        return False
    return index_name in {index["name"] for index in _inspector().get_indexes(table_name)}


def _has_unique_constraint(table_name: str, constraint_name: str) -> bool:
    if not _has_table(table_name):
        return False
    return constraint_name in {
        constraint["name"]
        for constraint in _inspector().get_unique_constraints(table_name)
        if constraint.get("name")
    }


def _create_feedback_indexes() -> None:
    indexes = [
        ("idx_search_feedback_workspace_created", ["workspace_id", "created_at"]),
        ("idx_search_feedback_workspace_type", ["workspace_id", "feedback_type", "created_at"]),
        ("idx_search_feedback_workspace_reason", ["workspace_id", "reason_code", "created_at"]),
        ("idx_search_feedback_workspace_result", ["workspace_id", "target_result_id", "created_at"]),
        ("ix_search_feedback_search_log_id", ["search_log_id"]),
        ("ix_search_feedback_query_id", ["query_id"]),
        ("ix_search_feedback_answer_id", ["answer_id"]),
        ("ix_search_feedback_user_id", ["user_id"]),
        ("ix_search_feedback_workspace_id", ["workspace_id"]),
        ("ix_search_feedback_target_result_id", ["target_result_id"]),
        ("ix_search_feedback_feedback_type", ["feedback_type"]),
        ("ix_search_feedback_reason_code", ["reason_code"]),
        ("ix_search_feedback_created_at", ["created_at"]),
        ("ix_search_feedback_updated_at", ["updated_at"]),
    ]
    for name, columns in indexes:
        if not _has_index("search_feedback", name):
            op.create_index(name, "search_feedback", columns, unique=False)


def upgrade() -> None:
    if not _has_column("search_logs", "result_snapshot"):
        op.add_column(
            "search_logs",
            sa.Column("result_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        )
    if not _has_column("search_logs", "retrieval_metadata"):
        op.add_column(
            "search_logs",
            sa.Column("retrieval_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        )

    if not _has_table("search_feedback"):
        op.create_table(
            "search_feedback",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("search_log_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("query_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("answer_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("context_key", sa.String(length=255), nullable=False),
            sa.Column("scope_key", sa.String(length=255), nullable=False),
            sa.Column("target_kind", sa.String(length=20), nullable=False),
            sa.Column("target_result_id", sa.String(length=255), nullable=True),
            sa.Column("feedback_type", sa.String(length=64), nullable=False),
            sa.Column("rating_value", sa.Integer(), nullable=True),
            sa.Column("reason_code", sa.String(length=64), nullable=True),
            sa.Column("comment", sa.Text(), nullable=True),
            sa.Column("query_text", sa.Text(), nullable=False),
            sa.Column("query_embedding_provider", sa.String(length=64), nullable=True),
            sa.Column("query_embedding_model", sa.String(length=128), nullable=True),
            sa.Column("result_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("result_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("answer_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("retrieval_diagnostics", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
            sa.ForeignKeyConstraint(["answer_id"], ["answers.id"]),
            sa.ForeignKeyConstraint(["query_id"], ["queries.id"]),
            sa.ForeignKeyConstraint(["search_log_id"], ["search_logs.id"]),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "workspace_id",
                "user_id",
                "context_key",
                "scope_key",
                name="uq_search_feedback_user_context_scope",
            ),
        )
    else:
        missing_columns = [
            ("search_log_id", postgresql.UUID(as_uuid=True)),
            ("query_id", postgresql.UUID(as_uuid=True)),
            ("answer_id", postgresql.UUID(as_uuid=True)),
            ("context_key", sa.String(length=255)),
            ("scope_key", sa.String(length=255)),
            ("target_kind", sa.String(length=20)),
            ("target_result_id", sa.String(length=255)),
            ("feedback_type", sa.String(length=64)),
            ("rating_value", sa.Integer()),
            ("reason_code", sa.String(length=64)),
            ("comment", sa.Text()),
            ("query_text", sa.Text()),
            ("query_embedding_provider", sa.String(length=64)),
            ("query_embedding_model", sa.String(length=128)),
            ("result_ids", postgresql.JSONB(astext_type=sa.Text())),
            ("result_snapshot", postgresql.JSONB(astext_type=sa.Text())),
            ("answer_snapshot", postgresql.JSONB(astext_type=sa.Text())),
            ("retrieval_diagnostics", postgresql.JSONB(astext_type=sa.Text())),
            ("metadata_json", postgresql.JSONB(astext_type=sa.Text())),
            ("created_at", sa.DateTime(timezone=True)),
            ("updated_at", sa.DateTime(timezone=True)),
        ]
        for name, column_type in missing_columns:
            if not _has_column("search_feedback", name):
                nullable = name not in {"context_key", "scope_key", "target_kind", "feedback_type", "query_text"}
                op.add_column("search_feedback", sa.Column(name, column_type, nullable=nullable))

        if not _has_unique_constraint("search_feedback", "uq_search_feedback_user_context_scope"):
            op.create_unique_constraint(
                "uq_search_feedback_user_context_scope",
                "search_feedback",
                ["workspace_id", "user_id", "context_key", "scope_key"],
            )

    _create_feedback_indexes()

    if _has_column("search_logs", "result_snapshot"):
        op.execute("UPDATE search_logs SET result_snapshot = '[]'::jsonb WHERE result_snapshot IS NULL")
        op.alter_column("search_logs", "result_snapshot", nullable=False)
    if _has_column("search_logs", "retrieval_metadata"):
        op.execute("UPDATE search_logs SET retrieval_metadata = '{}'::jsonb WHERE retrieval_metadata IS NULL")
        op.alter_column("search_logs", "retrieval_metadata", nullable=False)


def downgrade() -> None:
    if _has_table("search_feedback"):
        index_names = [
            "ix_search_feedback_updated_at",
            "ix_search_feedback_created_at",
            "ix_search_feedback_reason_code",
            "ix_search_feedback_feedback_type",
            "ix_search_feedback_target_result_id",
            "ix_search_feedback_workspace_id",
            "ix_search_feedback_user_id",
            "ix_search_feedback_answer_id",
            "ix_search_feedback_query_id",
            "ix_search_feedback_search_log_id",
            "idx_search_feedback_workspace_result",
            "idx_search_feedback_workspace_reason",
            "idx_search_feedback_workspace_type",
            "idx_search_feedback_workspace_created",
        ]
        for name in index_names:
            if _has_index("search_feedback", name):
                op.drop_index(name, table_name="search_feedback")
        op.drop_table("search_feedback")

    if _has_column("search_logs", "retrieval_metadata"):
        op.drop_column("search_logs", "retrieval_metadata")
    if _has_column("search_logs", "result_snapshot"):
        op.drop_column("search_logs", "result_snapshot")
