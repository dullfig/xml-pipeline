"""
memory_backend.py — In-memory implementation of SharedBackend.

This is the default backend for single-process operation.
It provides the same interface as Redis/Manager backends but stores
everything in Python data structures.

Thread-safe via threading.Lock (same as original ContextBuffer/ThreadRegistry).
"""

from __future__ import annotations

import threading
from typing import Dict, List, Optional


class InMemoryBackend:
    """
    In-memory shared backend implementation.

    This is equivalent to the original behavior - all data in-process.
    Use for development, testing, or single-process deployments.
    """

    def __init__(self) -> None:
        # Context buffer storage: thread_id → list of pickled slots
        self._buffer: Dict[str, List[bytes]] = {}
        self._buffer_lock = threading.Lock()

        # Thread registry storage: bidirectional mapping
        self._chain_to_uuid: Dict[str, str] = {}
        self._uuid_to_chain: Dict[str, str] = {}
        self._registry_lock = threading.Lock()

    # =========================================================================
    # Context Buffer Operations
    # =========================================================================

    def buffer_append(self, thread_id: str, slot_data: bytes) -> int:
        """Append a slot to a thread's buffer."""
        with self._buffer_lock:
            if thread_id not in self._buffer:
                self._buffer[thread_id] = []

            index = len(self._buffer[thread_id])
            self._buffer[thread_id].append(slot_data)
            return index

    def buffer_get_thread(self, thread_id: str) -> List[bytes]:
        """Get all slots for a thread."""
        with self._buffer_lock:
            return list(self._buffer.get(thread_id, []))

    def buffer_get_slot(self, thread_id: str, index: int) -> Optional[bytes]:
        """Get a specific slot by index."""
        with self._buffer_lock:
            slots = self._buffer.get(thread_id, [])
            if 0 <= index < len(slots):
                return slots[index]
            return None

    def buffer_thread_len(self, thread_id: str) -> int:
        """Get number of slots in a thread."""
        with self._buffer_lock:
            return len(self._buffer.get(thread_id, []))

    def buffer_thread_exists(self, thread_id: str) -> bool:
        """Check if a thread has any slots."""
        with self._buffer_lock:
            return thread_id in self._buffer

    def buffer_delete_thread(self, thread_id: str) -> bool:
        """Delete all slots for a thread."""
        with self._buffer_lock:
            if thread_id in self._buffer:
                del self._buffer[thread_id]
                return True
            return False

    def buffer_list_threads(self) -> List[str]:
        """List all thread IDs with slots."""
        with self._buffer_lock:
            return list(self._buffer.keys())

    def buffer_clear(self) -> None:
        """Clear all buffer data."""
        with self._buffer_lock:
            self._buffer.clear()

    # =========================================================================
    # Thread Registry Operations
    # =========================================================================

    def registry_set(self, chain: str, uuid: str) -> None:
        """Set bidirectional mapping: chain ↔ uuid."""
        with self._registry_lock:
            self._chain_to_uuid[chain] = uuid
            self._uuid_to_chain[uuid] = chain

    def registry_get_uuid(self, chain: str) -> Optional[str]:
        """Get UUID for a chain."""
        with self._registry_lock:
            return self._chain_to_uuid.get(chain)

    def registry_get_chain(self, uuid: str) -> Optional[str]:
        """Get chain for a UUID."""
        with self._registry_lock:
            return self._uuid_to_chain.get(uuid)

    def registry_delete(self, uuid: str) -> bool:
        """Delete mapping by UUID."""
        with self._registry_lock:
            chain = self._uuid_to_chain.pop(uuid, None)
            if chain:
                self._chain_to_uuid.pop(chain, None)
                return True
            return False

    def registry_list_all(self) -> Dict[str, str]:
        """Get all UUID → chain mappings."""
        with self._registry_lock:
            return dict(self._uuid_to_chain)

    def registry_clear(self) -> None:
        """Clear all registry data."""
        with self._registry_lock:
            self._chain_to_uuid.clear()
            self._uuid_to_chain.clear()

    # =========================================================================
    # Lifecycle
    # =========================================================================

    def close(self) -> None:
        """No resources to clean up for in-memory backend."""
        pass
