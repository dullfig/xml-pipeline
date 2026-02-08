"""
server.py — OOB privileged WebSocket server.

Runs on a separate localhost-only port. Accepts signed <privileged-msg>
requests, dispatches to handlers, and pushes events to subscribers.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from xml_pipeline.oob.auth import OOBAuthError, parse_request, verify_request
from xml_pipeline.oob.handlers import ASYNC_HANDLERS, SYNC_HANDLERS
from xml_pipeline.oob.protocol import build_error, build_system_push, build_thread_push

if TYPE_CHECKING:
    import websockets

    from xml_pipeline.crypto.identity import Identity
    from xml_pipeline.message_bus.stream_pump import PumpEvent, StreamPump

logger = logging.getLogger(__name__)


@dataclass
class Subscription:
    """A push event subscription from an OOB client."""
    subscription_id: str
    websocket: Any  # websockets.WebSocketServerProtocol
    thread_id: str | None = None  # None = all threads
    listener_filter: str | None = None
    include_payloads: bool = False


class OOBServer:
    """
    Out-of-band privileged WebSocket server.

    Runs on a separate localhost-only port from the main message bus.
    Handles privileged commands (register/unregister listeners, inject
    messages, introspect state, shutdown) and pushes events to subscribers.
    """

    def __init__(
        self,
        pump: StreamPump,
        *,
        bind: str = "127.0.0.1",
        port: int = 8766,
        identity: Identity | None = None,
    ) -> None:
        self.pump = pump
        self.bind = bind
        self.port = port
        self.identity = identity

        self._server: Any = None  # websockets.WebSocketServer
        self._clients: set[Any] = set()
        self._subscriptions: dict[str, Subscription] = {}

    async def start(self) -> None:
        """Start the OOB WebSocket server."""
        try:
            import websockets
        except ImportError:
            logger.warning(
                "websockets not installed — OOB channel disabled. "
                "Install with: pip install xml-pipeline[server]"
            )
            return

        self._server = await websockets.serve(
            self._connection_handler,
            self.bind,
            self.port,
        )

        # Subscribe to pump events for push delivery
        self.pump.subscribe_events(self._on_pump_event)

        logger.info(f"OOB server listening on ws://{self.bind}:{self.port}")

    async def stop(self) -> None:
        """Stop the OOB WebSocket server."""
        self.pump.unsubscribe_events(self._on_pump_event)

        # Close all client connections
        if self._clients:
            close_tasks = []
            for ws in list(self._clients):
                close_tasks.append(asyncio.ensure_future(ws.close()))
            if close_tasks:
                await asyncio.gather(*close_tasks, return_exceptions=True)
            self._clients.clear()

        # Clean up subscriptions
        self._subscriptions.clear()

        # Stop the server
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

        logger.info("OOB server stopped")

    async def _connection_handler(self, websocket: Any) -> None:
        """Handle a single WebSocket connection."""
        self._clients.add(websocket)
        remote = websocket.remote_address if hasattr(websocket, "remote_address") else "unknown"
        logger.debug(f"OOB client connected: {remote}")

        try:
            async for raw_message in websocket:
                if isinstance(raw_message, str):
                    raw_message = raw_message.encode("utf-8")
                try:
                    response = await self._dispatch(raw_message, websocket)
                    await websocket.send(response)
                except Exception as e:
                    logger.error(f"OOB dispatch error: {e}")
                    error_response = build_error("", "INTERNAL_ERROR", str(e))
                    await websocket.send(error_response)
        except Exception:
            pass  # Connection closed
        finally:
            self._clients.discard(websocket)
            # Clean up subscriptions for this client
            to_remove = [
                sid for sid, sub in self._subscriptions.items()
                if sub.websocket is websocket
            ]
            for sid in to_remove:
                del self._subscriptions[sid]
            logger.debug(f"OOB client disconnected: {remote}")

    async def _dispatch(self, raw_xml: bytes, websocket: Any) -> bytes:
        """Parse, verify, and dispatch a privileged request."""
        # Verify signature
        try:
            verify_request(raw_xml, self.identity)
        except OOBAuthError as e:
            logger.warning(f"OOB auth failed: {e}")
            return build_error("", "INVALID_SIGNATURE", str(e))

        # Parse command
        try:
            command_name, command_el, request_id = parse_request(raw_xml)
        except OOBAuthError as e:
            return build_error("", "PARSE_ERROR", str(e))

        logger.debug(f"OOB command: {command_name} (id={request_id})")

        # Dispatch to handler
        if command_name in ASYNC_HANDLERS:
            result = await ASYNC_HANDLERS[command_name](self.pump, command_el, request_id)
        elif command_name in SYNC_HANDLERS:
            result = SYNC_HANDLERS[command_name](self.pump, command_el, request_id)
        else:
            return build_error(
                request_id, "UNKNOWN_COMMAND", f"Unknown command: {command_name}"
            )

        # Handle special returns (subscribe-thread returns tuple)
        if isinstance(result, tuple):
            response_bytes, subscription_id = result
            # Store subscription
            from xml_pipeline.oob.handlers import _text, _bool
            thread_filter = _text(command_el, "thread-id") or None
            listener_filter = _text(command_el, "listener") or None
            include_payloads = _bool(command_el, "include-payloads")

            self._subscriptions[subscription_id] = Subscription(
                subscription_id=subscription_id,
                websocket=websocket,
                thread_id=thread_filter,
                listener_filter=listener_filter,
                include_payloads=include_payloads,
            )
            return response_bytes

        # Handle unsubscribe — remove subscription
        if command_name == "unsubscribe":
            from xml_pipeline.oob.handlers import _text
            sid = _text(command_el, "subscription-id")
            if sid in self._subscriptions:
                del self._subscriptions[sid]

        return result

    def _on_pump_event(self, event: PumpEvent) -> None:
        """Bridge pump events to OOB push delivery (non-blocking)."""
        if not self._subscriptions:
            return

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self._deliver_push(event))
        except RuntimeError:
            pass  # No event loop

    async def _deliver_push(self, event: PumpEvent) -> None:
        """Convert a PumpEvent to push XML and send to matching subscribers."""
        from xml_pipeline.message_bus.stream_pump import (
            MessageReceivedEvent,
            MessageSentEvent,
            AgentStateEvent,
            ThreadEvent,
            ReloadEvent,
        )

        for sub in list(self._subscriptions.values()):
            push_bytes = None

            if isinstance(event, (MessageReceivedEvent, MessageSentEvent)):
                # Check thread filter
                if sub.thread_id and event.thread_id != sub.thread_id:
                    continue
                # Check listener filter
                if sub.listener_filter:
                    if (event.to_id != sub.listener_filter and
                            event.from_id != sub.listener_filter):
                        continue
                evt_type = "message"
                push_bytes = build_thread_push(
                    thread_id=event.thread_id,
                    event_type=evt_type,
                    from_id=event.from_id,
                    to_id=event.to_id,
                    payload_type=event.payload_type,
                    subscription_id=sub.subscription_id,
                )

            elif isinstance(event, ThreadEvent):
                if sub.thread_id and event.thread_id != sub.thread_id:
                    continue
                push_bytes = build_thread_push(
                    thread_id=event.thread_id,
                    event_type=event.status,
                    subscription_id=sub.subscription_id,
                )

            elif isinstance(event, ReloadEvent):
                details_parts = []
                if event.added_listeners:
                    details_parts.append(f"added: {', '.join(event.added_listeners)}")
                if event.removed_listeners:
                    details_parts.append(f"removed: {', '.join(event.removed_listeners)}")
                push_bytes = build_system_push(
                    event_type="config-changed",
                    message="Configuration reloaded" if event.success else f"Reload failed: {event.error}",
                    details="; ".join(details_parts),
                    subscription_id=sub.subscription_id,
                )

            if push_bytes:
                try:
                    await sub.websocket.send(push_bytes)
                except Exception:
                    # Client disconnected — clean up
                    self._subscriptions.pop(sub.subscription_id, None)
