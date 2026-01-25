"""
sequence_registry.py — State storage for Sequence orchestration.

Tracks active sequence executions across handler invocations.
When a sequence starts, its state is registered here. As steps complete,
the state is updated. When all steps are done, the state is cleaned up.

Design:
- Thread-safe (same pattern as TodoRegistry)
- Keyed by sequence_id (short UUID)
- Tracks: steps list, current index, collected results
- Auto-cleanup when sequence completes or errors

Usage:
    registry = get_sequence_registry()

    # Start a sequence
    registry.create(
        sequence_id="abc123",
        steps=["calculator.add", "calculator.multiply"],
        return_to="greeter",
        thread_id="...",
        initial_payload="<AddPayload>...</AddPayload>",
    )

    # Advance on step completion
    state = registry.advance(sequence_id, step_result="<AddResult>42</AddResult>")
    if state.is_complete:
        # All steps done
        registry.remove(sequence_id)

    # On error
    registry.mark_failed(sequence_id, step="calculator.add", error="XSD validation failed")
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
import threading


@dataclass
class SequenceState:
    """State for an active sequence execution."""

    sequence_id: str
    steps: List[str]              # Ordered list of listener names
    return_to: str                # Where to send final result
    thread_id: str                # Original thread for returning
    from_id: str                  # Who started the sequence

    current_index: int = 0        # Which step we're on (0-based)
    results: List[str] = field(default_factory=list)  # XML results from each step
    last_result: Optional[str] = None  # Most recent step result (for chaining)

    failed: bool = False
    failed_step: Optional[str] = None
    error: Optional[str] = None

    @property
    def is_complete(self) -> bool:
        """True when all steps have been executed successfully."""
        return not self.failed and self.current_index >= len(self.steps)

    @property
    def current_step(self) -> Optional[str]:
        """Get current step name, or None if complete/failed."""
        if self.failed or self.current_index >= len(self.steps):
            return None
        return self.steps[self.current_index]

    @property
    def remaining_steps(self) -> List[str]:
        """Steps not yet executed."""
        if self.failed:
            return []
        return self.steps[self.current_index:]


class SequenceRegistry:
    """
    Registry for active sequence executions.

    Thread-safe. Singleton pattern via get_sequence_registry().
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sequences: Dict[str, SequenceState] = {}

    def create(
        self,
        sequence_id: str,
        steps: List[str],
        return_to: str,
        thread_id: str,
        from_id: str,
        initial_payload: str = "",
    ) -> SequenceState:
        """
        Create a new sequence execution.

        Args:
            sequence_id: Unique ID for this sequence
            steps: Ordered list of listener names to call
            return_to: Listener to send SequenceComplete to
            thread_id: Thread UUID for routing
            from_id: Who initiated the sequence
            initial_payload: XML payload for first step

        Returns:
            SequenceState for tracking
        """
        state = SequenceState(
            sequence_id=sequence_id,
            steps=steps,
            return_to=return_to,
            thread_id=thread_id,
            from_id=from_id,
            last_result=initial_payload if initial_payload else None,
        )

        with self._lock:
            self._sequences[sequence_id] = state

        return state

    def get(self, sequence_id: str) -> Optional[SequenceState]:
        """Get sequence state by ID."""
        with self._lock:
            return self._sequences.get(sequence_id)

    def advance(self, sequence_id: str, step_result: str) -> Optional[SequenceState]:
        """
        Record step completion and advance to next step.

        Args:
            sequence_id: Sequence to advance
            step_result: XML result from the completed step

        Returns:
            Updated SequenceState, or None if not found
        """
        with self._lock:
            state = self._sequences.get(sequence_id)
            if state is None or state.failed:
                return state

            # Record result
            state.results.append(step_result)
            state.last_result = step_result
            state.current_index += 1

            return state

    def mark_failed(
        self,
        sequence_id: str,
        step: str,
        error: str,
    ) -> Optional[SequenceState]:
        """
        Mark a sequence as failed.

        Args:
            sequence_id: Sequence that failed
            step: Which step failed
            error: Error message

        Returns:
            Updated SequenceState, or None if not found
        """
        with self._lock:
            state = self._sequences.get(sequence_id)
            if state is None:
                return None

            state.failed = True
            state.failed_step = step
            state.error = error

            return state

    def remove(self, sequence_id: str) -> bool:
        """
        Remove a sequence (cleanup after completion).

        Returns:
            True if found and removed, False if not found
        """
        with self._lock:
            return self._sequences.pop(sequence_id, None) is not None

    def list_active(self) -> List[str]:
        """List all active sequence IDs."""
        with self._lock:
            return list(self._sequences.keys())

    def clear(self) -> None:
        """Clear all sequences. Useful for testing."""
        with self._lock:
            self._sequences.clear()


# ============================================================================
# Singleton
# ============================================================================

_registry: Optional[SequenceRegistry] = None
_registry_lock = threading.Lock()


def get_sequence_registry() -> SequenceRegistry:
    """Get the global SequenceRegistry singleton."""
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                _registry = SequenceRegistry()
    return _registry


def reset_sequence_registry() -> None:
    """Reset the global sequence registry (for testing)."""
    global _registry
    with _registry_lock:
        if _registry is not None:
            _registry.clear()
        _registry = None
