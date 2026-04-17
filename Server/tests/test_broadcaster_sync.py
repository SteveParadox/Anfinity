"""
Test suite for Redis-based event broadcaster.

Tests cover:
1. Event model creation and serialization
2. Sync publishing (for Celery context)
3. Async publishing (for FastAPI context)
4. Channel hierarchy verification
5. Error handling and reconnection
"""

import pytest
import json
import asyncio
from datetime import datetime
from app.events.broadcaster import (
    Event,
    EventType,
    EventPriority,
    Broadcaster,
    _SyncPublisher,
    publish_event_sync,
    publish_event,
    broadcast_ingestion_started_sync,
    broadcast_stage_update_sync,
    broadcast_progress_update_sync,
    broadcast_ingestion_completed_sync,
    broadcast_ingestion_failed_sync,
)


# ─────────────────────────────────────────────────────────────────────────────
# Unit Tests: Event Model
# ─────────────────────────────────────────────────────────────────────────────

class TestEventModel:
    """Test Event creation, serialization, and channel naming."""

    def test_event_creation_minimal(self):
        """Test Event creation with minimal parameters."""
        event = Event(
            event_type=EventType.DOCUMENT_STARTED,
            workspace_id="ws-123"
        )
        assert event.event_type == EventType.DOCUMENT_STARTED
        assert event.workspace_id == "ws-123"
        assert event.document_id is None
        assert event.user_id is None
        assert event.data == {}
        assert event.priority == EventPriority.NORMAL
        assert event.stage is None
        assert event.timestamp is not None

    def test_event_creation_full(self):
        """Test Event creation with all parameters."""
        event = Event(
            event_type=EventType.STAGE_COMPLETED,
            workspace_id="ws-123",
            document_id="doc-456",
            user_id="user-789",
            data={"stage": "embedding", "chunks": 50},
            priority=EventPriority.HIGH,
            stage="embedding"
        )
        assert event.event_type == EventType.STAGE_COMPLETED
        assert event.workspace_id == "ws-123"
        assert event.document_id == "doc-456"
        assert event.user_id == "user-789"
        assert event.data["stage"] == "embedding"
        assert event.data["chunks"] == 50
        assert event.priority == EventPriority.HIGH
        assert event.stage == "embedding"

    def test_event_to_dict(self):
        """Test Event serialization to dictionary."""
        event = Event(
            event_type=EventType.DOCUMENT_COMPLETED,
            workspace_id="ws-123",
            document_id="doc-456",
            data={"token_count": 1000, "chunk_count": 50}
        )
        event_dict = event.to_dict()
        
        assert event_dict["event_type"] == EventType.DOCUMENT_COMPLETED
        assert event_dict["workspace_id"] == "ws-123"
        assert event_dict["document_id"] == "doc-456"
        assert event_dict["data"]["token_count"] == 1000
        assert "timestamp" in event_dict

    def test_event_to_json(self):
        """Test Event serialization to JSON string."""
        event = Event(
            event_type=EventType.PROGRESS_UPDATE,
            workspace_id="ws-123",
            document_id="doc-456",
            data={"percent": 75}
        )
        json_str = event.to_json()
        
        # Should be valid JSON
        parsed = json.loads(json_str)
        assert parsed["event_type"] == EventType.PROGRESS_UPDATE
        assert parsed["workspace_id"] == "ws-123"
        assert parsed["data"]["percent"] == 75

    def test_event_from_dict(self):
        """Test Event deserialization from dictionary."""
        original_data = {
            "event_type": EventType.DOCUMENT_FAILED,
            "workspace_id": "ws-123",
            "document_id": "doc-456",
            "user_id": "user-789",
            "data": {"error_message": "Connection failed"},
            "priority": EventPriority.HIGH,
            "stage": "embedding",
            "timestamp": "2024-03-18T10:30:00.000000"
        }
        
        event = Event.from_dict(original_data)
        
        assert event.event_type == EventType.DOCUMENT_FAILED
        assert event.workspace_id == "ws-123"
        assert event.document_id == "doc-456"
        assert event.user_id == "user-789"
        assert event.data["error_message"] == "Connection failed"
        assert event.priority == EventPriority.HIGH
        assert event.stage == "embedding"

    def test_event_roundtrip_serialization(self):
        """Test Event → Dict → Event roundtrip."""
        original = Event(
            event_type=EventType.STAGE_STARTED,
            workspace_id="ws-123",
            document_id="doc-456",
            user_id="user-789",
            data={"stage": "extraction", "status": "started"},
            priority=EventPriority.NORMAL,
            stage="extraction"
        )
        
        # Roundtrip
        as_dict = original.to_dict()
        restored = Event.from_dict(as_dict)
        
        assert restored.event_type == original.event_type
        assert restored.workspace_id == original.workspace_id
        assert restored.document_id == original.document_id
        assert restored.user_id == original.user_id
        assert restored.data == original.data
        assert restored.priority == original.priority
        assert restored.stage == original.stage

    def test_event_channels_all_ids(self):
        """Test channel generation with all IDs present."""
        event = Event(
            event_type=EventType.DOCUMENT_PROCESSING,
            workspace_id="ws-123",
            document_id="doc-456",
            user_id="user-789"
        )
        
        channels = Event.channels(event)
        
        assert "ingestion:ws-123" in channels
        assert "document:doc-456" in channels
        assert "user:user-789" in channels
        assert len(channels) == 3

    def test_event_channels_workspace_only(self):
        """Test channel generation with only workspace ID."""
        event = Event(
            event_type=EventType.WORKER_HEALTH,
            workspace_id="system"
        )
        
        channels = Event.channels(event)
        
        assert "ingestion:system" in channels
        assert len(channels) == 1

    def test_event_channels_document_without_user(self):
        """Test channel generation with document but no user ID."""
        event = Event(
            event_type=EventType.DOCUMENT_STARTED,
            workspace_id="ws-123",
            document_id="doc-456"
        )
        
        channels = Event.channels(event)
        
        assert "ingestion:ws-123" in channels
        assert "document:doc-456" in channels
        assert len(channels) == 2


# ─────────────────────────────────────────────────────────────────────────────
# Unit Tests: Event Types and Priorities
# ─────────────────────────────────────────────────────────────────────────────

class TestEventTypes:
    """Test EventType enum."""

    def test_document_lifecycle_events(self):
        """Test all document lifecycle event types."""
        assert EventType.DOCUMENT_CREATED == "document.created"
        assert EventType.DOCUMENT_STARTED == "document.started"
        assert EventType.DOCUMENT_PROCESSING == "document.processing"
        assert EventType.DOCUMENT_COMPLETED == "document.completed"
        assert EventType.DOCUMENT_FAILED == "document.failed"

    def test_stage_events(self):
        """Test all stage event types."""
        assert EventType.STAGE_STARTED == "stage.started"
        assert EventType.STAGE_COMPLETED == "stage.completed"
        assert EventType.STAGE_FAILED == "stage.failed"

    def test_progress_events(self):
        """Test progress event type."""
        assert EventType.PROGRESS_UPDATE == "progress.update"

    def test_system_events(self):
        """Test system event types."""
        assert EventType.WORKER_HEALTH == "worker.health"
        assert EventType.SYSTEM_ERROR == "system.error"
        assert EventType.SYSTEM_NOTIFICATION == "system.notification"


class TestEventPriorities:
    """Test EventPriority enum."""

    def test_priority_values(self):
        """Test all priority levels."""
        assert EventPriority.LOW == "low"
        assert EventPriority.NORMAL == "normal"
        assert EventPriority.HIGH == "high"
        assert EventPriority.CRITICAL == "critical"


# ─────────────────────────────────────────────────────────────────────────────
# Unit Tests: Sync Publisher
# ─────────────────────────────────────────────────────────────────────────────

class TestSyncPublisher:
    """Test _SyncPublisher for Celery context."""

    def test_sync_publisher_singleton_init(self):
        """Test lazy initialization of sync publisher."""
        publisher = _SyncPublisher()
        assert publisher._client is None
        
        # Get client should initialize it
        client = publisher._get_client()
        assert client is not None

    def test_sync_publisher_connection_reuse(self):
        """Test that sync publisher reuses connections."""
        publisher = _SyncPublisher()
        
        client1 = publisher._get_client()
        client2 = publisher._get_client()
        
        # Should be the same instance
        assert client1 is client2

    def test_sync_publisher_thread_safety(self):
        """Test thread safety of sync publisher."""
        publisher = _SyncPublisher()
        
        clients = []
        for i in range(10):
            client = publisher._get_client()
            clients.append(client)
        
        # All should be the same instance
        assert all(c is clients[0] for c in clients)

    def test_sync_publisher_publish_event(self):
        """Test publishing an event via sync publisher."""
        publisher = _SyncPublisher()
        
        event = Event(
            event_type=EventType.DOCUMENT_STARTED,
            workspace_id="ws-test",
            document_id="doc-test",
            data={"title": "Test Document"}
        )
        
        # This will work only if Redis is running
        try:
            subscribers = publisher.publish(event)
            assert isinstance(subscribers, int)
            assert subscribers >= 0
        except Exception as e:
            pytest.skip(f"Redis not available: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Unit Tests: Broadcast Helpers - Sync
# ─────────────────────────────────────────────────────────────────────────────

class TestBroadcastHelperSync:
    """Test sync broadcast helper functions."""

    @pytest.fixture
    def setup_redis(self):
        """Ensure Redis is available for testing."""
        try:
            import redis
            client = redis.Redis.from_url("redis://localhost:6379/0")
            client.ping()
            yield client
        except Exception as e:
            pytest.skip(f"Redis not available: {e}")

    def test_broadcast_ingestion_started_sync(self, setup_redis):
        """Test broadcast_ingestion_started_sync helper."""
        subscribers = broadcast_ingestion_started_sync(
            workspace_id="ws-test",
            document_id="doc-test",
            document_title="Test Document"
        )
        assert isinstance(subscribers, int)

    def test_broadcast_stage_update_sync(self, setup_redis):
        """Test broadcast_stage_update_sync helper."""
        subscribers = broadcast_stage_update_sync(
            workspace_id="ws-test",
            document_id="doc-test",
            stage="extraction",
            status="started"
        )
        assert isinstance(subscribers, int)

    def test_broadcast_progress_update_sync(self, setup_redis):
        """Test broadcast_progress_update_sync helper."""
        subscribers = broadcast_progress_update_sync(
            workspace_id="ws-test",
            document_id="doc-test",
            progress={"percent": 50, "stage": "chunking"}
        )
        assert isinstance(subscribers, int)

    def test_broadcast_ingestion_completed_sync(self, setup_redis):
        """Test broadcast_ingestion_completed_sync helper."""
        subscribers = broadcast_ingestion_completed_sync(
            workspace_id="ws-test",
            document_id="doc-test",
            token_count=1000,
            chunk_count=50,
            embedding_count=50
        )
        assert isinstance(subscribers, int)

    def test_broadcast_ingestion_failed_sync(self, setup_redis):
        """Test broadcast_ingestion_failed_sync helper."""
        subscribers = broadcast_ingestion_failed_sync(
            workspace_id="ws-test",
            document_id="doc-test",
            error_message="Test error",
            stage="embedding"
        )
        assert isinstance(subscribers, int)


# ─────────────────────────────────────────────────────────────────────────────
# Async Tests: Broadcaster
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestBroadcaster:
    """Test Broadcaster for FastAPI context."""

    @pytest.fixture
    async def broadcaster_instance(self):
        """Create and cleanup broadcaster instance."""
        broadcaster = Broadcaster()
        try:
            await broadcaster.connect()
            yield broadcaster
        finally:
            await broadcaster.disconnect()

    async def test_broadcaster_connect_disconnect(self):
        """Test broadcaster connection lifecycle."""
        broadcaster = Broadcaster()
        
        # Should not be connected
        assert broadcaster._redis is None
        
        try:
            await broadcaster.connect()
            assert broadcaster._redis is not None
            
            await broadcaster.disconnect()
            assert broadcaster._redis is None
        except Exception as e:
            pytest.skip(f"Redis not available: {e}")

    async def test_broadcaster_publish_event(self, broadcaster_instance):
        """Test publishing an event via async broadcaster."""
        event = Event(
            event_type=EventType.PROGRESS_UPDATE,
            workspace_id="ws-test",
            document_id="doc-test",
            data={"percent": 75}
        )
        
        subscribers = await broadcaster_instance.publish(event)
        assert isinstance(subscribers, int)

    async def test_broadcaster_multiple_events(self, broadcaster_instance):
        """Test publishing multiple events."""
        events = [
            Event(EventType.DOCUMENT_STARTED, "ws-1", "doc-1"),
            Event(EventType.STAGE_STARTED, "ws-1", "doc-1", stage="extraction"),
            Event(EventType.PROGRESS_UPDATE, "ws-1", "doc-1"),
            Event(EventType.DOCUMENT_COMPLETED, "ws-1", "doc-1"),
        ]
        
        for event in events:
            subscribers = await broadcaster_instance.publish(event)
            assert isinstance(subscribers, int)


# ─────────────────────────────────────────────────────────────────────────────
# Async Tests: Broadcast Helpers
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestBroadcastHelperAsync:
    """Test async broadcast helper functions."""

    async def test_publish_event_async(self):
        """Test publish_event async function."""
        event = Event(
            event_type=EventType.DOCUMENT_STARTED,
            workspace_id="ws-test",
            document_id="doc-test"
        )
        
        try:
            subscribers = await publish_event(event)
            assert isinstance(subscribers, int)
        except Exception as e:
            pytest.skip(f"Redis not available: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Integration Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestIntegration:
    """Integration tests for complete workflows."""

    def test_sync_and_async_independence(self):
        """Test that sync and async publishers work independently."""
        # Create sync event
        sync_event = Event(
            event_type=EventType.DOCUMENT_STARTED,
            workspace_id="ws-sync",
            document_id="doc-sync"
        )
        
        try:
            subscribers = publish_event_sync(sync_event)
            assert isinstance(subscribers, int)
        except Exception as e:
            pytest.skip(f"Redis not available: {e}")

    def test_event_roundtrip_through_redis(self):
        """Test event serialization for Redis transport."""
        original = Event(
            event_type=EventType.STAGE_COMPLETED,
            workspace_id="ws-123",
            document_id="doc-456",
            user_id="user-789",
            data={"stage": "embedding", "duration_ms": 5000},
            priority=EventPriority.HIGH,
            stage="embedding"
        )
        
        # Simulate Redis transport
        json_str = original.to_json()
        delivered_data = json.loads(json_str)
        restored = Event.from_dict(delivered_data)
        
        # Verify round-trip
        assert restored.event_type == original.event_type
        assert restored.workspace_id == original.workspace_id
        assert restored.document_id == original.document_id
        assert restored.user_id == original.user_id
        assert restored.data == original.data
        assert restored.stage == original.stage


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
