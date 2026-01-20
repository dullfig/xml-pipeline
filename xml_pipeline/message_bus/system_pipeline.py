"""
SystemPipeline — Entry point for external messages.

All messages from the outside world flow through this pipeline:
- Console input (@target message)
- Webhook/API calls
- Boot sequence

The system pipeline transforms raw input into proper XML envelopes
and injects them into the main message pump.

Architecture:
    ┌─────────────────────────────────────────────────────────┐
    │                    System Pipeline                       │
    │  [ingress] → [validate] → [envelope] → [route]          │
    └─────────────────────────────────────────────────────────┘
          ↑              ↑            ↑                  ↓
       console       webhook       boot            StreamPump
"""

from __future__ import annotations

import re
import uuid
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional, Callable, Any

if TYPE_CHECKING:
    from .stream_pump import StreamPump

from xml_pipeline.primitives.text_input import TextInput, TextOutput

logger = logging.getLogger(__name__)


@dataclass
class ExternalMessage:
    """
    Raw input from external source before processing.

    This is the intermediate representation in the system pipeline.
    """
    content: str
    target: Optional[str] = None  # Listener name (from @target or explicit)
    source: str = "console"       # console, webhook, api, boot
    user: Optional[str] = None    # Authenticated user
    timestamp: Optional[datetime] = None
    metadata: dict = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now(timezone.utc)
        if self.metadata is None:
            self.metadata = {}


class SystemPipeline:
    """
    Entry point for all external messages.

    Transforms raw input into XML envelopes and injects into the pump.

    Usage:
        pump = StreamPump(config)
        system = SystemPipeline(pump)

        # From console
        thread_id = await system.inject_console("@greeter Dan", user="admin")

        # From webhook
        thread_id = await system.inject_webhook(json_data, source="github")

        # Track responses
        system.subscribe(thread_id, callback)
    """

    # Pattern for @target message format
    TARGET_PATTERN = re.compile(r'^@(\S+)\s+(.+)$', re.DOTALL)

    def __init__(self, pump: 'StreamPump'):
        self.pump = pump

        # Response callbacks by thread_id
        self._subscribers: dict[str, list[Callable]] = {}

        # Validation rules
        self._rate_limits: dict[str, int] = {}  # user -> count
        self._max_rate: int = 100  # messages per minute

    # ------------------------------------------------------------------
    # Ingress: Accept raw input
    # ------------------------------------------------------------------

    async def inject_console(
        self,
        raw: str,
        user: str,
        default_target: Optional[str] = None,
    ) -> str:
        """
        Inject console input into the pipeline.

        Format: @target message
        Or just: message (uses default_target)

        Args:
            raw: Raw console input
            user: Authenticated username
            default_target: Default listener if no @target specified

        Returns:
            thread_id for tracking the conversation
        """
        raw = raw.strip()
        if not raw:
            raise ValueError("Empty message")

        # Parse @target format
        match = self.TARGET_PATTERN.match(raw)
        if match:
            target = match.group(1)
            content = match.group(2).strip()
        elif default_target:
            target = default_target
            content = raw
        else:
            raise ValueError("No target specified. Use @target message format.")

        msg = ExternalMessage(
            content=content,
            target=target,
            source="console",
            user=user,
        )

        return await self._process(msg)

    async def inject_webhook(
        self,
        data: dict,
        source: str = "webhook",
        user: Optional[str] = None,
    ) -> str:
        """
        Inject webhook/API payload into the pipeline.

        Expected format:
            {
                "target": "listener_name",
                "content": "message text",
                "metadata": {...}  # optional
            }

        Returns:
            thread_id for tracking
        """
        target = data.get("target")
        content = data.get("content", data.get("text", data.get("message", "")))

        if not target:
            raise ValueError("Webhook missing 'target' field")
        if not content:
            raise ValueError("Webhook missing 'content' field")

        msg = ExternalMessage(
            content=content,
            target=target,
            source=source,
            user=user,
            metadata=data.get("metadata", {}),
        )

        return await self._process(msg)

    async def inject_raw(
        self,
        target: str,
        content: str,
        source: str = "api",
        user: Optional[str] = None,
    ) -> str:
        """
        Direct injection with explicit target and content.

        Useful for programmatic access.
        """
        msg = ExternalMessage(
            content=content,
            target=target,
            source=source,
            user=user,
        )
        return await self._process(msg)

    # ------------------------------------------------------------------
    # Processing Pipeline
    # ------------------------------------------------------------------

    async def _process(self, msg: ExternalMessage) -> str:
        """
        Process external message through system pipeline.

        Steps:
        1. Validate (permissions, rate limits)
        2. Create payload (TextInput)
        3. Wrap in envelope
        4. Inject into pump
        """
        # Step 1: Validate
        await self._validate(msg)

        # Step 2: Create payload
        payload = self._create_payload(msg)

        # Step 3: Generate thread ID
        thread_id = self._generate_thread_id()

        # Step 4: Wrap in envelope
        envelope = self._wrap_envelope(payload, msg.target, thread_id, msg.source, msg.user)

        # Step 5: Inject into pump
        from_id = f"{msg.source}:{msg.user}" if msg.user else msg.source
        await self.pump.inject(envelope, thread_id=thread_id, from_id=from_id)

        logger.info(f"Injected {msg.source} message to {msg.target}: {thread_id[:8]}...")
        return thread_id

    async def _validate(self, msg: ExternalMessage) -> None:
        """
        Validate message.

        Checks:
        - Target listener exists
        - User has permission (if applicable)
        - Rate limits not exceeded
        """
        # Check target exists
        if msg.target not in self.pump.listeners:
            available = list(self.pump.listeners.keys())
            raise ValueError(f"Unknown target: {msg.target}. Available: {available}")

        # Rate limiting (simple per-user counter)
        if msg.user:
            count = self._rate_limits.get(msg.user, 0)
            if count >= self._max_rate:
                raise ValueError(f"Rate limit exceeded for user {msg.user}")
            self._rate_limits[msg.user] = count + 1

    def _create_payload(self, msg: ExternalMessage) -> TextInput:
        """Create TextInput payload from external message."""
        return TextInput(
            text=msg.content,
            source=msg.source,
            user=msg.user,
        )

    def _generate_thread_id(self) -> str:
        """Generate unique thread ID for external conversation."""
        return str(uuid.uuid4())

    def _wrap_envelope(
        self,
        payload: TextInput,
        target: str,
        thread_id: str,
        source: str,
        user: Optional[str],
    ) -> bytes:
        """Wrap payload in XML envelope."""
        # Use pump's envelope wrapper
        from_id = f"{source}:{user}" if user else source
        return self.pump._wrap_in_envelope(
            payload=payload,
            from_id=from_id,
            to_id=target,
            thread_id=thread_id,
        )

    # ------------------------------------------------------------------
    # Response Tracking
    # ------------------------------------------------------------------

    def subscribe(self, thread_id: str, callback: Callable[[Any], None]) -> None:
        """
        Subscribe to responses for a thread.

        The callback will be called when messages are sent back
        to the originating source (console, webhook, etc).
        """
        if thread_id not in self._subscribers:
            self._subscribers[thread_id] = []
        self._subscribers[thread_id].append(callback)

    def unsubscribe(self, thread_id: str, callback: Callable[[Any], None]) -> None:
        """Remove subscription."""
        if thread_id in self._subscribers:
            self._subscribers[thread_id] = [
                cb for cb in self._subscribers[thread_id] if cb != callback
            ]

    async def notify_response(self, thread_id: str, payload: Any) -> None:
        """
        Notify subscribers of a response.

        Called by the pump when a message is routed back to an external source.
        """
        callbacks = self._subscribers.get(thread_id, [])
        for cb in callbacks:
            try:
                if asyncio.iscoroutinefunction(cb):
                    await cb(payload)
                else:
                    cb(payload)
            except Exception as e:
                logger.error(f"Subscriber callback error: {e}")

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    def list_targets(self) -> list[str]:
        """List available target listeners."""
        return list(self.pump.listeners.keys())

    def reset_rate_limits(self) -> None:
        """Reset rate limit counters (call periodically)."""
        self._rate_limits.clear()


# Need asyncio for notify_response
import asyncio
