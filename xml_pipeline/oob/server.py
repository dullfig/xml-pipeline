"""
server.py — OOB privileged WebSocket server.

Runs on a separate localhost-only port. Accepts signed <privileged-msg>
requests, dispatches to handlers, and pushes events to subscribers.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from xml_pipeline.oob.auth import OOBAuthError, parse_request, verify_request
from xml_pipeline.oob.handlers import ASYNC_HANDLERS, SYNC_HANDLERS
from xml_pipeline.oob.protocol import build_error, build_system_push, build_thread_push

if TYPE_CHECKING:
    import websockets

    from xml_pipeline.crypto.identity import Identity
    from xml_pipeline.message_bus.events import PumpEvent
    from xml_pipeline.message_bus.stream_pump import StreamPump

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

    Supports optional TOTP authentication as a second factor alongside
    Ed25519 signing. When ``totp_secret`` is set, the first message on
    each connection must be a ``<totp-auth>`` command with a valid token.
    """

    def __init__(
        self,
        pump: StreamPump,
        *,
        bind: str = "127.0.0.1",
        port: int = 8766,
        identity: Identity | None = None,
        totp_secret: str | None = None,
    ) -> None:
        self.pump = pump
        self.bind = bind
        self.port = port
        self.identity = identity
        self.totp_secret = totp_secret

        self._server: Any = None  # websockets.WebSocketServer
        self._clients: set[Any] = set()
        self._subscriptions: dict[str, Subscription] = {}
        self._authenticated: set[int] = set()  # id(websocket) for TOTP-authenticated connections
        self._auth_lock = asyncio.Lock()  # Serializes TOTP check-and-set

        # TOTP brute-force protection: track failed attempts per remote address
        self._totp_failures: dict[str, list[float]] = defaultdict(list)
        self._TOTP_MAX_FAILURES = 5  # Max failures per window
        self._TOTP_WINDOW_SECONDS = 300.0  # 5-minute window

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
            max_size=1_048_576,  # 1 MB max message size
            max_queue=64,  # Limit pending messages per connection
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

                # TOTP gate: first message must be totp-auth when secret is set
                if self.totp_secret and id(websocket) not in self._authenticated:
                    async with self._auth_lock:
                        # Re-check after acquiring lock (another coroutine may have authed)
                        if id(websocket) in self._authenticated:
                            pass  # Already authed — fall through to dispatch
                        else:
                            # Rate-limit TOTP attempts per remote address
                            remote_key = str(
                                websocket.remote_address
                                if hasattr(websocket, "remote_address")
                                else "unknown"
                            )
                            now = time.monotonic()
                            # Prune old failures outside the window
                            self._totp_failures[remote_key] = [
                                t for t in self._totp_failures[remote_key]
                                if now - t < self._TOTP_WINDOW_SECONDS
                            ]
                            if len(self._totp_failures[remote_key]) >= self._TOTP_MAX_FAILURES:
                                logger.warning(
                                    f"TOTP rate limit exceeded for {remote_key}"
                                )
                                error_response = build_error(
                                    "", "RATE_LIMITED",
                                    "Too many failed attempts, try again later"
                                )
                                await websocket.send(error_response)
                                await websocket.close()
                                return

                            totp_ok = self._check_totp_auth(raw_message, websocket)
                            if totp_ok:
                                # Clear failures on success
                                self._totp_failures.pop(remote_key, None)
                                from xml_pipeline.oob.protocol import build_ack
                                await websocket.send(build_ack("", "TOTP authenticated"))
                                continue
                            else:
                                self._totp_failures[remote_key].append(now)
                                error_response = build_error("", "AUTH_REQUIRED", "TOTP authentication required")
                                await websocket.send(error_response)
                                await websocket.close()
                                return

                try:
                    response = await self._dispatch(raw_message, websocket)
                    await websocket.send(response)
                except Exception as e:
                    logger.error(f"OOB dispatch error: {e}", exc_info=True)
                    error_response = build_error(
                        "", "INTERNAL_ERROR", "Internal server error"
                    )
                    await websocket.send(error_response)
        except Exception:
            pass  # Connection closed
        finally:
            self._clients.discard(websocket)
            self._authenticated.discard(id(websocket))
            # Clean up subscriptions for this client
            to_remove = [
                sid for sid, sub in self._subscriptions.items()
                if sub.websocket is websocket
            ]
            for sid in to_remove:
                del self._subscriptions[sid]
            logger.debug(f"OOB client disconnected: {remote}")

    def _check_totp_auth(self, raw_xml: bytes, websocket: Any) -> bool:
        """
        Check if raw_xml is a valid totp-auth command with correct token.

        If valid, marks the connection as authenticated and returns True.
        """
        try:
            command_name, command_el, _request_id = parse_request(raw_xml)
        except OOBAuthError:
            return False

        if command_name != "totp-auth":
            return False

        # Extract <token> text
        from xml_pipeline.oob.handlers import _text
        token = _text(command_el, "token")
        if not token:
            return False

        from xml_pipeline.crypto.totp import verify_totp
        if verify_totp(self.totp_secret, token):
            self._authenticated.add(id(websocket))
            logger.info(f"OOB client TOTP authenticated")
            return True

        logger.warning("OOB TOTP verification failed")
        return False

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
        from xml_pipeline.message_bus.events import ReloadEvent

        # Invalidate all OOB sessions on config reload (policy may have changed)
        if isinstance(event, ReloadEvent):
            count = len(self._authenticated)
            self._authenticated.clear()
            if count:
                logger.info(
                    f"Config reloaded — invalidated {count} OOB session(s)"
                )

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
        from xml_pipeline.message_bus.events import (
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
