"""
LLM Router - the main entry point for LLM calls.

Agents just call:
    response = await router.complete(model="grok-4.1", messages=[...])

The router handles:
- Finding backends that serve the model
- Load balancing (failover, round-robin, least-loaded)
- Retries with exponential backoff
- Token tracking per agent
- Thread budget enforcement
- Usage event emission for billing
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Any, Optional

from xml_pipeline.llm.backend import (
    Backend,
    LLMRequest,
    LLMResponse,
    BackendError,
    RateLimitError,
    ProviderError,
    create_backend,
)

logger = logging.getLogger(__name__)


class Strategy(Enum):
    FAILOVER = "failover"        # Try in priority order, fail over on error
    ROUND_ROBIN = "round-robin"  # Rotate through backends
    LEAST_LOADED = "least-loaded"  # Pick backend with lowest current load


@dataclass
class AgentUsage:
    """Track token usage per agent."""
    total_tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    request_count: int = 0


@dataclass
class LLMRouter:
    """
    Routes LLM requests to appropriate backends.

    Config example:
        llm:
          strategy: failover
          retries: 3
          backends:
            - provider: xai
              api_key_env: XAI_API_KEY
            - provider: anthropic
              api_key_env: ANTHROPIC_API_KEY
    """
    backends: List[Backend] = field(default_factory=list)
    strategy: Strategy = Strategy.FAILOVER
    retries: int = 3
    retry_base_delay: float = 1.0
    retry_max_delay: float = 60.0

    # Per-agent token tracking
    _agent_usage: Dict[str, AgentUsage] = field(default_factory=dict, repr=False)

    # Round-robin state
    _rr_index: int = field(default=0, repr=False)
    _rr_lock: asyncio.Lock = field(default=None, repr=False)

    def __post_init__(self):
        self._rr_lock = asyncio.Lock()

    def add_backend(self, backend: Backend) -> None:
        """Add a backend to the router."""
        self.backends.append(backend)
        logger.info(f"Added backend: {backend.name} ({backend.provider})")

    def _find_backends(self, model: str) -> List[Backend]:
        """Find all backends that can serve this model."""
        candidates = [b for b in self.backends if b.serves_model(model)]

        if not candidates:
            raise ValueError(
                f"No backend available for model '{model}'. "
                f"Available backends: {[b.name for b in self.backends]}"
            )

        return candidates

    async def _select_backend(self, candidates: List[Backend]) -> Backend:
        """Select a backend based on strategy."""
        if self.strategy == Strategy.FAILOVER:
            # Sort by priority (lower = preferred)
            return sorted(candidates, key=lambda b: b.priority)[0]

        elif self.strategy == Strategy.ROUND_ROBIN:
            async with self._rr_lock:
                # Filter to just candidates, round-robin among them
                idx = self._rr_index % len(candidates)
                self._rr_index += 1
                return candidates[idx]

        elif self.strategy == Strategy.LEAST_LOADED:
            # Pick backend with lowest current load
            return min(candidates, key=lambda b: b.load)

        else:
            return candidates[0]

    async def complete(
        self,
        model: str,
        messages: List[Dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int = None,
        tools: List[Dict] = None,
        agent_id: str = None,
        thread_id: str = None,
        metadata: Dict[str, Any] = None,
    ) -> LLMResponse:
        """
        Execute a completion request.

        Args:
            model: Model name (e.g., "grok-4.1", "claude-sonnet-4")
            messages: Chat messages
            temperature: Sampling temperature
            max_tokens: Max tokens in response
            tools: Tool definitions for function calling
            agent_id: Optional agent ID for usage tracking
            thread_id: Optional thread ID for budget enforcement
            metadata: Optional metadata for usage events (org_id, user_id, etc.)

        Returns:
            LLMResponse with content and usage stats

        Raises:
            BudgetExhaustedError: If thread has no remaining budget
            BackendError: If all backends fail
        """
        # Estimate tokens for budget check (rough: 4 chars per token)
        estimated_tokens = sum(len(m.get("content", "")) for m in messages) // 4
        estimated_tokens = max(estimated_tokens, 100)  # minimum estimate

        # Check thread budget before proceeding
        if thread_id:
            from xml_pipeline.message_bus.budget_registry import get_budget_registry
            budget_registry = get_budget_registry()
            # This raises BudgetExhaustedError if over budget
            budget_registry.check_budget(thread_id, estimated_tokens)

        candidates = self._find_backends(model)
        request = LLMRequest(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools,
        )

        last_error = None
        tried_backends = set()
        start_time = time.monotonic()

        for attempt in range(self.retries + 1):
            # Select backend (different selection on retry for failover)
            if self.strategy == Strategy.FAILOVER and tried_backends:
                # Filter out already-tried backends
                remaining = [b for b in candidates if b.name not in tried_backends]
                if not remaining:
                    # All backends tried, start over with delay
                    remaining = candidates
                backend = sorted(remaining, key=lambda b: b.priority)[0]
            else:
                backend = await self._select_backend(candidates)

            tried_backends.add(backend.name)

            try:
                logger.debug(f"Attempting {model} on {backend.name} (attempt {attempt + 1})")
                response = await backend.complete(request)

                # Calculate latency
                latency_ms = (time.monotonic() - start_time) * 1000

                # Extract usage
                prompt_tokens = response.usage.get("prompt_tokens", 0)
                completion_tokens = response.usage.get("completion_tokens", 0)
                total_tokens = response.usage.get("total_tokens", 0)

                # Track per-agent usage (internal)
                if agent_id:
                    usage = self._agent_usage.setdefault(agent_id, AgentUsage())
                    usage.total_tokens += total_tokens
                    usage.prompt_tokens += prompt_tokens
                    usage.completion_tokens += completion_tokens
                    usage.request_count += 1

                # Record to thread budget (enforcement)
                crossed_thresholds = []
                if thread_id:
                    from xml_pipeline.message_bus.budget_registry import get_budget_registry
                    budget_registry = get_budget_registry()
                    _budget, crossed_thresholds = budget_registry.consume(
                        thread_id,
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                    )

                    # Inject BudgetWarning messages for any newly crossed thresholds
                    if crossed_thresholds:
                        await self._inject_budget_warnings(
                            thread_id=thread_id,
                            agent_id=agent_id,
                            crossed=crossed_thresholds,
                        )

                # Emit usage event (for billing)
                from xml_pipeline.llm.usage_tracker import get_usage_tracker
                tracker = get_usage_tracker()
                tracker.record(
                    thread_id=thread_id or "",
                    agent_id=agent_id,
                    model=response.model,
                    provider=backend.provider,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    latency_ms=latency_ms,
                    metadata=metadata,
                )

                return response

            except RateLimitError as e:
                last_error = e
                delay = e.retry_after or self._backoff_delay(attempt)
                logger.warning(f"Rate limited on {backend.name}, waiting {delay:.1f}s")
                await asyncio.sleep(delay)

            except ProviderError as e:
                last_error = e
                delay = self._backoff_delay(attempt)
                logger.warning(f"Provider error on {backend.name}: {e}, retrying in {delay:.1f}s")
                await asyncio.sleep(delay)

            except Exception as e:
                last_error = e
                logger.error(f"Unexpected error on {backend.name}: {e}")
                if attempt < self.retries:
                    delay = self._backoff_delay(attempt)
                    await asyncio.sleep(delay)

        # All retries exhausted
        raise BackendError(f"All backends failed for {model}: {last_error}") from last_error

    def _backoff_delay(self, attempt: int) -> float:
        """Calculate exponential backoff with jitter."""
        delay = self.retry_base_delay * (2 ** attempt)
        delay = min(delay, self.retry_max_delay)
        # Add jitter (±25%)
        jitter = delay * 0.25 * (random.random() * 2 - 1)
        return delay + jitter

    async def _inject_budget_warnings(
        self,
        thread_id: str,
        agent_id: Optional[str],
        crossed: List,  # List[BudgetThresholdCrossed]
    ) -> None:
        """
        Inject BudgetWarning messages when thresholds are crossed.

        Each crossed threshold generates a separate warning message
        sent to the agent via the stream pump.
        """
        from xml_pipeline.primitives.budget_warning import BudgetWarning

        # Get the stream pump (may not be initialized in tests)
        try:
            from xml_pipeline.message_bus.stream_pump import get_stream_pump
            pump = get_stream_pump()
        except RuntimeError:
            # Pump not initialized - log and skip
            logger.warning(
                f"Cannot inject BudgetWarning: StreamPump not initialized. "
                f"Thread {thread_id[:8]}... crossed thresholds: {[c.threshold_percent for c in crossed]}"
            )
            return

        for threshold in crossed:
            # Build human-readable message based on severity
            if threshold.severity == "final":
                message = (
                    f"CRITICAL: {threshold.percent_used:.0f}% of token budget used. "
                    f"Only {threshold.tokens_remaining:,} tokens remaining. "
                    f"Wrap up immediately or your next request may fail."
                )
            elif threshold.severity == "critical":
                message = (
                    f"WARNING: {threshold.percent_used:.0f}% of token budget used. "
                    f"{threshold.tokens_remaining:,} tokens remaining. "
                    f"Consider wrapping up your current task soon."
                )
            else:  # warning
                message = (
                    f"Note: {threshold.percent_used:.0f}% of token budget used. "
                    f"{threshold.tokens_remaining:,} tokens remaining."
                )

            warning_payload = BudgetWarning(
                percent_used=threshold.percent_used,
                tokens_used=threshold.tokens_used,
                tokens_remaining=threshold.tokens_remaining,
                max_tokens=threshold.max_tokens,
                severity=threshold.severity,
                message=message,
            )

            # Create envelope for the warning
            # Target is the agent that made the LLM call
            target = agent_id if agent_id else "system"

            envelope = pump._wrap_in_envelope(
                payload=warning_payload,
                from_id="system.budget",
                to_id=target,
                thread_id=thread_id,
            )

            # Inject into the pump's queue
            await pump.inject(envelope, thread_id=thread_id, from_id="system.budget")

            logger.info(
                f"BudgetWarning sent to {target}: {threshold.severity} "
                f"({threshold.percent_used:.0f}% used, {threshold.tokens_remaining:,} remaining)"
            )

    def get_agent_usage(self, agent_id: str) -> AgentUsage:
        """Get usage stats for an agent."""
        return self._agent_usage.get(agent_id, AgentUsage())

    def get_all_usage(self) -> Dict[str, AgentUsage]:
        """Get usage stats for all agents."""
        return dict(self._agent_usage)

    def reset_agent_usage(self, agent_id: str = None) -> None:
        """Reset usage stats for one or all agents."""
        if agent_id:
            self._agent_usage.pop(agent_id, None)
        else:
            self._agent_usage.clear()

    async def close(self) -> None:
        """Clean up all backends."""
        for backend in self.backends:
            await backend.close()


# =============================================================================
# Global Router Instance
# =============================================================================

_router: Optional[LLMRouter] = None


def get_router() -> LLMRouter:
    """Get the global router instance."""
    global _router
    if _router is None:
        _router = LLMRouter()
    return _router


def configure_router(config: Dict[str, Any]) -> LLMRouter:
    """
    Configure the global router from config dict.

    Config format:
        llm:
          strategy: failover
          retries: 3
          backends:
            - provider: xai
              api_key_env: XAI_API_KEY
              rate_limit_tpm: 100000
            - provider: anthropic
              api_key_env: ANTHROPIC_API_KEY
    """
    global _router

    strategy_str = config.get("strategy", "failover").lower().replace("-", "_")
    try:
        strategy = Strategy(strategy_str.replace("_", "-"))
    except ValueError:
        strategy = Strategy.FAILOVER

    _router = LLMRouter(
        strategy=strategy,
        retries=config.get("retries", 3),
        retry_base_delay=config.get("retry_base_delay", 1.0),
        retry_max_delay=config.get("retry_max_delay", 60.0),
    )

    for backend_config in config.get("backends", []):
        backend = create_backend(backend_config)
        _router.add_backend(backend)

    return _router


async def complete(
    model: str,
    messages: List[Dict[str, str]],
    *,
    thread_id: str = None,
    agent_id: str = None,
    metadata: Dict[str, Any] = None,
    **kwargs,
) -> LLMResponse:
    """
    Convenience function - calls get_router().complete().

    Usage:
        from xml_pipeline.llm import router
        response = await router.complete(
            "grok-4.1",
            messages,
            thread_id=metadata.thread_id,
            agent_id=metadata.own_name,
        )

    Args:
        model: Model name
        messages: Chat messages
        thread_id: Thread UUID for budget enforcement
        agent_id: Agent name for usage tracking
        metadata: Extra metadata for billing events
        **kwargs: Additional arguments (temperature, max_tokens, tools)

    Returns:
        LLMResponse with content and usage stats

    Raises:
        BudgetExhaustedError: If thread budget exhausted
    """
    return await get_router().complete(
        model,
        messages,
        thread_id=thread_id,
        agent_id=agent_id,
        metadata=metadata,
        **kwargs,
    )
