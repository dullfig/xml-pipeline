**AgentServer v2.1 — Organism Configuration**

This file is the canonical reference for `organism.yaml` format in v2.1.  
The old `configuration.md` is hereby obsolete and superseded.

The entire organism is declared in a single YAML file (default: `config/organism.yaml`).  
It is the single source of truth for initial composition, loaded at bootstrap.  
Runtime structural changes (add/remove/replace listeners) are performed exclusively via privileged OOB commands (hot-reload).

### Full Example (ResearchSwarm-01)

```yaml
organism:
  name: "ResearchSwarm-01"
  identity: "config/identity/private.ed25519"   # Ed25519 private key path
  port: 8765                                    # Main WSS message bus
  tls:
    cert: "certs/fullchain.pem"
    key: "certs/privkey.pem"

oob:                                            # Out-of-band privileged channel
  enabled: true
  bind: "127.0.0.1"                             # Localhost-only by default (GUI safe)
  port: 8766                                    # Separate WSS port from main bus
  # unix_socket: "/tmp/organism.sock"           # Alternative binding

thread_scheduling: "breadth-first"              # or "depth-first"

meta:
  enabled: true
  allow_list_capabilities: true
  allow_schema_requests: "admin"                # "admin" | "authenticated" | "none"
  allow_example_requests: "admin"
  allow_prompt_requests: "admin"
  allow_remote: false                           # Federation peers may query meta

listeners:
  - name: calculator.add
    payload_class: examples.calculator.AddPayload
    handler: examples.calculator.add_handler
    description: "Adds two integers and returns their sum."

  - name: calculator.multiply
    payload_class: examples.calculator.MultiplyPayload
    handler: examples.calculator.multiply_handler
    description: "Multiplies two integers and returns their product."

  - name: local_summarizer
    payload_class: agents.summarizer.SummarizePayload
    handler: agents.summarizer.summarize_handler
    description: "Summarizes text via local LLM."

  - name: researcher
    payload_class: agents.researcher.ResearchPayload
    handler: agents.researcher.research_handler
    description: "Primary research agent that reasons and coordinates tools."
    agent: true                                 # LLM agent → unique root tag, own_name exposed
    peers:                                      # Allowed call targets
      - calculator.add
      - calculator.multiply
      - local_summarizer
      - web_search                              # gateway group, defined below

  - name: search.google
    payload_class: gateways.google.SearchPayload
    handler: gateways.google.search_handler
    description: "Google search gateway."
    broadcast: true                             # Shares root tag with other search.* listeners

  - name: search.bing
    payload_class: gateways.google.SearchPayload      # Identical dataclass required
    handler: gateways.bing.search_handler
    description: "Bing search gateway."
    broadcast: true

gateways:
  - name: web_search
    remote_url: "wss://trusted-search-node.example.org"
    trusted_identity: "pubkeys/search_node.ed25519.pub"
    description: "Federated web search gateway group."

llm:
  strategy: failover                      # failover | round-robin | least-loaded
  retries: 3                              # Max retry attempts per request
  retry_base_delay: 1.0                   # Base delay for exponential backoff
  retry_max_delay: 60.0                   # Maximum delay between retries

  backends:
    - provider: xai
      api_key_env: XAI_API_KEY            # Read from environment
      priority: 1                         # Lower = preferred for failover
      rate_limit_tpm: 100000              # Tokens per minute
      max_concurrent: 20                  # Max concurrent requests

    - provider: anthropic
      api_key_env: ANTHROPIC_API_KEY
      priority: 2

    - provider: ollama
      base_url: http://localhost:11434
      supported_models: [llama3, mistral]
```

### Sections Explained

#### `organism`
Core identity and main bus.
- `name`: Human identifier, used in logs and discovery.
- `identity`: Path to Ed25519 private key (signing, federation, OOB auth).
- `port` / `tls`: Main encrypted message bus.

#### `oob`
Privileged local control channel (GUI/hot-reload ready).
- Disabled → fully static configuration (restart required for changes).
- Bound to localhost by default for security.

#### `thread_scheduling`
Subthread execution policy across the organism.
- `"breadth-first"` (default): fair round-robin, prevents deep branch starvation.
- `"depth-first"`: aggressive dive into branches.

#### `meta`
Introspection controls (`https://xml-pipeline.org/ns/meta/v1` namespace).
- Flags control who may request capability lists, schemas, examples, prompts.

#### `listeners`
All bounded capabilities (tools and agents).
- `name`: Unique registered name (dots allowed for hierarchy). Becomes prefix of derived root tag.
- `payload_class`: Full import path to `@xmlify` dataclass.
- `handler`: Full import path to async handler function.
- `description`: **Mandatory** short blurb — leads auto-generated tool prompts.
- `agent: true`: Designates LLM-driven listener → enforces unique root tag, exposes `own_name` in HandlerMetadata.
- `peers:`: List of registered names (or gateway groups) this listener is allowed to address. Enforced by pump for agents.
- `broadcast: true`: Opt-in flag allowing multiple listeners to share the exact same derived root tag (used for parallel gateways).

#### `gateways`
Federation peers (trusted remote organisms).
- Declared separately for clarity.
- Referenced in agent `peers:` lists by their registered `name`.

#### `llm`
LLM router configuration for agents. See `llm-router-v2.1.md` for complete specification.
- `strategy`: Backend selection strategy.
  - `failover` (default): Try backends in priority order, fail over on error.
  - `round-robin`: Distribute requests evenly across backends.
  - `least-loaded`: Route to backend with lowest current load.
- `retries`: Max retry attempts per request.
- `backends`: List of provider configurations.
  - `provider`: Provider type (`xai`, `anthropic`, `openai`, `ollama`).
  - `api_key_env`: Environment variable name containing the API key.
  - `priority`: Lower = preferred (for failover strategy).
  - `rate_limit_tpm`: Tokens per minute limit.
  - `max_concurrent`: Max concurrent requests to this backend.
  - `base_url`: Override default API endpoint (required for Ollama).
  - `supported_models`: Model names this backend handles (Ollama only).

### Environment Variables (.env)

API keys and secrets should **never** be stored in YAML. Use environment variables instead.

The bootstrap process automatically loads `.env` from the project root via `python-dotenv`:

```env
# .env (add to .gitignore!)
XAI_API_KEY=xai-abc123...
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
```

Reference in `organism.yaml` via `api_key_env`:

```yaml
llm:
  backends:
    - provider: xai
      api_key_env: XAI_API_KEY    # Reads from environment
```

### Key Invariants (v2.1)
- Root tag = `{lowercase_name}.{lowercase_dataclass_name}` — fully derived, never written manually.
- Registered names must be unique across the organism.
- Normal listeners have globally unique root tags.
- Broadcast listeners may share root tags intentionally (same dataclass required).
- Agents always have unique root tags (enforced automatically).
- All structural changes after bootstrap require privileged OOB hot-reload.

This YAML is the organism’s DNA — precise, auditable, minimal, and fully aligned with listener-class-v2.1.md.
