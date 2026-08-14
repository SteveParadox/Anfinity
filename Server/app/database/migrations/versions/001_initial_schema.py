"""Initial schema

Revision ID: 001
Revises: 
Create Date: 2025-02-21 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create users table
    op.create_table(
        'users',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('email', sa.String(255), unique=True, nullable=False, index=True),
        sa.Column('hashed_password', sa.String(255), nullable=True),
        sa.Column('full_name', sa.String(255), nullable=True),
        sa.Column('google_id', sa.String(255), unique=True, nullable=True),
        sa.Column('github_id', sa.String(255), unique=True, nullable=True),
        sa.Column('is_active', sa.Integer, default=1),
        sa.Column('is_superuser', sa.Integer, default=0),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), onupdate=sa.text('now()')),
    )
    
    # Create workspaces table
    op.create_table(
        'workspaces',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('description', sa.Text, nullable=True),
        sa.Column('owner_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('settings', postgresql.JSONB, default=dict),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), onupdate=sa.text('now()')),
    )
    
    # Create workspace_members table
    op.create_table(
        'workspace_members',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('workspace_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('workspaces.id'), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('role', sa.String(50), default='member'),
        sa.Column('joined_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
        sa.UniqueConstraint('workspace_id', 'user_id', name='unique_workspace_member'),
    )
    
    # Create documents table
    op.create_table(
        'documents',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('workspace_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('workspaces.id'), nullable=False, index=True),
        sa.Column('title', sa.String(500), nullable=False),
        sa.Column('content_hash', sa.String(64), nullable=True, index=True),
        sa.Column('source_type', sa.Enum('UPLOAD', 'SLACK', 'NOTION', 'GDRIVE', 'GITHUB', 'EMAIL', 'WEB_CLIP', name='sourcetype'), nullable=False),
        sa.Column('source_metadata', postgresql.JSONB, default=dict),
        sa.Column('storage_path', sa.String(500), nullable=True),
        sa.Column('storage_url', sa.String(1000), nullable=True),
        sa.Column('status', sa.Enum('PENDING', 'PROCESSING', 'INDEXED', 'FAILED', 'DELETED', name='documentstatus'), default='PENDING'),
        sa.Column('processed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('token_count', sa.Integer, default=0),
        sa.Column('chunk_count', sa.Integer, default=0),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), onupdate=sa.text('now()')),
    )
    
    # Create chunks table
    op.create_table(
        'chunks',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('document_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('documents.id'), nullable=False, index=True),
        sa.Column('chunk_index', sa.Integer, nullable=False),
        sa.Column('text', sa.Text, nullable=False),
        sa.Column('token_count', sa.Integer, default=0),
        sa.Column('context_before', sa.Text, nullable=True),
        sa.Column('context_after', sa.Text, nullable=True),
        sa.Column('metadata', postgresql.JSONB, default=dict),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
        sa.UniqueConstraint('document_id', 'chunk_index', name='unique_chunk_index'),
    )
    
    # Create embeddings table
    op.create_table(
        'embeddings',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('chunk_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('chunks.id'), nullable=False, unique=True),
        sa.Column('vector_id', sa.String(255), nullable=False, index=True),
        sa.Column('collection_name', sa.String(255), nullable=False),
        sa.Column('model_used', sa.String(255), nullable=False),
        sa.Column('embedding_dimension', sa.Integer, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
    )
    
    # Create ingestion_logs table
    op.create_table(
        'ingestion_logs',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('document_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('documents.id'), nullable=False, index=True),
        sa.Column('status', sa.Enum('PENDING', 'PROCESSING', 'INDEXED', 'FAILED', 'DELETED', name='documentstatus'), nullable=False),
        sa.Column('stage', sa.String(100), nullable=True),
        sa.Column('message', sa.Text, nullable=True),
        sa.Column('error_message', sa.Text, nullable=True),
        sa.Column('duration_ms', sa.Integer, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
    )
    
    # Create connectors table
    op.create_table(
        'connectors',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('workspace_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('workspaces.id'), nullable=False),
        sa.Column('connector_type', sa.String(50), nullable=False),
        sa.Column('access_token', sa.Text, nullable=True),
        sa.Column('refresh_token', sa.Text, nullable=True),
        sa.Column('token_expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('config', postgresql.JSONB, default=dict),
        sa.Column('is_active', sa.Integer, default=1),
        sa.Column('last_sync_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), onupdate=sa.text('now()')),
        sa.UniqueConstraint('workspace_id', 'connector_type', name='unique_workspace_connector'),
    )
    
    # Create notes table
    op.create_table(
        'notes',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('workspace_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('workspaces.id'), nullable=True),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('title', sa.String(500), nullable=False),
        sa.Column('content', sa.Text, nullable=False),
        sa.Column('summary', sa.Text, nullable=True),
        sa.Column('ai_generated', sa.Integer, default=0),
        sa.Column('confidence_score', sa.Float, nullable=True),
        sa.Column('tags', postgresql.JSONB, default=list),
        sa.Column('connections', postgresql.JSONB, default=list),
        sa.Column('source_url', sa.String(1000), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), onupdate=sa.text('now()')),
    )
    
    # Create queries table
    op.create_table(
        'queries',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('workspace_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('workspaces.id'), nullable=False, index=True),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('query_text', sa.Text, nullable=False),
        sa.Column('response_time_ms', sa.Integer, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
    )
    
    # Create answers table
    op.create_table(
        'answers',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('query_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('queries.id'), nullable=False, index=True),
        sa.Column('workspace_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('workspaces.id'), nullable=False, index=True),
        sa.Column('answer_text', sa.Text, nullable=False),
        sa.Column('confidence_score', sa.Float, nullable=False),
        sa.Column('sources', postgresql.JSONB, default=list),
        sa.Column('model_used', sa.String(100), nullable=True),
        sa.Column('tokens_used', sa.Integer, nullable=True),
        sa.Column('verification_status', sa.String(20), default='pending'),
        sa.Column('verified_by', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('verified_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('verification_comment', sa.Text, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
    )
    
    # Create verifications table
    op.create_table(
        'verifications',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('answer_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('answers.id'), nullable=False, index=True),
        sa.Column('workspace_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('workspaces.id'), nullable=False, index=True),
        sa.Column('verified_by', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('comment', sa.Text, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
    )
    
    # Create feedback table
    op.create_table(
        'feedback',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('answer_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('answers.id'), nullable=False, index=True),
        sa.Column('workspace_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('workspaces.id'), nullable=False, index=True),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('rating', sa.Integer, nullable=False),
        sa.Column('comment', sa.Text, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
    )
    
    # Create audit_logs table
    op.create_table(
        'audit_logs',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id'), nullable=True, index=True),
        sa.Column('workspace_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('workspaces.id'), nullable=True, index=True),
        sa.Column('action', sa.String(50), nullable=False, index=True),
        sa.Column('entity_type', sa.String(50), nullable=True, index=True),
        sa.Column('entity_id', postgresql.UUID(as_uuid=True), nullable=True, index=True),
        sa.Column('metadata', postgresql.JSONB, default=dict),
        sa.Column('ip_address', sa.String(45), nullable=True),
        sa.Column('user_agent', sa.String(500), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), index=True),
    )
    
    # Create indexes
    op.create_index('idx_document_workspace_status', 'documents', ['workspace_id', 'status'])
    op.create_index('idx_chunk_document', 'chunks', ['document_id', 'chunk_index'])
    op.create_index('idx_ingestion_log_document', 'ingestion_logs', ['document_id', 'created_at'])
    op.create_index('idx_note_workspace', 'notes', ['workspace_id', 'created_at'])
    op.create_index('idx_note_user', 'notes', ['user_id', 'updated_at'])
    op.create_index('idx_feedback_answer', 'feedback', ['answer_id', 'created_at'])
    op.create_index('idx_audit_workspace_action', 'audit_logs', ['workspace_id', 'action', 'created_at'])
    op.create_index('idx_audit_user_action', 'audit_logs', ['user_id', 'action', 'created_at'])
    op.create_index('idx_audit_entity', 'audit_logs', ['entity_type', 'entity_id'])


def downgrade() -> None:
    op.drop_table('audit_logs')
    op.drop_table('feedback')
    op.drop_table('verifications')
    op.drop_table('answers')
    op.drop_table('queries')
    op.drop_table('notes')
    op.drop_table('connectors')
    op.drop_table('ingestion_logs')
    op.drop_table('embeddings')
    op.drop_table('chunks')
    op.drop_table('documents')
    op.drop_table('workspace_members')
    op.drop_table('workspaces')
    op.drop_table('users')
    op.execute('DROP TYPE IF EXISTS documentstatus')
    op.execute('DROP TYPE IF EXISTS sourcetype')
