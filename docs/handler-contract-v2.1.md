# AgentServer v2.1 — Handler Contract
**January 10, 2026** (Updated)

This document is the single canonical specification for all capability handlers in AgentServer v2.1.
All examples, documentation, and implementation must conform to this contract.

## Handler Signature

Every handler **must** be declared with the following signature:

```python
async def handler(
    payload: PayloadDataclass,      # XSD-validated, deserialized @xmlify dataclass instance
    metadata: HandlerMetadata       # Trustworthy context provided by the message pump
) -> HandlerResponse | None:
    ...
```

- Handlers **must** be asynchronous (`async def`).
- Synchronous functions are not permitted and will not be auto-wrapped.
- The `metadata` parameter is mandatory.
- Return `HandlerResponse` to send a message, or `None` for no response.

## HandlerResponse

Handlers return a clean dataclass + target. The pump handles envelope wrapping.

```python
@dataclass
class HandlerResponse:
    payload: Any    # @xmlify dataclass instance
    to: str         # Target listener name (or use .respond() for caller)
```

### Forward to Named Target
```python
return HandlerResponse(
    payload=GreetingResponse(message="Hello!"),
    to="shouter",
)
```

### Respond to Caller (Prunes Call Chain)
```python
return HandlerResponse.respond(
    payload=ResultPayload(value=42)
)
```

When using `.respond()`, the pump:
1. Looks up the call chain from thread registry
2. Prunes the last segment (the responder)
3. Routes back to the caller
4. Sub-threads are terminated (see Response Semantics below)

### No Response
```python
return None  # Chain ends here, no message emitted
```

## HandlerMetadata

```python
@dataclass
class HandlerMetadata:
    thread_id: str                  # Opaque thread UUID — maps to hidden call chain
    from_id: str                    # Who sent this message (previous hop)
    own_name: str | None = None     # This listener's name (only if agent: true)
    is_self_call: bool = False      # True if message is from self
    usage_instructions: str = ""    # Auto-generated peer schemas for LLM prompts
```

### Field Rationale

| Field | Purpose |
|-------|---------|
| `thread_id` | Opaque UUID for thread-scoped storage. Maps internally to call chain (hidden from handler). |
| `from_id` | Previous hop in call chain. Useful for context but not for routing (use `.respond()`). |
| `own_name` | Enables self-referential reasoning. Only populated for `agent: true` listeners. |
| `is_self_call` | Detect self-messages (e.g., `<todo-until>` loops). |
| `usage_instructions` | Auto-generated from peer schemas. Inject into LLM system prompt. |

## Security Model

The message pump enforces security boundaries. Handlers are **untrusted code**.

### Envelope Control (Pump Enforced)

| Field | Handler Control | Pump Override |
|-------|-----------------|---------------|
| `from` | None | Always set to `listener.name` |
| `to` | Requests target | Validated against `peers` list |
| `thread` | None | Managed by thread registry |
| `payload` | Full control | — |

### Peer Constraint Enforcement

Agents can only send to listeners declared in their `peers` list:

```yaml
listeners:
  - name: greeter
    agent: true
    peers: [shouter, logger]  # Can only send to these
```

If an agent tries to send to an undeclared peer:
1. Message is **blocked** (never routed)
2. Details logged internally (for debugging)
3. Generic `SystemError` sent back to agent (no topology leak)
4. Thread stays alive — agent can retry

```xml
<SystemError>
  <code>routing</code>
  <message>Message could not be delivered. Please verify your target and try again.</message>
  <retry-allowed>true</retry-allowed>
</SystemError>
```

### Thread Privacy

- Handlers see opaque UUIDs, never actual call chains
- Call chain `console.router.greeter.shouter` → appears as `uuid-xyz`
- Even `from_id` only reveals immediate caller, not full path

## Response Semantics

**Critical for LLM agents to understand:**

When you **respond** (return to caller via `.respond()`), your call chain is pruned:

- Any sub-agents you called are effectively terminated
- Their state/context is lost (calculator memory, scratch space, etc.)
- You cannot call them again in the same context after responding

**Therefore:** Complete ALL sub-tasks before responding. If you need results from a peer, wait for their response first.

This is injected into `usage_instructions` automatically.

## Example Handlers

### Pure Tool (No Agent Flag)

```python
async def add_handler(payload: AddPayload, metadata: HandlerMetadata) -> HandlerResponse:
    result = payload.a + payload.b
    return HandlerResponse(
        payload=ResultPayload(value=result),
        to=metadata.from_id,  # Return to whoever called
    )
```

### LLM Agent

```python
async def research_handler(payload: ResearchPayload, metadata: HandlerMetadata) -> HandlerResponse:
    from agentserver.llm import complete

    # Build prompt with peer awareness
    system_prompt = metadata.usage_instructions + "\n\nYou are a research agent."

    response = await complete(
        model="grok-4.1",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": payload.query},
        ],
        agent_id=metadata.own_name,
    )

    return HandlerResponse(
        payload=ResearchResult(answer=response.content),
        to="summarizer",  # Forward to next agent
    )
```

### Terminal Handler (Display Only)

```python
async def console_display(payload: ConsoleOutput, metadata: HandlerMetadata) -> None:
    print(f"[{payload.source}] {payload.text}")
    return None  # End of chain
```

## Backwards Compatibility

Legacy handlers returning `bytes` are still supported but deprecated:

```python
# DEPRECATED - still works but not recommended
async def old_handler(payload, metadata) -> bytes:
    return b"<result>...</result>"
```

New code should use `HandlerResponse` for:
- Type safety
- Automatic envelope wrapping
- Peer constraint enforcement
- Thread chain management

---

**v2.1 Contract** — Updated January 10, 2026
