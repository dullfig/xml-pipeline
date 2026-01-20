"""
Token bucket for rate limiting LLM API calls.

Implements a classic token bucket algorithm:
- Bucket fills at a steady rate (tokens_per_minute / 60 per second)
- Requests consume tokens from the bucket
- If bucket is empty, request waits or fails
"""

import asyncio
import time
from dataclasses import dataclass, field


@dataclass
class TokenBucket:
    """
    Async-safe token bucket for rate limiting.

    Args:
        tokens_per_minute: Refill rate (TPM)
        burst_capacity: Max tokens the bucket can hold (defaults to TPM)
    """
    tokens_per_minute: int
    burst_capacity: int = None

    # Runtime state
    _tokens: float = field(default=None, repr=False)
    _last_refill: float = field(default=None, repr=False)
    _lock: asyncio.Lock = field(default=None, repr=False)

    def __post_init__(self):
        if self.burst_capacity is None:
            self.burst_capacity = self.tokens_per_minute
        self._tokens = float(self.burst_capacity)
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        """Add tokens based on elapsed time."""
        now = time.monotonic()
        elapsed = now - self._last_refill

        # tokens_per_second = tokens_per_minute / 60
        tokens_to_add = elapsed * (self.tokens_per_minute / 60.0)
        self._tokens = min(self.burst_capacity, self._tokens + tokens_to_add)
        self._last_refill = now

    async def acquire(self, tokens: int, timeout: float = None) -> bool:
        """
        Try to consume tokens from the bucket.

        Args:
            tokens: Number of tokens to consume
            timeout: Max seconds to wait (None = wait forever, 0 = don't wait)

        Returns:
            True if tokens acquired, False if timed out
        """
        deadline = None if timeout is None else time.monotonic() + timeout

        async with self._lock:
            while True:
                self._refill()

                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return True

                if timeout == 0:
                    return False

                # Calculate wait time for enough tokens
                tokens_needed = tokens - self._tokens
                wait_seconds = tokens_needed / (self.tokens_per_minute / 60.0)

                # Respect deadline
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return False
                    wait_seconds = min(wait_seconds, remaining)

                # Release lock while waiting
                self._lock.release()
                try:
                    await asyncio.sleep(wait_seconds)
                finally:
                    await self._lock.acquire()

    def try_acquire(self, tokens: int) -> bool:
        """Non-blocking acquire. Returns False if not enough tokens."""
        self._refill()
        if self._tokens >= tokens:
            self._tokens -= tokens
            return True
        return False

    @property
    def available(self) -> float:
        """Current tokens available (approximate, doesn't lock)."""
        self._refill()
        return self._tokens

    def report(self, actual_tokens: int) -> None:
        """
        Adjust after learning actual token usage.

        Call this after an LLM response when you know the real token count.
        If we estimated 1000 but it was actually 1200, consume 200 more.
        If we estimated 1000 but it was 800, give back 200.
        """
        # This is called after the fact, so we just need to track
        # the delta. The initial acquire already happened.
        pass  # For now, we'll handle this at the router level
