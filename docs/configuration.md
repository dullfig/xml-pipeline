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

auth:                                           # Authentication for OOB channel
  totp_secret_env: ORGANISM_TOTP_SECRET         # Env var holding base32 TOTP secret
  totp_required: true                           # Require TOTP on OOB connections

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
    timeout_seconds: 300                        # Handler timeout (default 30s)
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

peer_tables:                                    # Privilege tiers (subtract-only)
  - name: admin
    entries:
      - listener: researcher
        peers: [calculator.add, calculator.multiply, local_summarizer, web_search]

  - name: operator
    parent: admin                               # Ceiling = admin's peer list
    entries:
      - listener: researcher
        peers: [calculator.add, calculator.multiply]

  - name: viewer
    parent: operator
    entries:
      - listener: researcher
        peers: [calculator.add]

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

#### `auth`
Authentication settings for the OOB privileged channel.
- `totp_secret_env`: Environment variable name holding the base32 TOTP secret. When set, OOB connections require TOTP as a second factor alongside Ed25519 signing.
- `totp_required`: If `true`, connections without a valid TOTP token are rejected. Generate a secret with `xml-pipeline keygen --totp`.

**TOTP handshake:** When `totp_secret_env` is configured, the first message on each OOB connection must be:
```xml
<privileged-msg version="1.1">
  <payload id="auth"><totp-auth><token>123456</token></totp-auth></payload>
</privileged-msg>
```

#### `thread_scheduling` *(not yet implemented)*
Subthread execution policy across the organism.
- `"breadth-first"` (default): fair round-robin, prevents deep branch starvation.
- `"depth-first"`: aggressive dive into branches.

**Note:** This field is parsed but **not yet used** by the dispatcher. All messages are currently processed in FIFO order.

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
- `timeout_seconds`: Handler execution timeout in seconds (default `30`). On timeout, `SystemError(code="timeout")` is sent back to the caller; the thread stays alive. Use higher values for LLM agents that make external API calls.

#### `gateways`
Federation peers (trusted remote organisms).
- Declared separately for clarity.
- Referenced in agent `peers:` lists by their registered `name`.

**Note:** Gateways are **not yet implemented** at runtime. These declarations are parsed for forward-compatibility but no outbound connections or message forwarding occurs.

#### `peer_tables`
Named peer tables for thread-scoped privilege enforcement, declared in YAML. Tables use a **subtract-only hierarchy**: YAML `listener.peers` is the root ceiling — tables can only restrict, never expand beyond it.

- `name`: Unique table name (e.g., `"admin"`, `"operator"`, `"viewer"`).
- `parent`: Optional parent table name. If set, this table's ceiling is the parent's peer list instead of the YAML listener.peers. If omitted, ceiling comes from YAML.
- `entries`: List of `{ listener, peers }` mappings defining allowed peers for each listener in this table.

**Ceiling enforcement:**
- Every peer listed in a table must exist in its ceiling (parent table's peers, or YAML `listener.peers`).
- `modify_peer_table(..., grant=[])` also enforces the ceiling — you can restore revoked peers up to the ceiling but never exceed it.
- `modify_peer_table(..., revoke=[])` always succeeds (subtraction is always valid).

**Registration order:** Tables are registered in topological order during bootstrap (parents before children). Circular parent chains are detected and rejected.

```yaml
peer_tables:
  - name: admin
    # No parent → ceiling is YAML listener.peers
    entries:
      - listener: concierge
        peers: [calculator, search, billing]

  - name: operator
    parent: admin
    entries:
      - listener: concierge
        peers: [calculator, search]  # Must be subset of admin.concierge

  - name: viewer
    parent: operator
    entries:
      - listener: concierge
        peers: [calculator]          # Must be subset of operator.concierge
```

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

### Peer Tables (Runtime Privilege Tiers)

Peer tables provide thread-scoped privilege enforcement, allowing different threads
to use different peer mappings. They are registered at runtime (not in YAML) via
the programmatic API or OOB privileged commands.

**Programmatic API:**
```python
pump.register_peer_table("premium", {
    "concierge": ["calculator", "search", "billing"],
})
pump.register_peer_table("basic", {
    "concierge": ["calculator"],
})

# Inject with a specific table
await pump.inject("concierge", Request(query="help"), routing_table="premium")

# Modify at runtime (immediate effect on all threads using this table)
pump.modify_peer_table("premium", "concierge", revoke=["search"])
```

**OOB Commands:**

Register a peer table:
```xml
<register-peer-table xmlns="https://xml-pipeline.org/privileged-msg">
  <name>premium</name>
  <entries>
    <entry>
      <listener>concierge</listener>
      <peers><peer>calculator</peer><peer>search</peer></peers>
    </entry>
  </entries>
</register-peer-table>
```

Modify a peer table (grant/revoke):
```xml
<modify-peer-table xmlns="https://xml-pipeline.org/privileged-msg">
  <name>premium</name>
  <listener>concierge</listener>
  <grant><peer>billing</peer></grant>
  <revoke><peer>search</peer></revoke>
</modify-peer-table>
```

See [Public API](api.md#peer-tables) for full reference.

### Key Invariants (v2.1)
- Root tag = `{lowercase_name}.{lowercase_dataclass_name}` — fully derived, never written manually.
- Registered names must be unique across the organism.
- Normal listeners have globally unique root tags.
- Broadcast listeners may share root tags intentionally (same dataclass required).
- Agents always have unique root tags (enforced automatically).
- All structural changes after bootstrap require privileged OOB hot-reload.
- Peer tables are runtime-only (not declared in YAML); managed via API or OOB commands.
- Handler timeout is enforced via `asyncio.wait_for()` on every dispatch; default 30 seconds, configurable per-listener.

This YAML is the organism's DNA — precise, auditable, minimal, and fully aligned with listener-class-v2.1.md.
