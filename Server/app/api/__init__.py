"""API route package.

Keep package imports side-effect free so tests and lightweight imports do not
eagerly initialize external clients like storage backends.
"""

__all__ = [
    "approval_workflows",
    "auth",
    "workspaces",
    "documents",
    "notes",
    "knowledge_graph",
    "audit",
    "connectors",
    "ingestion",
]
