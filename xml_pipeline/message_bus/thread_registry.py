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
"""

import uuid
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple
import threading


@dataclass
class ThreadRegistry:
    """
    Bidirectional mapping between UUIDs and call chains.

    Thread-safe for concurrent access.

    The registry maintains a root thread established at boot time.
    All external messages without a known parent are registered as
    children of the root thread.
    """
    _chain_to_uuid: Dict[str, str] = field(default_factory=dict)
    _uuid_to_chain: Dict[str, str] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _root_uuid: Optional[str] = field(default=None)
    _root_chain: str = field(default="system")

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
        with self._lock:
            if self._root_uuid is not None:
                return self._root_uuid

            self._root_chain = f"system.{organism_name}"
            self._root_uuid = str(uuid.uuid4())
            self._chain_to_uuid[self._root_chain] = self._root_uuid
            self._uuid_to_chain[self._root_uuid] = self._root_chain
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
        with self._lock:
            if chain in self._chain_to_uuid:
                return self._chain_to_uuid[chain]

            new_uuid = str(uuid.uuid4())
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
            new_uuid = str(uuid.uuid4())
            self._chain_to_uuid[new_chain] = new_uuid
            self._uuid_to_chain[new_uuid] = new_chain
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
                new_uuid = str(uuid.uuid4())
                self._chain_to_uuid[pruned_chain] = new_uuid
                self._uuid_to_chain[new_uuid] = pruned_chain

            # Clean up old UUID (optional - could keep for debugging)
            # self._cleanup_uuid(thread_id)

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

    def _cleanup_uuid(self, thread_id: str) -> None:
        """Remove a UUID mapping (internal, call with lock held)."""
        chain = self._uuid_to_chain.pop(thread_id, None)
        if chain:
            self._chain_to_uuid.pop(chain, None)

    def cleanup(self, thread_id: str) -> None:
        """Explicitly clean up a thread UUID."""
        with self._lock:
            self._cleanup_uuid(thread_id)

    def debug_dump(self) -> Dict[str, str]:
        """Return current mappings for debugging."""
        with self._lock:
            return dict(self._uuid_to_chain)


# Global registry instance
_registry: Optional[ThreadRegistry] = None


def get_registry() -> ThreadRegistry:
    """Get the global thread registry."""
    global _registry
    if _registry is None:
        _registry = ThreadRegistry()
    return _registry
