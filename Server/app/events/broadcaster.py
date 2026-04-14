"""Redis-based event broadcaster for real-time notifications.

This module provides a production-ready event broadcasting system using Redis pub/sub.
It decouples Celery workers from WebSocket connections, allowing scalable real-time updates.

Events are published to Redis channels using a hierarchical naming scheme:
- ingestion:{workspace_id}  - All ingestion events for a workspace
- document:{document_id}    - All events for a specific document
- user:{user_id}            - All user-scoped events

ARCHITECTURE NOTE — async vs sync publishing
--------------------------------------------
* FastAPI routes / WebSocket handlers  → use `await publish_event(event)` or
  the `async broadcast_*` helpers.
* Celery tasks                          → use `broadcast_*_sync()` helpers or
  `publish_event_sync(event)` directly.

The sync path uses a *synchronous* Redis client (redis.Redis, not
redis.asyncio) so it never touches an event loop.  This is the correct fix
for the recurring "Event loop is closed" error that occurs when Celery workers
call asyncio.run() on a coroutine that holds a reference to an already-closed
async Redis connection.
"""

import json
import logging
import threading
from typing import Any, Dict, Optional
from datetime import datetime
from enum import Enum

import redis.asyncio as aioredis
import redis as syncredis          # ← synchronous redis client
from app.config import settings

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Event model
# ─────────────────────────────────────────────────────────────────────────────

class EventType(str, Enum):
    """Event types emitted during ingestion and processing."""

    # Document lifecycle
    DOCUMENT_CREATED    = "document.created"
    DOCUMENT_STARTED    = "document.started"
    DOCUMENT_PROCESSING = "document.processing"
    DOCUMENT_COMPLETED  = "document.completed"
    DOCUMENT_FAILED     = "document.failed"

    # Stage events
    STAGE_STARTED   = "stage.started"
    STAGE_COMPLETED = "stage.completed"
    STAGE_FAILED    = "stage.failed"

    # Progress
    PROGRESS_UPDATE = "progress.update"

    # Worker / system
    WORKER_HEALTH        = "worker.health"
    SYSTEM_ERROR         = "system.error"
    SYSTEM_NOTIFICATION  = "system.notification"


class EventPriority(str, Enum):
    LOW      = "low"
    NORMAL   = "normal"
    HIGH     = "high"
    CRITICAL = "critical"


class Event:
    """Represents a single broadcast event."""

    def __init__(
        self,
        event_type: EventType,
        workspace_id: str,
        document_id: Optional[str] = None,
        user_id: Optional[str] = None,
        data: Optional[Dict[str, Any]] = None,
        priority: EventPriority = EventPriority.NORMAL,
        stage: Optional[str] = None,
    ):
        self.event_type  = event_type
        self.workspace_id = workspace_id
        self.document_id = document_id
        self.user_id     = user_id
        self.data        = data or {}
        self.priority    = priority
        self.stage       = stage
        self.timestamp   = datetime.utcnow().isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_type":   self.event_type,
            "workspace_id": self.workspace_id,
            "document_id":  self.document_id,
            "user_id":      self.user_id,
            "data":         self.data,
            "priority":     self.priority,
            "stage":        self.stage,
            "timestamp":    self.timestamp,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Event":
        event = cls(
            event_type=EventType(data["event_type"]),
            workspace_id=data["workspace_id"],
            document_id=data.get("document_id"),
            user_id=data.get("user_id"),
            data=data.get("data", {}),
            priority=EventPriority(data.get("priority", EventPriority.NORMAL)),
            stage=data.get("stage"),
        )
        event.timestamp = data.get("timestamp", datetime.utcnow().isoformat())
        return event

    @staticmethod
    def channels(event: "Event") -> list:
        """Return the Redis channels this event should be published to."""
        ch = [f"ingestion:{event.workspace_id}"]
        if event.document_id:
            ch.append(f"document:{event.document_id}")
        if event.user_id:
            ch.append(f"user:{event.user_id}")
        return ch


# ─────────────────────────────────────────────────────────────────────────────
# Async broadcaster  (for FastAPI / WebSocket context)
# ─────────────────────────────────────────────────────────────────────────────

class Broadcaster:
    """Async Redis broadcaster — for use inside FastAPI/async contexts only."""

    def __init__(self, redis_url: str = None):
        self.redis_url = redis_url or settings.REDIS_URL
        self._redis: Optional[aioredis.Redis] = None

    async def connect(self) -> None:
        if self._redis is None:
            try:
                self._redis = await aioredis.from_url(
                    self.redis_url,
                    encoding="utf8",
                    decode_responses=True,
                    socket_keepalive=True,
                )
                logger.info("Event broadcaster connected to Redis")
            except Exception as exc:
                logger.error("Failed to connect to Redis: %s", exc)
                raise

    async def disconnect(self) -> None:
        if self._redis:
            await self._redis.aclose()
            self._redis = None

    async def publish(self, event: Event) -> int:
        if self._redis is None:
            logger.warning("Async broadcaster not connected — skipping publish")
            return 0

        message = event.to_json()
        subscribers = 0
        try:
            for channel in Event.channels(event):
                n = await self._redis.publish(channel, message)
                subscribers += n
                logger.debug("Published %s → %s (%d subscribers)", event.event_type, channel, n)
        except Exception as exc:
            logger.error("Error publishing event: %s", exc)
        return subscribers

    async def subscribe(self, channel: str):
        if self._redis is None:
            await self.connect()
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(channel)
        return pubsub


# ─────────────────────────────────────────────────────────────────────────────
# Sync publisher  (for Celery tasks — NO event loop required)
# ─────────────────────────────────────────────────────────────────────────────

class _SyncPublisher:
    """Thread-safe synchronous Redis publisher backed by a connection pool.

    A single connection pool is shared across all Celery worker threads /
    green-threads.  The pool is created lazily on first use and is never
    torn down, so there is no "connection closed" race condition.

    This is the correct tool for publishing from Celery tasks.
    """

    def __init__(self, redis_url: str = None):
        self._redis_url = redis_url or settings.REDIS_URL
        self._client: Optional[syncredis.Redis] = None
        self._lock = threading.Lock()

    def _get_client(self) -> syncredis.Redis:
        """Return a healthy synchronous Redis client, reconnecting if needed."""
        with self._lock:
            if self._client is None:
                self._client = syncredis.from_url(
                    self._redis_url,
                    decode_responses=True,
                    socket_keepalive=True,
                    socket_connect_timeout=5,
                    socket_timeout=5,
                    retry_on_timeout=True,
                )
                logger.debug("Sync Redis publisher connected")
            return self._client

    def publish(self, event: Event) -> int:
        """Publish *event* to all relevant channels.  Returns subscriber count."""
        try:
            client = self._get_client()
            message = event.to_json()
            subscribers = 0
            for channel in Event.channels(event):
                n = client.publish(channel, message)
                subscribers += n
                logger.debug(
                    "Sync-published %s → %s (%d subscribers)",
                    event.event_type, channel, n,
                )
            return subscribers
        except Exception as exc:
            logger.error("Sync publish failed: %s", exc)
            # Invalidate client so it reconnects on the next call
            with self._lock:
                self._client = None
            return 0


# ─────────────────────────────────────────────────────────────────────────────
# Singletons
# ─────────────────────────────────────────────────────────────────────────────

_broadcaster: Optional[Broadcaster] = None
_sync_publisher: Optional[_SyncPublisher] = None


async def get_broadcaster() -> Broadcaster:
    """Return the global async Broadcaster, connecting on first call."""
    global _broadcaster
    if _broadcaster is None:
        _broadcaster = Broadcaster()
        await _broadcaster.connect()
    return _broadcaster


def _get_sync_publisher() -> _SyncPublisher:
    """Return the global synchronous publisher (thread-safe, lazy init)."""
    global _sync_publisher
    if _sync_publisher is None:
        _sync_publisher = _SyncPublisher()
    return _sync_publisher


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

async def publish_event(event: Event) -> int:
    """Publish an event from an *async* context (FastAPI, WebSocket handlers).

    Do NOT call this from Celery tasks — use publish_event_sync() instead.
    """
    broadcaster = await get_broadcaster()
    return await broadcaster.publish(event)


def publish_event_sync(event: Event) -> int:
    """Publish an event from a *synchronous* context (Celery tasks).

    Uses a persistent sync Redis connection pool — no event loop involved.
    Safe to call from any thread at any time.
    """
    return _get_sync_publisher().publish(event)


# ─────────────────────────────────────────────────────────────────────────────
# Convenience helpers — async (FastAPI)
# ─────────────────────────────────────────────────────────────────────────────

async def broadcast_ingestion_started(workspace_id, document_id, document_title) -> int:
    return await publish_event(Event(
        event_type=EventType.DOCUMENT_STARTED,
        workspace_id=workspace_id,
        document_id=document_id,
        data={"document_title": document_title},
    ))


async def broadcast_stage_update(workspace_id, document_id, stage, status, progress=None) -> int:
    return await publish_event(Event(
        event_type=EventType.STAGE_COMPLETED if status == "completed" else EventType.STAGE_STARTED,
        workspace_id=workspace_id,
        document_id=document_id,
        stage=stage,
        data={"stage": stage, "status": status, "progress": progress or {}},
    ))


async def broadcast_progress_update(workspace_id, document_id, progress) -> int:
    return await publish_event(Event(
        event_type=EventType.PROGRESS_UPDATE,
        workspace_id=workspace_id,
        document_id=document_id,
        data=progress,
    ))


async def broadcast_ingestion_completed(workspace_id, document_id, token_count, chunk_count, embedding_count) -> int:
    return await publish_event(Event(
        event_type=EventType.DOCUMENT_COMPLETED,
        workspace_id=workspace_id,
        document_id=document_id,
        data={"token_count": token_count, "chunk_count": chunk_count, "embedding_count": embedding_count},
        priority=EventPriority.HIGH,
    ))


async def broadcast_ingestion_failed(workspace_id, document_id, error_message, stage="unknown") -> int:
    return await publish_event(Event(
        event_type=EventType.DOCUMENT_FAILED,
        workspace_id=workspace_id,
        document_id=document_id,
        stage=stage,
        data={"error_message": error_message, "stage": stage},
        priority=EventPriority.HIGH,
    ))


async def broadcast_worker_health(worker_id, status, tasks_active, tasks_processed, uptime_seconds) -> int:
    return await publish_event(Event(
        event_type=EventType.WORKER_HEALTH,
        workspace_id="system",
        data={
            "worker_id": worker_id, "status": status,
            "tasks_active": tasks_active, "tasks_processed": tasks_processed,
            "uptime_seconds": uptime_seconds,
        },
    ))


# ─────────────────────────────────────────────────────────────────────────────
# Convenience helpers — sync (Celery tasks)
# ─────────────────────────────────────────────────────────────────────────────

def broadcast_ingestion_started_sync(workspace_id, document_id, document_title) -> int:
    return publish_event_sync(Event(
        event_type=EventType.DOCUMENT_STARTED,
        workspace_id=workspace_id,
        document_id=document_id,
        data={"document_title": document_title},
    ))


def broadcast_stage_update_sync(workspace_id, document_id, stage, status, progress=None) -> int:
    return publish_event_sync(Event(
        event_type=EventType.STAGE_COMPLETED if status == "completed" else EventType.STAGE_STARTED,
        workspace_id=workspace_id,
        document_id=document_id,
        stage=stage,
        data={"stage": stage, "status": status, "progress": progress or {}},
    ))


def broadcast_progress_update_sync(workspace_id, document_id, progress) -> int:
    return publish_event_sync(Event(
        event_type=EventType.PROGRESS_UPDATE,
        workspace_id=workspace_id,
        document_id=document_id,
        data=progress,
    ))


def broadcast_ingestion_completed_sync(workspace_id, document_id, token_count, chunk_count, embedding_count) -> int:
    return publish_event_sync(Event(
        event_type=EventType.DOCUMENT_COMPLETED,
        workspace_id=workspace_id,
        document_id=document_id,
        data={"token_count": token_count, "chunk_count": chunk_count, "embedding_count": embedding_count},
        priority=EventPriority.HIGH,
    ))


def broadcast_ingestion_failed_sync(workspace_id, document_id, error_message, stage="unknown") -> int:
    return publish_event_sync(Event(
        event_type=EventType.DOCUMENT_FAILED,
        workspace_id=workspace_id,
        document_id=document_id,
        stage=stage,
        data={"error_message": error_message, "stage": stage},
        priority=EventPriority.HIGH,
    ))


def broadcast_worker_health_sync(worker_id, status, tasks_active, tasks_processed, uptime_seconds) -> int:
    return publish_event_sync(Event(
        event_type=EventType.WORKER_HEALTH,
        workspace_id="system",
        data={
            "worker_id": worker_id, "status": status,
            "tasks_active": tasks_active, "tasks_processed": tasks_processed,
            "uptime_seconds": uptime_seconds,
        },
    ))
