"""
Thread Budget Registry — Enforces per-thread token limits.

Each thread has a token budget that tracks:
- Total tokens consumed (prompt + completion)
- Requests made
- Remaining budget

When a thread exhausts its budget, LLM calls are blocked.

Example config:
    organism:
      max_tokens_per_thread: 100000  # 100k tokens per thread
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class ThreadBudget:
    """Track token usage for a single thread."""

    max_tokens: int
    prompt_tokens: int = 0
    completion_tokens: int = 0
    request_count: int = 0

    @property
    def total_tokens(self) -> int:
        """Total tokens consumed."""
        return self.prompt_tokens + self.completion_tokens

    @property
    def remaining(self) -> int:
        """Remaining token budget."""
        return max(0, self.max_tokens - self.total_tokens)

    @property
    def is_exhausted(self) -> bool:
        """True if budget is exhausted."""
        return self.total_tokens >= self.max_tokens

    def can_consume(self, estimated_tokens: int) -> bool:
        """Check if we can consume the given tokens without exceeding budget."""
        return self.total_tokens + estimated_tokens <= self.max_tokens

    def consume(
        self,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
    ) -> None:
        """Record token consumption."""
        self.prompt_tokens += prompt_tokens
        self.completion_tokens += completion_tokens
        self.request_count += 1


class BudgetExhaustedError(Exception):
    """Raised when a thread's token budget is exhausted."""

    def __init__(self, thread_id: str, used: int, max_tokens: int):
        self.thread_id = thread_id
        self.used = used
        self.max_tokens = max_tokens
        super().__init__(
            f"Thread {thread_id[:8]}... budget exhausted: "
            f"{used}/{max_tokens} tokens used"
        )


class ThreadBudgetRegistry:
    """
    Manages token budgets per thread.

    Thread-safe for concurrent access.

    Usage:
        registry = get_budget_registry()
        registry.configure(max_tokens_per_thread=100000)

        # Before LLM call
        registry.check_budget(thread_id, estimated_tokens=1000)

        # After LLM call
        registry.consume(thread_id, prompt=500, completion=300)

        # Get usage
        budget = registry.get_budget(thread_id)
        print(f"Used: {budget.total_tokens}, Remaining: {budget.remaining}")
    """

    def __init__(self, max_tokens_per_thread: int = 100_000):
        """
        Initialize budget registry.

        Args:
            max_tokens_per_thread: Default budget for new threads.
        """
        self._max_tokens_per_thread = max_tokens_per_thread
        self._budgets: Dict[str, ThreadBudget] = {}
        self._lock = threading.Lock()

    def configure(self, max_tokens_per_thread: int) -> None:
        """
        Update default max tokens for new threads.

        Existing threads keep their current budgets.
        """
        with self._lock:
            self._max_tokens_per_thread = max_tokens_per_thread

    @property
    def max_tokens_per_thread(self) -> int:
        """Get the default max tokens per thread."""
        return self._max_tokens_per_thread

    def get_budget(self, thread_id: str) -> ThreadBudget:
        """
        Get or create budget for a thread.

        Args:
            thread_id: Thread UUID

        Returns:
            ThreadBudget instance
        """
        with self._lock:
            if thread_id not in self._budgets:
                self._budgets[thread_id] = ThreadBudget(
                    max_tokens=self._max_tokens_per_thread
                )
            return self._budgets[thread_id]

    def check_budget(
        self,
        thread_id: str,
        estimated_tokens: int = 0,
    ) -> bool:
        """
        Check if thread has budget for the estimated tokens.

        Args:
            thread_id: Thread UUID
            estimated_tokens: Estimated tokens for the request

        Returns:
            True if budget available

        Raises:
            BudgetExhaustedError if budget is exhausted
        """
        budget = self.get_budget(thread_id)

        if budget.is_exhausted:
            raise BudgetExhaustedError(
                thread_id=thread_id,
                used=budget.total_tokens,
                max_tokens=budget.max_tokens,
            )

        if not budget.can_consume(estimated_tokens):
            raise BudgetExhaustedError(
                thread_id=thread_id,
                used=budget.total_tokens,
                max_tokens=budget.max_tokens,
            )

        return True

    def consume(
        self,
        thread_id: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
    ) -> ThreadBudget:
        """
        Record token consumption for a thread.

        Args:
            thread_id: Thread UUID
            prompt_tokens: Prompt tokens used
            completion_tokens: Completion tokens used

        Returns:
            Updated ThreadBudget
        """
        budget = self.get_budget(thread_id)
        with self._lock:
            budget.consume(prompt_tokens, completion_tokens)
        return budget

    def get_usage(self, thread_id: str) -> Dict[str, int]:
        """
        Get usage stats for a thread.

        Returns:
            Dict with prompt_tokens, completion_tokens, total_tokens,
            remaining, max_tokens, request_count
        """
        budget = self.get_budget(thread_id)
        return {
            "prompt_tokens": budget.prompt_tokens,
            "completion_tokens": budget.completion_tokens,
            "total_tokens": budget.total_tokens,
            "remaining": budget.remaining,
            "max_tokens": budget.max_tokens,
            "request_count": budget.request_count,
        }

    def get_all_usage(self) -> Dict[str, Dict[str, int]]:
        """Get usage stats for all threads."""
        with self._lock:
            return {
                thread_id: {
                    "prompt_tokens": b.prompt_tokens,
                    "completion_tokens": b.completion_tokens,
                    "total_tokens": b.total_tokens,
                    "remaining": b.remaining,
                    "max_tokens": b.max_tokens,
                    "request_count": b.request_count,
                }
                for thread_id, b in self._budgets.items()
            }

    def reset_thread(self, thread_id: str) -> None:
        """Reset budget for a specific thread."""
        with self._lock:
            self._budgets.pop(thread_id, None)

    def cleanup_thread(self, thread_id: str) -> Optional[ThreadBudget]:
        """
        Remove budget when thread is pruned/completed.

        Returns the final budget for logging/billing, or None if not found.
        """
        with self._lock:
            return self._budgets.pop(thread_id, None)

    def clear(self) -> None:
        """Clear all budgets (for testing)."""
        with self._lock:
            self._budgets.clear()


# =============================================================================
# Global Instance
# =============================================================================

_registry: Optional[ThreadBudgetRegistry] = None
_registry_lock = threading.Lock()


def get_budget_registry() -> ThreadBudgetRegistry:
    """Get the global budget registry."""
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                _registry = ThreadBudgetRegistry()
    return _registry


def configure_budget_registry(max_tokens_per_thread: int) -> ThreadBudgetRegistry:
    """Configure the global budget registry."""
    registry = get_budget_registry()
    registry.configure(max_tokens_per_thread)
    return registry


def reset_budget_registry() -> None:
    """Reset the global registry (for testing)."""
    global _registry
    with _registry_lock:
        if _registry is not None:
            _registry.clear()
        _registry = None
