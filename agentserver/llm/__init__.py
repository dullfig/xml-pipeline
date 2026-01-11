"""
LLM abstraction layer.

Usage:
    from agentserver.llm import router

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
    )
"""

from agentserver.llm.router import (
    LLMRouter,
    get_router,
    configure_router,
    complete,
    Strategy,
)
from agentserver.llm.backend import LLMRequest, LLMResponse, BackendError

__all__ = [
    "LLMRouter",
    "get_router",
    "configure_router",
    "complete",
    "Strategy",
    "LLMRequest",
    "LLMResponse",
    "BackendError",
]
