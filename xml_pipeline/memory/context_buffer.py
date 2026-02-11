"""
context_buffer.py — Virtual memory manager for AI agents.

Provides thread-scoped, append-only storage for validated message payloads.
Handlers receive immutable references to buffer slots, never copies.

Design principles:
- Append-only: Messages cannot be modified after insertion
- Thread isolation: Handlers only see their thread's context
- Immutable references: Handlers get read-only views
- Complete audit trail: All messages preserved in order

Analogous to OS virtual memory:
- Thread ID = virtual address space
- Buffer slot = memory page
- Thread registry = page table
- Immutable reference = read-only mapping

Usage:
    buffer = get_context_buffer()

    # Append validated payload (returns slot reference)
    ref = buffer.append(thread_id, payload, metadata)

    # Handler receives reference
    handler(ref.payload, ref.metadata)

    # Get thread history
    history = buffer.get_thread(thread_id)

For multi-process deployments, the buffer can use a shared backend:
    from xml_pipeline.memory.shared_backend import get_shared_backend, BackendConfig

    config = BackendConfig(backend_type="redis", redis_url="redis://localhost:6379")
    backend = get_shared_backend(config)
    buffer = get_context_buffer(backend=backend)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Iterator, TYPE_CHECKING
from datetime import datetime, timezone
import threading
import uuid

if TYPE_CHECKING:
    from xml_pipeline.memory.shared_backend import SharedBackend


@dataclass(frozen=True)
class SlotMetadata:
    """Immutable metadata for a buffer slot."""
    thread_id: str
    from_id: str
    to_id: str
    slot_index: int
    timestamp: str
    payload_type: str

    # Handler-facing metadata (subset exposed to handlers)
    own_name: Optional[str] = None
    is_self_call: bool = False
    usage_instructions: str = ""


@dataclass(frozen=True)
class BufferSlot:
    """
    Immutable slot in the context buffer.

    frozen=True ensures the slot cannot be modified after creation.
    Handlers receive this directly - they cannot mutate it.
    """
    payload: Any                    # The validated @xmlify dataclass (immutable reference)
    metadata: SlotMetadata          # Immutable slot metadata

    @property
    def thread_id(self) -> str:
        return self.metadata.thread_id

    @property
    def from_id(self) -> str:
        return self.metadata.from_id

    @property
    def to_id(self) -> str:
        return self.metadata.to_id

    @property
    def index(self) -> int:
        return self.metadata.slot_index


class ThreadContext:
    """
    Append-only context buffer for a single thread.

    All slots are immutable once appended.
    """

    def __init__(self, thread_id: str):
        self.thread_id = thread_id
        self._slots: List[BufferSlot] = []
        self._lock = threading.Lock()
        self._created_at = datetime.now(timezone.utc)

    def append(
        self,
        payload: Any,
        from_id: str,
        to_id: str,
        own_name: Optional[str] = None,
        is_self_call: bool = False,
        usage_instructions: str = "",
    ) -> BufferSlot:
        """
        Append a validated payload to this thread's context.

        Returns the immutable slot reference.
        """
        with self._lock:
            slot_index = len(self._slots)

            metadata = SlotMetadata(
                thread_id=self.thread_id,
                from_id=from_id,
                to_id=to_id,
                slot_index=slot_index,
                timestamp=datetime.now(timezone.utc).isoformat(),
                payload_type=type(payload).__name__,
                own_name=own_name,
                is_self_call=is_self_call,
                usage_instructions=usage_instructions,
            )

            slot = BufferSlot(payload=payload, metadata=metadata)
            self._slots.append(slot)

            return slot

    def __len__(self) -> int:
        with self._lock:
            return len(self._slots)

    def __getitem__(self, index: int) -> BufferSlot:
        with self._lock:
            return self._slots[index]

    def __iter__(self) -> Iterator[BufferSlot]:
        # Return a copy of the list to avoid mutation during iteration
        with self._lock:
            return iter(list(self._slots))

    def get_slice(self, start: int = 0, end: Optional[int] = None) -> List[BufferSlot]:
        """Get a slice of the context (for paging/windowing)."""
        with self._lock:
            return list(self._slots[start:end])

    def get_by_type(self, payload_type: str) -> List[BufferSlot]:
        """Get all slots with a specific payload type."""
        with self._lock:
            return [s for s in self._slots if s.metadata.payload_type == payload_type]

    def get_from(self, from_id: str) -> List[BufferSlot]:
        """Get all slots from a specific sender."""
        with self._lock:
            return [s for s in self._slots if s.from_id == from_id]


class ContextBuffer:
    """
    Global context buffer managing all thread contexts.

    Thread-safe. Singleton pattern via get_context_buffer().

    Supports two storage modes:
    1. Local mode (default): Uses in-process ThreadContext objects
    2. Shared mode: Uses SharedBackend (Redis, Manager) for cross-process access

    In shared mode, slots are serialized via pickle and stored in the backend.
    This enables cross-process state sharing for distributed deployments.
    """

    def __init__(self, backend: Optional[SharedBackend] = None):
        """
        Initialize context buffer.

        Args:
            backend: Optional shared backend for cross-process storage.
                     If None, uses in-process storage (original behavior).
        """
        self._backend = backend

        # Local storage (used when no backend)
        self._threads: Dict[str, ThreadContext] = {}
        self._lock = threading.Lock()

        # Limits (can be configured)
        self.max_slots_per_thread: int = 10000
        self.max_threads: int = 1000

    @property
    def is_shared(self) -> bool:
        """Return True if using shared backend."""
        return self._backend is not None

    def get_or_create_thread(self, thread_id: str) -> ThreadContext:
        """
        Get existing thread context or create new one.

        Note: In shared mode, this creates a local proxy that syncs with backend.
        """
        with self._lock:
            if thread_id not in self._threads:
                if len(self._threads) >= self.max_threads:
                    # GC: remove oldest thread (simple strategy)
                    oldest = min(self._threads.values(), key=lambda t: t._created_at)
                    del self._threads[oldest.thread_id]

                self._threads[thread_id] = ThreadContext(thread_id)

            return self._threads[thread_id]

    def append(
        self,
        thread_id: str,
        payload: Any,
        from_id: str,
        to_id: str,
        own_name: Optional[str] = None,
        is_self_call: bool = False,
        usage_instructions: str = "",
    ) -> BufferSlot:
        """
        Append a validated payload to a thread's context.

        This is the main entry point for the pipeline.
        Returns the immutable slot reference.

        In shared mode, the slot is serialized and stored in the backend.
        """
        if self._backend is not None:
            # Shared mode: serialize and store in backend
            return self._append_shared(
                thread_id=thread_id,
                payload=payload,
                from_id=from_id,
                to_id=to_id,
                own_name=own_name,
                is_self_call=is_self_call,
                usage_instructions=usage_instructions,
            )

        # Local mode: use ThreadContext
        thread = self.get_or_create_thread(thread_id)

        # Enforce slot limit
        if len(thread) >= self.max_slots_per_thread:
            raise MemoryError(
                f"Thread {thread_id} exceeded max slots ({self.max_slots_per_thread})"
            )

        return thread.append(
            payload=payload,
            from_id=from_id,
            to_id=to_id,
            own_name=own_name,
            is_self_call=is_self_call,
            usage_instructions=usage_instructions,
        )

    def _append_shared(
        self,
        thread_id: str,
        payload: Any,
        from_id: str,
        to_id: str,
        own_name: Optional[str] = None,
        is_self_call: bool = False,
        usage_instructions: str = "",
    ) -> BufferSlot:
        """Append to shared backend."""
        from xml_pipeline.memory.shared_backend import serialize_slot

        assert self._backend is not None

        # Get current slot count for index
        current_len = self._backend.buffer_thread_len(thread_id)

        # Enforce slot limit
        if current_len >= self.max_slots_per_thread:
            raise MemoryError(
                f"Thread {thread_id} exceeded max slots ({self.max_slots_per_thread})"
            )

        # Create metadata
        metadata = SlotMetadata(
            thread_id=thread_id,
            from_id=from_id,
            to_id=to_id,
            slot_index=current_len,
            timestamp=datetime.now(timezone.utc).isoformat(),
            payload_type=type(payload).__name__,
            own_name=own_name,
            is_self_call=is_self_call,
            usage_instructions=usage_instructions,
        )

        # Create slot
        slot = BufferSlot(payload=payload, metadata=metadata)

        # Serialize and store
        slot_data = serialize_slot(slot)
        self._backend.buffer_append(thread_id, slot_data)

        return slot

    def get_thread(self, thread_id: str) -> Optional[ThreadContext]:
        """
        Get a thread's context (None if not found).

        In shared mode, returns a local ThreadContext populated from backend.
        """
        if self._backend is not None:
            return self._get_thread_shared(thread_id)

        with self._lock:
            return self._threads.get(thread_id)

    def _get_thread_shared(self, thread_id: str) -> Optional[ThreadContext]:
        """Get thread from shared backend."""
        from xml_pipeline.memory.shared_backend import deserialize_slot

        assert self._backend is not None

        if not self._backend.buffer_thread_exists(thread_id):
            return None

        # Create local ThreadContext and populate from backend
        thread = ThreadContext(thread_id)
        slot_data_list = self._backend.buffer_get_thread(thread_id)

        for slot_data in slot_data_list:
            slot = deserialize_slot(slot_data)
            thread._slots.append(slot)

        return thread

    def get_thread_slots(self, thread_id: str) -> List[BufferSlot]:
        """
        Get all slots for a thread as a list.

        More efficient than get_thread() when you just need slots.
        """
        if self._backend is not None:
            from xml_pipeline.memory.shared_backend import deserialize_slot

            slot_data_list = self._backend.buffer_get_thread(thread_id)
            return [deserialize_slot(data) for data in slot_data_list]

        with self._lock:
            thread = self._threads.get(thread_id)
            if thread:
                return list(thread._slots)
            return []

    def thread_exists(self, thread_id: str) -> bool:
        """Check if a thread exists."""
        if self._backend is not None:
            return self._backend.buffer_thread_exists(thread_id)

        with self._lock:
            return thread_id in self._threads

    def delete_thread(self, thread_id: str) -> bool:
        """Delete a thread's context (GC)."""
        if self._backend is not None:
            return self._backend.buffer_delete_thread(thread_id)

        with self._lock:
            if thread_id in self._threads:
                del self._threads[thread_id]
                return True
            return False

    def get_stats(self) -> Dict[str, Any]:
        """Get buffer statistics."""
        if self._backend is not None:
            threads = self._backend.buffer_list_threads()
            total_slots = sum(
                self._backend.buffer_thread_len(t) for t in threads
            )
            return {
                "thread_count": len(threads),
                "total_slots": total_slots,
                "max_threads": self.max_threads,
                "max_slots_per_thread": self.max_slots_per_thread,
                "threads": threads,
                "backend": "shared",
            }

        with self._lock:
            total_slots = sum(len(t) for t in self._threads.values())
            return {
                "thread_count": len(self._threads),
                "total_slots": total_slots,
                "max_threads": self.max_threads,
                "max_slots_per_thread": self.max_slots_per_thread,
                "threads": list(self._threads.keys()),
                "backend": "local",
            }

    def clear(self) -> None:
        """Clear all contexts (for testing)."""
        if self._backend is not None:
            self._backend.buffer_clear()
            return

        with self._lock:
            self._threads.clear()


# ============================================================================
# Singleton
# ============================================================================

_buffer: Optional[ContextBuffer] = None
_buffer_lock = threading.Lock()


def get_context_buffer(backend: Optional[SharedBackend] = None) -> ContextBuffer:
    """
    Get the global ContextBuffer singleton.

    Args:
        backend: Optional shared backend for cross-process storage.
                 Only used on first call (when creating the singleton).
                 Subsequent calls return the existing singleton.

    Returns:
        Global ContextBuffer instance.
    """
    global _buffer
    if _buffer is None:
        with _buffer_lock:
            if _buffer is None:
                _buffer = ContextBuffer(backend=backend)
    return _buffer


def reset_context_buffer() -> None:
    """Reset the global context buffer (for testing)."""
    global _buffer
    with _buffer_lock:
        if _buffer is not None:
            _buffer.clear()
        _buffer = None


# ============================================================================
# Handler-facing metadata adapter
# ============================================================================

def slot_to_handler_metadata(slot: BufferSlot) -> 'HandlerMetadata':
    """
    Convert SlotMetadata to HandlerMetadata for backward compatibility.

    Handlers still receive HandlerMetadata, but it's derived from the slot.
    """
    from xml_pipeline.message_bus.message_state import HandlerMetadata

    return HandlerMetadata(
        thread_id=slot.metadata.thread_id,
        from_id=slot.metadata.from_id,
        own_name=slot.metadata.own_name,
        is_self_call=slot.metadata.is_self_call,
        usage_instructions=slot.metadata.usage_instructions,
    )
