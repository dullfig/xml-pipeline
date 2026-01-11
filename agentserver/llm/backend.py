"""
LLM Backend implementations.

Each backend wraps a specific provider's API (XAI, Anthropic, OpenAI, Ollama).
Backends are stateless HTTP clients - the Router handles orchestration.
"""

from __future__ import annotations

import asyncio
import os
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, AsyncIterator

import httpx

from agentserver.llm.token_bucket import TokenBucket

logger = logging.getLogger(__name__)


@dataclass
class LLMRequest:
    """Standardized request shape for all providers."""
    messages: List[Dict[str, str]]
    model: str
    temperature: float = 0.7
    max_tokens: Optional[int] = None
    tools: Optional[List[Dict]] = None
    stream: bool = False


@dataclass
class LLMResponse:
    """Standardized response shape."""
    content: str
    model: str
    usage: Dict[str, int]  # prompt_tokens, completion_tokens, total_tokens
    finish_reason: str
    raw: Any = None  # provider-specific raw response


class BackendError(Exception):
    """Base error for backend issues."""
    pass


class RateLimitError(BackendError):
    """Hit rate limit - should retry with backoff."""
    retry_after: Optional[float] = None


class ProviderError(BackendError):
    """Provider returned an error (5xx, etc)."""
    status_code: int = None


@dataclass
class Backend(ABC):
    """
    Abstract LLM backend.

    Handles:
    - HTTP client management
    - Request/response translation for specific provider
    - Concurrency limiting (semaphore)
    - Token bucket rate limiting
    """
    # Required fields first
    name: str
    api_key: str

    # Fields with defaults
    provider: str = ""
    base_url: str = ""
    priority: int = 1  # lower = preferred for failover
    rate_limit_tpm: int = 100000
    max_concurrent: int = 20
    timeout: float = 120.0

    # Runtime state (initialized in __post_init__)
    _semaphore: asyncio.Semaphore = field(default=None, repr=False)
    _token_bucket: TokenBucket = field(default=None, repr=False)
    _client: httpx.AsyncClient = field(default=None, repr=False)

    # Track current load for least-loaded balancing
    _active_requests: int = field(default=0, repr=False)

    def __post_init__(self):
        self._semaphore = asyncio.Semaphore(self.max_concurrent)
        self._token_bucket = TokenBucket(self.rate_limit_tpm)
        self._client = None  # Lazy init
        self._active_requests = 0

    async def _get_client(self) -> httpx.AsyncClient:
        """Lazy-init HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout,
                headers=self._auth_headers(),
            )
        return self._client

    @abstractmethod
    def _auth_headers(self) -> Dict[str, str]:
        """Provider-specific auth headers."""
        pass

    @abstractmethod
    def serves_model(self, model: str) -> bool:
        """Does this backend serve the given model?"""
        pass

    @abstractmethod
    async def _do_completion(self, client: httpx.AsyncClient, request: LLMRequest) -> LLMResponse:
        """Provider-specific completion logic."""
        pass

    async def complete(self, request: LLMRequest) -> LLMResponse:
        """
        Execute a completion request with rate limiting and concurrency control.
        """
        # Estimate tokens for rate limiting (rough: 4 chars per token)
        estimated_tokens = sum(len(m.get("content", "")) for m in request.messages) // 4
        estimated_tokens = max(estimated_tokens, 100)  # minimum estimate

        # Wait for rate limit bucket
        await self._token_bucket.acquire(estimated_tokens)

        # Wait for concurrency slot
        async with self._semaphore:
            self._active_requests += 1
            try:
                client = await self._get_client()
                response = await self._do_completion(client, request)

                # Adjust token bucket based on actual usage
                actual_tokens = response.usage.get("total_tokens", estimated_tokens)
                delta = actual_tokens - estimated_tokens
                if delta > 0:
                    # Used more than estimated - consume extra (non-blocking)
                    self._token_bucket.try_acquire(delta)

                return response

            finally:
                self._active_requests -= 1

    @property
    def load(self) -> float:
        """Current load factor (0-1) for least-loaded balancing."""
        return self._active_requests / self.max_concurrent

    async def close(self):
        """Clean up HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None


# =============================================================================
# Provider Implementations
# =============================================================================

@dataclass
class XAIBackend(Backend):
    """xAI (Grok) backend."""

    provider: str = "xai"
    base_url: str = "https://api.x.ai/v1"

    def _auth_headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}

    def serves_model(self, model: str) -> bool:
        return model.lower().startswith("grok")

    async def _do_completion(self, client: httpx.AsyncClient, request: LLMRequest) -> LLMResponse:
        payload = {
            "model": request.model,
            "messages": request.messages,
            "temperature": request.temperature,
        }
        if request.max_tokens:
            payload["max_tokens"] = request.max_tokens
        if request.tools:
            payload["tools"] = request.tools

        resp = await client.post("/chat/completions", json=payload)

        if resp.status_code == 429:
            retry_after = resp.headers.get("retry-after")
            err = RateLimitError(f"Rate limited by {self.provider}")
            err.retry_after = float(retry_after) if retry_after else None
            raise err

        if resp.status_code >= 500:
            err = ProviderError(f"{self.provider} server error: {resp.status_code}")
            err.status_code = resp.status_code
            raise err

        resp.raise_for_status()
        data = resp.json()

        return LLMResponse(
            content=data["choices"][0]["message"]["content"],
            model=data.get("model", request.model),
            usage=data.get("usage", {}),
            finish_reason=data["choices"][0].get("finish_reason", "stop"),
            raw=data,
        )


@dataclass
class AnthropicBackend(Backend):
    """Anthropic (Claude) backend."""

    provider: str = "anthropic"
    base_url: str = "https://api.anthropic.com/v1"

    def _auth_headers(self) -> Dict[str, str]:
        return {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
        }

    def serves_model(self, model: str) -> bool:
        model_lower = model.lower()
        return "claude" in model_lower or "anthropic" in model_lower

    async def _do_completion(self, client: httpx.AsyncClient, request: LLMRequest) -> LLMResponse:
        # Anthropic uses a different message format
        # Extract system message if present
        system = None
        messages = []
        for msg in request.messages:
            if msg["role"] == "system":
                system = msg["content"]
            else:
                messages.append(msg)

        payload = {
            "model": request.model,
            "messages": messages,
            "max_tokens": request.max_tokens or 4096,
        }
        if system:
            payload["system"] = system
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.tools:
            payload["tools"] = request.tools

        resp = await client.post("/messages", json=payload)

        if resp.status_code == 429:
            retry_after = resp.headers.get("retry-after")
            err = RateLimitError(f"Rate limited by {self.provider}")
            err.retry_after = float(retry_after) if retry_after else None
            raise err

        if resp.status_code >= 500:
            err = ProviderError(f"{self.provider} server error: {resp.status_code}")
            err.status_code = resp.status_code
            raise err

        resp.raise_for_status()
        data = resp.json()

        # Anthropic returns content as array of blocks
        content = ""
        for block in data.get("content", []):
            if block.get("type") == "text":
                content += block.get("text", "")

        return LLMResponse(
            content=content,
            model=data.get("model", request.model),
            usage={
                "prompt_tokens": data.get("usage", {}).get("input_tokens", 0),
                "completion_tokens": data.get("usage", {}).get("output_tokens", 0),
                "total_tokens": (
                    data.get("usage", {}).get("input_tokens", 0) +
                    data.get("usage", {}).get("output_tokens", 0)
                ),
            },
            finish_reason=data.get("stop_reason", "end_turn"),
            raw=data,
        )


@dataclass
class OpenAIBackend(Backend):
    """OpenAI (GPT) backend - also works with compatible APIs."""

    provider: str = "openai"
    base_url: str = "https://api.openai.com/v1"

    def _auth_headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}

    def serves_model(self, model: str) -> bool:
        model_lower = model.lower()
        return "gpt" in model_lower or "o1" in model_lower or "o3" in model_lower

    async def _do_completion(self, client: httpx.AsyncClient, request: LLMRequest) -> LLMResponse:
        payload = {
            "model": request.model,
            "messages": request.messages,
            "temperature": request.temperature,
        }
        if request.max_tokens:
            payload["max_tokens"] = request.max_tokens
        if request.tools:
            payload["tools"] = request.tools

        resp = await client.post("/chat/completions", json=payload)

        if resp.status_code == 429:
            retry_after = resp.headers.get("retry-after")
            err = RateLimitError(f"Rate limited by {self.provider}")
            err.retry_after = float(retry_after) if retry_after else None
            raise err

        if resp.status_code >= 500:
            err = ProviderError(f"{self.provider} server error: {resp.status_code}")
            err.status_code = resp.status_code
            raise err

        resp.raise_for_status()
        data = resp.json()

        return LLMResponse(
            content=data["choices"][0]["message"]["content"],
            model=data.get("model", request.model),
            usage=data.get("usage", {}),
            finish_reason=data["choices"][0].get("finish_reason", "stop"),
            raw=data,
        )


@dataclass
class OllamaBackend(Backend):
    """Ollama (local) backend."""

    provider: str = "ollama"
    base_url: str = "http://localhost:11434"
    supported_models: List[str] = field(default_factory=list)  # configured in yaml

    def _auth_headers(self) -> Dict[str, str]:
        return {}  # Ollama doesn't need auth

    def serves_model(self, model: str) -> bool:
        # If specific models configured, check against those
        if self.supported_models:
            return model.lower() in [m.lower() for m in self.supported_models]
        # Otherwise, assume it can try anything (local models)
        return True

    async def _do_completion(self, client: httpx.AsyncClient, request: LLMRequest) -> LLMResponse:
        # Ollama uses /api/chat
        payload = {
            "model": request.model,
            "messages": request.messages,
            "stream": False,
            "options": {
                "temperature": request.temperature,
            },
        }

        resp = await client.post("/api/chat", json=payload)
        resp.raise_for_status()
        data = resp.json()

        return LLMResponse(
            content=data["message"]["content"],
            model=data.get("model", request.model),
            usage={
                "prompt_tokens": data.get("prompt_eval_count", 0),
                "completion_tokens": data.get("eval_count", 0),
                "total_tokens": (
                    data.get("prompt_eval_count", 0) +
                    data.get("eval_count", 0)
                ),
            },
            finish_reason="stop",
            raw=data,
        )


# =============================================================================
# Factory
# =============================================================================

PROVIDER_CLASSES = {
    "xai": XAIBackend,
    "anthropic": AnthropicBackend,
    "openai": OpenAIBackend,
    "ollama": OllamaBackend,
}


def create_backend(config: Dict[str, Any]) -> Backend:
    """Create a backend from config dict."""
    provider = config.get("provider", "").lower()
    if provider not in PROVIDER_CLASSES:
        raise ValueError(f"Unknown provider: {provider}. Available: {list(PROVIDER_CLASSES.keys())}")

    cls = PROVIDER_CLASSES[provider]

    # Get API key from env var if specified
    api_key = config.get("api_key", "")
    if config.get("api_key_env"):
        api_key = os.environ.get(config["api_key_env"], "")

    return cls(
        name=config.get("name", provider),
        api_key=api_key,
        base_url=config.get("base_url", cls.__dataclass_fields__["base_url"].default),
        priority=config.get("priority", 1),
        rate_limit_tpm=config.get("rate_limit_tpm", 100000),
        max_concurrent=config.get("max_concurrent", 20),
        timeout=config.get("timeout", 120.0),
    )
