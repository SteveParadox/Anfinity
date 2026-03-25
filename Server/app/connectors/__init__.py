"""Connectors module for multi-source document ingestion."""
from app.connectors.base import BaseConnector, ConnectorType, ConnectorDocument
from app.connectors.notion import NotionConnector
from app.connectors.slack import SlackConnector
from app.connectors.gdrive import GoogleDriveConnector
from app.connectors.confluence import ConfluenceConnector
from app.connectors.github import GitHubConnector
from app.connectors.email import EmailConnector
from app.connectors.meeting_transcripts import MeetingTranscriptParser, TranscriptEntry

__all__ = [
    "BaseConnector",
    "ConnectorType",
    "ConnectorDocument",
    "NotionConnector",
    "SlackConnector",
    "GoogleDriveConnector",
    "ConfluenceConnector",
    "GitHubConnector",
    "EmailConnector",
    "MeetingTranscriptParser",
    "TranscriptEntry",
]
