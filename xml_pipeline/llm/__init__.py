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

Usage Persistence (for billing):
    from xml_pipeline.llm import get_usage_store

    store = await get_usage_store()

    # Query historical usage
    events = await store.query(
        start_time="2025-01-01T00:00:00Z",
        org_id="org-123",
    )

    # Get billing summary
    summary = await store.get_billing_summary(org_id="org-123")
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
from xml_pipeline.llm.usage_store import (
    UsageStore,
    BillingSummary,
    get_usage_store,
    close_usage_store,
    reset_usage_store,
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
    # Usage tracking (in-memory)
    "UsageTracker",
    "UsageEvent",
    "get_usage_tracker",
    "reset_usage_tracker",
    # Usage persistence (SQLite)
    "UsageStore",
    "BillingSummary",
    "get_usage_store",
    "close_usage_store",
    "reset_usage_store",
]
