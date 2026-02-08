"""
websocket.py — WebSocket endpoints for AgentServer.

Provides:
- /ws — Main control channel with state snapshot and real-time events
- /ws/messages — Dedicated message log stream with filtering
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from xml_pipeline.server.models import (
    SubscribeRequest,
    WSConnectedEvent,
)

if TYPE_CHECKING:
    from xml_pipeline.server.state import ServerState


class ConnectionManager:
    """Manages WebSocket connections and subscriptions."""

    def __init__(self) -> None:
        self.active_connections: Set[WebSocket] = set()
        self.subscriptions: Dict[WebSocket, SubscribeRequest] = {}

    async def connect(self, websocket: WebSocket) -> None:
        """Accept a new WebSocket connection."""
        await websocket.accept()
        self.active_connections.add(websocket)
        self.subscriptions[websocket] = SubscribeRequest()  # Default: all events

    def disconnect(self, websocket: WebSocket) -> None:
        """Remove a WebSocket connection."""
        self.active_connections.discard(websocket)
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

    def __init__(self) -> None:
        self.active_connections: Set[WebSocket] = set()
        self.filters: Dict[WebSocket, Dict[str, Any]] = {}

    async def connect(self, websocket: WebSocket) -> None:
        """Accept a new WebSocket connection."""
        await websocket.accept()
        self.active_connections.add(websocket)
        self.filters[websocket] = {}  # Default: all messages

    def disconnect(self, websocket: WebSocket) -> None:
        """Remove a WebSocket connection."""
        self.active_connections.discard(websocket)
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
    manager = ConnectionManager()
    message_manager = MessageStreamManager()

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
        await manager.connect(websocket)

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
                    data = await websocket.receive_json()
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
        await message_manager.connect(websocket)

        try:
            while True:
                try:
                    data = await websocket.receive_json()
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
