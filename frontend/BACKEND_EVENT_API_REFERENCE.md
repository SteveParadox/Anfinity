# Backend Event-Driven API Reference

## WebSocket & SSE Endpoints

### WebSocket: Real-Time Ingestion Events

**Endpoint:** `ws://localhost:8000/events/ws/ingestion/{workspace_id}`

**Authentication:** JWT token as query parameter: `?token=jwt-token`

**Usage:**

```typescript
const ws = new WebSocket(
  `ws://localhost:8000/events/ws/ingestion/workspace-123?token=jwt-token`
);

ws.onmessage = (event) => {
  const data = JSON.parse(event.data);
  console.log('Event:', data.event_type, data);
};

ws.onopen = () => {
  // Send keepalive ping
  ws.send(JSON.stringify({ type: 'ping' }));
};
```

**Heartbeat:** Send `{ "type": "ping" }` every 30 seconds to keep connection alive.

**Connection:** Supports multiple simultaneous connections per user per workspace.

---

### Server-Sent Events (SSE): Fallback for WebSocket

**Endpoint:** `GET /events/sse/ingestion/{workspace_id}`

**Authentication:** Bearer token in `Authorization` header

**Usage:**

```typescript
const eventSource = new EventSource('/events/sse/ingestion/workspace-123', {
  headers: { Authorization: `Bearer jwt-token` },
});

eventSource.onmessage = (event) => {
  const data = JSON.parse(event.data);
  console.log('Event:', data);
};

// Keepalive events are sent automatically
```

**Advantages:**
- Simpler than WebSocket
- Better browser compatibility
- No special headers needed

**Disadvantages:**
- One-way communication only
- Cannot send client messages

---

## Event Types & Payloads

### Document Lifecycle Events

#### `document.started`

Document ingestion has started.

```typescript
{
  event_type: 'document.started',
  workspace_id: 'workspace-123',
  document_id: 'doc-456',
  timestamp: '2026-03-09T10:30:00.000Z',
  priority: 'high',
  data: {
    document_title: 'My Document.pdf'
  }
}
```

#### `stage.started`

A processing stage has started (download, parse, chunk, embedding).

```typescript
{
  event_type: 'stage.started',
  workspace_id: 'workspace-123',
  document_id: 'doc-456',
  stage: 'chunking',
  timestamp: '2026-03-09T10:30:05.000Z',
  priority: 'normal',
  data: {
    stage: 'chunking',
    status: 'started',
    progress: {
      status: 'Creating chunks...'
    }
  }
}
```

**Possible stages:**
- `download`: Downloading file from storage
- `parse`: Parsing file content
- `chunking`: Splitting into chunks
- `embedding`: Generating embeddings

#### `stage.completed`

A processing stage has completed.

```typescript
{
  event_type: 'stage.completed',
  workspace_id: 'workspace-123',
  document_id: 'doc-456',
  stage: 'chunking',
  timestamp: '2026-03-09T10:30:15.000Z',
  priority: 'normal',
  data: {
    stage: 'chunking',
    status: 'completed',
    progress: {
      chunks_created: 42,
      duration_ms: 10000
    }
  }
}
```

#### `progress.update`

Detailed progress update (high frequency).

```typescript
{
  event_type: 'progress.update',
  workspace_id: 'workspace-123',
  document_id: 'doc-456',
  timestamp: '2026-03-09T10:30:08.000Z',
  priority: 'normal',
  data: {
    current_chunk: 5,
    total_chunks: 42,
    bytes_processed: 51200,
    estimated_time_remaining_seconds: 8
  }
}
```

#### `document.completed`

Document indexing completed successfully.

```typescript
{
  event_type: 'document.completed',
  workspace_id: 'workspace-123',
  document_id: 'doc-456',
  timestamp: '2026-03-09T10:30:30.000Z',
  priority: 'high',
  data: {
    token_count: 2500,
    chunk_count: 42,
    embedding_count: 42,
    total_duration_ms: 30000
  }
}
```

#### `document.failed`

Document processing failed.

```typescript
{
  event_type: 'document.failed',
  workspace_id: 'workspace-123',
  document_id: 'doc-456',
  stage: 'embedding',
  timestamp: '2026-03-09T10:30:20.000Z',
  priority: 'high',
  data: {
    error_message: 'Failed to generate embeddings: API rate limit exceeded',
    stage: 'embedding'
  }
}
```

---

## REST API Endpoints

### Ingestion Status

#### `GET /ingestion/status/{document_id}`

Get ingestion status for a single document.

**Response:**

```json
{
  "document_id": "doc-456",
  "title": "My Document.pdf",
  "source_type": "file",
  "status": "indexed",
  "progress": {
    "chunks_created": 42,
    "embeddings_created": 42,
    "total_tokens": 2500
  },
  "logs": [
    {
      "stage": "download",
      "status": "completed",
      "duration_ms": 2000,
      "timestamp": "2026-03-09T10:30:01.000Z"
    },
    {
      "stage": "parse",
      "status": "completed",
      "duration_ms": 5000,
      "timestamp": "2026-03-09T10:30:06.000Z"
    },
    {
      "stage": "chunk",
      "status": "completed",
      "duration_ms": 10000,
      "timestamp": "2026-03-09T10:30:16.000Z"
    },
    {
      "stage": "index",
      "status": "completed",
      "duration_ms": 15000,
      "timestamp": "2026-03-09T10:30:31.000Z"
    }
  ],
  "created_at": "2026-03-09T10:30:00.000Z",
  "updated_at": "2026-03-09T10:30:31.000Z"
}
```

---

#### `GET /ingestion/workspace/{workspace_id}/status`

Get overall ingestion status for a workspace.

**Query Parameters:**
- `status_filter` (optional): Filter by status (pending, processing, indexed, failed)

**Response:**

```json
{
  "workspace_id": "workspace-123",
  "total_documents": 150,
  "status_breakdown": {
    "pending": 5,
    "processing": 8,
    "indexed": 132,
    "failed": 5
  },
  "aggregated_stats": {
    "total_chunks": 5430,
    "total_embeddings": 5430,
    "total_tokens": 850000,
    "average_processing_time_ms": 18500
  },
  "recent_logs": [
    {
      "document_id": "doc-456",
      "stage": "complete",
      "status": "indexed",
      "duration_ms": 28000,
      "timestamp": "2026-03-09T10:30:31.000Z"
    }
  ]
}
```

---

### Monitoring & Health

#### `GET /monitoring/health/system`

System-wide health check.

**Response:**

```json
{
  "status": "healthy",
  "components": {
    "database": "healthy",
    "redis": "healthy",
    "vector_db": "healthy"
  },
  "timestamp": "2026-03-09T10:30:31.000Z"
}
```

**Status Values:**
- `healthy`: All systems operational
- `degraded`: Some components unhealthy
- `unhealthy`: Major systems down

---

#### `GET /monitoring/health/cache`

Embeddings cache health and statistics.

**Response:**

```json
{
  "cache_type": "hybrid (L1 memory + L2 Redis)",
  "statistics": {
    "l1_size": 850,
    "l1_hits": 4250,
    "l2_hits": 1200,
    "misses": 650,
    "total_requests": 6100,
    "l1_hit_rate": 69.7,
    "l2_hit_rate": 19.7,
    "overall_hit_rate": 89.4
  },
  "status": "healthy"
}
```

---

#### `GET /monitoring/health/workers`

Celery worker health status (admin only).

**Response:**

```json
{
  "status": "healthy",
  "active_workers": 3,
  "active_tasks": 8,
  "workers": {
    "celery@worker-1": {
      "tasks_active": 3,
      "pool": {
        "implementation": "prefork",
        "max-concurrency": 4,
        "running": 3
      },
      "registered_tasks": 15
    },
    "celery@worker-2": {
      "tasks_active": 3,
      "pool": {
        "implementation": "prefork",
        "max-concurrency": 4,
        "running": 3
      },
      "registered_tasks": 15
    },
    "celery@worker-3": {
      "tasks_active": 2,
      "pool": {
        "implementation": "prefork",
        "max-concurrency": 4,
        "running": 2
      },
      "registered_tasks": 15
    }
  }
}
```

---

#### `GET /monitoring/metrics/ingestion`

Ingestion pipeline metrics.

**Response:**

```json
{
  "total_documents": 150,
  "status_breakdown": {
    "pending": 5,
    "processing": 8,
    "indexed": 132,
    "failed": 5
  },
  "documents_last_24h": 45,
  "average_processing_time_ms": 18500,
  "success_rate": 96.4
}
```

---

#### `GET /events/health`

Event system health check.

**Response:**

```json
{
  "status": "healthy",
  "redis_connected": true,
  "active_workspaces": 12,
  "total_connections": 24
}
```

---

### Dead Letter Queue (Admin Only)

#### `GET /dlq/pending`

Get pending DLQ items awaiting review.

**Query Parameters:**
- `limit` (optional): Max items to return (default: 100)

**Response:**

```json
[
  {
    "id": "dlq-789",
    "task_name": "process_document",
    "task_id": "celery-task-456",
    "document_id": "doc-123",
    "workspace_id": "workspace-123",
    "error_type": "ValueError",
    "error_message": "Invalid file format",
    "status": "pending",
    "retry_count": 3,
    "failed_at": "2026-03-09T10:25:00.000Z",
    "reviewed_at": null,
    "resolved_at": null
  }
]
```

---

#### `GET /dlq/stats`

Get DLQ statistics (admin only).

**Response:**

```json
{
  "total_failed": 45,
  "by_status": {
    "pending": 5,
    "reviewed": 10,
    "in_retry": 3,
    "resolved": 25,
    "archived": 2
  },
  "most_common_errors": [
    {
      "error_type": "TimeoutError",
      "count": 15
    },
    {
      "error_type": "ValueError",
      "count": 12
    },
    {
      "error_type": "APIError",
      "count": 10
    }
  ],
  "most_affected_tasks": [
    {
      "task_name": "process_document",
      "count": 30
    },
    {
      "task_name": "sync_connector",
      "count": 10
    }
  ]
}
```

---

## Error Handling

### WebSocket Errors

```typescript
ws.onerror = (error) => {
  console.error('WebSocket error:', error);
  // Implement fallback to SSE or retry logic
};

ws.onclose = (event) => {
  if (event.code === 1000) {
    console.log('Normal closure');
  } else {
    console.error('Unexpected closure, code:', event.code);
    // Attempt reconnection
  }
};
```

### SSE Errors

```typescript
eventSource.onerror = (error) => {
  if (eventSource.readyState === EventSource.CLOSED) {
    console.log('Connection closed');
  } else {
    console.error('SSE error:', error);
  }
};
```

### HTTP Errors

```typescript
try {
  const response = await fetch('/ingestion/status/doc-123', {
    headers: { Authorization: `Bearer ${token}` },
  });
  
  if (!response.ok) {
    const error = await response.json();
    console.error(`${response.status}: ${error.error.message}`);
  }
} catch (error) {
  console.error('Network error:', error);
}
```

---

## Rate Limiting

All endpoints are rate-limited per user:

- **Default:** 100 requests per minute
- **WebSocket:** 1 connection per workspace per user
- **SSE:** 1 connection per workspace per user

Response headers indicate rate limit status:

```
X-RateLimit-Limit: 100
X-RateLimit-Remaining: 95
X-RateLimit-Reset: 1646837440
```

When rate limited, response is `429 Too Many Requests`.

---

## Authentication

All endpoints require JWT authentication:

```typescript
// In HTTP headers
headers: {
  Authorization: `Bearer ${jwtToken}`
}

// In WebSocket query params
new WebSocket(`ws://localhost:8000/events/ws/ingestion/workspace-123?token=${jwtToken}`)
```

Token must be valid with appropriate workspace access.

---

## CORS Configuration

Production deployments configure CORS for:

- Same-origin requests
- Specified frontend domains (configurable)
- WebSocket/SSE origins

```
Access-Control-Allow-Origin: https://frontend.example.com
Access-Control-Allow-Methods: GET, POST, PUT, DELETE
Access-Control-Allow-Headers: Authorization, Content-Type
```

---

## Connection Limits

- **Max concurrent WebSocket connections per user:** 10
- **Max concurrent SSE connections per user:** 10
- **Connection timeout:** 300 seconds (5 minutes)
- **Idle timeout:** 90 seconds (auto-disconnect)

---

## Event Ordering

Events are guaranteed to be in order for a single document. For multiple documents, events may arrive out of order depending on processing speed.

Proper event sequencing for a document:
1. `document.started`
2. `stage.started` (download)
3. `stage.completed` (download)
4. `stage.started` (parse)
5. `stage.completed` (parse)
6. ... (chunking)
7. ... (embedding)
8. `document.completed` OR `document.failed`

---

## Backward Compatibility

### Event Schema Changes

New fields may be added to event payloads as `data` properties. Existing clients should:
1. Ignore unknown fields
2. Provide sensible defaults for missing optional fields
3. Not expect specific field order

### API Versioning

Future API versions will be available at:
- `/v2/ingestion/status/{document_id}`
- `/events/v2/...`

Current endpoints (`/ingestion/`, `/events/`) are v1 and will be maintained for backward compatibility.

## Additional Resources

- [Frontend Integration Guide](./FRONTEND_EVENT_INTEGRATION.md)
- [Event System Architecture](./EVENT_DRIVEN_ARCHITECTURE.md)
- [API Documentation](./API_DOCUMENTATION.md)
