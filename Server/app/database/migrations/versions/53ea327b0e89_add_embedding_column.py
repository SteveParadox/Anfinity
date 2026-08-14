"""add embedding column

Revision ID: 53ea327b0e89
Revises: d1cff45fd5dd
Create Date: 2026-03-05 13:36:23.398292

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '53ea327b0e89'
down_revision = 'd1cff45fd5dd'
branch_labels = None
depends_on = None


def _has_column(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))


def _has_index(inspector: sa.Inspector, table_name: str, index_name: str) -> bool:
    return any(index["name"] == index_name for index in inspector.get_indexes(table_name))


def _has_enum(bind: sa.Connection, enum_name: str) -> bool:
    return bool(
        bind.execute(
            sa.text("SELECT 1 FROM pg_type WHERE typname = :enum_name"),
            {"enum_name": enum_name},
        ).scalar()
    )


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _has_column(inspector, 'audit_logs', 'metadata'):
        op.drop_column('audit_logs', 'metadata')

    if not _has_column(inspector, 'chunks', 'chunk_metadata'):
        op.add_column('chunks', sa.Column('chunk_metadata', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    if _has_column(inspector, 'chunks', 'metadata'):
        op.drop_column('chunks', 'metadata')

    if not _has_index(inspector, 'documents', op.f('ix_documents_status')):
        op.create_index(op.f('ix_documents_status'), 'documents', ['status'], unique=False)

    if not _has_column(inspector, 'notes', 'embedding'):
        op.add_column('notes', sa.Column('embedding', sa.String(length=10000), nullable=True))
    if not _has_column(inspector, 'notes', 'word_count'):
        op.add_column('notes', sa.Column('word_count', sa.Integer(), nullable=True))

    workspacerole_enum = sa.Enum('OWNER', 'ADMIN', 'MEMBER', 'VIEWER', name='workspacerole')
    if not _has_enum(bind, 'workspacerole'):
        workspacerole_enum.create(bind, checkfirst=True)

    role_column = next((column for column in inspector.get_columns('workspace_members') if column['name'] == 'role'), None)
    role_type_name = getattr(role_column.get('type') if role_column else None, 'name', None)
    if role_type_name != 'workspacerole':
        op.alter_column(
            'workspace_members',
            'role',
            existing_type=sa.VARCHAR(length=50),
            type_=workspacerole_enum,
            nullable=False,
            postgresql_using="role::text::workspacerole",
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    workspacerole_enum = sa.Enum('OWNER', 'ADMIN', 'MEMBER', 'VIEWER', name='workspacerole')

    role_column = next((column for column in inspector.get_columns('workspace_members') if column['name'] == 'role'), None)
    role_type_name = getattr(role_column.get('type') if role_column else None, 'name', None)
    if role_type_name == 'workspacerole':
        op.alter_column(
            'workspace_members',
            'role',
            existing_type=workspacerole_enum,
            type_=sa.VARCHAR(length=50),
            nullable=True,
        )

    if _has_column(inspector, 'notes', 'word_count'):
        op.drop_column('notes', 'word_count')
    if _has_column(inspector, 'notes', 'embedding'):
        op.drop_column('notes', 'embedding')

    if _has_index(inspector, 'documents', op.f('ix_documents_status')):
        op.drop_index(op.f('ix_documents_status'), table_name='documents')

    if not _has_column(inspector, 'chunks', 'metadata'):
        op.add_column('chunks', sa.Column('metadata', postgresql.JSONB(astext_type=sa.Text()), autoincrement=False, nullable=True))
    if _has_column(inspector, 'chunks', 'chunk_metadata'):
        op.drop_column('chunks', 'chunk_metadata')

    if not _has_column(inspector, 'audit_logs', 'metadata'):
        op.add_column('audit_logs', sa.Column('metadata', postgresql.JSONB(astext_type=sa.Text()), autoincrement=False, nullable=True))
