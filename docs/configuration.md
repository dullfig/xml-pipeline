# Configuration — organism.yaml

The entire organism is declared in a single YAML file (default: `config/organism.yaml`).  
All listeners, agents, and federation gateways are instantiated from this file at startup.  
Changes require a restart (hot-reload planned for future).

## Example Full Configuration

```yaml
organism:
  name: "ResearchSwarm-01"
  identity: "config/identity/private.ed25519"   # Ed25519 private key for signing
  port: 8765
  tls:
    cert: "certs/fullchain.pem"
    key: "certs/privkey.pem"

meta:
  enabled: true
  allow_list_capabilities: true      # Public catalog of capability names
  allow_schema_requests: "admin"     # "admin" | "authenticated" | "none"
  allow_example_requests: "admin"
  allow_prompt_requests: "admin"
  allow_remote: false                # Federation peers can query meta

listeners:
  - name: calculator.add
    payload_class: examples.calculator.AddPayload
    handler: examples.calculator.add_handler
    description: "Integer addition"

  - name: calculator.subtract
    payload_class: examples.calculator.SubtractPayload
    handler: examples.calculator.subtract_handler

  - name: summarizer
    payload_class: agents.summarizer.SummarizePayload
    handler: agents.summarizer.summarize_handler
    description: "Text summarization via local LLM"

agents:
  - name: researcher
    system_prompt: "prompts/researcher_system.txt"
    tools:
      - calculator.add
      - calculator.subtract
      - summarizer
      - name: web_search                # Remote tool via gateway below
        remote: true

gateways:
  - name: web_search
    remote_url: "wss://trusted-search-node.example.org"
    trusted_identity: "pubkeys/search_node.ed25519.pub"
    description: "Federated web search capability"
```

## Sections Explained

### `organism`
Core server settings.

- `name`: Human-readable identifier (used in logs, discovery).
- `identity`: Path to Ed25519 private key (for envelope signing, federation auth).
- `port` / `tls`: Single-port WSS configuration.

### `meta`
Controls the privileged introspection facility (`https://xml-platform.org/meta/v1`).

- `allow_list_capabilities`: Publicly visible catalog (safe).
- `allow_*_requests`: Restrict schema/example/prompt emission to admin or authenticated sessions.
- `allow_remote`: Whether federation peers can query your meta namespace.

### `listeners`
All bounded capabilities. Each entry triggers autonomous registration:

- `name`: Logical capability name (used in discovery, YAML tools lists). Dots allowed for hierarchy.
- `payload_class`: Full import path to the `@xmlify` dataclass (defines contract).
- `handler`: Full import path to the handler callable (`dict → bytes`).
- `description`: Optional human-readable text (included in `list-capabilities`).

At startup:
1. Import payload_class and handler.
2. Instantiate `Listener(payload_class=..., handler=..., name=...)`.
3. `bus.register(listener)` → XSD synthesis, Lark grammar generation, prompt caching.

Filesystem artifacts:
- XSDs cached as `schemas/<name_with_underscores>/v1.xsd` (dots → underscores for Linux safety).

### `agents`
LLM-based reasoning agents.

- `name`: Agent identifier.
- `system_prompt`: Path to static prompt file.
- `tools`: List of local capability names or remote references.
  - Local: direct name match (`calculator.add`).
  - Remote: `name:` + `remote: true` → routed via matching gateway.

Live capability prompts are auto-injected into the agent's system prompt at runtime (no stale copies).

### `gateways`
Federation peers.

- `name`: Local alias for the remote organism.
- `remote_url`: WSS endpoint.
- `trusted_identity`: Path to remote's Ed25519 public key.
- `description`: Optional.

Remote tools referenced in `agents.tools` are routed through the gateway with matching `name`.

## Future Extensions (planned)

- Hot-reload of configuration.
- Per-agent privilege scoping.
- Capability versioning in YAML (`version: v2`).

This YAML is the **single source of truth** for organism composition.  
Edit → restart → new bounded minds appear, fully self-describing and attack-resistant.
