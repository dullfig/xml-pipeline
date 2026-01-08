# AgentServer v2.1 — Handler Contract
**January 08, 2026**

This document is the single canonical specification for all capability handlers in AgentServer v2.1.  
All examples, documentation, and implementation must conform to this contract.

## Handler Signature (Locked)

Every handler **must** be declared with the following exact signature:

```python
async def handler(
    payload: PayloadDataclass,      # XSD-validated, deserialized @xmlify dataclass instance
    metadata: HandlerMetadata       # Minimal trustworthy context provided by the message pump
) -> bytes:
    ...
```

- Handlers **must** be asynchronous (`async def`).
- Synchronous functions are not permitted and will not be auto-wrapped.
- The `metadata` parameter is mandatory.
- The return value **must** be a `bytes` object containing one or more raw XML payload fragments.
- Returning `None` or any non-`bytes` value is a programming error and will trigger a protective `<huh>` emission.

## HandlerMetadata

```python
@dataclass(frozen=True)
class HandlerMetadata:
    thread_id: str                  # Opaque thread UUID — safe for thread-scoped storage
    own_name: str | None = None     # Registered name of the executing listener.
                                    # Populated ONLY for listeners with `agent: true` in organism.yaml
```

### Field Rationale
- `thread_id`: Enables isolated per-thread state (e.g., conversation memory, calculator history) without exposing topology.
- `own_name`: Allows LLM agents to produce self-referential reasoning text while remaining blind to routing mechanics.

No sender identity (`from_id`) is provided — preserving full topology privacy.

## Security Model

The message pump captures all security-critical information (sender name, thread hierarchy, peers list enforcement) in trusted coroutine scope **before** invoking the handler.

Handlers are treated as **untrusted code**. They receive only the minimal safe context defined above and cannot:
- Forge provenance
- Escape thread boundaries
- Probe or leak topology
- Route arbitrarily

## Example Handlers

**Pure tool (no agent flag):**
```python
async def add_handler(payload: AddPayload, metadata: HandlerMetadata) -> bytes:
    result = payload.a + payload.b
    return f"<result>{result}</result>".encode("utf-8")
```

**LLM agent (agent: true):**
```python
async def research_handler(payload: ResearchPayload, metadata: HandlerMetadata) -> bytes:
    own = metadata.own_name or "researcher"  # safe fallback
    return b"""
    <thought>I am the """ + own.encode() + b""" agent. Next step...</thought>
    <calculator.add.addpayload><a>7</a><b>35</b></calculator.add.addpayload>
    """
```

## References in Other Documentation

- All code examples in README.md, self-grammar-generation.md, and configuration.md must match this contract.
- listener-class-v2.1.md now references this file as the authoritative source for signature and metadata.

---

This contract is now **locked** for v2.1