"""
payloads.py — Universal SwarmMessage dataclass for the coding swarm.

All listeners in the coding swarm use the same payload type. Routing is
discriminated by the ``role`` field. Root tags remain unique per listener:
``coordinator.swarmmessage``, ``architect.swarmmessage``, etc.
"""

from dataclasses import dataclass

from third_party.xmlable import xmlify


@xmlify
@dataclass
class SwarmMessage:
    """Single message type shared across all coding-swarm listeners."""
    role: str         # Discriminator (task, design-request, design-result, ...)
    tool_name: str    # WASM tool being built
    content: str      # Primary payload (requirements, WIT, source, etc.)
    status: str       # "pending", "success", "error"
    error: str        # Error details (empty on success)
    iteration: int    # Retry count
    phase: str        # Current coordinator phase
