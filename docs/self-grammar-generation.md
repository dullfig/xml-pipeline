# Autonomous Registration & Introspection (v2.1)
**Updated: January 10, 2026**

In AgentServer v2.1, tool definition is radically simple: one `@xmlify` dataclass + handler + description. **No manual XSDs, no fragile JSON item mappings, no custom prompt engineering.** The organism auto-generates everything needed for validation, routing, and LLM wiring.

Manual XSDs, grammars, and tool descriptions are obsolete. Listeners **autonomously generate** their contracts and metadata at registration time. Introspection is a privileged core facility.

## The Developer Experience

Declare your payload contract as an `@xmlify` dataclass + a pure async handler function that returns `HandlerResponse` or `None`. Register with a name and description. That's it.

```python
from xmlable import xmlify
from dataclasses import dataclass
from agentserver.message_bus.message_state import HandlerMetadata, HandlerResponse

@xmlify
@dataclass
class AddPayload:
    """Addition capability."""
    a: int = 0  # First operand
    b: int = 0  # Second operand

@xmlify
@dataclass
class ResultPayload:
    """Calculation result."""
    value: int = 0

async def add_handler(payload: AddPayload, metadata: HandlerMetadata) -> HandlerResponse:
    result = payload.a + payload.b
    return HandlerResponse.respond(
        payload=ResultPayload(value=result)
    )

# LLM agent example
async def agent_handler(payload: AgentPayload, metadata: HandlerMetadata) -> HandlerResponse:
    # Build prompt with peer schemas
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
        payload=ThoughtPayload(content=response.content),
        to="next_peer",
    )
```

The pump:
1. Validates input against the XSD
2. Deserializes to typed dataclass instance
3. Calls handler with payload + metadata
4. Wraps returned payload in envelope with correct `<from>`, `<to>`, `<thread>`
5. Re-injects into pipeline for validation and routing

## Handler Contract (v2.1)

All handlers **must** follow this signature:

```python
async def handler(
    payload: PayloadDataclass,      # XSD-validated, deserialized @xmlify instance
    metadata: HandlerMetadata       # Trustworthy context from pump
) -> HandlerResponse | None:
    ...
```

- Handlers **must** be async (`async def`)
- Return `HandlerResponse` to send a message
- Return `None` to terminate chain (no message)

See `handler-contract-v2.1.md` for complete specification.

## Autonomous Chain Reaction on Registration

1. **XSD Synthesis**
   From `@xmlify` payload_class → generates/caches `schemas/calculator.add/v1.xsd`.
   Namespace: `https://xml-pipeline.org/ns/calculator/v1` (derived or explicit). Root = lowercase class name.

2. **Example & Prompt Synthesis**
   From dataclass fields + description:
   ```
   Tool: calculator.add
   Description: Adds two integers and returns their sum.

   Example Input:
   <add>
     <a>40</a>
     <b>2</b>
   </add>

   Params: a(int) - First operand, b(int) - Second operand
   Returns: Typed response payload
   ```
   Auto-injected into wired agents' system prompts via `metadata.usage_instructions`.

3. **Registry Update**
   Bus catalogs by `name` and `(namespace, root)`. Ready for routing + meta queries.

## Usage Instructions (Auto-Generated)

When an agent has declared `peers`, the pump automatically builds `usage_instructions` containing peer schemas:

```python
async def agent_handler(payload, metadata):
    # metadata.usage_instructions contains:
    # """
    # You can call the following tools by emitting their XML payloads:
    #
    # ## calculator.add
    # Adds two integers and returns their sum.
    #
    # ```xml
    # <addpayload xmlns="https://xml-pipeline.org/ns/calculator/v1">
    #   <a>40</a>
    #   <b>2</b>
    # </addpayload>
    # ```
    # ...
    # """
    pass
```

This replaces manual tool prompt engineering.

## Introspection: Privileged Meta Facility

Query the core MessageBus via reserved `https://xml-pipeline.org/ns/meta/v1`:

```xml
<message ...>
  <payload xmlns="https://xml-pipeline.org/ns/meta/v1">
    <request-schema>
      <capability>calculator.add</capability>
    </request-schema>
  </payload>
</message>
```

Core handler returns XSD bytes, example XML, or prompt fragment.
Controlled per YAML (`meta.allow_schema_requests: "admin"` etc.). No topology leaks.

Other ops: `request-example`, `request-prompt`, `list-capabilities`.

## Key Advantages

- **Zero Drift**: Edit dataclass → restart/hot-reload → XSD/example/prompt regenerate.
- **Attack-Resistant**: lxml XSD validation → typed instance → handler.
- **Type-Safe Returns**: Handlers return typed dataclasses, pump handles envelopes.
- **Peer-Aware Agents**: Auto-generated `usage_instructions` from peer schemas.
- **Sovereign Wiring**: YAML agents get live prompt fragments at startup.
- **Discoverable**: Namespaces served live at https://xml-pipeline.org/ns/... for tools and federation.

*The tool declares its contract and purpose. The organism enforces and describes it exactly.*

---

**v2.1 Specification** — Updated January 10, 2026
