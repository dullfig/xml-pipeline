"""
shared_backend.py — Abstract backend interface for shared state.

Provides a Protocol defining operations needed by ContextBuffer and ThreadRegistry
to work across processes. Implementations include:
- InMemoryBackend: Default, single-process (current behavior)
- ManagerBackend: multiprocessing.Manager for local multi-process
- RedisBackend: Redis for distributed/multi-tenant scenarios

All backends support:
- Context buffer operations (thread slots)
- Thread registry operations (UUID ↔ chain mapping)
- TTL for automatic garbage collection
"""

from __future__ import annotations

import pickle
from abc import abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol, Tuple, runtime_checkable


@dataclass
class BackendConfig:
    """Configuration for shared backend selection."""

    backend_type: str = "memory"  # "memory", "manager", "redis"

    # Redis-specific config
    redis_url: str = "redis://localhost:6379"
    redis_prefix: str = "xp:"
    redis_ttl: int = 86400  # 24 hours default

    # Manager-specific config (for local multiprocess)
    manager_address: Optional[Tuple[str, int]] = None
    manager_authkey: Optional[bytes] = None

    # Common limits
    max_slots_per_thread: int = 10000
    max_threads: int = 1000


@runtime_checkable
class SharedBackend(Protocol):
    """
    Protocol for shared state backends.

    All methods should be synchronous (blocking) for simplicity.
    Async wrappers can be added at the caller level if needed.
    """

    # =========================================================================
    # Context Buffer Operations
    # =========================================================================

    @abstractmethod
    def buffer_append(
        self,
        thread_id: str,
        slot_data: bytes,  # Pickled BufferSlot
    ) -> int:
        """
        Append a slot to a thread's buffer.

        Args:
            thread_id: UUID of the thread
            slot_data: Pickled BufferSlot bytes

        Returns:
            Index of the appended slot (0-based)
        """
        ...

    @abstractmethod
    def buffer_get_thread(self, thread_id: str) -> List[bytes]:
        """
        Get all slots for a thread.

        Args:
            thread_id: UUID of the thread

        Returns:
            List of pickled BufferSlot bytes (in order)
        """
        ...

    @abstractmethod
    def buffer_get_slot(self, thread_id: str, index: int) -> Optional[bytes]:
        """
        Get a specific slot by index.

        Args:
            thread_id: UUID of the thread
            index: Slot index (0-based)

        Returns:
            Pickled BufferSlot bytes, or None if not found
        """
        ...

    @abstractmethod
    def buffer_thread_len(self, thread_id: str) -> int:
        """Get number of slots in a thread."""
        ...

    @abstractmethod
    def buffer_thread_exists(self, thread_id: str) -> bool:
        """Check if a thread has any slots."""
        ...

    @abstractmethod
    def buffer_delete_thread(self, thread_id: str) -> bool:
        """Delete all slots for a thread. Returns True if thread existed."""
        ...

    @abstractmethod
    def buffer_list_threads(self) -> List[str]:
        """List all thread IDs with slots."""
        ...

    @abstractmethod
    def buffer_clear(self) -> None:
        """Clear all buffer data (for testing)."""
        ...

    # =========================================================================
    # Thread Registry Operations
    # =========================================================================

    @abstractmethod
    def registry_set(self, chain: str, uuid: str) -> None:
        """
        Set bidirectional mapping: chain ↔ uuid.

        Args:
            chain: Dot-separated call chain (e.g., "console.router.greeter")
            uuid: UUID string for this chain
        """
        ...

    @abstractmethod
    def registry_get_uuid(self, chain: str) -> Optional[str]:
        """
        Get UUID for a chain.

        Args:
            chain: Call chain to look up

        Returns:
            UUID string, or None if not found
        """
        ...

    @abstractmethod
    def registry_get_chain(self, uuid: str) -> Optional[str]:
        """
        Get chain for a UUID.

        Args:
            uuid: UUID to look up

        Returns:
            Chain string, or None if not found
        """
        ...

    @abstractmethod
    def registry_delete(self, uuid: str) -> bool:
        """
        Delete mapping by UUID.

        Removes both chain→uuid and uuid→chain mappings.

        Returns:
            True if mapping existed
        """
        ...

    @abstractmethod
    def registry_list_all(self) -> Dict[str, str]:
        """
        Get all UUID → chain mappings.

        Returns:
            Dict mapping UUID to chain
        """
        ...

    @abstractmethod
    def registry_clear(self) -> None:
        """Clear all registry data (for testing)."""
        ...

    # =========================================================================
    # Lifecycle
    # =========================================================================

    @abstractmethod
    def close(self) -> None:
        """Close connections and clean up resources."""
        ...


# =============================================================================
# Serialization Helpers
# =============================================================================


def serialize_slot(slot: Any) -> bytes:
    """Serialize a BufferSlot to bytes using pickle."""
    return pickle.dumps(slot)


def deserialize_slot(data: bytes) -> Any:
    """Deserialize bytes back to a BufferSlot."""
    return pickle.loads(data)


# =============================================================================
# Factory
# =============================================================================

_backend: Optional[SharedBackend] = None


def get_shared_backend(config: Optional[BackendConfig] = None) -> SharedBackend:
    """
    Get or create the global shared backend.

    Backend selection:
    1. If Redis URL configured and redis available → RedisBackend
    2. If Manager configured → ManagerBackend
    3. Otherwise → InMemoryBackend (default)

    Thread-safe singleton pattern.
    """
    global _backend

    if _backend is not None:
        return _backend

    if config is None:
        config = BackendConfig()

    if config.backend_type == "redis":
        from xml_pipeline.memory.redis_backend import RedisBackend

        _backend = RedisBackend(
            url=config.redis_url,
            prefix=config.redis_prefix,
            ttl=config.redis_ttl,
        )
    elif config.backend_type == "manager":
        from xml_pipeline.memory.manager_backend import ManagerBackend

        _backend = ManagerBackend(
            address=config.manager_address,
            authkey=config.manager_authkey,
        )
    else:
        # Default: in-memory backend
        from xml_pipeline.memory.memory_backend import InMemoryBackend

        _backend = InMemoryBackend()

    return _backend


def reset_shared_backend() -> None:
    """Reset the global backend (for testing)."""
    global _backend
    if _backend is not None:
        _backend.close()
        _backend = None
