"""API routes module."""
from app.api import auth, workspaces, documents, notes, query, knowledge_graph, audit, connectors, ingestion

__all__ = [
    "auth",
    "workspaces",
    "documents",
    "notes",
    "knowledge_graph",
    "audit",
    "connectors",
    "ingestion",
]
