"""
events.py — Pump event types for external observers.

Event classes emitted by StreamPump for UI, monitoring, and debugging.
Subscribe via pump.subscribe_events(callback).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional


# ============================================================================
# Event Hooks
# ============================================================================

@dataclass
class PumpEvent:
    """Base class for pump events."""
    pass


@dataclass
class MessageReceivedEvent(PumpEvent):
    """Fired when a message is received by a handler."""
    thread_id: str
    from_id: str
    to_id: str
    payload_type: str
    payload: Any


@dataclass
class MessageSentEvent(PumpEvent):
    """Fired when a handler sends a response."""
    thread_id: str
    from_id: str
    to_id: str
    payload_type: str
    payload: Any


@dataclass
class AgentStateEvent(PumpEvent):
    """Fired when an agent's processing state changes."""
    agent_name: str
    state: str  # "idle", "processing", "waiting", "error"
    thread_id: Optional[str] = None


@dataclass
class ThreadEvent(PumpEvent):
    """Fired when a thread is created or completed."""
    thread_id: str
    status: str  # "created", "active", "completed", "error", "killed"
    participants: List[str] = field(default_factory=list)
    error: Optional[str] = None


@dataclass
class ReloadEvent(PumpEvent):
    """Fired when organism configuration is reloaded."""
    success: bool
    added_listeners: List[str] = field(default_factory=list)
    removed_listeners: List[str] = field(default_factory=list)
    updated_listeners: List[str] = field(default_factory=list)
    error: Optional[str] = None


EventCallback = Callable[[PumpEvent], None]
