# llm_connection.py
import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

logger = logging.getLogger("agentserver.llm")


@dataclass
class LLMRequest:
    """Standardized request shape passed to all providers."""
    messages: List[Dict[str, str]]
    model: Optional[str] = None  # provider may ignore if fixed in config
    temperature: float = 0.7
    max_tokens: Optional[int] = None
    tools: Optional[List[Dict]] = None
    stream: bool = False
    # extra provider-specific kwargs
    extra: Dict[str, Any] = None


@dataclass
class LLMResponse:
    """Unified response shape."""
    content: str
    usage: Dict[str, int]  # prompt_tokens, completion_tokens, total_tokens
    finish_reason: str
    raw: Any = None  # provider-specific raw response for debugging


class LLMConnection(ABC):
    """Abstract base class for all LLM providers."""

    def __init__(self, name: str, config: dict):
        self.name = name
        self.config = config
        self.rate_limit_tpm: Optional[int] = config.get("rate-limit", {}).get("tokens-per-minute")
        self.max_concurrent: Optional[int] = config.get("max-concurrent-requests")
        self._semaphore = asyncio.Semaphore(self.max_concurrent or 20)
        self._token_bucket = None  # optional token bucket impl later

    @abstractmethod
    async def chat_completion(self, request: LLMRequest) -> LLMResponse:
        """Non-streaming completion."""
        pass

    @abstractmethod
    async def stream_completion(self, request: LLMRequest):
        """Async generator yielding partial content strings."""
        pass

    async def __aenter__(self):
        await self._semaphore.acquire()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self._semaphore.release()


class LLMConnectionPool:
    """
    Global, owner-controlled pool of LLM connections.
    Populated at boot or via signed privileged-command.
    """

    def __init__(self):
        self._pools: Dict[str, LLMConnection] = {}
        self._lock = asyncio.Lock()

    async def register(self, name: str, config: dict) -> None:
        """
        Add or replace a pool entry.
        Called only from boot config or validated privileged-command handler.
        """
        async with self._lock:
            provider_type = config.get("provider")
            if provider_type == "xai":
                connection = XAIConnection(name, config)
            elif provider_type == "anthropic":
                connection = AnthropicConnection(name, config)
            elif provider_type == "ollama" or provider_type == "local":
                connection = OllamaConnection(name, config)
            else:
                raise ValueError(f"Unknown LLM provider: {provider_type}")

            old = self._pools.get(name)
            if old:
                logger.info(f"Replacing LLM pool '{name}'")
            else:
                logger.info(f"Adding LLM pool '{name}'")

            self._pools[name] = connection

    async def remove(self, name: str) -> None:
        async with self._lock:
            if name in self._pools:
                del self._pools[name]
                logger.info(f"Removed LLM pool '{name}'")

    def get(self, name: str) -> LLMConnection:
        """Synchronous get — safe because pools don't change mid-request."""
        try:
            return self._pools[name]
        except KeyError:
            raise KeyError(f"LLM pool '{name}' not configured") from None

    def list_names(self) -> List[str]:
        return list(self._pools.keys())


# Example concrete providers (stubs — flesh out with real HTTP later)

class XAIConnection(LLMConnection):
    async def chat_completion(self, request: LLMRequest) -> LLMResponse:
        # TODO: real async httpx to https://api.x.ai/v1/chat/completions
        raise NotImplementedError

    async def stream_completion(self, request: LLMRequest):
        # yield partial deltas
        yield "streaming not yet implemented"


class AnthropicConnection(LLMConnection):
    async def chat_completion(self, request: LLMRequest) -> LLMResponse:
        raise NotImplementedError

    async def stream_completion(self, request: LLMRequest):
        raise NotImplementedError


class OllamaConnection(LLMConnection):
    async def chat_completion(self, request: LLMRequest) -> LLMResponse:
        raise NotImplementedError

    async def stream_completion(self, request: LLMRequest):
        raise NotImplementedError