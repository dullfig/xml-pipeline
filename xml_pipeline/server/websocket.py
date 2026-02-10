"""
websocket.py — WebSocket endpoints for AgentServer.

Provides:
- /ws — Main control channel with state snapshot and real-time events
- /ws/messages — Dedicated message log stream with filtering
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from xml_pipeline.server.models import (
    SubscribeRequest,
    WSConnectedEvent,
)

if TYPE_CHECKING:
    from xml_pipeline.server.state import ServerState

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Connection limits
# ---------------------------------------------------------------------------

MAX_CONNECTIONS = 50       # Total across both endpoints
MAX_PER_IP = 10            # Per remote IP across both endpoints
IDLE_TIMEOUT = 300.0       # 5 minutes with no client message → disconnect


class ConnectionLimiter:
    """Shared admission control for all WebSocket endpoints.

    Tracks total connections and per-IP counts. Both ``ConnectionManager``
    and ``MessageStreamManager`` register through this limiter so limits
    are enforced globally.
    """

    def __init__(
        self,
        max_total: int = MAX_CONNECTIONS,
        max_per_ip: int = MAX_PER_IP,
    ) -> None:
        self.max_total = max_total
        self.max_per_ip = max_per_ip
        self._connections: Set[WebSocket] = set()
        self._ip_counts: Dict[str, int] = defaultdict(int)

    def _client_ip(self, websocket: WebSocket) -> str:
        """Extract remote IP from the WebSocket."""
        if websocket.client:
            return websocket.client.host
        return "unknown"

    def admit(self, websocket: WebSocket) -> Optional[str]:
        """Check if a new connection should be admitted.

        Returns None if OK, or an error reason string if rejected.
        """
        if len(self._connections) >= self.max_total:
            return f"max connections reached ({self.max_total})"

        ip = self._client_ip(websocket)
        if self._ip_counts[ip] >= self.max_per_ip:
            return f"per-IP limit reached ({self.max_per_ip})"

        return None

    def register(self, websocket: WebSocket) -> None:
        """Track a newly accepted connection."""
        self._connections.add(websocket)
        ip = self._client_ip(websocket)
        self._ip_counts[ip] += 1

    def unregister(self, websocket: WebSocket) -> None:
        """Remove a closed connection from tracking."""
        self._connections.discard(websocket)
        ip = self._client_ip(websocket)
        self._ip_counts[ip] = max(0, self._ip_counts[ip] - 1)
        if self._ip_counts[ip] == 0:
            self._ip_counts.pop(ip, None)

    @property
    def total(self) -> int:
        return len(self._connections)


class ConnectionManager:
    """Manages WebSocket connections and subscriptions."""

    def __init__(self, limiter: ConnectionLimiter) -> None:
        self.active_connections: Set[WebSocket] = set()
        self.subscriptions: Dict[WebSocket, SubscribeRequest] = {}
        self._limiter = limiter

    async def connect(self, websocket: WebSocket) -> bool:
        """Accept a new WebSocket connection.

        Returns True if admitted, False if rejected (connection closed).
        """
        reason = self._limiter.admit(websocket)
        if reason:
            logger.warning(f"WebSocket /ws rejected: {reason}")
            await websocket.close(code=1013, reason=reason)
            return False
        await websocket.accept()
        self.active_connections.add(websocket)
        self._limiter.register(websocket)
        self.subscriptions[websocket] = SubscribeRequest()  # Default: all events
        return True

    def disconnect(self, websocket: WebSocket) -> None:
        """Remove a WebSocket connection."""
        if websocket in self.active_connections:
            self.active_connections.discard(websocket)
            self._limiter.unregister(websocket)
        self.subscriptions.pop(websocket, None)

    def set_subscription(self, websocket: WebSocket, sub: SubscribeRequest) -> None:
        """Update subscription filters for a connection."""
        self.subscriptions[websocket] = sub

    def should_send(self, websocket: WebSocket, event: Dict[str, Any]) -> bool:
        """Check if an event should be sent to this connection based on filters."""
        sub = self.subscriptions.get(websocket)
        if sub is None:
            return True  # No filters = send all

        event_type = event.get("event", "")

        # Filter by event type
        if sub.events and event_type not in sub.events:
            return False

        # Filter by thread
        thread_id = event.get("thread_id") or event.get("threadId")
        if sub.threads and thread_id and thread_id not in sub.threads:
            return False

        # Filter by agent
        agent = event.get("agent")
        if sub.agents and agent and agent not in sub.agents:
            return False

        # For message events, check from/to
        if event_type == "message":
            msg = event.get("message", {})
            from_id = msg.get("from") or msg.get("fromId")
            to_id = msg.get("to") or msg.get("toId")
            if sub.agents:
                if from_id not in sub.agents and to_id not in sub.agents:
                    return False

        # Filter by payload type
        if sub.payload_types:
            payload_type = None
            if event_type == "message":
                payload_type = event.get("message", {}).get("payloadType")
            if payload_type and payload_type not in sub.payload_types:
                return False

        return True

    async def broadcast(self, event: Dict[str, Any]) -> None:
        """Broadcast an event to all connections that match their subscription."""
        disconnected: List[WebSocket] = []

        for websocket in self.active_connections:
            if self.should_send(websocket, event):
                try:
                    await websocket.send_json(event)
                except Exception:
                    disconnected.append(websocket)

        # Clean up disconnected clients
        for ws in disconnected:
            self.disconnect(ws)


class MessageStreamManager:
    """Manages WebSocket connections for /ws/messages endpoint."""

    def __init__(self, limiter: ConnectionLimiter) -> None:
        self.active_connections: Set[WebSocket] = set()
        self.filters: Dict[WebSocket, Dict[str, Any]] = {}
        self._limiter = limiter

    async def connect(self, websocket: WebSocket) -> bool:
        """Accept a new WebSocket connection.

        Returns True if admitted, False if rejected (connection closed).
        """
        reason = self._limiter.admit(websocket)
        if reason:
            logger.warning(f"WebSocket /ws/messages rejected: {reason}")
            await websocket.close(code=1013, reason=reason)
            return False
        await websocket.accept()
        self.active_connections.add(websocket)
        self._limiter.register(websocket)
        self.filters[websocket] = {}  # Default: all messages
        return True

    def disconnect(self, websocket: WebSocket) -> None:
        """Remove a WebSocket connection."""
        if websocket in self.active_connections:
            self.active_connections.discard(websocket)
            self._limiter.unregister(websocket)
        self.filters.pop(websocket, None)

    def set_filter(self, websocket: WebSocket, filter_config: Dict[str, Any]) -> None:
        """Update message filter for a connection."""
        self.filters[websocket] = filter_config

    def should_send(self, websocket: WebSocket, message: Dict[str, Any]) -> bool:
        """Check if a message should be sent to this connection."""
        flt = self.filters.get(websocket, {})
        if not flt:
            return True

        # Filter by agents
        agents = flt.get("agents", [])
        if agents:
            from_id = message.get("from") or message.get("fromId")
            to_id = message.get("to") or message.get("toId")
            if from_id not in agents and to_id not in agents:
                return False

        # Filter by threads
        threads = flt.get("threads", [])
        if threads:
            thread_id = message.get("thread_id") or message.get("threadId")
            if thread_id not in threads:
                return False

        # Filter by payload types
        payload_types = flt.get("payload_types", [])
        if payload_types:
            payload_type = message.get("payloadType") or message.get("payload_type")
            if payload_type not in payload_types:
                return False

        return True

    async def broadcast_message(self, message: Dict[str, Any]) -> None:
        """Broadcast a message to all filtered connections."""
        disconnected: List[WebSocket] = []

        for websocket in self.active_connections:
            if self.should_send(websocket, message):
                try:
                    await websocket.send_json(message)
                except Exception:
                    disconnected.append(websocket)

        for ws in disconnected:
            self.disconnect(ws)


def create_websocket_router(state: "ServerState") -> APIRouter:
    """Create WebSocket router with state dependency."""
    router = APIRouter()
    limiter = ConnectionLimiter()
    manager = ConnectionManager(limiter)
    message_manager = MessageStreamManager(limiter)

    # Subscribe state to WebSocket broadcasting
    async def on_state_event(event: Dict[str, Any]) -> None:
        """Forward state events to WebSocket connections."""
        await manager.broadcast(event)

        # Also forward message events to the message stream
        if event.get("event") == "message":
            msg = event.get("message", {})
            await message_manager.broadcast_message(msg)

    state.subscribe(on_state_event)

    @router.websocket("/ws")
    async def websocket_control(websocket: WebSocket) -> None:
        """
        Main control channel WebSocket.

        On connect, sends full state snapshot. Then pushes events as they occur.
        Accepts commands: subscribe, inject.
        """
        if not await manager.connect(websocket):
            return

        try:
            # Send connected event with state snapshot
            threads, _ = state.get_threads(limit=100)
            connected_event = WSConnectedEvent(
                organism=state.get_organism_info(),
                agents=state.get_agents(),
                threads=threads,
            )
            await websocket.send_json(connected_event.model_dump(by_alias=True))

            # Listen for commands
            while True:
                try:
                    data = await asyncio.wait_for(
                        websocket.receive_json(),
                        timeout=IDLE_TIMEOUT,
                    )
                except asyncio.TimeoutError:
                    logger.info("WebSocket /ws idle timeout — disconnecting")
                    break
                except Exception:
                    # Connection closed or invalid data
                    break

                cmd = data.get("cmd", "")

                if cmd == "subscribe":
                    # Update subscription filters
                    sub = SubscribeRequest(
                        threads=data.get("threads"),
                        agents=data.get("agents"),
                        payload_types=data.get("payload_types"),
                        events=data.get("events"),
                    )
                    manager.set_subscription(websocket, sub)
                    await websocket.send_json({"event": "subscribed", "filters": data})

                elif cmd == "inject":
                    # Inject a message (same as REST /inject)
                    # TODO: Actual pump injection requires resolving the
                    # payload class from the JSON target name, which needs
                    # listener registry lookup + dynamic @xmlify instantiation.
                    # Currently records the message in state (useful for UI)
                    # but does not call pump.inject().
                    target = data.get("to")
                    payload = data.get("payload", {})
                    thread_id = data.get("thread_id")

                    if not target:
                        await websocket.send_json(
                            {"event": "error", "error": "Missing 'to' field"}
                        )
                        continue

                    agent = state.get_agent(target)
                    if agent is None:
                        await websocket.send_json(
                            {"event": "error", "error": f"Unknown agent: {target}"}
                        )
                        continue

                    import uuid

                    thread_id = thread_id or str(uuid.uuid4())
                    payload_type = next(iter(payload.keys()), "Payload")

                    msg_id = await state.record_message(
                        thread_id=thread_id,
                        from_id="api",
                        to_id=target,
                        payload_type=payload_type,
                        payload=payload,
                    )

                    await websocket.send_json(
                        {
                            "event": "injected",
                            "thread_id": thread_id,
                            "message_id": msg_id,
                        }
                    )

                else:
                    await websocket.send_json(
                        {"event": "error", "error": f"Unknown command: {cmd}"}
                    )

        except WebSocketDisconnect:
            pass
        finally:
            manager.disconnect(websocket)

    @router.websocket("/ws/messages")
    async def websocket_messages(websocket: WebSocket) -> None:
        """
        Dedicated message log stream.

        Streams all messages flowing through the organism.
        Clients can filter by agents, threads, and payload types.
        """
        if not await message_manager.connect(websocket):
            return

        try:
            while True:
                try:
                    data = await asyncio.wait_for(
                        websocket.receive_json(),
                        timeout=IDLE_TIMEOUT,
                    )
                except asyncio.TimeoutError:
                    logger.info("WebSocket /ws/messages idle timeout — disconnecting")
                    break
                except Exception:
                    break

                cmd = data.get("cmd", "")

                if cmd == "subscribe":
                    # Update filter
                    filter_config = data.get("filter", {})
                    message_manager.set_filter(websocket, filter_config)
                    await websocket.send_json({"event": "subscribed", "filter": filter_config})

                else:
                    await websocket.send_json(
                        {"event": "error", "error": f"Unknown command: {cmd}"}
                    )

        except WebSocketDisconnect:
            pass
        finally:
            message_manager.disconnect(websocket)

    return router
