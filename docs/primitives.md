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
