# BloxServer LLM Abstraction Layer — Resilient Multi-Provider Architecture

**Status:** Design
**Date:** January 2026

## Overview

The LLM abstraction layer is the critical path for all AI operations in BloxServer. It must handle:

- **Viral growth**: 100 → 10,000 users overnight
- **Provider outages**: Single provider down ≠ platform down
- **Fair access**: Paid users prioritized, free users served fairly
- **Cost control**: Platform keys vs BYOK (Bring Your Own Key)
- **Low latency**: Sub-second for simple calls, reasonable for complex

This document specifies the defense-in-depth architecture that survives success.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     LLM Abstraction Layer                        │
│                                                                  │
│  Request → [Rate Limit] → [Cache Check] → [Queue] → [Dispatch]  │
│                │               │              │          │       │
│                ▼               ▼              ▼          ▼       │
│           Per-user        Semantic       Priority   Provider    │
│           per-tier        cache          queues     pool +      │
│           limits          (30%+ hits)    (by tier)  failover    │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │ BYOK (Bring Your Own Key)                                   ││
│  │ Pro+ users with own API keys bypass platform limits         ││
│  └─────────────────────────────────────────────────────────────┘│
│                                                                  │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │ High Frequency Tier                                         ││
│  │ Dedicated capacity, custom SLA — contact sales              ││
│  └─────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────┘
```

## Tier Limits

| Tier | Price | Requests/min | Tokens/min | Concurrent | Latency SLA |
|------|-------|--------------|------------|------------|-------------|
| **Free** | $0 | 10 | 10,000 | 2 | Best effort |
| **Pro** | $29/mo | 60 | 100,000 | 10 | < 30s P95 |
| **Enterprise** | Custom | 300 | 500,000 | 50 | < 10s P95 |
| **High Frequency** | Custom | Custom | Custom | Dedicated | Custom SLA |
| **BYOK** (any tier) | — | Unlimited* | Unlimited* | 20 | User's provider |

*BYOK users are limited only by their own provider's rate limits.

### High Frequency Tier

For users requiring:
- **Low latency**: Sub-second response times
- **High throughput**: Thousands of requests per minute
- **Guaranteed capacity**: Dedicated provider allocations
- **Custom models**: Fine-tuned or private deployments

**Use cases:**
- Real-time trading signals
- Live customer support at scale
- High-volume content generation
- Latency-sensitive applications

**Pricing:** Custom — based on capacity reservation, SLA requirements, and volume.

**Landing page CTA:**
```
┌─────────────────────────────────────────────────────────────┐
│                                                              │
│  Need High Frequency?                                        │
│                                                              │
│  Building something that needs thousands of requests per     │
│  minute with sub-second latency? Let's talk dedicated        │
│  capacity and custom SLAs.                                   │
│                                                              │
│  [Contact Sales →]                                           │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

## Layer 1: Intake Rate Limiting

First line of defense. Rejects requests before they consume resources.

### Implementation

```python
from dataclasses import dataclass
from enum import Enum
import time

class Tier(Enum):
    FREE = "free"
    PRO = "pro"
    ENTERPRISE = "enterprise"
    HIGH_FREQUENCY = "high_frequency"

@dataclass
class TierLimits:
    requests_per_minute: int
    tokens_per_minute: int
    max_concurrent: int

TIER_LIMITS = {
    Tier.FREE: TierLimits(10, 10_000, 2),
    Tier.PRO: TierLimits(60, 100_000, 10),
    Tier.ENTERPRISE: TierLimits(300, 500_000, 50),
    Tier.HIGH_FREQUENCY: TierLimits(10_000, 10_000_000, 500),  # Custom per customer
}

@dataclass
class RateLimitResult:
    allowed: bool
    use_user_key: bool = False
    retry_after: int | None = None
    reason: str | None = None
    concurrent_key: str | None = None

async def rate_limit_check(user: User, request: LLMRequest) -> RateLimitResult:
    """Check if user can make this request."""

    # BYOK users bypass platform limits
    if user.has_own_api_key(request.provider):
        return RateLimitResult(allowed=True, use_user_key=True)

    limits = TIER_LIMITS[user.tier]

    # Check requests per minute (sliding window)
    rpm_key = f"ratelimit:{user.id}:rpm"
    now = time.time()
    window_start = now - 60

    # Remove old entries, add new one, count
    pipe = redis.pipeline()
    pipe.zremrangebyscore(rpm_key, 0, window_start)
    pipe.zadd(rpm_key, {str(now): now})
    pipe.zcard(rpm_key)
    pipe.expire(rpm_key, 120)
    _, _, current_rpm, _ = await pipe.execute()

    if current_rpm > limits.requests_per_minute:
        return RateLimitResult(
            allowed=False,
            retry_after=int(60 - (now - window_start)),
            reason=f"Rate limit: {limits.requests_per_minute} requests/minute"
        )

    # Check concurrent requests
    concurrent_key = f"ratelimit:{user.id}:concurrent"
    current_concurrent = await redis.incr(concurrent_key)
    await redis.expire(concurrent_key, 300)  # 5 min TTL as safety

    if current_concurrent > limits.max_concurrent:
        await redis.decr(concurrent_key)
        return RateLimitResult(
            allowed=False,
            retry_after=1,
            reason=f"Max concurrent: {limits.max_concurrent} requests"
        )

    return RateLimitResult(allowed=True, concurrent_key=concurrent_key)

async def release_concurrent(concurrent_key: str):
    """Release concurrent slot after request completes."""
    if concurrent_key:
        await redis.decr(concurrent_key)
```

### Rate Limit Headers

Return standard headers so clients can self-regulate:

```python
def rate_limit_headers(user: User) -> dict:
    limits = TIER_LIMITS[user.tier]
    current = await get_current_usage(user.id)

    return {
        "X-RateLimit-Limit": str(limits.requests_per_minute),
        "X-RateLimit-Remaining": str(max(0, limits.requests_per_minute - current.rpm)),
        "X-RateLimit-Reset": str(int(time.time()) + 60),
    }
```

## Layer 2: Semantic Cache

Identical requests return cached responses. Reduces load and cost.

### Cache Key Generation

```python
import hashlib
import json

def hash_request(request: LLMRequest) -> str:
    """Generate deterministic cache key for request."""

    # Include all parameters that affect output
    cache_input = {
        "model": request.model,
        "messages": [
            {"role": m.role, "content": m.content}
            for m in request.messages
        ],
        "temperature": request.temperature,
        "max_tokens": request.max_tokens,
        "tools": request.tools,  # Tool definitions matter
        # Exclude: user_id, timestamps, request_id
    }

    serialized = json.dumps(cache_input, sort_keys=True)
    return hashlib.sha256(serialized.encode()).hexdigest()[:32]
```

### Cache Logic

```python
@dataclass
class CachedResponse:
    response: LLMResponse
    cached_at: float
    hit_count: int

async def check_semantic_cache(request: LLMRequest) -> LLMResponse | None:
    """Check if we've seen this exact request before."""

    cache_key = f"llmcache:{hash_request(request)}"
    cached = await redis.get(cache_key)

    if cached:
        data = json.loads(cached)

        # Update hit count for analytics
        await redis.hincrby(f"llmcache:stats", "hits", 1)

        return LLMResponse(
            content=data["content"],
            model=data["model"],
            usage=data["usage"],
            cached=True,
        )

    await redis.hincrby(f"llmcache:stats", "misses", 1)
    return None

async def cache_response(request: LLMRequest, response: LLMResponse):
    """Cache response with TTL based on determinism."""

    # Don't cache errors or empty responses
    if response.error or not response.content:
        return

    cache_key = f"llmcache:{hash_request(request)}"

    # TTL based on temperature (determinism)
    if request.temperature == 0:
        ttl = 86400  # 24 hours for deterministic
    elif request.temperature < 0.3:
        ttl = 3600   # 1 hour
    elif request.temperature < 0.7:
        ttl = 300    # 5 minutes
    else:
        return       # Don't cache high-temperature responses

    cache_data = {
        "content": response.content,
        "model": response.model,
        "usage": response.usage,
        "cached_at": time.time(),
    }

    await redis.setex(cache_key, ttl, json.dumps(cache_data))
```

### Expected Cache Performance

| Use Case | Temperature | Expected Hit Rate |
|----------|-------------|-------------------|
| Tool calls (same inputs) | 0 | 70-90% |
| Structured extraction | 0-0.3 | 50-70% |
| Agent reasoning | 0.5-0.7 | 20-40% |
| Creative content | 0.8-1.0 | ~0% |

**Aggregate impact:** 30-40% reduction in API calls for typical workloads.

## Layer 3: Priority Queues

Paid users get priority. Free users are served fairly but can be shed under load.

### Queue Structure

```python
# Redis sorted set with composite score
# Score = (priority * 1B) + timestamp
# Lower score = higher priority + earlier arrival

QUEUE_PRIORITIES = {
    Tier.HIGH_FREQUENCY: 0,  # Highest priority (dedicated customers)
    Tier.ENTERPRISE: 1,
    Tier.PRO: 2,
    "trial": 2,              # Trials get Pro priority (first impression)
    Tier.FREE: 3,            # Lowest priority
}

@dataclass
class QueuedRequest:
    ticket_id: str
    user_id: str
    tier: str
    request: LLMRequest
    enqueued_at: float
    use_user_key: bool = False

async def enqueue_request(user: User, request: LLMRequest, use_user_key: bool) -> str:
    """Add request to priority queue, return ticket ID."""

    ticket_id = f"ticket:{uuid.uuid4().hex}"
    priority = QUEUE_PRIORITIES.get(user.tier, 3)

    # Composite score: priority (billions) + timestamp (seconds)
    score = priority * 1_000_000_000 + time.time()

    queued = QueuedRequest(
        ticket_id=ticket_id,
        user_id=str(user.id),
        tier=user.tier,
        request=request,
        enqueued_at=time.time(),
        use_user_key=use_user_key,
    )

    await redis.zadd("llm:queue", {json.dumps(asdict(queued)): score})

    # Set a result placeholder
    await redis.setex(f"llm:result:{ticket_id}", 300, "pending")

    return ticket_id
```

### Queue Workers

```python
async def queue_worker():
    """Process requests from the queue."""

    while True:
        # Get highest priority item (lowest score)
        items = await redis.zpopmin("llm:queue", count=1)

        if not items:
            await asyncio.sleep(0.1)  # Brief pause if queue empty
            continue

        data, score = items[0]
        queued = QueuedRequest(**json.loads(data))

        try:
            # Select provider and execute
            response = await execute_llm_request(queued)

            # Store result
            await redis.setex(
                f"llm:result:{queued.ticket_id}",
                300,
                json.dumps({"status": "success", "response": asdict(response)})
            )

        except Exception as e:
            await redis.setex(
                f"llm:result:{queued.ticket_id}",
                300,
                json.dumps({"status": "error", "error": str(e)})
            )

async def wait_for_result(ticket_id: str, timeout: float = 120) -> LLMResponse:
    """Wait for queued request to complete."""

    deadline = time.time() + timeout

    while time.time() < deadline:
        result = await redis.get(f"llm:result:{ticket_id}")

        if result and result != "pending":
            data = json.loads(result)
            if data["status"] == "success":
                return LLMResponse(**data["response"])
            else:
                raise LLMError(data["error"])

        await asyncio.sleep(0.1)

    raise RequestTimeout("Request timed out")
```

### Queue Health Monitoring

```python
@dataclass
class QueueHealth:
    size: int
    oldest_wait_seconds: float
    by_tier: dict[str, int]
    status: str  # healthy, degraded, critical

async def get_queue_health() -> QueueHealth:
    """Get queue metrics for monitoring and load shedding."""

    queue_size = await redis.zcard("llm:queue")

    # Get oldest item
    oldest = await redis.zrange("llm:queue", 0, 0, withscores=True)
    if oldest:
        oldest_score = oldest[0][1]
        oldest_time = oldest_score % 1_000_000_000
        wait_time = time.time() - oldest_time
    else:
        wait_time = 0

    # Count by tier
    all_items = await redis.zrange("llm:queue", 0, -1)
    by_tier = {}
    for item in all_items:
        data = json.loads(item)
        tier = data.get("tier", "unknown")
        by_tier[tier] = by_tier.get(tier, 0) + 1

    # Determine status
    if queue_size < 500:
        status = "healthy"
    elif queue_size < 2000:
        status = "degraded"
    else:
        status = "critical"

    return QueueHealth(
        size=queue_size,
        oldest_wait_seconds=wait_time,
        by_tier=by_tier,
        status=status,
    )
```

## Layer 4: Multi-Provider Pool with Circuit Breakers

Never depend on a single provider.

### Provider Configuration

```python
@dataclass
class ProviderConfig:
    name: str
    base_url: str
    api_key_env: str
    models: list[str]
    max_concurrent: int
    priority: int  # Lower = preferred
    timeout: float = 60.0

PROVIDERS = {
    "anthropic": ProviderConfig(
        name="anthropic",
        base_url="https://api.anthropic.com/v1",
        api_key_env="ANTHROPIC_API_KEY",
        models=["claude-sonnet-4-20250514", "claude-opus-4-20250514", "claude-haiku-3"],
        max_concurrent=100,
        priority=1,
    ),
    "openai": ProviderConfig(
        name="openai",
        base_url="https://api.openai.com/v1",
        api_key_env="OPENAI_API_KEY",
        models=["gpt-4o", "gpt-4o-mini", "o1", "o3-mini"],
        max_concurrent=50,
        priority=2,
    ),
    "xai": ProviderConfig(
        name="xai",
        base_url="https://api.x.ai/v1",
        api_key_env="XAI_API_KEY",
        models=["grok-3", "grok-3-mini"],
        max_concurrent=50,
        priority=1,
    ),
    "together": ProviderConfig(
        name="together",
        base_url="https://api.together.xyz/v1",
        api_key_env="TOGETHER_API_KEY",
        models=["llama-3-70b", "mixtral-8x7b"],
        max_concurrent=100,
        priority=3,  # Fallback
    ),
}
```

### Circuit Breaker State

```python
@dataclass
class CircuitState:
    provider: str
    healthy: bool = True
    failures: int = 0
    successes: int = 0
    last_failure: float = 0
    circuit_open_until: float = 0
    current_load: int = 0

# In-memory state (could be Redis for distributed)
CIRCUIT_STATES: dict[str, CircuitState] = {
    name: CircuitState(provider=name)
    for name in PROVIDERS
}

CIRCUIT_CONFIG = {
    "failure_threshold": 5,      # Failures before opening
    "success_threshold": 3,      # Successes before closing
    "open_duration": 30,         # Seconds circuit stays open
    "half_open_requests": 1,     # Requests allowed in half-open state
}

async def record_success(provider: str):
    """Record successful request."""
    state = CIRCUIT_STATES[provider]
    state.successes += 1
    state.failures = 0

    if not state.healthy and state.successes >= CIRCUIT_CONFIG["success_threshold"]:
        state.healthy = True
        logger.info(f"Circuit closed for {provider}")

async def record_failure(provider: str, error: Exception):
    """Record failed request, potentially open circuit."""
    state = CIRCUIT_STATES[provider]
    state.failures += 1
    state.successes = 0
    state.last_failure = time.time()

    if state.failures >= CIRCUIT_CONFIG["failure_threshold"]:
        state.healthy = False
        state.circuit_open_until = time.time() + CIRCUIT_CONFIG["open_duration"]
        logger.error(f"Circuit opened for {provider}: {error}")
        await alert_ops(f"LLM provider {provider} circuit opened")

def is_provider_available(provider: str) -> bool:
    """Check if provider can accept requests."""
    state = CIRCUIT_STATES[provider]
    config = PROVIDERS[provider]

    # Circuit open?
    if not state.healthy:
        if time.time() < state.circuit_open_until:
            return False
        # Half-open: allow limited requests to probe

    # At capacity?
    if state.current_load >= config.max_concurrent:
        return False

    return True
```

### Provider Selection

```python
def get_providers_for_model(model: str) -> list[str]:
    """Get providers that support this model."""
    return [
        name for name, config in PROVIDERS.items()
        if model in config.models or any(model.startswith(m.split("-")[0]) for m in config.models)
    ]

async def select_provider(request: LLMRequest, user_key: str | None = None) -> tuple[str, str]:
    """Select best available provider, return (provider_name, api_key)."""

    candidates = get_providers_for_model(request.model)

    if not candidates:
        raise UnsupportedModel(f"No provider supports model: {request.model}")

    # Filter to available providers
    available = [p for p in candidates if is_provider_available(p)]

    if not available:
        raise NoProvidersAvailable(
            "All providers for this model are currently unavailable. "
            "Please try again in a few seconds."
        )

    # Sort by priority, then by current load
    available.sort(key=lambda p: (
        PROVIDERS[p].priority,
        CIRCUIT_STATES[p].current_load / PROVIDERS[p].max_concurrent
    ))

    selected = available[0]

    # Determine API key
    if user_key:
        api_key = user_key
    else:
        api_key = os.environ[PROVIDERS[selected].api_key_env]

    return selected, api_key
```

## Layer 5: BYOK (Bring Your Own Key)

Pro+ users can add their own API keys to bypass platform limits.

### Database Schema

```sql
CREATE TABLE user_api_keys (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    provider VARCHAR(50) NOT NULL,
    encrypted_key BYTEA NOT NULL,
    key_hint VARCHAR(20),  -- Last 4 chars for display: "...abc123"
    is_valid BOOLEAN DEFAULT true,
    last_used_at TIMESTAMPTZ,
    last_error VARCHAR(255),
    created_at TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE(user_id, provider)
);

CREATE INDEX idx_user_api_keys_user ON user_api_keys(user_id);
```

### Key Encryption

```python
from cryptography.fernet import Fernet

# Platform encryption key (from environment, rotated periodically)
ENCRYPTION_KEY = Fernet(os.environ["API_KEY_ENCRYPTION_KEY"])

def encrypt_api_key(key: str) -> bytes:
    """Encrypt user's API key for storage."""
    return ENCRYPTION_KEY.encrypt(key.encode())

def decrypt_api_key(encrypted: bytes) -> str:
    """Decrypt user's API key for use."""
    return ENCRYPTION_KEY.decrypt(encrypted).decode()

async def store_user_api_key(user_id: str, provider: str, api_key: str):
    """Store encrypted API key for user."""

    # Validate key format
    if not validate_key_format(provider, api_key):
        raise InvalidAPIKey(f"Invalid {provider} API key format")

    # Test the key
    if not await test_api_key(provider, api_key):
        raise InvalidAPIKey(f"API key validation failed for {provider}")

    encrypted = encrypt_api_key(api_key)
    key_hint = f"...{api_key[-6:]}"

    await db.execute("""
        INSERT INTO user_api_keys (user_id, provider, encrypted_key, key_hint)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (user_id, provider)
        DO UPDATE SET encrypted_key = $3, key_hint = $4, is_valid = true, last_error = NULL
    """, user_id, provider, encrypted, key_hint)

async def get_user_api_key(user_id: str, provider: str) -> str | None:
    """Get decrypted API key for user, if they have one."""

    row = await db.fetchrow("""
        SELECT encrypted_key, is_valid
        FROM user_api_keys
        WHERE user_id = $1 AND provider = $2
    """, user_id, provider)

    if not row or not row["is_valid"]:
        return None

    return decrypt_api_key(row["encrypted_key"])
```

### BYOK Request Flow

```python
async def execute_with_byok(user: User, request: LLMRequest) -> LLMResponse:
    """Execute request, preferring user's own key if available."""

    # Check for user's key
    user_key = await get_user_api_key(user.id, get_provider_for_model(request.model))

    if user_key:
        # Use user's key - bypass platform rate limits
        try:
            response = await call_provider_direct(request, user_key)

            # Update last used
            await db.execute("""
                UPDATE user_api_keys
                SET last_used_at = NOW(), last_error = NULL
                WHERE user_id = $1 AND provider = $2
            """, user.id, request.provider)

            return response

        except AuthenticationError:
            # Key is invalid - mark it and fall back to platform
            await db.execute("""
                UPDATE user_api_keys
                SET is_valid = false, last_error = 'Authentication failed'
                WHERE user_id = $1 AND provider = $2
            """, user.id, request.provider)

            # Notify user
            await send_notification(user, "api_key_invalid", {
                "provider": request.provider
            })

            # Fall through to platform key

    # Use platform key (with rate limiting)
    return await execute_with_platform_key(user, request)
```

## Layer 6: Backpressure & Graceful Degradation

When overwhelmed, fail gracefully and prioritize paid users.

### Load Shedding

```python
async def should_shed_load(user: User, queue_health: QueueHealth) -> bool:
    """Determine if this request should be rejected to protect the system."""

    # High Frequency and Enterprise never shed
    if user.tier in [Tier.HIGH_FREQUENCY, Tier.ENTERPRISE]:
        return False

    # Pro shed only in critical
    if user.tier == Tier.PRO and queue_health.status != "critical":
        return False

    # Free tier shed in degraded or critical
    if user.tier == Tier.FREE and queue_health.status in ["degraded", "critical"]:
        # Probabilistic shedding based on queue size
        shed_probability = min(0.9, (queue_health.size - 500) / 2000)
        return random.random() < shed_probability

    return False
```

### Graceful Error Messages

```python
class ServiceDegraded(Exception):
    """Raised when load shedding rejects a request."""

    def __init__(self, tier: str, queue_health: QueueHealth):
        if tier == Tier.FREE:
            message = (
                "We're experiencing high demand. Free tier requests are "
                "temporarily paused. Upgrade to Pro for priority access, "
                "or try again in a few minutes."
            )
            retry_after = 60
        else:
            message = (
                "High demand is causing delays. Your request has been queued. "
                "Expected wait time: ~{} seconds."
            ).format(int(queue_health.oldest_wait_seconds * 1.5))
            retry_after = 30

        self.message = message
        self.retry_after = retry_after
        super().__init__(message)
```

### Timeout Handling

```python
async def execute_with_timeout(request: LLMRequest, provider: str, api_key: str) -> LLMResponse:
    """Execute request with appropriate timeout."""

    # Timeout based on expected response size
    if request.max_tokens and request.max_tokens > 2000:
        timeout = 120  # Long responses need more time
    else:
        timeout = 60

    try:
        async with asyncio.timeout(timeout):
            return await call_provider(request, provider, api_key)
    except asyncio.TimeoutError:
        await record_failure(provider, TimeoutError("Request timed out"))
        raise RequestTimeout(
            f"Request timed out after {timeout}s. "
            "Try reducing max_tokens or simplifying the prompt."
        )
```

## Main Entry Point

```python
async def handle_llm_request(user: User, request: LLMRequest) -> LLMResponse:
    """
    Main entry point for all LLM requests.
    Implements full defense-in-depth stack.
    """

    concurrent_key = None

    try:
        # Layer 1: Rate limiting
        rate_result = await rate_limit_check(user, request)
        if not rate_result.allowed:
            raise RateLimitExceeded(
                message=rate_result.reason,
                retry_after=rate_result.retry_after
            )
        concurrent_key = rate_result.concurrent_key

        # Layer 2: Semantic cache
        cached = await check_semantic_cache(request)
        if cached:
            return cached

        # Layer 3: Check queue health for load shedding
        queue_health = await get_queue_health()
        if await should_shed_load(user, queue_health):
            raise ServiceDegraded(user.tier, queue_health)

        # Layer 4: Enqueue with priority
        ticket_id = await enqueue_request(user, request, rate_result.use_user_key)

        # Layer 5: Wait for result
        response = await wait_for_result(ticket_id, timeout=120)

        # Layer 6: Cache successful response
        await cache_response(request, response)

        return response

    finally:
        # Always release concurrent slot
        if concurrent_key:
            await release_concurrent(concurrent_key)
```

## Monitoring & Alerts

### Key Metrics

| Metric | Source | Warning | Critical |
|--------|--------|---------|----------|
| Queue depth | Redis ZCARD | > 500 | > 2000 |
| P50 latency | Request timing | > 10s | > 30s |
| P99 latency | Request timing | > 60s | > 120s |
| Cache hit rate | Redis stats | < 25% | < 10% |
| Provider error rate | Circuit state | > 5% | > 20% |
| Circuit breaker open | Circuit state | Any | Multiple |
| Free tier rejection rate | Load shedding | > 20% | > 50% |

### Alerting

```python
# PagerDuty / Slack alerts
ALERTS = {
    "queue_critical": {
        "condition": lambda h: h.size > 2000,
        "severity": "critical",
        "message": "LLM queue depth critical: {size} requests backed up"
    },
    "provider_down": {
        "condition": lambda p: not p.healthy,
        "severity": "warning",
        "message": "Provider {name} circuit breaker open"
    },
    "all_providers_down": {
        "condition": lambda: all(not s.healthy for s in CIRCUIT_STATES.values()),
        "severity": "critical",
        "message": "ALL LLM providers are down!"
    },
}
```

### Dashboard Queries

```sql
-- Requests per minute by tier
SELECT
    date_trunc('minute', created_at) as minute,
    tier,
    COUNT(*) as requests
FROM llm_requests
WHERE created_at > NOW() - INTERVAL '1 hour'
GROUP BY 1, 2
ORDER BY 1 DESC;

-- Error rate by provider
SELECT
    provider,
    COUNT(*) FILTER (WHERE status = 'error') * 100.0 / COUNT(*) as error_rate
FROM llm_requests
WHERE created_at > NOW() - INTERVAL '1 hour'
GROUP BY provider;

-- BYOK adoption
SELECT
    tier,
    COUNT(*) FILTER (WHERE used_user_key) * 100.0 / COUNT(*) as byok_percentage
FROM llm_requests
WHERE created_at > NOW() - INTERVAL '24 hours'
GROUP BY tier;
```

## Viral Day Playbook

What to do when that tweet hits:

### Hour 0-1: Detection
- Alert: Queue depth > 500
- Action: Monitor, no intervention needed

### Hour 1-2: Escalation
- Alert: Queue depth > 1000, latency spiking
- Action:
  - Verify all provider circuits are healthy
  - Check cache hit rate (should be climbing)
  - Prepare to enable aggressive load shedding

### Hour 2-4: Peak
- Alert: Queue depth > 2000, free tier rejections > 30%
- Action:
  - Enable aggressive load shedding for free tier
  - Send "high demand" email to free users with upgrade CTA
  - Monitor Pro/Enterprise latency (must stay < 30s)
  - Tweet acknowledgment: "We're experiencing high demand due to [reason]. Pro users unaffected."

### Hour 4-8: Stabilization
- Queue draining as cache warms and load shedding works
- Many users convert to Pro or add BYOK keys
- Circuits recovering as providers stabilize

### Post-Mortem
- Review metrics: peak queue, rejection rate, conversion rate
- Adjust tier limits if needed
- Consider adding provider capacity for sustained growth

---

## References

- [Stripe-style rate limiting](https://stripe.com/docs/rate-limits)
- [Circuit breaker pattern](https://martinfowler.com/bliki/CircuitBreaker.html)
- [Token bucket algorithm](https://en.wikipedia.org/wiki/Token_bucket)
- [BloxServer Billing](bloxserver-billing.md) — Tier definitions and pricing
