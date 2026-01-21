# LLM Router

The LLM Router provides a unified interface for language model calls. Agents request a model by name; the router handles backend selection, failover, rate limiting, and retries.

## Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        Agent Handler                             │
│   response = await complete("grok-4.1", messages)               │
└─────────────────────────────────┬───────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────┐
│                         LLM Router                               │
│  • Find backends serving model                                   │
│  • Select backend (strategy)                                     │
│  • Retry on failure                                              │
│  • Track usage per agent                                         │
└────────────┬────────────────┬────────────────┬──────────────────┘
             │                │                │
             ▼                ▼                ▼
      ┌──────────┐     ┌──────────┐     ┌──────────┐
      │   XAI    │     │Anthropic │     │  Ollama  │
      │ Backend  │     │ Backend  │     │ Backend  │
      └──────────┘     └──────────┘     └──────────┘
```

## Quick Start

### Basic Usage

```python
from xml_pipeline.platform.llm_api import complete

response = await complete(
    model="grok-4.1",
    messages=[
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "Hello!"},
    ],
)

print(response.content)
```

### In a Handler

```python
async def my_agent(payload: Query, metadata: HandlerMetadata) -> HandlerResponse:
    from xml_pipeline.platform.llm_api import complete

    response = await complete(
        model="grok-4.1",
        messages=[
            {"role": "system", "content": metadata.usage_instructions},
            {"role": "user", "content": payload.question},
        ],
        temperature=0.7,
        max_tokens=2048,
    )

    return HandlerResponse(
        payload=Answer(text=response.content),
        to="output",
    )
```

## Configuration

### organism.yaml

```yaml
llm:
  strategy: failover           # Backend selection strategy
  retries: 3                   # Max retry attempts
  retry_base_delay: 1.0        # Base delay for backoff
  retry_max_delay: 60.0        # Max delay between retries

  backends:
    - provider: xai
      api_key_env: XAI_API_KEY
      priority: 1              # Lower = preferred
      rate_limit_tpm: 100000   # Tokens per minute
      max_concurrent: 20       # Concurrent request limit

    - provider: anthropic
      api_key_env: ANTHROPIC_API_KEY
      priority: 2

    - provider: openai
      api_key_env: OPENAI_API_KEY
      priority: 3

    - provider: ollama
      base_url: http://localhost:11434
      supported_models: [llama3, mistral]
```

### Environment Variables

```env
# .env file
XAI_API_KEY=xai-abc123...
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
```

## Supported Providers

| Provider | Models | Auth |
|----------|--------|------|
| `xai` | grok-* | Bearer token |
| `anthropic` | claude-* | x-api-key header |
| `openai` | gpt-*, o1-*, o3-* | Bearer token |
| `ollama` | Any local model | None (local) |

### Model Routing

The router automatically selects backends based on model name:

- `grok-4.1` → XAI backend
- `claude-sonnet-4` → Anthropic backend
- `gpt-4o` → OpenAI backend
- `llama3` → Ollama (if in `supported_models`)

## Strategies

### Failover (Default)

Tries backends in priority order. Falls back on error.

```yaml
llm:
  strategy: failover
  backends:
    - provider: xai
      priority: 1        # Try first
    - provider: anthropic
      priority: 2        # Fallback
```

### Round-Robin

Distributes requests evenly across backends.

```yaml
llm:
  strategy: round-robin
```

### Least-Loaded

Routes to the backend with lowest current load.

```yaml
llm:
  strategy: least-loaded
```

## Response Format

```python
@dataclass
class LLMResponse:
    content: str                    # Generated text
    model: str                      # Model used
    usage: Dict[str, int]           # Token counts
    finish_reason: str              # stop, length, tool_calls
    raw: Any                        # Provider-specific response
```

### Usage Dict

```python
response.usage = {
    "prompt_tokens": 150,
    "completion_tokens": 50,
    "total_tokens": 200,
}
```

## Parameters

```python
response = await complete(
    model="grok-4.1",              # Required: model name
    messages=[...],                # Required: conversation
    temperature=0.7,               # Optional: randomness (0-2)
    max_tokens=2048,               # Optional: response limit
    top_p=0.9,                     # Optional: nucleus sampling
    stop=["END"],                  # Optional: stop sequences
)
```

## Error Handling

### Rate Limits

On 429 responses:
1. Reads `Retry-After` header
2. Falls back to exponential backoff with jitter
3. Tries next backend (if failover)

### Provider Errors

On 5xx responses:
1. Logs error
2. Retries with backoff
3. Tries next backend (if failover)

### All Backends Failed

```python
from xml_pipeline.llm.router import BackendError

try:
    response = await complete(model, messages)
except BackendError as e:
    # All backends failed
    logger.error(f"LLM call failed: {e}")
```

## Rate Limiting

Each backend has independent limits:

- **Token bucket**: Limits tokens per minute (`rate_limit_tpm`)
- **Semaphore**: Limits concurrent requests (`max_concurrent`)

Requests wait if limits are reached.

## Token Tracking

Track usage per agent:

```python
from xml_pipeline.llm.router import get_router

router = get_router()

# Get usage for agent
usage = router.get_agent_usage("greeter")
print(f"Total tokens: {usage.total_tokens}")
print(f"Requests: {usage.request_count}")

# Reset tracking
router.reset_agent_usage("greeter")
```

## Best Practices

### 1. Use System Prompts

```python
response = await complete(
    model="grok-4.1",
    messages=[
        {"role": "system", "content": metadata.usage_instructions},
        {"role": "user", "content": payload.query},
    ],
)
```

### 2. Handle Errors Gracefully

```python
try:
    response = await complete(model, messages)
except BackendError:
    return HandlerResponse(
        payload=ErrorResponse(message="LLM unavailable"),
        to=metadata.from_id,
    )
```

### 3. Set Appropriate Limits

```yaml
llm:
  backends:
    - provider: xai
      rate_limit_tpm: 50000   # Conservative limit
      max_concurrent: 10      # Prevent overload
```

### 4. Use Failover for Reliability

```yaml
llm:
  strategy: failover
  backends:
    - provider: xai
      priority: 1
    - provider: anthropic
      priority: 2  # Backup
```

## See Also

- [[Writing Handlers]] — Using LLM in handlers
- [[Configuration]] — Full LLM configuration
- [[Architecture Overview]] — System architecture
