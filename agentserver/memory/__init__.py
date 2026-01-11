"""
memory — Virtual memory management for AI agents.

Provides thread-scoped, append-only context buffers with:
- Immutable slots (handlers can't modify messages)
- Thread isolation (handlers only see their context)
- Complete audit trail (all messages preserved)
- GC and limits (prevent runaway memory usage)
"""

from agentserver.memory.context_buffer import (
    ContextBuffer,
    ThreadContext,
    BufferSlot,
    SlotMetadata,
    get_context_buffer,
    slot_to_handler_metadata,
)

__all__ = [
    "ContextBuffer",
    "ThreadContext",
    "BufferSlot",
    "SlotMetadata",
    "get_context_buffer",
    "slot_to_handler_metadata",
]
