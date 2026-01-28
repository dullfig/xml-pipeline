"""
LLM abstraction layer.

Usage:
    from xml_pipeline.llm import router

    # Configure once at startup (or via organism.yaml)
    router.configure_router({
        "strategy": "failover",
        "backends": [
            {"provider": "xai", "api_key_env": "XAI_API_KEY"},
        ]
    })

    # Then anywhere in your code:
    response = await router.complete(
        model="grok-4.1",
        messages=[{"role": "user", "content": "Hello"}],
        thread_id=metadata.thread_id,  # For budget enforcement
        agent_id=metadata.own_name,    # For usage tracking
    )

Usage Tracking:
    from xml_pipeline.llm import get_usage_tracker

    tracker = get_usage_tracker()

    # Subscribe to events for billing
    tracker.subscribe(lambda event: billing_api.record(event))

    # Query totals
    totals = tracker.get_totals()
"""

from xml_pipeline.llm.router import (
    LLMRouter,
    get_router,
    configure_router,
    complete,
    Strategy,
)
from xml_pipeline.llm.backend import LLMRequest, LLMResponse, BackendError
from xml_pipeline.llm.usage_tracker import (
    UsageTracker,
    UsageEvent,
    get_usage_tracker,
    reset_usage_tracker,
)

__all__ = [
    # Router
    "LLMRouter",
    "get_router",
    "configure_router",
    "complete",
    "Strategy",
    # Backend
    "LLMRequest",
    "LLMResponse",
    "BackendError",
    # Usage tracking
    "UsageTracker",
    "UsageEvent",
    "get_usage_tracker",
    "reset_usage_tracker",
]
