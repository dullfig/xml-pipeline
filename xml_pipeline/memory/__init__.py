"""
memory — Virtual memory management for AI agents.

Provides thread-scoped, append-only context buffers with:
- Immutable slots (handlers can't modify messages)
- Thread isolation (handlers only see their context)
- Complete audit trail (all messages preserved)
- GC and limits (prevent runaway memory usage)

For multi-process deployments, supports shared backends:
- InMemoryBackend: Default single-process storage
- ManagerBackend: multiprocessing.Manager for local multi-process
- RedisBackend: Redis for distributed deployments

Usage:
    # Default (in-memory, single process)
    buffer = get_context_buffer()

    # With Redis backend
    from xml_pipeline.memory.shared_backend import BackendConfig, get_shared_backend
    config = BackendConfig(backend_type="redis", redis_url="redis://localhost:6379")
    backend = get_shared_backend(config)
    buffer = get_context_buffer(backend=backend)
"""

from xml_pipeline.memory.context_buffer import (
    ContextBuffer,
    ThreadContext,
    BufferSlot,
    SlotMetadata,
    get_context_buffer,
    reset_context_buffer,
    slot_to_handler_metadata,
)

from xml_pipeline.memory.shared_backend import (
    SharedBackend,
    BackendConfig,
    get_shared_backend,
    reset_shared_backend,
    serialize_slot,
    deserialize_slot,
)

__all__ = [
    # Context buffer
    "ContextBuffer",
    "ThreadContext",
    "BufferSlot",
    "SlotMetadata",
    "get_context_buffer",
    "reset_context_buffer",
    "slot_to_handler_metadata",
    # Shared backend
    "SharedBackend",
    "BackendConfig",
    "get_shared_backend",
    "reset_shared_backend",
    "serialize_slot",
    "deserialize_slot",
]
