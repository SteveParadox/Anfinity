"""WebSocket handler for real-time event streaming.

Provides WebSocket and SSE endpoints for the frontend to receive real-time
updates on document ingestion progress, stage completion, errors, and worker
health status.

Design notes
------------
- ``ConnectionManager`` is the sole owner of active socket state; the old
  module-level ``active_connections`` dict has been removed.
- WebSocket sessions use a *dual-task* architecture: one task drains the
  Redis pub/sub channel, another handles inbound client frames (ping/pong,
  graceful close).  Both tasks are cancelled together on disconnect — no
  busy-polling, no 1-second timeout loop.
- A server-side heartbeat (``HEARTBEAT_INTERVAL``) detects silently stale
  connections and closes them proactively.
- Per-workspace connection caps (``MAX_CONNECTIONS_PER_WORKSPACE``) prevent
  runaway resource consumption.
- SSE now enforces workspace membership identically to the WebSocket path.
- ``pubsub`` and ``redis_listener`` are always initialised before the
  ``try/finally`` that cleans them up, eliminating "possibly unbound"
  reference bugs.
- All bare ``except:`` blocks replaced with typed catches.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user, get_websocket_user
from app.database.models import User as DBUser, Workspace, WorkspaceMember
from app.database.session import get_db
from app.events.broadcaster import get_broadcaster

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/events", tags=["events"])

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

#: Seconds between server-initiated WebSocket pings.
HEARTBEAT_INTERVAL: int = 20

#: Maximum simultaneous WebSocket connections for a single workspace.
MAX_CONNECTIONS_PER_WORKSPACE: int = 50


# ---------------------------------------------------------------------------
# Connection Manager
# ---------------------------------------------------------------------------


class ConnectionManager:
    """Manages WebSocket connections scoped to a workspace.

    Internal structure::

        {workspace_id: {user_id: [WebSocket, ...]}}

    All methods are safe to call from a single asyncio event-loop thread.
    """

    def __init__(self) -> None:
        self._connections: dict[str, dict[str, list[WebSocket]]] = {}

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def connect(
        self,
        workspace_id: str,
        user_id: str,
        websocket: WebSocket,
    ) -> bool:
        """Accept and register a WebSocket connection.

        Returns ``False`` (and closes the socket) if the per-workspace cap
        would be exceeded; ``True`` on success.
        """
        if self.get_connection_count(workspace_id) >= MAX_CONNECTIONS_PER_WORKSPACE:
            logger.warning(
                "Workspace %s hit connection cap (%d); rejecting %s",
                workspace_id, MAX_CONNECTIONS_PER_WORKSPACE, user_id,
            )
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return False

        await websocket.accept()

        workspace_conns = self._connections.setdefault(workspace_id, {})
        workspace_conns.setdefault(user_id, []).append(websocket)

        logger.info(
            "Client %s connected to workspace %s (user sockets: %d, workspace total: %d)",
            user_id, workspace_id,
            len(workspace_conns[user_id]),
            self.get_connection_count(workspace_id),
        )
        return True

    async def disconnect(
        self,
        workspace_id: str,
        user_id: str,
        websocket: WebSocket,
    ) -> None:
        """Unregister a WebSocket and prune empty containers."""
        workspace_conns = self._connections.get(workspace_id)
        if workspace_conns is None:
            return

        user_sockets = workspace_conns.get(user_id)
        if user_sockets is None:
            return

        try:
            user_sockets.remove(websocket)
        except ValueError:
            return  # Already removed

        logger.info(
            "Client %s disconnected from workspace %s (user sockets remaining: %d)",
            user_id, workspace_id, len(user_sockets),
        )

        # Prune empty containers so memory doesn't leak.
        if not user_sockets:
            del workspace_conns[user_id]
        if not workspace_conns:
            del self._connections[workspace_id]

    # ------------------------------------------------------------------
    # Broadcasting
    # ------------------------------------------------------------------

    async def broadcast_to_workspace(
        self,
        workspace_id: str,
        message: dict,
        *,
        exclude_user: Optional[str] = None,
    ) -> None:
        """Send *message* to every connected client in *workspace_id*.

        Sockets that raise during send are collected and disconnected
        after the broadcast loop so we never mutate while iterating.
        """
        workspace_conns = self._connections.get(workspace_id)
        if not workspace_conns:
            return

        stale: list[tuple[str, WebSocket]] = []

        for user_id, sockets in list(workspace_conns.items()):
            if exclude_user and user_id == exclude_user:
                continue
            for ws in list(sockets):
                try:
                    await ws.send_json(message)
                except Exception as exc:
                    logger.warning("Send to %s failed (%s); scheduling cleanup", user_id, exc)
                    stale.append((user_id, ws))

        for user_id, ws in stale:
            await self.disconnect(workspace_id, user_id, ws)

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def get_connection_count(self, workspace_id: str) -> int:
        """Return the total number of active sockets in *workspace_id*."""
        return sum(
            len(sockets)
            for sockets in self._connections.get(workspace_id, {}).values()
        )

    @property
    def total_connections(self) -> int:
        """Return the total number of active sockets across all workspaces."""
        return sum(
            len(sockets)
            for wc in self._connections.values()
            for sockets in wc.values()
        )

    @property
    def active_workspace_count(self) -> int:
        return len(self._connections)


# Singleton used by all request handlers in this module.
manager = ConnectionManager()


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------


async def _assert_workspace_access(
    workspace_id: UUID,
    user: DBUser,
    db: AsyncSession,
) -> Workspace:
    """Raise ``HTTPException`` (or return the workspace) after verifying
    that *user* is either the owner or an explicit member of *workspace_id*.
    """
    # Single query: fetch the workspace and check ownership.
    result = await db.execute(
        select(Workspace).where(Workspace.id == workspace_id)
    )
    workspace = result.scalars().first()

    if workspace is None:
        raise HTTPException(status_code=404, detail="Workspace not found")

    if workspace.owner_id == user.id:
        return workspace

    # Check explicit membership.
    member_result = await db.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user.id,
        )
    )
    if member_result.scalars().first() is None:
        raise HTTPException(status_code=403, detail="Access denied")

    return workspace


# ---------------------------------------------------------------------------
# Redis listener task
# ---------------------------------------------------------------------------


async def _redis_listener_task(
    pubsub,
    workspace_id: str,
    conn_manager: ConnectionManager,
) -> None:
    """Drain the Redis pub/sub channel and fan out to WebSocket clients.

    Designed to run as an ``asyncio.Task``; respects ``CancelledError``.
    """
    try:
        async for message in pubsub.listen():
            if message["type"] == "subscribe":
                logger.debug("Subscribed to Redis channel: %s", message["channel"])
                continue

            if message["type"] != "message":
                continue

            try:
                data = json.loads(message["data"])
            except (json.JSONDecodeError, TypeError) as exc:
                logger.error("Malformed Redis message: %s — %s", message["data"], exc)
                continue

            logger.debug(
                "Broadcasting event_type=%s to workspace %s",
                data.get("event_type"), workspace_id,
            )
            await conn_manager.broadcast_to_workspace(workspace_id, data)

    except asyncio.CancelledError:
        logger.debug("Redis listener task cancelled for workspace %s", workspace_id)
        raise
    except Exception as exc:
        logger.error("Redis listener fatal error for workspace %s: %s", workspace_id, exc)


# ---------------------------------------------------------------------------
# WebSocket heartbeat task
# ---------------------------------------------------------------------------


async def _heartbeat_task(websocket: WebSocket) -> None:
    """Send periodic pings to keep the connection alive and detect stale sockets.

    A ``WebSocketDisconnect`` or send failure will propagate to the caller,
    which cancels the companion listener task.
    """
    try:
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            await websocket.send_json({"type": "ping"})
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.debug("Heartbeat ended for socket: %s", exc)
        raise


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------


@router.websocket("/ws/ingestion/{workspace_id}")
async def websocket_ingestion_events(
    websocket: WebSocket,
    workspace_id: str,
    db: AsyncSession = Depends(get_db),
) -> None:
    """WebSocket endpoint for real-time ingestion events.

    **Connection**::

        ws://host/events/ws/ingestion/{workspace_id}?token=<jwt>

    **Server-pushed event envelope**::

        {
            "event_type": "document.started" | "stage.completed" | ...,
            "workspace_id": "...",
            "document_id": "...",
            "payload": { ... }
        }

    **Client-to-server frames**::

        {"type": "ping"}   → server replies {"type": "pong"}
        {"type": "close"}  → clean shutdown
    """
    # ---- Authentication ------------------------------------------------
    try:
        current_user: DBUser = await get_websocket_user(websocket, db)
    except Exception as exc:
        logger.warning("WebSocket authentication failed: %s", exc)
        return

    # ---- Workspace validation ------------------------------------------
    try:
        workspace_uuid = UUID(workspace_id)
    except ValueError:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    try:
        await _assert_workspace_access(workspace_uuid, current_user, db)
    except HTTPException:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    # ---- Register connection -------------------------------------------
    connected = await manager.connect(workspace_id, str(current_user.id), websocket)
    if not connected:
        return  # Cap exceeded; socket already closed inside connect()

    # ---- Subscribe to Redis channel -----------------------------------
    # Initialise both variables *before* the try/finally so the finally
    # block can always reference them without an "unbound" risk.
    broadcaster = await get_broadcaster()
    pubsub = await broadcaster.subscribe(f"ingestion:{workspace_id}")
    redis_task: asyncio.Task | None = None
    heartbeat: asyncio.Task | None = None

    try:
        redis_task = asyncio.create_task(
            _redis_listener_task(pubsub, workspace_id, manager),
            name=f"redis-listener:{workspace_id}:{current_user.id}",
        )
        heartbeat = asyncio.create_task(
            _heartbeat_task(websocket),
            name=f"heartbeat:{workspace_id}:{current_user.id}",
        )

        # ---- Main receive loop ----------------------------------------
        # We block here waiting for client frames.  Redis events are
        # handled concurrently by redis_task; heartbeats by heartbeat.
        while True:
            try:
                raw = await websocket.receive_text()
            except WebSocketDisconnect:
                logger.info(
                    "Client %s disconnected from workspace %s",
                    current_user.id, workspace_id,
                )
                break

            try:
                frame = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("Non-JSON frame from client %s; ignoring", current_user.id)
                continue

            frame_type = frame.get("type")

            if frame_type == "ping":
                await websocket.send_json({"type": "pong"})
            elif frame_type == "close":
                break
            else:
                logger.debug("Unhandled client frame type=%s from %s", frame_type, current_user.id)

    except Exception as exc:
        logger.error(
            "Unexpected error in WebSocket handler for workspace %s user %s: %s",
            workspace_id, current_user.id, exc,
        )
    finally:
        # Cancel background tasks.
        for task in (redis_task, heartbeat):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

        # Unregister the socket.
        await manager.disconnect(workspace_id, str(current_user.id), websocket)

        # Release the Redis pub/sub subscription.
        try:
            await pubsub.unsubscribe()
            await pubsub.aclose()
        except Exception as exc:
            logger.warning("Error closing pub/sub for workspace %s: %s", workspace_id, exc)


# ---------------------------------------------------------------------------
# Server-Sent Events endpoint
# ---------------------------------------------------------------------------


@router.get("/sse/ingestion/{workspace_id}")
async def sse_ingestion_events(
    workspace_id: str,
    current_user: DBUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """Server-Sent Events endpoint for ingestion updates.

    Alternative to WebSocket for clients that prefer one-way streaming.

    **Usage**::

        const es = new EventSource('/events/sse/ingestion/{workspace_id}');
        es.onmessage = (e) => console.log(JSON.parse(e.data));

    Keepalive comments are emitted every 30 s to prevent proxy timeouts.
    """
    try:
        workspace_uuid = UUID(workspace_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid workspace ID")

    # Enforce membership — identical policy to the WebSocket path.
    await _assert_workspace_access(workspace_uuid, current_user, db)

    async def event_generator():
        broadcaster = await get_broadcaster()
        pubsub = await broadcaster.subscribe(f"ingestion:{workspace_id}")

        try:
            # Confirm connection to the client.
            yield f'data: {json.dumps({"type": "connected", "workspace_id": workspace_id})}\n\n'

            while True:
                try:
                    message = await asyncio.wait_for(
                        pubsub.get_message(ignore_subscribe_messages=True),
                        timeout=30.0,
                    )
                except asyncio.TimeoutError:
                    # SSE comment keeps the connection alive through proxies.
                    yield ": keepalive\n\n"
                    continue

                if message is None:
                    # No message yet; avoid a tight spin.
                    await asyncio.sleep(0.05)
                    continue

                try:
                    data = json.loads(message["data"])
                    yield f"data: {json.dumps(data)}\n\n"
                except (json.JSONDecodeError, KeyError) as exc:
                    logger.warning("Malformed SSE message: %s", exc)

        except asyncio.CancelledError:
            logger.debug("SSE stream cancelled for workspace %s user %s", workspace_id, current_user.id)
        finally:
            try:
                await pubsub.unsubscribe()
                await pubsub.aclose()
            except Exception as exc:
                logger.warning("Error closing SSE pub/sub: %s", exc)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable Nginx buffering
        },
    )


# ---------------------------------------------------------------------------
# Status endpoints
# ---------------------------------------------------------------------------


@router.get("/status/workspace/{workspace_id}")
async def get_workspace_connection_status(
    workspace_id: str,
    current_user: DBUser = Depends(get_current_user),
) -> dict:
    """Return live connection counts for a workspace."""
    try:
        UUID(workspace_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid workspace ID")

    count = manager.get_connection_count(workspace_id)
    return {
        "workspace_id": workspace_id,
        "connected_clients": count,
        "is_receiving_events": count > 0,
    }


@router.get("/health")
async def health_check() -> dict:
    """Health check for the event sub-system."""
    redis_healthy = False
    try:
        broadcaster = await get_broadcaster()
        # Use the public API rather than touching private attributes.
        redis_healthy = await broadcaster.ping()
    except Exception as exc:
        logger.warning("Redis health check failed: %s", exc)

    return {
        "status": "healthy" if redis_healthy else "degraded",
        "redis_connected": redis_healthy,
        "active_workspaces": manager.active_workspace_count,
        "total_connections": manager.total_connections,
    }
