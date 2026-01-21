# Configuration Reference

Organisms are configured via YAML files. The default location is `config/organism.yaml`.

## Minimal Configuration

```yaml
organism:
  name: my-organism

listeners:
  - name: greeter
    payload_class: handlers.hello.Greeting
    handler: handlers.hello.handle_greeting
    description: Greeting handler
```

## Full Configuration Reference

```yaml
# ============================================================
# ORGANISM SECTION
# Core identity and network settings
# ============================================================
organism:
  name: "my-organism"              # Human-readable name (required)
  port: 8765                       # WebSocket port (optional)
  identity: "config/identity.key"  # Ed25519 private key path (optional)
  tls:                             # TLS settings (optional)
    cert: "certs/fullchain.pem"
    key: "certs/privkey.pem"

# ============================================================
# LLM SECTION
# Language model routing configuration
# ============================================================
llm:
  strategy: failover               # failover | round-robin | least-loaded
  retries: 3                       # Max retry attempts
  retry_base_delay: 1.0            # Base delay for exponential backoff
  retry_max_delay: 60.0            # Maximum delay between retries

  backends:
    - provider: xai                # xai | anthropic | openai | ollama
      api_key_env: XAI_API_KEY     # Environment variable name
      priority: 1                  # Lower = preferred (for failover)
      rate_limit_tpm: 100000       # Tokens per minute limit
      max_concurrent: 20           # Max concurrent requests

    - provider: anthropic
      api_key_env: ANTHROPIC_API_KEY
      priority: 2

    - provider: ollama
      base_url: http://localhost:11434
      supported_models: [llama3, mistral]

# ============================================================
# BACKEND SECTION (Optional)
# Shared state for multiprocess deployments
# ============================================================
backend:
  type: memory                     # memory | manager | redis
  # Redis-specific settings (when type: redis)
  redis_url: "redis://localhost:6379"
  redis_prefix: "xp:"              # Key prefix for multi-tenancy
  redis_ttl: 86400                 # TTL in seconds (24 hours)

# ============================================================
# PROCESS POOL SECTION (Optional)
# Worker processes for CPU-bound handlers
# ============================================================
process_pool:
  workers: 4                       # Number of worker processes
  max_tasks_per_child: 100         # Restart workers after N tasks

# ============================================================
# LISTENERS SECTION
# Message handlers (tools and agents)
# ============================================================
listeners:
  # Simple tool (non-agent)
  - name: calculator.add
    payload_class: handlers.calc.AddPayload
    handler: handlers.calc.add_handler
    description: "Adds two numbers"

  # LLM Agent
  - name: researcher
    payload_class: handlers.research.ResearchQuery
    handler: handlers.research.research_handler
    description: "Research agent that searches and synthesizes"
    agent: true                    # Marks as LLM agent
    peers:                         # Allowed call targets
      - calculator.add
      - web_search
    prompt: |                      # System prompt for LLM
      You are a research assistant.
      Use tools to find information.

  # CPU-bound handler (runs in process pool)
  - name: librarian
    payload_class: handlers.librarian.Query
    handler: handlers.librarian.handle_query
    description: "Document analysis with heavy computation"
    cpu_bound: true                # Dispatch to ProcessPoolExecutor

# ============================================================
# GATEWAYS SECTION (Optional)
# Federation with remote organisms
# ============================================================
gateways:
  - name: remote_search
    remote_url: "wss://search.example.org"
    trusted_identity: "keys/search_node.pub"
    description: "Federated search gateway"
```

## Section Details

### organism

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | Yes | Human-readable organism name |
| `port` | int | No | WebSocket server port |
| `identity` | path | No | Ed25519 private key for signing |
| `tls.cert` | path | No | TLS certificate path |
| `tls.key` | path | No | TLS private key path |

### llm.backends[]

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `provider` | string | Yes | `xai`, `anthropic`, `openai`, `ollama` |
| `api_key_env` | string | Depends | Env var containing API key |
| `base_url` | string | No | Override API endpoint |
| `priority` | int | No | Lower = preferred (default: 1) |
| `rate_limit_tpm` | int | No | Tokens per minute limit |
| `max_concurrent` | int | No | Max concurrent requests |
| `supported_models` | list | No | Models this backend serves (ollama) |

### listeners[]

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | Yes | Unique listener name |
| `payload_class` | string | Yes | Import path to `@xmlify` dataclass |
| `handler` | string | Yes | Import path to handler function |
| `description` | string | Yes | Short description (used in prompts) |
| `agent` | bool | No | Is this an LLM agent? (default: false) |
| `peers` | list | No | Allowed call targets for agents |
| `prompt` | string | No | System prompt for LLM agents |
| `cpu_bound` | bool | No | Run in ProcessPoolExecutor (default: false) |
| `broadcast` | bool | No | Allow shared root tag (default: false) |

### backend

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | string | No | `memory`, `manager`, `redis` (default: memory) |
| `redis_url` | string | If redis | Redis connection URL |
| `redis_prefix` | string | No | Key prefix (default: `xp:`) |
| `redis_ttl` | int | No | Key TTL in seconds |

### process_pool

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `workers` | int | No | Number of worker processes (default: CPU count) |
| `max_tasks_per_child` | int | No | Tasks before worker restart |

## Environment Variables

API keys should be stored in environment variables, referenced via `api_key_env`:

```env
# .env file
XAI_API_KEY=xai-abc123...
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
```

## Validation

Validate your configuration without running:

```bash
xml-pipeline check config/organism.yaml
```

## See Also

- [[Quick Start]] — Get started quickly
- [[Writing Handlers]] — Create handlers
- [[LLM Router]] — LLM backend details
