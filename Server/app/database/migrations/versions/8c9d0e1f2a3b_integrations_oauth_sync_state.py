"""Add shared integrations OAuth and sync state.

Revision ID: 8c9d0e1f2a3b
Revises: 7b8c9d0e1f2a
Create Date: 2026-04-23 14:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "8c9d0e1f2a3b"
down_revision = "7b8c9d0e1f2a"
branch_labels = None
depends_on = None


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


def _index_exists(bind: sa.Connection, index_name: str) -> bool:
    return bool(
        bind.execute(
            sa.text("SELECT to_regclass(:index_name) IS NOT NULL"),
            {"index_name": f"public.{index_name}"},
        ).scalar()
    )


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    bind = op.get_bind()
    if not _column_exists(bind, table_name, column.name):
        op.add_column(table_name, column)


def _create_index_if_missing(index_name: str, table_name: str, columns: tuple[str, ...], *, unique: bool = False) -> None:
    bind = op.get_bind()
    if not _index_exists(bind, index_name):
        op.create_index(index_name, table_name, list(columns), unique=unique)


def upgrade() -> None:
    bind = op.get_bind()

    _add_column_if_missing("connectors", sa.Column("scopes", postgresql.JSONB(astext_type=sa.Text()), nullable=True, server_default=sa.text("'[]'::jsonb")))
    _add_column_if_missing("connectors", sa.Column("external_account_id", sa.String(length=255), nullable=True))
    _add_column_if_missing("connectors", sa.Column("external_account_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True, server_default=sa.text("'{}'::jsonb")))
    _add_column_if_missing("connectors", sa.Column("sync_status", sa.String(length=50), nullable=False, server_default="idle"))
    _add_column_if_missing("connectors", sa.Column("last_sync_started_at", sa.DateTime(timezone=True), nullable=True))
    _add_column_if_missing("connectors", sa.Column("last_sync_completed_at", sa.DateTime(timezone=True), nullable=True))
    _add_column_if_missing("connectors", sa.Column("last_sync_error", sa.Text(), nullable=True))
    _add_column_if_missing("connectors", sa.Column("sync_cursor", postgresql.JSONB(astext_type=sa.Text()), nullable=True, server_default=sa.text("'{}'::jsonb")))

    _create_index_if_missing("ix_connectors_external_account_id", "connectors", ("external_account_id",))
    _create_index_if_missing("ix_connectors_sync_status", "connectors", ("sync_status",))

    if not _table_exists(bind, "integration_sync_items"):
        op.create_table(
            "integration_sync_items",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column("connector_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("provider", sa.String(length=50), nullable=False),
            sa.Column("external_type", sa.String(length=50), nullable=False),
            sa.Column("external_id", sa.String(length=512), nullable=False),
            sa.Column("external_updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("local_note_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("local_document_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("source_hash", sa.String(length=64), nullable=True),
            sa.Column("sync_direction", sa.String(length=20), nullable=False, server_default="pull"),
            sa.Column("sync_status", sa.String(length=50), nullable=False, server_default="synced"),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True, server_default=sa.text("'{}'::jsonb")),
            sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.ForeignKeyConstraint(["connector_id"], ["connectors.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["local_note_id"], ["notes.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["local_document_id"], ["documents.id"], ondelete="SET NULL"),
            sa.UniqueConstraint("connector_id", "external_type", "external_id", name="uq_integration_sync_item_external"),
        )

    _create_index_if_missing("ix_integration_sync_items_connector_id", "integration_sync_items", ("connector_id",))
    _create_index_if_missing("ix_integration_sync_items_workspace_id", "integration_sync_items", ("workspace_id",))
    _create_index_if_missing("ix_integration_sync_items_provider", "integration_sync_items", ("provider",))
    _create_index_if_missing("ix_integration_sync_items_external_type", "integration_sync_items", ("external_type",))
    _create_index_if_missing("ix_integration_sync_items_local_note_id", "integration_sync_items", ("local_note_id",))
    _create_index_if_missing("ix_integration_sync_items_local_document_id", "integration_sync_items", ("local_document_id",))
    _create_index_if_missing("ix_integration_sync_items_source_hash", "integration_sync_items", ("source_hash",))
    _create_index_if_missing("ix_integration_sync_items_sync_status", "integration_sync_items", ("sync_status",))
    _create_index_if_missing("ix_integration_sync_items_first_seen_at", "integration_sync_items", ("first_seen_at",))
    _create_index_if_missing("ix_integration_sync_items_last_seen_at", "integration_sync_items", ("last_seen_at",))
    _create_index_if_missing("ix_integration_sync_items_last_synced_at", "integration_sync_items", ("last_synced_at",))
    _create_index_if_missing("idx_integration_sync_items_provider_seen", "integration_sync_items", ("provider", "last_seen_at"))
    _create_index_if_missing("idx_integration_sync_items_workspace_provider", "integration_sync_items", ("workspace_id", "provider", "external_type"))
    _create_index_if_missing("idx_integration_sync_items_note_provider", "integration_sync_items", ("local_note_id", "provider"))


def downgrade() -> None:
    bind = op.get_bind()
    if _table_exists(bind, "integration_sync_items"):
        op.drop_table("integration_sync_items")

    for index_name in ("ix_connectors_sync_status", "ix_connectors_external_account_id"):
        if _index_exists(bind, index_name):
            op.drop_index(index_name, table_name="connectors")

    for column_name in (
        "sync_cursor",
        "last_sync_error",
        "last_sync_completed_at",
        "last_sync_started_at",
        "sync_status",
        "external_account_metadata",
        "external_account_id",
        "scopes",
    ):
        if _column_exists(bind, "connectors", column_name):
            op.drop_column("connectors", column_name)
