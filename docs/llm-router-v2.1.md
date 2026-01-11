# AgentServer v2.1 — LLM Router Abstraction
**January 10, 2026**

This document specifies the LLM router abstraction layer that provides model-agnostic access to language models for agents.

## Overview

The LLM router provides a unified interface for LLM calls. Agents simply request a model by name; the router handles:

- Backend selection (which provider serves this model)
- Load balancing (failover, round-robin, least-loaded)
- Retries with exponential backoff and jitter
- Rate limiting per backend
- Concurrency control
- Per-agent token tracking

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Agent Handler                             │
│   response = await complete("grok-4.1", messages, agent_id=...) │
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
      │   XAI    │     │Anthropic │     │  OpenAI  │  ...
      │ Backend  │     │ Backend  │     │ Backend  │
      └──────────┘     └──────────┘     └──────────┘
```

## Usage

### Simple Call

```python
from agentserver.llm import complete

response = await complete(
    model="grok-4.1",
    messages=[
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Hello!"},
    ],
)
print(response.content)
```

### With Agent Tracking

```python
response = await complete(
    model="grok-4.1",
    messages=messages,
    agent_id=metadata.own_name,  # Track tokens per agent
    temperature=0.7,
    max_tokens=2048,
)
```

### In Handler Context

```python
async def research_handler(payload: ResearchPayload, metadata: HandlerMetadata) -> HandlerResponse:
    from agentserver.llm import complete

    response = await complete(
        model="grok-4.1",
        messages=[
            {"role": "system", "content": metadata.usage_instructions},
            {"role": "user", "content": payload.query},
        ],
        agent_id=metadata.own_name,
    )

    return HandlerResponse(
        payload=ResearchResult(answer=response.content),
        to="summarizer",
    )
```

## Configuration

The router is configured via the `llm` section in `organism.yaml`:

```yaml
llm:
  strategy: failover      # failover | round-robin | least-loaded
  retries: 3              # Max retry attempts per request
  retry_base_delay: 1.0   # Base delay for exponential backoff
  retry_max_delay: 60.0   # Maximum delay between retries

  backends:
    - provider: xai
      api_key_env: XAI_API_KEY      # Read from environment variable
      priority: 1                    # Lower = preferred for failover
      rate_limit_tpm: 100000        # Tokens per minute limit
      max_concurrent: 20            # Max concurrent requests

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

API keys should be stored in environment variables (never in YAML). Create a `.env` file:

```env
XAI_API_KEY=xai-abc123...
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
```

The bootstrap process loads `.env` automatically via `python-dotenv`.

## Strategies

### Failover (Default)

Backends are tried in priority order. On failure, falls back to next available backend.

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

Requests are distributed evenly across backends.

```yaml
llm:
  strategy: round-robin
```

### Least-Loaded

Requests go to the backend with the lowest current load (fewest active requests relative to max_concurrent).

```yaml
llm:
  strategy: least-loaded
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
- Ollama matches configured `supported_models` or accepts all if not specified

## Response Format

```python
@dataclass
class LLMResponse:
    content: str                    # The generated text
    model: str                      # Actual model used
    usage: Dict[str, int]           # Token counts
    finish_reason: str              # stop, length, tool_calls, etc.
    raw: Any                        # Provider-specific raw response
```

Usage dict contains:
- `prompt_tokens`: Input token count
- `completion_tokens`: Output token count
- `total_tokens`: Sum of both

## Error Handling

### Rate Limits

On 429 responses, the router:
1. Reads `Retry-After` header if present
2. Falls back to exponential backoff with jitter
3. Tries next backend (if failover strategy)

### Provider Errors

On 5xx responses:
1. Logs the error
2. Retries with backoff
3. Tries next backend (if failover strategy)

### Exhausted Retries

If all retries fail, raises `BackendError`:

```python
try:
    response = await complete(model, messages)
except BackendError as e:
    # All backends failed
    logger.error(f"LLM call failed: {e}")
```

## Token Tracking

The router tracks tokens per agent for budgeting and monitoring:

```python
from agentserver.llm.router import get_router

router = get_router()

# Get usage for specific agent
usage = router.get_agent_usage("greeter")
print(f"Total tokens: {usage.total_tokens}")
print(f"Requests: {usage.request_count}")

# Get all usage
all_usage = router.get_all_usage()

# Reset tracking
router.reset_agent_usage("greeter")  # One agent
router.reset_agent_usage()            # All agents
```

## Rate Limiting

Each backend has independent rate limiting:

- **Token bucket**: Limits tokens per minute (`rate_limit_tpm`)
- **Semaphore**: Limits concurrent requests (`max_concurrent`)

Requests wait if either limit is reached. This prevents overwhelming provider APIs.

## Extensibility

### Adding a New Provider

1. Create a new backend class in `backend.py`:

```python
@dataclass
class MyProviderBackend(Backend):
    provider: str = "myprovider"
    base_url: str = "https://api.myprovider.com/v1"

    def _auth_headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}

    def serves_model(self, model: str) -> bool:
        return model.lower().startswith("mymodel")

    async def _do_completion(self, client: httpx.AsyncClient, request: LLMRequest) -> LLMResponse:
        # Provider-specific implementation
        ...
```

2. Register in `PROVIDER_CLASSES`:

```python
PROVIDER_CLASSES = {
    # ...existing providers...
    "myprovider": MyProviderBackend,
}
```

## Integration with Message Pump

The LLM router is initialized during organism bootstrap and is available globally. Token usage tracking integrates with the message pump's resource stewardship (thread-level token budgets).

Future: Token usage will be reported back to the message pump for per-thread budget enforcement.

---

**v2.1 Specification** — Updated January 10, 2026
