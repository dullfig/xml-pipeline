"""
LLM Connection module - provides llm_pool for backward compatibility.

The actual implementation lives in agentserver.llm.router.
This module re-exports the router as llm_pool for listeners.
"""

from xml_pipeline.llm.router import get_router, configure_router, LLMRouter
from xml_pipeline.llm.backend import (
    LLMRequest,
    LLMResponse,
    Backend,
    BackendError,
    RateLimitError,
    ProviderError,
)

__all__ = [
    "llm_pool",
    "LLMRequest",
    "LLMResponse",
    "Backend",
    "BackendError",
    "RateLimitError",
    "ProviderError",
    "configure_router",
]


class LLMPool:
    """
    Wrapper around the LLM router that provides a simpler interface for listeners.

    Usage:
        from xml_pipeline.listeners.llm_connection import llm_pool

        response = await llm_pool.complete(
            model="grok-2",
            messages=[{"role": "user", "content": "Hello"}],
            temperature=0.7,
        )
    """

    def __init__(self):
        self._router: LLMRouter | None = None

    @property
    def router(self) -> LLMRouter:
        """Get or create the router instance."""
        if self._router is None:
            self._router = get_router()
        return self._router

    def configure(self, config: dict) -> None:
        """Configure the underlying router."""
        self._router = configure_router(config)

    async def complete(
        self,
        model: str,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        tools: list[dict] | None = None,
        agent_id: str | None = None,
    ) -> str:
        """
        Execute a completion and return just the content string.

        This is the simplified interface for listeners - returns just the
        response text, not the full LLMResponse object.
        """
        response = await self.router.complete(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools,
            agent_id=agent_id,
        )
        return response.content

    async def complete_full(
        self,
        model: str,
        messages: list[dict[str, str]],
        **kwargs,
    ) -> LLMResponse:
        """
        Execute a completion and return the full LLMResponse.

        Use this when you need access to usage stats, finish_reason, etc.
        """
        return await self.router.complete(model=model, messages=messages, **kwargs)

    def get_usage(self, agent_id: str):
        """Get usage stats for an agent."""
        return self.router.get_agent_usage(agent_id)

    async def close(self):
        """Clean up resources."""
        if self._router:
            await self._router.close()


# Global instance
llm_pool = LLMPool()
