**listener-class-v2.1.md**  
**January 07, 2026**  
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
from xml_pipeline import Listener, HandlerMetadata

@xmlify
@dataclass
class AddPayload:
    """Addition request."""
    a: int = 0          # Field docstrings become parameter descriptions in prompts
    b: int = 0

async def add_handler(
    payload: AddPayload,
    metadata: HandlerMetadata
) -> bytes:
    result = payload.a + payload.b
    return f"<result>{result}</result>".encode("utf-8")
```

### Handler Signature and Metadata (Locked)
See [handler-contract-v2.1.md](handler-contract-v2.1.md) for the canonical handler signature and metadata definition.

Typical uses:
- Stateful tools → key persistent data by `thread_id`
- Agents → reason about provenance using `from_id`, optionally refer to themselves via `own_name`

### Handler Return Requirements
The handler **must** return a `bytes` object containing one or more payload root elements.

Returning `None` or a non-`bytes` value is a programming error.

The message pump protects the organism by injecting a diagnostic payload if invalid bytes are returned:

```python
if response_bytes is None or not isinstance(response_bytes, bytes):
    response_bytes = b"<huh>Handler failed to return valid bytes — likely missing return statement or wrong type</huh>"
```

This ensures:
- The thread never hangs due to a forgotten return
- The error is immediately visible in logs and thread history
- LLM agents can observe and self-correct

**Correct examples**
```python
async def good(... ) -> bytes:
    return b"<result>42</result>"

async def also_good(... ) -> bytes:
    # fast synchronous-style computation
    return b"<empty/>"
```

**Dangerous (triggers <huh> injection)**
```python
async def bad(... ):
    computation()
    # forgot return → implicit None
```

Always explicitly return `bytes`.

### Multi-Payload Emission & Envelope Injection
Handlers return **raw XML fragments only**. The pump performs all enveloping:

1. Wrap response in `<dummy>` (tolerant of dirty output)
2. Extract all root elements found inside
3. For each extracted payload:
   - Inherit current `<thread>`
   - Inject `<from>` = registered name of the executing listener
   - Build full `<message>` envelope
   - Re-inject into the pipeline(s) matching the payload’s derived root tag

Example emission enabling parallel tool calls + self-continuation:

```python
async def researcher_step(... ) -> bytes:
    return b"""
    <thought>Need weather and a calculation...</thought>
    <web_search.searchpayload>
      <query>current weather Los Angeles</query>
    </web_search.searchpayload>
    <calculator.add.addpayload>
      <a>7</a>
      <b>35</b>
    </calculator.add.addpayload>
    """
```

The three payloads are automatically enveloped with correct provenance and routed.

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
- Handlers remain pure except for small trustworthy metadata
- All envelope construction and provenance injection performed exclusively by the pump
- Zero manual XSDs, examples, or prompt fragments required

This specification is now locked for AgentServer v2.1. All code, examples, and future documentation must align with this file.

--- 

Ready for the next piece (message pump implementation notes, OOB channel, stateful listener examples, etc.) whenever you are.