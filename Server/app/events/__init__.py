"""Real-time event broadcasting system for ingestion pipeline.

Provides:
- Redis pub/sub based event broadcasting
- WebSocket and SSE endpoints for frontend
- Event types and definitions
- Integration with Celery tasks
"""

from app.events.broadcaster import (
    Broadcaster,
    Event,
    EventType,
    EventPriority,
    get_broadcaster,
    publish_event,
    publish_event_sync,
    broadcast_ingestion_started,
    broadcast_stage_update,
    broadcast_progress_update,
    broadcast_ingestion_completed,
    broadcast_ingestion_failed,
    broadcast_worker_health,
    # Sync versions for Celery tasks
    broadcast_ingestion_started_sync,
    broadcast_stage_update_sync,
    broadcast_progress_update_sync,
    broadcast_ingestion_completed_sync,
    broadcast_ingestion_failed_sync,
    broadcast_worker_health_sync,
)
from app.events.websocket import (
    router as websocket_router,
    ConnectionManager,
)

__all__ = [
    "Broadcaster",
    "Event",
    "EventType",
    "EventPriority",
    "get_broadcaster",
    # Async functions (FastAPI)
    "publish_event",
    "broadcast_ingestion_started",
    "broadcast_stage_update",
    "broadcast_progress_update",
    "broadcast_ingestion_completed",
    "broadcast_ingestion_failed",
    "broadcast_worker_health",
    # Sync functions (Celery)
    "publish_event_sync",
    "broadcast_ingestion_started_sync",
    "broadcast_stage_update_sync",
    "broadcast_progress_update_sync",
    "broadcast_ingestion_completed_sync",
    "broadcast_ingestion_failed_sync",
    "broadcast_worker_health_sync",
    # WebSocket
    "websocket_router",
    "ConnectionManager",
]
