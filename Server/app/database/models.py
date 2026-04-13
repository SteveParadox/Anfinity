"""SQLAlchemy models for PostgreSQL."""
from datetime import datetime
from typing import List, Optional
from enum import Enum as PyEnum
import uuid

from sqlalchemy import (
    Column, String, DateTime, ForeignKey, Integer, 
    JSON, Text, Enum, Float, Index, UniqueConstraint
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship, declarative_base
from sqlalchemy.sql import func

Base = declarative_base()


class DocumentStatus(str, PyEnum):
    """Document processing status."""
    PENDING = "pending"
    PROCESSING = "processing"
    INDEXED = "indexed"
    FAILED = "failed"
    DELETED = "deleted"


class SourceType(str, PyEnum):
    """Document source types."""
    UPLOAD = "upload"
    SLACK = "slack"
    NOTION = "notion"
    GDRIVE = "gdrive"
    GITHUB = "github"
    EMAIL = "email"
    WEB_CLIP = "web_clip"


class ChunkStatus(str, PyEnum):
    """Chunk embedding processing status."""
    PENDING = "pending"      # Chunk created, waiting for embedding
    EMBEDDED = "embedded"    # Embedding successfully created
    FAILED = "failed"        # Embedding generation failed


class WorkspaceRole(str, PyEnum):
    """Workspace member roles."""
    OWNER = "owner"      # Full control over workspace
    ADMIN = "admin"      # Can manage members and settings
    MEMBER = "member"    # Can create and edit documents
    VIEWER = "viewer"    # Read-only access


class GraphNodeType(str, PyEnum):
    """Knowledge graph node types."""

    WORKSPACE = "workspace"
    NOTE = "note"
    ENTITY = "entity"
    TAG = "tag"


class GraphEdgeType(str, PyEnum):
    """Knowledge graph edge types."""

    WORKSPACE_CONTAINS_NOTE = "workspace_contains_note"
    NOTE_MENTIONS_ENTITY = "note_mentions_entity"
    NOTE_HAS_TAG = "note_has_tag"
    NOTE_LINKS_NOTE = "note_links_note"
    NOTE_RELATED_NOTE = "note_related_note"
    ENTITY_CO_OCCURS_WITH_ENTITY = "entity_co_occurs_with_entity"
    TAG_CO_OCCURS_WITH_TAG = "tag_co_occurs_with_tag"


class Workspace(Base):
    """Workspace model for multi-tenancy."""
    __tablename__ = "workspaces"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    owner_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    
    # Settings
    settings = Column(JSONB, default=dict)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Relationships
    owner = relationship("User", back_populates="owned_workspaces")
    members = relationship("WorkspaceMember", back_populates="workspace")
    documents = relationship("Document", back_populates="workspace")


class User(Base):
    """User model."""
    __tablename__ = "users"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String(255), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=True)
    full_name = Column(String(255), nullable=True)
    
    # OAuth providers
    google_id = Column(String(255), unique=True, nullable=True)
    github_id = Column(String(255), unique=True, nullable=True)
    
    # Settings
    is_active = Column(Integer, default=1)
    is_superuser = Column(Integer, default=0)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Relationships
    owned_workspaces = relationship("Workspace", back_populates="owner")
    workspace_memberships = relationship("WorkspaceMember", back_populates="user")
    connectors = relationship("Connector", back_populates="user")


class WorkspaceMember(Base):
    """Workspace membership model."""
    __tablename__ = "workspace_members"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    role = Column(Enum(WorkspaceRole), default=WorkspaceRole.MEMBER, nullable=False)
    
    # Timestamps
    joined_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    workspace = relationship("Workspace", back_populates="members")
    user = relationship("User", back_populates="workspace_memberships")
    
    __table_args__ = (
        UniqueConstraint('workspace_id', 'user_id', name='unique_workspace_member'),
    )


class Document(Base):
    """Document model for storing metadata."""
    __tablename__ = "documents"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False, index=True)
    
    # Content
    title = Column(String(500), nullable=False)
    content_hash = Column(String(64), nullable=True, index=True)  # For deduplication
    
    # Source
    source_type = Column(Enum(SourceType), nullable=False, default=SourceType.UPLOAD)
    source_metadata = Column(JSONB, default=dict)  # Original source info
    
    # Storage
    storage_path = Column(String(500), nullable=True)  # S3 path
    storage_url = Column(String(1000), nullable=True)  # Presigned URL
    
    # Status
    status = Column(Enum(DocumentStatus), default=DocumentStatus.PENDING, index=True)
    
    # Processing
    processed_at = Column(DateTime(timezone=True), nullable=True)
    token_count = Column(Integer, default=0)
    chunk_count = Column(Integer, default=0)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Relationships
    workspace = relationship("Workspace", back_populates="documents")
    chunks = relationship("Chunk", back_populates="document", cascade="all, delete-orphan")
    ingestion_logs = relationship("IngestionLog", back_populates="document", cascade="all, delete-orphan")
    
    # Indexes
    __table_args__ = (
        Index('idx_document_workspace_status', 'workspace_id', 'status'),
    )


class Chunk(Base):
    """Text chunk model."""
    __tablename__ = "chunks"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = Column(UUID(as_uuid=True), ForeignKey("documents.id"), nullable=False, index=True)
    
    # Content
    chunk_index = Column(Integer, nullable=False)
    text = Column(Text, nullable=False)
    token_count = Column(Integer, default=0)
    
    # Context
    context_before = Column(Text, nullable=True)  # Previous chunk snippet
    context_after = Column(Text, nullable=True)   # Next chunk snippet
    
    # Metadata
    chunk_metadata = Column(JSONB, default=dict)  # Page num, section, etc.
    
    # Embedding Status (for idempotency on retry)
    chunk_status = Column(Enum(ChunkStatus), default=ChunkStatus.PENDING, nullable=False, index=True)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    # Relationships
    document = relationship("Document", back_populates="chunks")
    embedding = relationship(
        "Embedding",
        back_populates="chunk",
        uselist=False,
        cascade="all, delete-orphan",
        single_parent=True,
    )
    
    __table_args__ = (
        UniqueConstraint('document_id', 'chunk_index', name='unique_chunk_index'),
        Index('idx_chunk_document', 'document_id', 'chunk_index'),
        Index('idx_chunk_status', 'chunk_status'),
    )


class Embedding(Base):
    """Embedding metadata model."""
    __tablename__ = "embeddings"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    chunk_id = Column(UUID(as_uuid=True), ForeignKey("chunks.id", ondelete="CASCADE"), nullable=False, unique=True)
    
    # Vector DB reference
    vector_id = Column(String(255), nullable=False, index=True)  # Qdrant point ID
    collection_name = Column(String(255), nullable=False)
    
    # Model info
    model_used = Column(String(255), nullable=False)
    embedding_dimension = Column(Integer, nullable=False)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    chunk = relationship("Chunk", back_populates="embedding")


class IngestionLog(Base):
    """Ingestion audit log."""
    __tablename__ = "ingestion_logs"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = Column(UUID(as_uuid=True), ForeignKey("documents.id"), nullable=False, index=True)
    
    # Log details
    status = Column(Enum(DocumentStatus), nullable=False)
    stage = Column(String(100), nullable=True)  # parsing, chunking, embedding, indexing
    message = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    
    # Performance metrics
    duration_ms = Column(Integer, nullable=True)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    document = relationship("Document", back_populates="ingestion_logs")
    
    # Indexes
    __table_args__ = (
        Index('idx_ingestion_log_document', 'document_id', 'created_at'),
    )


class Connector(Base):
    """External connector configuration."""
    __tablename__ = "connectors"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    workspace_id = Column(UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False)
    
    # Connector type
    connector_type = Column(String(50), nullable=False)  # slack, notion, gdrive, github
    
    # OAuth tokens (encrypted)
    access_token = Column(Text, nullable=True)
    refresh_token = Column(Text, nullable=True)
    token_expires_at = Column(DateTime(timezone=True), nullable=True)
    
    # Configuration
    config = Column(JSONB, default=dict)  # Channel IDs, folders, etc.
    
    # Status
    is_active = Column(Integer, default=1)
    last_sync_at = Column(DateTime(timezone=True), nullable=True)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Relationships
    user = relationship("User", back_populates="connectors")
    
    __table_args__ = (
        UniqueConstraint('workspace_id', 'connector_type', name='unique_workspace_connector'),
    )


class Query(Base):
    """User query history."""
    __tablename__ = "queries"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    
    # Query content
    query_text = Column(Text, nullable=False)
    
    # Performance metrics
    response_time_ms = Column(Integer, nullable=True)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    answers = relationship("Answer", back_populates="query", cascade="all, delete-orphan")


class Answer(Base):
    """AI-generated answer to a query."""
    __tablename__ = "answers"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    query_id = Column(UUID(as_uuid=True), ForeignKey("queries.id"), nullable=False, index=True)
    workspace_id = Column(UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False, index=True)
    
    # Answer content
    answer_text = Column(Text, nullable=False)
    confidence_score = Column(Float, nullable=False)
    
    # Sources
    sources = Column(JSONB, default=list)  # List of {chunk_id, document_id, similarity}
    
    # LLM info
    model_used = Column(String(100), nullable=True)
    tokens_used = Column(Integer, nullable=True)
    
    # Verification status
    verification_status = Column(String(20), default="pending")  # pending, verified, rejected
    verified_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    verified_at = Column(DateTime(timezone=True), nullable=True)
    verification_comment = Column(Text, nullable=True)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    query = relationship("Query", back_populates="answers")
    feedback = relationship("Feedback", back_populates="answer", cascade="all, delete-orphan")


class Verification(Base):
    """Answer verification record."""
    __tablename__ = "verifications"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    answer_id = Column(UUID(as_uuid=True), ForeignKey("answers.id"), nullable=False, index=True)
    workspace_id = Column(UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False, index=True)
    verified_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    
    # Verification details
    status = Column(String(20), nullable=False)  # approved, rejected
    comment = Column(Text, nullable=True)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Feedback(Base):
    """User feedback on answers."""
    __tablename__ = "feedback"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    answer_id = Column(UUID(as_uuid=True), ForeignKey("answers.id"), nullable=False, index=True)
    workspace_id = Column(UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    
    # Feedback content
    rating = Column(Integer, nullable=False)  # 1-5
    comment = Column(Text, nullable=True)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    answer = relationship("Answer", back_populates="feedback")
    
    __table_args__ = (
        Index('idx_feedback_answer', 'answer_id', 'created_at'),
    )


class AuditLog(Base):
    """Comprehensive audit log for compliance."""
    __tablename__ = "audit_logs"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    
    # Who
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True, index=True)
    
    # Where
    workspace_id = Column(UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=True, index=True)
    
    # What
    action = Column(String(50), nullable=False, index=True)
    entity_type = Column(String(50), nullable=True, index=True)
    entity_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    
    # Context
    audit_metadata = Column(JSONB, default=dict)
    
    # Client info
    ip_address = Column(String(45), nullable=True)
    user_agent = Column(String(500), nullable=True)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    
    # Indexes
    __table_args__ = (
        Index('idx_audit_workspace_action', 'workspace_id', 'action', 'created_at'),
        Index('idx_audit_user_action', 'user_id', 'action', 'created_at'),
        Index('idx_audit_entity', 'entity_type', 'entity_id'),
    )


class Note(Base):
    """User notes model (for frontend integration)."""
    __tablename__ = "notes"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    
    # Content
    title = Column(String(500), nullable=False)
    content = Column(Text, nullable=False)
    summary = Column(Text, nullable=True)
    
    # Note type (note, web-clip, document, voice, ai-generated)
    note_type = Column(String(50), default='note', nullable=False)
    
    # AI features
    ai_generated = Column(Integer, default=0)
    confidence_score = Column(Float, nullable=True)
    
    # Embedding for semantic similarity (pgvector)
    # From 'embed_note' function or during note creation
    # Stores 1536D OpenAI embeddings as JSON (~20-25KB when serialized)
    embedding = Column(Text, nullable=True)  # Changed from String(10000) to Text for larger embeddings
    
    # Content metrics
    word_count = Column(Integer, default=0)
    
    # Tags (stored as JSON array)
    tags = Column(JSONB, default=list)
    
    # Connections to other notes
    connections = Column(JSONB, default=list)  # Array of note IDs
    
    # Source
    source_url = Column(String(1000), nullable=True)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Indexes
    __table_args__ = (
        Index('idx_note_workspace', 'workspace_id', 'created_at'),
        Index('idx_note_user', 'user_id', 'updated_at'),
        Index('idx_note_type', 'note_type'),
    )


class NoteConnectionSuggestion(Base):
    """Suggested note-to-note connection derived from similarity."""

    __tablename__ = "note_connection_suggestions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    source_note_id = Column(UUID(as_uuid=True), ForeignKey("notes.id", ondelete="CASCADE"), nullable=False, index=True)
    suggested_note_id = Column(UUID(as_uuid=True), ForeignKey("notes.id", ondelete="CASCADE"), nullable=False, index=True)

    similarity_score = Column(Float, nullable=False)
    reason = Column(Text, nullable=False)
    status = Column(String(20), nullable=False, default="pending", index=True)  # pending | confirmed | dismissed
    suggestion_metadata = Column("metadata", JSONB, default=dict)

    responded_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    responded_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), index=True)

    source_note = relationship("Note", foreign_keys=[source_note_id], lazy="joined")
    suggested_note = relationship("Note", foreign_keys=[suggested_note_id], lazy="joined")
    responder = relationship("User", foreign_keys=[responded_by], lazy="joined")

    __table_args__ = (
        UniqueConstraint("workspace_id", "source_note_id", "suggested_note_id", name="uq_note_connection_suggestion_pair"),
        Index("idx_note_connection_suggestions_source_status", "source_note_id", "status", "updated_at"),
        Index("idx_note_connection_suggestions_workspace_status", "workspace_id", "status", "created_at"),
    )


class GraphNode(Base):
    """Persisted knowledge-graph node."""

    __tablename__ = "graph_nodes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    node_type = Column(Enum(GraphNodeType), nullable=False, index=True)
    external_id = Column(String(255), nullable=False)
    label = Column(String(500), nullable=False)
    normalized_label = Column(String(500), nullable=False, index=True)
    weight = Column(Float, nullable=False, default=1.0)
    node_metadata = Column("metadata", JSONB, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), index=True)

    __table_args__ = (
        UniqueConstraint("workspace_id", "node_type", "external_id", name="uq_graph_node_workspace_type_external"),
        Index("idx_graph_node_workspace_type", "workspace_id", "node_type"),
        Index("idx_graph_node_workspace_label", "workspace_id", "normalized_label"),
    )


class GraphEdge(Base):
    """Persisted knowledge-graph edge."""

    __tablename__ = "graph_edges"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    edge_type = Column(Enum(GraphEdgeType), nullable=False, index=True)
    source_node_id = Column(UUID(as_uuid=True), ForeignKey("graph_nodes.id", ondelete="CASCADE"), nullable=False, index=True)
    target_node_id = Column(UUID(as_uuid=True), ForeignKey("graph_nodes.id", ondelete="CASCADE"), nullable=False, index=True)
    weight = Column(Float, nullable=False, default=1.0)
    edge_metadata = Column("metadata", JSONB, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), index=True)

    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "edge_type",
            "source_node_id",
            "target_node_id",
            name="uq_graph_edge_workspace_type_pair",
        ),
        Index("idx_graph_edge_workspace_type", "workspace_id", "edge_type"),
        Index("idx_graph_edge_workspace_source", "workspace_id", "source_node_id"),
        Index("idx_graph_edge_workspace_target", "workspace_id", "target_node_id"),
    )


class GraphCluster(Base):
    """Persisted semantic graph cluster."""

    __tablename__ = "graph_clusters"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    cluster_key = Column(String(120), nullable=False)
    label = Column(String(120), nullable=False)
    description = Column(Text, nullable=False)
    importance = Column(Float, nullable=False, default=1.0)
    cluster_metadata = Column("metadata", JSONB, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), index=True)

    memberships = relationship("GraphClusterMembership", back_populates="cluster", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("workspace_id", "cluster_key", name="uq_graph_cluster_workspace_key"),
        Index("idx_graph_cluster_workspace_updated", "workspace_id", "updated_at"),
    )


class GraphClusterMembership(Base):
    """Maps graph nodes into a persisted semantic cluster."""

    __tablename__ = "graph_cluster_memberships"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    cluster_id = Column(UUID(as_uuid=True), ForeignKey("graph_clusters.id", ondelete="CASCADE"), nullable=False, index=True)
    node_id = Column(UUID(as_uuid=True), ForeignKey("graph_nodes.id", ondelete="CASCADE"), nullable=False, index=True)
    membership_score = Column(Float, nullable=False, default=0.0)
    cluster_rank = Column(Integer, nullable=False, default=0)
    membership_metadata = Column("metadata", JSONB, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), index=True)

    cluster = relationship("GraphCluster", back_populates="memberships", lazy="joined")
    node = relationship("GraphNode", lazy="joined")

    __table_args__ = (
        UniqueConstraint("workspace_id", "node_id", name="uq_graph_cluster_membership_workspace_node"),
        UniqueConstraint("cluster_id", "node_id", name="uq_graph_cluster_membership_cluster_node"),
        Index("idx_graph_cluster_membership_cluster_rank", "cluster_id", "cluster_rank"),
        Index("idx_graph_cluster_membership_workspace_cluster", "workspace_id", "cluster_id"),
    )


class ChunkWeight(Base):
    """STEP 8: Chunk credibility weights for feedback-based learning."""
    __tablename__ = "chunk_weights"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    chunk_id = Column(String(255), nullable=False, index=True)  # Reference to chunk
    document_id = Column(UUID(as_uuid=True), ForeignKey("documents.id"), nullable=False, index=True)
    workspace_id = Column(UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False, index=True)
    
    # Weight tracking
    credibility_score = Column(Float, default=1.0)  # 0.0 to 2.0 multiplier
    positive_feedback_count = Column(Integer, default=0)  # Times marked as correct
    negative_feedback_count = Column(Integer, default=0)  # Times marked as incorrect
    total_uses = Column(Integer, default=0)  # Total times retrieved
    accuracy_rate = Column(Float, default=0.5)  # (positive / (positive + negative))
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), index=True)
    
    # Indexes
    __table_args__ = (
        Index('idx_chunk_weight_workspace', 'workspace_id', 'created_at'),
        Index('idx_chunk_weight_accuracy', 'accuracy_rate'),
    )


class ConflictReport(Base):
    """FEATURE 3: Conflict detection engine - stores identified contradictions in notes."""
    __tablename__ = "conflict_reports"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False, index=True)
    
    # Note references
    note_id_a = Column(UUID(as_uuid=True), ForeignKey("notes.id"), nullable=False)
    note_id_b = Column(UUID(as_uuid=True), ForeignKey("notes.id"), nullable=False)
    
    # Conflict classification
    conflict_type = Column(
        String(50), 
        nullable=False,
        comment="Type: factual | opinion | date | numerical"
    )
    
    # Conflict details
    conflict_summary = Column(Text, nullable=False)  # AI-generated explanation
    conflict_quote_a = Column(Text, nullable=True)  # Relevant excerpt from note A
    conflict_quote_b = Column(Text, nullable=True)  # Relevant excerpt from note B
    
    # Scoring
    similarity_score = Column(Float, nullable=False)  # 0.0 to 1.0
    severity = Column(String(50), nullable=False, default="medium")  # low | medium | high
    
    # Resolution tracking
    status = Column(String(50), nullable=False, default="pending")  # pending | resolved | dismissed
    resolution_note = Column(Text, nullable=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    resolved_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Relationships
    note_a = relationship("Note", foreign_keys=[note_id_a], lazy="joined")
    note_b = relationship("Note", foreign_keys=[note_id_b], lazy="joined")
    resolver = relationship("User", lazy="joined", foreign_keys=[resolved_by])
    
    # Indexes
    __table_args__ = (
        UniqueConstraint('note_id_a', 'note_id_b', name='uq_conflict_pair'),
        Index('idx_conflict_workspace_status', 'workspace_id', 'status', 'created_at'),
        Index('idx_conflict_severity', 'severity', 'created_at'),
    )


class SearchQuery(Base):
    """Search query history for analytics and learning."""
    __tablename__ = "search_queries"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    
    # Query details
    query_text = Column(String(2000), nullable=False)
    
    # Query embedding for similarity clustering
    # Stores 1536D OpenAI embeddings as JSON (~20-25KB when serialized)
    query_embedding = Column(Text, nullable=True)  # Changed from String(10000) to Text for larger embeddings
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    
    # Relationships
    workspace = relationship("Workspace")
    user = relationship("User")
    
    # Indexes
    __table_args__ = (
        Index('idx_search_query_workspace', 'workspace_id', 'created_at'),
        Index('idx_search_query_user', 'user_id', 'created_at'),
    )


class SearchLog(Base):
    """Search query execution log with results tracking."""
    __tablename__ = "search_logs"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    
    # Query details
    query_text = Column(String(2000), nullable=False)
    
    # Results
    result_chunk_ids = Column(JSONB, default=list)  # Array of chunk UUIDs returned
    result_count = Column(Integer, default=0)
    
    # User interaction (updated on click)
    clicked_count = Column(Integer, default=0)
    clicked_chunk_ids = Column(JSONB, default=list)
    
    # Performance metrics
    search_duration_ms = Column(Integer, nullable=True)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Relationships
    workspace = relationship("Workspace")
    user = relationship("User")
    
    # Indexes
    __table_args__ = (
        Index('idx_search_log_workspace', 'workspace_id', 'created_at'),
        Index('idx_search_log_user', 'user_id', 'created_at'),
        Index('idx_search_log_query', 'query_text'),
    )
