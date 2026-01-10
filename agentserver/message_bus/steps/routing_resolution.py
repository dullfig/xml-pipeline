"""
routing_resolution.py — Resolve routing based on derived root tag.

This step computes the root tag from the deserialized payload and looks it up
in a routing table (root_tag → list[Listener]).

NOTE: The StreamPump has routing built-in via _route_step(). This standalone
step is provided for custom pipeline configurations or testing.

Usage:
    routing_step = make_routing_step(routing_table)
    state = await routing_step(state)

Part of AgentServer v2.1 message pump.
"""

from __future__ import annotations

from typing import Dict, List, Callable, Awaitable, TYPE_CHECKING

from agentserver.message_bus.message_state import MessageState

if TYPE_CHECKING:
    from agentserver.message_bus.stream_pump import Listener


def make_routing_step(
    routing_table: Dict[str, List["Listener"]]
) -> Callable[[MessageState], Awaitable[MessageState]]:
    """
    Factory: create a routing step with a specific routing table.

    The routing table maps root tags to lists of listeners:
        {"agent.payload": [listener1, listener2], ...}
    """

    async def routing_resolution_step(state: MessageState) -> MessageState:
        """
        Resolve which listener(s) should handle this payload.

        Root tag = f"{from_id.lower()}.{payload_class_name.lower()}"

        Supports:
          - Normal unique routing (one listener)
          - Broadcast (multiple listeners if same root tag)

        If no match → error, falls to system pipeline.
        """
        if state.payload is None:
            state.error = "routing_resolution_step: no deserialized payload"
            return state

        if state.to_id is None:
            state.error = "routing_resolution_step: missing to_id"
            return state

        payload_class_name = type(state.payload).__name__.lower()
        root_tag = f"{state.to_id.lower()}.{payload_class_name}"

        targets = routing_table.get(root_tag)

        if not targets:
            state.error = f"routing_resolution_step: unknown root tag '{root_tag}'"
            return state

        state.target_listeners = targets
        return state

    routing_resolution_step.__name__ = "routing_resolution_step"
    return routing_resolution_step
