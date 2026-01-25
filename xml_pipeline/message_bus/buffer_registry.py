"""
buffer_registry.py — State storage for Buffer (fan-out) orchestration.

Tracks active buffer executions that fan-out to parallel workers.
When a buffer starts, N items are dispatched in parallel. Results are
collected here. When all results are in (or timeout), BufferComplete is sent.

Design:
- Thread-safe (same pattern as TodoRegistry, SequenceRegistry)
- Keyed by buffer_id (short UUID)
- Tracks: total items, received results, success/failure per item
- Supports fire-and-forget mode (collect=False)

Usage:
    registry = get_buffer_registry()

    # Start a buffer
    registry.create(
        buffer_id="abc123",
        total_items=5,
        return_to="greeter",
        thread_id="...",
        collect=True,
    )

    # Record result from worker
    state = registry.record_result(
        buffer_id="abc123",
        index=2,
        result="<SearchResult>...</SearchResult>",
        success=True,
    )

    if state.is_complete:
        # All workers done
        final_results = state.results
        registry.remove(buffer_id)
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
import threading


@dataclass
class BufferItemResult:
    """Result from a single buffer item (worker)."""

    index: int
    result: str                   # XML result
    success: bool = True
    error: Optional[str] = None


@dataclass
class BufferState:
    """State for an active buffer execution."""

    buffer_id: str
    total_items: int              # How many items were dispatched
    return_to: str                # Where to send BufferComplete
    thread_id: str                # Original thread for returning
    from_id: str                  # Who started the buffer
    target: str                   # Target listener for items
    collect: bool = True          # Whether to wait for results

    results: Dict[int, BufferItemResult] = field(default_factory=dict)
    completed_count: int = 0
    successful_count: int = 0

    @property
    def is_complete(self) -> bool:
        """True when all items have reported back."""
        return self.completed_count >= self.total_items

    @property
    def pending_count(self) -> int:
        """Number of items still pending."""
        return self.total_items - self.completed_count

    def get_ordered_results(self) -> List[Optional[BufferItemResult]]:
        """Get results in order (None for missing indices)."""
        return [self.results.get(i) for i in range(self.total_items)]


class BufferRegistry:
    """
    Registry for active buffer executions.

    Thread-safe. Singleton pattern via get_buffer_registry().
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buffers: Dict[str, BufferState] = {}

    def create(
        self,
        buffer_id: str,
        total_items: int,
        return_to: str,
        thread_id: str,
        from_id: str,
        target: str,
        collect: bool = True,
    ) -> BufferState:
        """
        Create a new buffer execution.

        Args:
            buffer_id: Unique ID for this buffer
            total_items: Number of items being dispatched
            return_to: Listener to send BufferComplete to
            thread_id: Thread UUID for routing
            from_id: Who initiated the buffer
            target: Target listener for each item
            collect: Whether to wait for and collect results

        Returns:
            BufferState for tracking
        """
        state = BufferState(
            buffer_id=buffer_id,
            total_items=total_items,
            return_to=return_to,
            thread_id=thread_id,
            from_id=from_id,
            target=target,
            collect=collect,
        )

        with self._lock:
            self._buffers[buffer_id] = state

        return state

    def get(self, buffer_id: str) -> Optional[BufferState]:
        """Get buffer state by ID."""
        with self._lock:
            return self._buffers.get(buffer_id)

    def record_result(
        self,
        buffer_id: str,
        index: int,
        result: str,
        success: bool = True,
        error: Optional[str] = None,
    ) -> Optional[BufferState]:
        """
        Record a result from a worker.

        Args:
            buffer_id: Buffer this result belongs to
            index: Which item index (0-based)
            result: XML result from the worker
            success: Whether the worker succeeded
            error: Error message if failed

        Returns:
            Updated BufferState, or None if buffer not found
        """
        with self._lock:
            state = self._buffers.get(buffer_id)
            if state is None:
                return None

            # Don't double-count results for same index
            if index in state.results:
                return state

            item_result = BufferItemResult(
                index=index,
                result=result,
                success=success,
                error=error,
            )
            state.results[index] = item_result
            state.completed_count += 1
            if success:
                state.successful_count += 1

            return state

    def remove(self, buffer_id: str) -> bool:
        """
        Remove a buffer (cleanup after completion).

        Returns:
            True if found and removed, False if not found
        """
        with self._lock:
            return self._buffers.pop(buffer_id, None) is not None

    def list_active(self) -> List[str]:
        """List all active buffer IDs."""
        with self._lock:
            return list(self._buffers.keys())

    def clear(self) -> None:
        """Clear all buffers. Useful for testing."""
        with self._lock:
            self._buffers.clear()


# ============================================================================
# Singleton
# ============================================================================

_registry: Optional[BufferRegistry] = None
_registry_lock = threading.Lock()


def get_buffer_registry() -> BufferRegistry:
    """Get the global BufferRegistry singleton."""
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                _registry = BufferRegistry()
    return _registry


def reset_buffer_registry() -> None:
    """Reset the global buffer registry (for testing)."""
    global _registry
    with _registry_lock:
        if _registry is not None:
            _registry.clear()
        _registry = None
