"""Align graph enum labels with ORM enum values.

Revision ID: c1d2e3f4a5b6
Revises: 206aa02452b6
Create Date: 2026-04-16
"""

from alembic import op
import sqlalchemy as sa


revision = "c1d2e3f4a5b6"
down_revision = "206aa02452b6"
branch_labels = None
depends_on = None


def _enum_value_exists(bind: sa.Connection, type_name: str, value: str) -> bool:
    return bool(
        bind.execute(
            sa.text(
                """
                SELECT 1
                FROM pg_type t
                JOIN pg_enum e ON e.enumtypid = t.oid
                WHERE t.typname = :type_name
                  AND e.enumlabel = :value
                LIMIT 1
                """
            ),
            {"type_name": type_name, "value": value},
        ).scalar()
    )


def _rename_or_add_enum_value(bind: sa.Connection, type_name: str, old_value: str, new_value: str) -> None:
    has_old = _enum_value_exists(bind, type_name, old_value)
    has_new = _enum_value_exists(bind, type_name, new_value)

    if has_old and not has_new:
        op.execute(f"ALTER TYPE {type_name} RENAME VALUE '{old_value}' TO '{new_value}'")
        return

    if not has_new:
        op.execute(f"ALTER TYPE {type_name} ADD VALUE '{new_value}'")


def _normalize_table_enum_rows(table_name: str, column_name: str, old_value: str, new_value: str, type_name: str) -> None:
    op.execute(
        f"""
        UPDATE {table_name}
        SET {column_name} = CAST('{new_value}' AS {type_name})
        WHERE {column_name}::text = '{old_value}'
        """
    )


def upgrade() -> None:
    bind = op.get_bind()

    with op.get_context().autocommit_block():
        for old_value, new_value in (
            ("WORKSPACE", "workspace"),
            ("NOTE", "note"),
            ("ENTITY", "entity"),
            ("TAG", "tag"),
        ):
            _rename_or_add_enum_value(bind, "graphnodetype", old_value, new_value)

        for old_value, new_value in (
            ("WORKSPACE_CONTAINS_NOTE", "workspace_contains_note"),
            ("NOTE_MENTIONS_ENTITY", "note_mentions_entity"),
            ("NOTE_HAS_TAG", "note_has_tag"),
            ("NOTE_LINKS_NOTE", "note_links_note"),
            ("NOTE_RELATED_NOTE", "note_related_note"),
            ("ENTITY_CO_OCCURS_WITH_ENTITY", "entity_co_occurs_with_entity"),
            ("TAG_CO_OCCURS_WITH_TAG", "tag_co_occurs_with_tag"),
        ):
            _rename_or_add_enum_value(bind, "graphedgetype", old_value, new_value)

    for old_value, new_value in (
        ("WORKSPACE", "workspace"),
        ("NOTE", "note"),
        ("ENTITY", "entity"),
        ("TAG", "tag"),
    ):
        _normalize_table_enum_rows("graph_nodes", "node_type", old_value, new_value, "graphnodetype")

    for old_value, new_value in (
        ("WORKSPACE_CONTAINS_NOTE", "workspace_contains_note"),
        ("NOTE_MENTIONS_ENTITY", "note_mentions_entity"),
        ("NOTE_HAS_TAG", "note_has_tag"),
        ("NOTE_LINKS_NOTE", "note_links_note"),
        ("NOTE_RELATED_NOTE", "note_related_note"),
        ("ENTITY_CO_OCCURS_WITH_ENTITY", "entity_co_occurs_with_entity"),
        ("TAG_CO_OCCURS_WITH_TAG", "tag_co_occurs_with_tag"),
    ):
        _normalize_table_enum_rows("graph_edges", "edge_type", old_value, new_value, "graphedgetype")


def downgrade() -> None:
    # Enum value removal is intentionally omitted because PostgreSQL does not
    # support dropping enum labels safely in a portable way.
    pass
