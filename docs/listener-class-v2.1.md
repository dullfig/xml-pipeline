**listener-class-v2.1.md**
**January 10, 2026** (Updated)
**AgentServer v2.1 — The Listener Class & Registration**

This is the canonical documentation for defining and registering capabilities in AgentServer v2.1.  
All other descriptions of listener creation are superseded by this file.

### Purpose
A **Listener** declares a bounded, sovereign capability consisting of:
- A precise input contract (an `@xmlify` dataclass)
- A pure async handler function
- A mandatory human-readable description

From this declaration alone, the organism autonomously generates:
- Cached XSD schema
- Example payload
- Rich tool-prompt fragment
- Dedicated preprocessing pipeline
- Routing table entry with a fully derived, globally unique root tag

The developer supplies **only** Python code and a minimal YAML entry. Everything else is drift-proof and attack-resistant.

### Root Tag Derivation (Locked Rule)
The wire-format root element of every payload is constructed automatically as:

```
{lowercase_registered_name}.{lowercase_dataclass_name}
```

Examples:

| Registered name       | Dataclass name      | → Root tag on wire                       |
|-----------------------|---------------------|------------------------------------------|
| calculator.add        | AddPayload          | calculator.add.addpayload                |
| calculator.multiply   | MultiplyPayload     | calculator.multiply.multiplypayload      |
| researcher            | ResearchPayload     | researcher.researchpayload               |
| web_search            | SearchPayload       | web_search.searchpayload                 |

This derivation happens once at registration time.  
The developer **never** writes, repeats, or overrides the root tag.

### YAML Entry (organism.yaml)
Required fields:

```yaml
listeners:
  - name: calculator.add                     # Unique across organism, dots allowed for hierarchy
    payload_class: tools.calculator.AddPayload
    handler: tools.calculator.add_handler
    description: "Adds two integers and returns their sum."  # Mandatory for prompt generation

  - name: researcher
    payload_class: agents.researcher.ResearchPayload
    handler: agents.researcher.research_handler
    description: "Primary research agent that reasons and coordinates tools."
    agent: true                              # Flags LLM agent → unique root enforced, own_name exposed
    peers:                                   # Allowed targets this agent may address
      - calculator.add
      - web_search

  - name: search.google
    payload_class: gateways.google.SearchPayload
    handler: gateways.google.search_handler
    description: "Google search gateway."
    broadcast: true                          # Opt-in: permits sharing root tag with other search.* listeners
```

Optional flags:
- `agent: true` → designates an LLM-driven listener (enforces unique root tag, exposes `own_name` in metadata)
- `peers:` → list of registered names this listener is allowed to call (enforced by pump for agents)
- `broadcast: true` → allows multiple listeners to intentionally share the same derived root tag (used for parallel gateways/retrievers)

### Python Declaration
```python
from xmlable import xmlify
from dataclasses import dataclass
from agentserver.message_bus.message_state import HandlerMetadata, HandlerResponse

@xmlify
@dataclass
class AddPayload:
    """Addition request."""
    a: int = 0          # Field docstrings become parameter descriptions in prompts
    b: int = 0

@xmlify
@dataclass
class ResultPayload:
    """Calculation result."""
    value: int = 0

async def add_handler(
    payload: AddPayload,
    metadata: HandlerMetadata
) -> HandlerResponse:
    result = payload.a + payload.b
    return HandlerResponse.respond(
        payload=ResultPayload(value=result)
    )
```

### Handler Signature and Metadata (Locked)
See [handler-contract-v2.1.md](handler-contract-v2.1.md) for the canonical handler signature and metadata definition.

Typical uses:
- Stateful tools → key persistent data by `thread_id`
- Agents → reason about provenance using `from_id`, optionally refer to themselves via `own_name`

### Handler Return Requirements

Handlers return `HandlerResponse` or `None`:

| Return | Effect |
|--------|--------|
| `HandlerResponse(payload, to)` | Send payload to named target |
| `HandlerResponse.respond(payload)` | Return to caller (prunes call chain) |
| `None` | Terminate chain, no message emitted |

The pump handles all envelope wrapping. Handlers never construct XML envelopes.

**Correct examples**
```python
async def forward_handler(payload, metadata) -> HandlerResponse:
    return HandlerResponse(
        payload=ProcessedPayload(data="..."),
        to="next_listener",
    )

async def respond_handler(payload, metadata) -> HandlerResponse:
    result = compute(payload)
    return HandlerResponse.respond(
        payload=ResultPayload(value=result)
    )

async def terminal_handler(payload, metadata) -> None:
    print(payload.message)
    return None  # Chain ends here
```

### Envelope Injection

Handlers return typed `HandlerResponse` objects. The pump performs all enveloping:

1. Serialize payload dataclass to XML
2. Build envelope with correct metadata:
   - `<from>` = registered name of the executing listener (ALWAYS pump-injected)
   - `<to>` = validated target from HandlerResponse
   - `<thread>` = managed by thread registry (extended or pruned based on response type)
3. Re-inject into pipeline for validation and routing

**Security enforcement:**
- `<from>` is ALWAYS set by the pump (handlers cannot forge identity)
- `<to>` is validated against `peers` list for agents
- `<thread>` is managed by the thread registry (handlers cannot escape context)

See `handler-contract-v2.1.md` for complete security model.

### Registration Lifecycle
At startup or privileged OOB hot-reload:

1. Import `payload_class` and `handler`
2. Derive root tag (`registered_name.dataclass_name`)
3. Enforce global uniqueness (unless `broadcast: true`)
4. Validate mandatory description
5. Generate and cache XSD, example, and prompt fragment
6. Instantiate dedicated preprocessing pipeline
7. Insert into routing table

Any failure (duplicate root, missing description, import error) → clear error message and abort/reject.

### Best Practices
- Use hierarchical registered names (`category.sub.action`) for logical grouping and readable wire tags.
- Choose clear, specific dataclass names — they become permanent parts of the wire format.
- Always write a concise, accurate description — it leads every auto-generated tool prompt.
- For agents, declare only the minimal necessary `peers` — keeps prompts bounded and secure.
- Pure tools rarely need a `peers` entry.

### Summary of Key Invariants
- Root tag fully derived, never manually specified
- Global uniqueness guaranteed by registered-name prefix
- Handlers return typed `HandlerResponse` or `None` (never raw bytes or envelopes)
- Handlers receive trustworthy metadata including peer `usage_instructions` for LLMs
- All envelope construction and provenance injection performed exclusively by the pump
- `<from>` always pump-injected (handlers cannot forge identity)
- `<to>` validated against `peers` list for agents (cannot route to undeclared targets)
- `<thread>` managed by thread registry (handlers cannot escape context)
- Zero manual XSDs, examples, or prompt fragments required

---

**v2.1 Specification** — Updated January 10, 2026