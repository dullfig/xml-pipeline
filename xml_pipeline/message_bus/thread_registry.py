"""
Thread Registry — Maps opaque UUIDs to call chains.

Call chains track the path a message has taken through the system:
  A calls B → chain: "a.b"
  B calls C → chain: "a.b.c"

UUIDs obscure the topology from agents. They only see an opaque
thread_id, not the actual call chain.

Response routing:
  When an agent returns <response>, the registry:
  1. Looks up the UUID to get the chain
  2. Prunes the last segment (the responder)
  3. Routes to the new last segment (the caller)
  4. Updates/cleans up the registry

For multi-process deployments, the registry can use a shared backend:
  from xml_pipeline.memory.shared_backend import get_shared_backend, BackendConfig

  config = BackendConfig(backend_type="redis", redis_url="redis://localhost:6379")
  backend = get_shared_backend(config)
  registry = get_registry(backend=backend)
"""

from __future__ import annotations

import uuid as uuid_module
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple, TYPE_CHECKING
import threading

if TYPE_CHECKING:
    from xml_pipeline.memory.shared_backend import SharedBackend


class ThreadRegistry:
    """
    Bidirectional mapping between UUIDs and call chains.

    Thread-safe for concurrent access.

    The registry maintains a root thread established at boot time.
    All external messages without a known parent are registered as
    children of the root thread.

    Supports two storage modes:
    1. Local mode (default): Uses in-process dictionaries
    2. Shared mode: Uses SharedBackend (Redis, Manager) for cross-process access
    """

    def __init__(self, backend: Optional[SharedBackend] = None):
        """
        Initialize thread registry.

        Args:
            backend: Optional shared backend for cross-process storage.
                     If None, uses in-process storage (original behavior).
        """
        self._backend = backend

        # Local storage (used when no backend)
        self._chain_to_uuid: Dict[str, str] = {}
        self._uuid_to_chain: Dict[str, str] = {}
        self._lock = threading.Lock()

        # Root thread tracking
        self._root_uuid: Optional[str] = None
        self._root_chain: str = "system"

        # Peer table roots: table_name -> root_uuid
        self._table_roots: Dict[str, str] = {}

    @property
    def is_shared(self) -> bool:
        """Return True if using shared backend."""
        return self._backend is not None

    def initialize_root(self, organism_name: str = "organism") -> str:
        """
        Initialize the root thread at boot time.

        This must be called once at startup before any messages are processed.
        The root thread is the ancestor of all other threads.

        Args:
            organism_name: Name of the organism (for the root chain)

        Returns:
            UUID for the root thread
        """
        if self._backend is not None:
            return self._initialize_root_shared(organism_name)

        with self._lock:
            if self._root_uuid is not None:
                return self._root_uuid

            self._root_chain = f"system.{organism_name}"
            self._root_uuid = str(uuid_module.uuid4())
            self._chain_to_uuid[self._root_chain] = self._root_uuid
            self._uuid_to_chain[self._root_uuid] = self._root_chain
            return self._root_uuid

    def _initialize_root_shared(self, organism_name: str) -> str:
        """Initialize root in shared backend."""
        assert self._backend is not None

        self._root_chain = f"system.{organism_name}"

        # Check if root already exists in backend
        existing_uuid = self._backend.registry_get_uuid(self._root_chain)
        if existing_uuid:
            self._root_uuid = existing_uuid
            return existing_uuid

        # Create new root
        self._root_uuid = str(uuid_module.uuid4())
        self._backend.registry_set(self._root_chain, self._root_uuid)
        return self._root_uuid

    @property
    def root_uuid(self) -> Optional[str]:
        """Get the root thread UUID (None if not initialized)."""
        return self._root_uuid

    @property
    def root_chain(self) -> str:
        """Get the root chain string."""
        return self._root_chain

    def get_or_create(self, chain: str) -> str:
        """
        Get existing UUID for chain, or create new one.

        Args:
            chain: Dot-separated call chain (e.g., "console.router.greeter")

        Returns:
            UUID string for this chain
        """
        if self._backend is not None:
            existing = self._backend.registry_get_uuid(chain)
            if existing:
                return existing
            new_uuid = str(uuid_module.uuid4())
            self._backend.registry_set(chain, new_uuid)
            return new_uuid

        with self._lock:
            if chain in self._chain_to_uuid:
                return self._chain_to_uuid[chain]

            new_uuid = str(uuid_module.uuid4())
            self._chain_to_uuid[chain] = new_uuid
            self._uuid_to_chain[new_uuid] = chain
            return new_uuid

    def lookup(self, thread_id: str) -> Optional[str]:
        """
        Look up chain for a UUID.

        Args:
            thread_id: UUID to look up

        Returns:
            Chain string, or None if not found
        """
        if self._backend is not None:
            return self._backend.registry_get_chain(thread_id)

        with self._lock:
            return self._uuid_to_chain.get(thread_id)

    def extend_chain(self, current_uuid: str, next_hop: str) -> str:
        """
        Extend a chain with a new hop and get UUID for the extended chain.

        Args:
            current_uuid: Current thread UUID
            next_hop: Name of the next listener in the chain

        Returns:
            UUID for the extended chain
        """
        if self._backend is not None:
            return self._extend_chain_shared(current_uuid, next_hop)

        with self._lock:
            current_chain = self._uuid_to_chain.get(current_uuid, "")
            if current_chain:
                new_chain = f"{current_chain}.{next_hop}"
            else:
                new_chain = next_hop

            # Check if extended chain already exists
            if new_chain in self._chain_to_uuid:
                return self._chain_to_uuid[new_chain]

            # Create new UUID for extended chain
            new_uuid = str(uuid_module.uuid4())
            self._chain_to_uuid[new_chain] = new_uuid
            self._uuid_to_chain[new_uuid] = new_chain
            return new_uuid

    def _extend_chain_shared(self, current_uuid: str, next_hop: str) -> str:
        """Extend chain in shared backend."""
        assert self._backend is not None

        current_chain = self._backend.registry_get_chain(current_uuid) or ""
        if current_chain:
            new_chain = f"{current_chain}.{next_hop}"
        else:
            new_chain = next_hop

        # Check if extended chain already exists
        existing = self._backend.registry_get_uuid(new_chain)
        if existing:
            return existing

        # Create new UUID for extended chain
        new_uuid = str(uuid_module.uuid4())
        self._backend.registry_set(new_chain, new_uuid)
        return new_uuid

    def prune_for_response(self, thread_id: str) -> Tuple[Optional[str], Optional[str]]:
        """
        Prune chain for a response and get the target.

        When an agent responds, we:
        1. Look up the chain
        2. Remove the last segment (the responder)
        3. Return the new target (new last segment) and new UUID

        Args:
            thread_id: Current thread UUID

        Returns:
            Tuple of (target_listener, new_thread_uuid) or (None, None) if chain exhausted
        """
        if self._backend is not None:
            return self._prune_for_response_shared(thread_id)

        with self._lock:
            chain = self._uuid_to_chain.get(thread_id)
            if not chain:
                return None, None

            parts = chain.split(".")
            if len(parts) <= 1:
                # Chain exhausted - no one to respond to
                # Clean up
                self._cleanup_uuid(thread_id)
                return None, None

            # Prune last segment
            pruned_parts = parts[:-1]
            target = pruned_parts[-1]  # New last segment is the target
            pruned_chain = ".".join(pruned_parts)

            # Get or create UUID for pruned chain
            if pruned_chain in self._chain_to_uuid:
                new_uuid = self._chain_to_uuid[pruned_chain]
            else:
                new_uuid = str(uuid_module.uuid4())
                self._chain_to_uuid[pruned_chain] = new_uuid
                self._uuid_to_chain[new_uuid] = pruned_chain

            return target, new_uuid

    def _prune_for_response_shared(self, thread_id: str) -> Tuple[Optional[str], Optional[str]]:
        """Prune chain in shared backend."""
        assert self._backend is not None

        chain = self._backend.registry_get_chain(thread_id)
        if not chain:
            return None, None

        parts = chain.split(".")
        if len(parts) <= 1:
            # Chain exhausted
            self._backend.registry_delete(thread_id)
            return None, None

        # Prune last segment
        pruned_parts = parts[:-1]
        target = pruned_parts[-1]
        pruned_chain = ".".join(pruned_parts)

        # Get or create UUID for pruned chain
        existing = self._backend.registry_get_uuid(pruned_chain)
        if existing:
            return target, existing

        new_uuid = str(uuid_module.uuid4())
        self._backend.registry_set(pruned_chain, new_uuid)
        return target, new_uuid

    def start_chain(self, initiator: str, target: str) -> str:
        """
        Start a new call chain.

        Args:
            initiator: Name of the caller
            target: Name of the callee

        Returns:
            UUID for the new chain
        """
        chain = f"{initiator}.{target}"
        return self.get_or_create(chain)

    def register_thread(self, thread_id: str, initiator: str, target: str) -> str:
        """
        Register an existing UUID to a new call chain.

        Used when external messages arrive with a pre-assigned thread UUID
        (from thread_assignment_step) that isn't in the registry yet.

        The chain is rooted at the system root if one exists.

        Args:
            thread_id: Existing UUID from the message
            initiator: Name of the caller (e.g., "console")
            target: Name of the callee (e.g., "router")

        Returns:
            The same thread_id (now registered)
        """
        if self._backend is not None:
            return self._register_thread_shared(thread_id, initiator, target)

        with self._lock:
            # Check if UUID already registered (shouldn't happen, but be safe)
            if thread_id in self._uuid_to_chain:
                return thread_id

            # Build chain rooted at system root
            if self._root_uuid is not None:
                chain = f"{self._root_chain}.{initiator}.{target}"
            else:
                chain = f"{initiator}.{target}"

            # Check if chain already has a different UUID
            if chain in self._chain_to_uuid:
                # Chain exists with different UUID - extend instead
                existing_uuid = self._chain_to_uuid[chain]
                return existing_uuid

            # Register the external UUID to this chain
            self._chain_to_uuid[chain] = thread_id
            self._uuid_to_chain[thread_id] = chain
            return thread_id

    def _register_thread_shared(self, thread_id: str, initiator: str, target: str) -> str:
        """Register thread in shared backend."""
        assert self._backend is not None

        # Check if UUID already registered
        if self._backend.registry_get_chain(thread_id):
            return thread_id

        # Build chain rooted at system root
        if self._root_uuid is not None:
            chain = f"{self._root_chain}.{initiator}.{target}"
        else:
            chain = f"{initiator}.{target}"

        # Check if chain already has a different UUID
        existing = self._backend.registry_get_uuid(chain)
        if existing:
            return existing

        # Register the external UUID to this chain
        self._backend.registry_set(chain, thread_id)
        return thread_id

    # ------------------------------------------------------------------
    # Peer Table Roots
    # ------------------------------------------------------------------

    def initialize_table_root(self, table_name: str, organism_name: str) -> str:
        """
        Create a root chain for a peer table: '{table_name}.{organism_name}'.

        This mirrors initialize_root() but uses the table name as the chain
        prefix instead of 'system'. All threads created under this root will
        carry the table name in their chain, enabling per-thread privilege
        enforcement.

        Args:
            table_name: Name of the peer table (e.g., "premium", "basic")
            organism_name: Name of the organism

        Returns:
            UUID for the table root thread
        """
        with self._lock:
            if table_name in self._table_roots:
                return self._table_roots[table_name]

            chain = f"{table_name}.{organism_name}"
            root_uuid = str(uuid_module.uuid4())
            self._chain_to_uuid[chain] = root_uuid
            self._uuid_to_chain[root_uuid] = chain
            self._table_roots[table_name] = root_uuid
            return root_uuid

    def get_table_root(self, table_name: str) -> Optional[str]:
        """
        Get root UUID for a named peer table.

        Args:
            table_name: Name of the peer table

        Returns:
            Root UUID, or None if table not initialized
        """
        return self._table_roots.get(table_name)

    def get_table_for_thread(self, thread_id: str) -> Optional[str]:
        """
        Extract table name from chain prefix.

        Returns None for 'system.*' chains (default threads).
        For tabled threads like 'premium.organism.external.greeter',
        returns 'premium'.

        Args:
            thread_id: Opaque thread UUID

        Returns:
            Table name, or None if this is a default (system) thread
        """
        if self._backend is not None:
            chain = self._backend.registry_get_chain(thread_id)
        else:
            with self._lock:
                chain = self._uuid_to_chain.get(thread_id)

        if not chain:
            return None

        prefix = chain.split(".", 1)[0]
        if prefix == "system":
            return None
        # Verify it's an actual registered table
        if prefix in self._table_roots:
            return prefix
        return None

    def _cleanup_uuid(self, thread_id: str) -> None:
        """Remove a UUID mapping (internal, call with lock held)."""
        chain = self._uuid_to_chain.pop(thread_id, None)
        if chain:
            self._chain_to_uuid.pop(chain, None)

    def cleanup(self, thread_id: str) -> None:
        """Explicitly clean up a thread UUID."""
        if self._backend is not None:
            self._backend.registry_delete(thread_id)
            return

        with self._lock:
            self._cleanup_uuid(thread_id)

    def debug_dump(self) -> Dict[str, str]:
        """Return current mappings for debugging."""
        if self._backend is not None:
            return self._backend.registry_list_all()

        with self._lock:
            return dict(self._uuid_to_chain)

    def clear(self) -> None:
        """Clear all thread mappings (for testing only)."""
        if self._backend is not None:
            self._backend.registry_clear()
            self._root_uuid = None
            self._root_chain = "system"
            self._table_roots.clear()
            return

        with self._lock:
            self._chain_to_uuid.clear()
            self._uuid_to_chain.clear()
            self._root_uuid = None
            self._root_chain = "system"
            self._table_roots.clear()


# Global registry instance
_registry: Optional[ThreadRegistry] = None
_registry_lock = threading.Lock()


def get_registry(backend: Optional[SharedBackend] = None) -> ThreadRegistry:
    """
    Get the global thread registry.

    Args:
        backend: Optional shared backend for cross-process storage.
                 Only used on first call (when creating the singleton).
                 Subsequent calls return the existing singleton.

    Returns:
        Global ThreadRegistry instance.
    """
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                _registry = ThreadRegistry(backend=backend)
    return _registry


def reset_registry() -> None:
    """Reset the global thread registry (for testing)."""
    global _registry
    with _registry_lock:
        if _registry is not None:
            _registry.clear()
        _registry = None
