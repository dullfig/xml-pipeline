"""
Usage Tracker — Production billing and gas usage metering.

This module provides hooks for tracking LLM usage at the platform level.
External billing systems can subscribe to usage events for metering.

Usage Tracking Layers:
1. Per-agent (LLMRouter._agent_usage) — Internal token tracking
2. Per-thread (ThreadBudgetRegistry) — Enforcement limits
3. Platform (UsageTracker) — Production billing/metering

Example:
    from xml_pipeline.llm.usage_tracker import get_usage_tracker

    tracker = get_usage_tracker()

    # Subscribe to usage events (for billing webhook, database, etc.)
    def record_usage(event: UsageEvent):
        billing_db.record(
            org_id=event.metadata.get("org_id"),
            tokens=event.total_tokens,
            cost=event.estimated_cost,
        )

    tracker.subscribe(record_usage)

    # Query aggregate usage
    totals = tracker.get_totals()
    print(f"Total tokens: {totals['total_tokens']}")
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional


@dataclass
class UsageEvent:
    """
    Usage event emitted after each LLM completion.

    This is the main interface for billing systems.
    """

    # Request identification
    thread_id: str
    agent_id: Optional[str]
    model: str
    provider: str

    # Token usage
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int

    # Timing
    timestamp: str  # ISO 8601
    latency_ms: float  # Request duration

    # Cost estimation (if available)
    estimated_cost: Optional[float] = None

    # Extensible metadata (org_id, user_id, etc.)
    metadata: Dict[str, Any] = field(default_factory=dict)


# Cost per 1M tokens for common models (approximate, update as needed)
MODEL_COSTS: Dict[str, Dict[str, float]] = {
    # xAI Grok
    "grok-4.1": {"prompt": 3.0, "completion": 15.0},
    "grok-3": {"prompt": 3.0, "completion": 15.0},
    # Anthropic Claude
    "claude-opus-4": {"prompt": 15.0, "completion": 75.0},
    "claude-sonnet-4": {"prompt": 3.0, "completion": 15.0},
    "claude-sonnet-3-5": {"prompt": 3.0, "completion": 15.0},
    # OpenAI
    "gpt-4o": {"prompt": 2.5, "completion": 10.0},
    "gpt-4o-mini": {"prompt": 0.15, "completion": 0.6},
    "o1": {"prompt": 15.0, "completion": 60.0},
    "o3-mini": {"prompt": 1.1, "completion": 4.4},
}


def estimate_cost(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
) -> Optional[float]:
    """
    Estimate cost in USD for a completion.

    Returns None if model pricing is unknown.
    """
    # Normalize model name for lookup
    model_lower = model.lower()

    # Find matching pricing (prefer longest prefix match)
    pricing = None
    best_match_len = 0

    for model_prefix, costs in MODEL_COSTS.items():
        prefix_lower = model_prefix.lower()
        if model_lower.startswith(prefix_lower):
            if len(prefix_lower) > best_match_len:
                pricing = costs
                best_match_len = len(prefix_lower)

    if pricing is None:
        return None

    # Cost = (tokens / 1M) * cost_per_million
    prompt_cost = (prompt_tokens / 1_000_000) * pricing["prompt"]
    completion_cost = (completion_tokens / 1_000_000) * pricing["completion"]

    return round(prompt_cost + completion_cost, 6)


UsageCallback = Callable[[UsageEvent], None]


@dataclass
class UsageTotals:
    """Aggregate usage statistics."""

    total_tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    request_count: int = 0
    total_cost: float = 0.0
    total_latency_ms: float = 0.0


class UsageTracker:
    """
    Platform-level usage tracking for billing and metering.

    Thread-safe. Supports multiple subscribers for real-time event streaming.

    Integration points:
    - Webhook to billing API
    - Database for usage records
    - Metrics/observability (Prometheus, DataDog)
    - Real-time dashboard (WebSocket)
    """

    def __init__(self):
        self._callbacks: List[UsageCallback] = []
        self._lock = threading.Lock()

        # Aggregate tracking
        self._totals = UsageTotals()
        self._per_agent: Dict[str, UsageTotals] = {}
        self._per_model: Dict[str, UsageTotals] = {}

    def subscribe(self, callback: UsageCallback) -> None:
        """
        Subscribe to usage events.

        Callbacks are invoked synchronously after each LLM completion.
        For async processing, use a queue in your callback.
        """
        with self._lock:
            self._callbacks.append(callback)

    def unsubscribe(self, callback: UsageCallback) -> None:
        """Unsubscribe from usage events."""
        with self._lock:
            if callback in self._callbacks:
                self._callbacks.remove(callback)

    def record(
        self,
        thread_id: str,
        agent_id: Optional[str],
        model: str,
        provider: str,
        prompt_tokens: int,
        completion_tokens: int,
        latency_ms: float,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> UsageEvent:
        """
        Record a usage event and notify subscribers.

        Called by LLMRouter after each completion.

        Returns:
            The created UsageEvent (for chaining/logging)
        """
        total_tokens = prompt_tokens + completion_tokens

        event = UsageEvent(
            thread_id=thread_id,
            agent_id=agent_id,
            model=model,
            provider=provider,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            timestamp=datetime.now(timezone.utc).isoformat(),
            latency_ms=latency_ms,
            estimated_cost=estimate_cost(model, prompt_tokens, completion_tokens),
            metadata=metadata or {},
        )

        # Update aggregates
        with self._lock:
            self._update_totals(self._totals, event)

            if agent_id:
                if agent_id not in self._per_agent:
                    self._per_agent[agent_id] = UsageTotals()
                self._update_totals(self._per_agent[agent_id], event)

            if model not in self._per_model:
                self._per_model[model] = UsageTotals()
            self._update_totals(self._per_model[model], event)

            # Copy callbacks to avoid holding lock during invocation
            callbacks = list(self._callbacks)

        # Notify subscribers (outside lock)
        for callback in callbacks:
            try:
                callback(event)
            except Exception:
                # Don't let subscriber errors break tracking
                pass

        return event

    def _update_totals(self, totals: UsageTotals, event: UsageEvent) -> None:
        """Update aggregate totals from an event."""
        totals.total_tokens += event.total_tokens
        totals.prompt_tokens += event.prompt_tokens
        totals.completion_tokens += event.completion_tokens
        totals.request_count += 1
        totals.total_latency_ms += event.latency_ms
        if event.estimated_cost:
            totals.total_cost += event.estimated_cost

    def get_totals(self) -> Dict[str, Any]:
        """Get aggregate usage totals."""
        with self._lock:
            return {
                "total_tokens": self._totals.total_tokens,
                "prompt_tokens": self._totals.prompt_tokens,
                "completion_tokens": self._totals.completion_tokens,
                "request_count": self._totals.request_count,
                "total_cost": round(self._totals.total_cost, 4),
                "avg_latency_ms": (
                    self._totals.total_latency_ms / self._totals.request_count
                    if self._totals.request_count > 0
                    else 0
                ),
            }

    def get_agent_totals(self, agent_id: str) -> Dict[str, Any]:
        """Get usage totals for a specific agent."""
        with self._lock:
            totals = self._per_agent.get(agent_id, UsageTotals())
            return {
                "total_tokens": totals.total_tokens,
                "prompt_tokens": totals.prompt_tokens,
                "completion_tokens": totals.completion_tokens,
                "request_count": totals.request_count,
                "total_cost": round(totals.total_cost, 4),
            }

    def get_model_totals(self, model: str) -> Dict[str, Any]:
        """Get usage totals for a specific model."""
        with self._lock:
            totals = self._per_model.get(model, UsageTotals())
            return {
                "total_tokens": totals.total_tokens,
                "prompt_tokens": totals.prompt_tokens,
                "completion_tokens": totals.completion_tokens,
                "request_count": totals.request_count,
                "total_cost": round(totals.total_cost, 4),
            }

    def get_all_agent_totals(self) -> Dict[str, Dict[str, Any]]:
        """Get usage totals for all agents."""
        with self._lock:
            return {
                agent_id: {
                    "total_tokens": t.total_tokens,
                    "prompt_tokens": t.prompt_tokens,
                    "completion_tokens": t.completion_tokens,
                    "request_count": t.request_count,
                    "total_cost": round(t.total_cost, 4),
                }
                for agent_id, t in self._per_agent.items()
            }

    def get_all_model_totals(self) -> Dict[str, Dict[str, Any]]:
        """Get usage totals for all models."""
        with self._lock:
            return {
                model: {
                    "total_tokens": t.total_tokens,
                    "prompt_tokens": t.prompt_tokens,
                    "completion_tokens": t.completion_tokens,
                    "request_count": t.request_count,
                    "total_cost": round(t.total_cost, 4),
                }
                for model, t in self._per_model.items()
            }

    def reset(self) -> None:
        """Reset all tracking (for testing)."""
        with self._lock:
            self._totals = UsageTotals()
            self._per_agent.clear()
            self._per_model.clear()


# =============================================================================
# Global Instance
# =============================================================================

_tracker: Optional[UsageTracker] = None
_tracker_lock = threading.Lock()


def get_usage_tracker() -> UsageTracker:
    """Get the global usage tracker."""
    global _tracker
    if _tracker is None:
        with _tracker_lock:
            if _tracker is None:
                _tracker = UsageTracker()
    return _tracker


def reset_usage_tracker() -> None:
    """Reset the global tracker (for testing)."""
    global _tracker
    with _tracker_lock:
        if _tracker is not None:
            _tracker.reset()
        _tracker = None
