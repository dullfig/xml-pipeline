# AgentServer v2.1 — System Primitives
**Updated: January 10, 2026**

This document specifies system-level message types and handler return semantics.

## Handler Return Semantics

Handlers control message flow through their return value, not through magic XML tags.

### Forward to Target

```python
return HandlerResponse(
    payload=MyPayload(...),
    to="target_listener",
)
```
- Pump validates target against `peers` list (for agents)
- Extends thread chain: `a.b` → `a.b.target`
- Target receives the payload with updated thread

### Respond to Caller

```python
return HandlerResponse.respond(
    payload=ResultPayload(...)
)
```
- Pump looks up call chain from thread registry
- Prunes last segment (the responder)
- Routes to new tail (the caller)
- **Sub-threads are terminated** (calculator memory, scratch space, etc.)

### Terminate Chain

```python
return None
```
- No message emitted
- Chain ends here
- Thread can be cleaned up

## Thread Lifecycle & Pruning

Threads represent call chains through the system. The thread registry maps opaque UUIDs
to actual paths like `console.router.greeter.calculator`.

### Thread Creation

Threads are created when:
1. **External message arrives** — Console or WebSocket sends a message
2. **Handler forwards to peer** — `HandlerResponse(to="peer")` extends the chain

```
Console sends @greeter hello
  → Thread created: "system.organism.console.greeter"
  → UUID: 550e8400-e29b-41d4-...

Greeter forwards to shouter
  → Chain extended: "system.organism.console.greeter.shouter"
  → New UUID: 6ba7b810-9dad-...
```

### Thread Pruning (Critical)

Pruning happens when a handler returns `.respond()`:

```python
# In calculator handler
return HandlerResponse.respond(payload=ResultPayload(value=42))
```

**What happens:**
1. Registry looks up current chain: `console.router.greeter.calculator`
2. Prunes last segment: → `console.router.greeter`
3. Identifies target (new tail): `greeter`
4. Creates/reuses UUID for pruned chain
5. Routes response to `greeter` with the pruned thread

**Visual:**
```
Before pruning:
  console → router → greeter → calculator
                               ↑ (current)

After .respond():
  console → router → greeter
                     ↑ (response delivered here)
```

### What Gets Cleaned Up

When a thread is pruned or terminated:

| Resource | Cleanup Behavior |
|----------|------------------|
| Thread UUID mapping | Removed from registry |
| Context buffer slots | Slots for that thread are deleted |
| In-flight messages | Completed or dropped (no orphans) |
| Sub-thread branches | Automatically pruned (cascading) |

**Important:** Sub-threads spawned by a responding handler are effectively orphaned.
If `greeter` spawned `calculator` and `summarizer`, then responds to `router`, both
`calculator` and `summarizer` branches become unreachable.

### When Cleanup Happens

| Event | Cleanup |
|-------|---------|
| `.respond()` | Current UUID cleaned; pruned chain used |
| `return None` | Thread terminates; UUID can be cleaned |
| Chain exhausted | Root reached; entire chain cleaned |
| Idle timeout | (Future) Stale threads garbage collected |

### Thread Privacy

Handlers only see opaque UUIDs via `metadata.thread_id`. They never see:
- The actual call chain (`console.router.greeter`)
- Other thread UUIDs
- The thread registry

This prevents topology probing. Even if a handler is compromised, it cannot:
- Discover who called it (beyond `from_id` = immediate caller)
- Map the organism's structure
- Forge thread IDs to access other conversations

### Debugging Threads

For debugging, the registry provides `debug_dump()`:

```python
from xml_pipeline.message_bus.thread_registry import get_registry

registry = get_registry()
chains = registry.debug_dump()
# {'550e8400...': 'console.router.greeter', ...}
```

**Note:** This is for operator debugging only, never exposed to handlers.

## System Messages

These payload elements are emitted by the system (pump) only. Agents cannot emit them.

### `<huh>` — Validation Error

Emitted when message processing fails (XSD validation, unknown root tag, etc.).

```xml
<huh xmlns="https://xml-pipeline.org/ns/core/v1">
  <error>Invalid payload structure</error>
  <original-attempt>SGVsbG8gV29ybGQ=</original-attempt>
</huh>
```

| Field | Description |
|-------|-------------|
| `error` | Brief, canned error message (never raw validator output) |
| `original-attempt` | Base64-encoded raw bytes (truncated if large) |

**Security notes:**
- Error messages are intentionally abstract and generic
- Identical messages for "wrong schema" vs "capability doesn't exist"
- Prevents topology probing by agents or external callers
- Authorized introspection available via meta queries only

### `<SystemError>` — Routing/Delivery Failure

Emitted when a handler tries to send to an unauthorized or unreachable target.

```xml
<SystemError xmlns="">
  <code>routing</code>
  <message>Message could not be delivered. Please verify your target and try again.</message>
  <retry-allowed>true</retry-allowed>
</SystemError>
```

| Field | Description |
|-------|-------------|
| `code` | Error category: `routing`, `validation`, `timeout` |
| `message` | Generic, non-revealing description |
| `retry-allowed` | Whether agent can retry the operation |

**Key properties:**
- Keeps thread alive (agent can retry)
- Never reveals topology (no "target doesn't exist" vs "not authorized")
- Replaces the failed message in the flow

## Agent Iteration Patterns

### Blind Self-Iteration

LLM agents iterate by emitting payloads with their own root tag. With unique root tags per agent, this automatically routes back to themselves.

```python
# In agent handler
return HandlerResponse(
    payload=ThinkPayload(reasoning="Let me think more..."),
    to=metadata.own_name,  # Routes to self
)
```

The pump sets `is_self_call=True` in metadata for these messages.

### Visible Planning (Optional)

Agents may include planning constructs in their output for clarity:

```xml
<answer>
  I need to:
  <todo-until condition="have final answer">
    1. Search for relevant data
    2. Analyze results
    3. Synthesize conclusion
  </todo-until>

  Starting with step 1...
</answer>
```

**Note:** `<todo-until>` is NOT interpreted by the system. It's visible structured text that LLMs can use for planning. The actual iteration happens through normal message routing.

## Response Semantics Warning

**Critical for LLM agents:**

When you respond (return to caller via `.respond()`), your call chain is pruned:

- Any sub-agents you called are effectively terminated
- Their state/context is lost (calculator memory, scratch space, etc.)
- You cannot call them again in the same context after responding

**Therefore:** Complete ALL sub-tasks before responding. If you need results from a peer, wait for their response first.

This warning is automatically included in `usage_instructions` provided to agents.

---

**v2.1 Specification** — Updated January 10, 2026
