"""
manager_backend.py — multiprocessing.Manager implementation of SharedBackend.

Uses Python's multiprocessing.Manager for cross-process state sharing
without requiring Redis. Good for local development and testing.

Key differences from InMemoryBackend:
- Data structures are proxied across processes
- Slightly higher overhead than in-memory
- No TTL/expiration (use for short-lived test runs)
- No persistence (data lost on manager shutdown)

Usage:
    # Start manager in main process
    backend = ManagerBackend()

    # Workers connect via the same address
    # (Manager handles cross-process communication)
"""

from __future__ import annotations

import logging
import multiprocessing
from multiprocessing.managers import SyncManager
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class ManagerBackend:
    """
    multiprocessing.Manager-backed shared state.

    Provides cross-process state without external dependencies.
    Suitable for local multi-process testing when Redis isn't available.
    """

    def __init__(
        self,
        address: Optional[Tuple[str, int]] = None,
        authkey: Optional[bytes] = None,
    ) -> None:
        """
        Initialize Manager backend.

        Args:
            address: (host, port) for remote manager connection.
                     If None, creates a local manager.
            authkey: Authentication key for manager.
                     If None, uses process authkey.
        """
        self._is_server = address is None
        self._manager: Optional[SyncManager] = None

        if self._is_server:
            # Create a local manager (server mode)
            self._manager = multiprocessing.Manager()
            self._init_data_structures()
            logger.info("Manager backend started (server mode)")
        else:
            # Connect to remote manager (client mode)
            # Note: For remote connection, you'd need a custom SyncManager
            # This is simplified for now - just use local manager
            self._manager = multiprocessing.Manager()
            self._init_data_structures()
            logger.info("Manager backend started (client mode)")

    def _init_data_structures(self) -> None:
        """Initialize managed data structures."""
        if self._manager is None:
            raise RuntimeError("Manager not initialized")

        # Context buffer: thread_id → list of pickled slots
        self._buffer: Dict[str, List[bytes]] = self._manager.dict()

        # Thread registry: bidirectional mapping
        self._chain_to_uuid: Dict[str, str] = self._manager.dict()
        self._uuid_to_chain: Dict[str, str] = self._manager.dict()

        # Lock for complex operations
        self._lock = self._manager.Lock()

    # =========================================================================
    # Context Buffer Operations
    # =========================================================================

    def buffer_append(self, thread_id: str, slot_data: bytes) -> int:
        """Append a slot to a thread's buffer."""
        with self._lock:
            if thread_id not in self._buffer:
                # Manager.dict doesn't support nested assignment directly
                # We need to get, modify, put back
                self._buffer[thread_id] = self._manager.list()  # type: ignore

            # Get the list, append, and count
            slots = self._buffer[thread_id]
            slots.append(slot_data)
            return len(slots) - 1

    def buffer_get_thread(self, thread_id: str) -> List[bytes]:
        """Get all slots for a thread."""
        with self._lock:
            if thread_id not in self._buffer:
                return []
            # Convert manager.list to regular list
            return list(self._buffer[thread_id])

    def buffer_get_slot(self, thread_id: str, index: int) -> Optional[bytes]:
        """Get a specific slot by index."""
        with self._lock:
            if thread_id not in self._buffer:
                return None
            slots = self._buffer[thread_id]
            if 0 <= index < len(slots):
                return slots[index]
            return None

    def buffer_thread_len(self, thread_id: str) -> int:
        """Get number of slots in a thread."""
        with self._lock:
            if thread_id not in self._buffer:
                return 0
            return len(self._buffer[thread_id])

    def buffer_thread_exists(self, thread_id: str) -> bool:
        """Check if a thread has any slots."""
        with self._lock:
            return thread_id in self._buffer

    def buffer_delete_thread(self, thread_id: str) -> bool:
        """Delete all slots for a thread."""
        with self._lock:
            if thread_id in self._buffer:
                del self._buffer[thread_id]
                return True
            return False

    def buffer_list_threads(self) -> List[str]:
        """List all thread IDs with slots."""
        with self._lock:
            return list(self._buffer.keys())

    def buffer_clear(self) -> None:
        """Clear all buffer data."""
        with self._lock:
            # Can't call .clear() on manager.dict proxy
            keys = list(self._buffer.keys())
            for key in keys:
                del self._buffer[key]

    # =========================================================================
    # Thread Registry Operations
    # =========================================================================

    def registry_set(self, chain: str, uuid: str) -> None:
        """Set bidirectional mapping: chain ↔ uuid."""
        with self._lock:
            self._chain_to_uuid[chain] = uuid
            self._uuid_to_chain[uuid] = chain

    def registry_get_uuid(self, chain: str) -> Optional[str]:
        """Get UUID for a chain."""
        with self._lock:
            return self._chain_to_uuid.get(chain)

    def registry_get_chain(self, uuid: str) -> Optional[str]:
        """Get chain for a UUID."""
        with self._lock:
            return self._uuid_to_chain.get(uuid)

    def registry_delete(self, uuid: str) -> bool:
        """Delete mapping by UUID."""
        with self._lock:
            chain = self._uuid_to_chain.get(uuid)
            if chain:
                del self._uuid_to_chain[uuid]
                del self._chain_to_uuid[chain]
                return True
            return False

    def registry_list_all(self) -> Dict[str, str]:
        """Get all UUID → chain mappings."""
        with self._lock:
            return dict(self._uuid_to_chain)

    def registry_clear(self) -> None:
        """Clear all registry data."""
        with self._lock:
            # Can't call .clear() on manager.dict proxy
            for key in list(self._chain_to_uuid.keys()):
                del self._chain_to_uuid[key]
            for key in list(self._uuid_to_chain.keys()):
                del self._uuid_to_chain[key]

    # =========================================================================
    # Lifecycle
    # =========================================================================

    def close(self) -> None:
        """Shutdown manager."""
        if self._manager and self._is_server:
            try:
                self._manager.shutdown()
                logger.info("Manager backend shutdown")
            except Exception as e:
                logger.warning(f"Manager shutdown error: {e}")
        self._manager = None

    # =========================================================================
    # Info
    # =========================================================================

    def info(self) -> Dict[str, int]:
        """Get backend statistics."""
        with self._lock:
            return {
                "buffer_threads": len(self._buffer),
                "registry_entries": len(self._uuid_to_chain),
            }
