# Handler Contract

The complete specification for handler functions in xml-pipeline.

## Signature

Every handler must be declared as:

```python
async def handler(
    payload: PayloadDataclass,
    metadata: HandlerMetadata
) -> HandlerResponse | None:
    ...
```

### Requirements

| Aspect | Requirement |
|--------|-------------|
| Function type | Must be `async def` |
| First parameter | XSD-validated `@xmlify` dataclass |
| Second parameter | `HandlerMetadata` (required) |
| Return type | `HandlerResponse` or `None` |

## HandlerMetadata

```python
@dataclass
class HandlerMetadata:
    thread_id: str              # Opaque thread UUID
    from_id: str                # Who sent this message
    own_name: str | None        # This listener's name (agents only)
    is_self_call: bool          # True if message from self
    usage_instructions: str     # Peer schemas for LLM prompts
    todo_nudge: str             # System reminders
```

### Field Details

#### thread_id

Opaque UUID for the current conversation thread.

- Use for thread-scoped storage
- Never parse or make assumptions about format
- Maps internally to call chain (hidden)

#### from_id

The registered name of the listener that sent this message.

- Only shows immediate sender, not full chain
- Use for logging/debugging
- Don't use for routing (use `HandlerResponse.to`)

#### own_name

This listener's registered name. Only set for agents (`agent: true`).

- Enables self-referential reasoning
- Used for self-iteration: `to=metadata.own_name`
- `None` for non-agent listeners

#### is_self_call

`True` if this message was sent by this same handler.

- Detect iteration loops
- Handle self-messages differently if needed

#### usage_instructions

Auto-generated documentation of peer capabilities.

- Contains XSD schemas of declared peers
- Inject into LLM system prompts
- Empty if no peers declared

#### todo_nudge

System-generated reminders about pending todos.

- Contains info about raised watchers
- Used by agents for task tracking
- Empty if no pending todos

## HandlerResponse

```python
@dataclass
class HandlerResponse:
    payload: Any    # @xmlify dataclass instance
    to: str         # Target listener name
```

### Construction Methods

#### Direct Construction

Forward to a specific listener:

```python
return HandlerResponse(
    payload=MyResponse(data="result"),
    to="next-handler",
)
```

#### Respond to Caller

Return to whoever sent the message:

```python
return HandlerResponse.respond(
    payload=ResultPayload(value=42)
)
```

### Return None

End the chain with no response:

```python
return None
```

## Return Types

| Return | Effect |
|--------|--------|
| `HandlerResponse(to="x")` | Forward to listener "x" |
| `HandlerResponse.respond()` | Return to caller (prunes chain) |
| `None` | Terminate chain |

## Envelope Control

The system enforces these rules on responses:

| Field | Handler Control | System Override |
|-------|-----------------|-----------------|
| `<from>` | None | Always `listener.name` |
| `<to>` | Via `response.to` | Validated against peers |
| `<thread>` | None | Managed by registry |
| `<payload>` | Full control | — |

## Peer Constraints

Agents can only send to declared peers:

```yaml
listeners:
  - name: greeter
    agent: true
    peers: [shouter, logger]  # Only these allowed
```

### Violation Handling

If agent sends to undeclared peer:

1. Message **blocked** (never routed)
2. `SystemError` returned to agent
3. Thread stays alive (can retry)

```xml
<SystemError>
  <code>routing</code>
  <message>Message could not be delivered.</message>
  <retry-allowed>true</retry-allowed>
</SystemError>
```

## Response Semantics

### Critical: Pruning on Respond

When you call `.respond()`, the call chain is **pruned**:

```
Before: console → greeter → calculator
                           ↑ (you respond here)

After:  console → greeter
                 ↑ (response delivered here)
```

**Consequences:**

- Sub-agents you called are terminated
- Their state/context is lost
- You cannot call them again in this context

**Therefore:** Complete ALL sub-tasks before responding.

## Examples

### Simple Tool

```python
@xmlify
@dataclass
class AddPayload:
    a: int
    b: int

@xmlify
@dataclass
class AddResult:
    sum: int

async def add_handler(payload: AddPayload, metadata: HandlerMetadata) -> HandlerResponse:
    result = payload.a + payload.b
    return HandlerResponse.respond(payload=AddResult(sum=result))
```

### LLM Agent

```python
async def research_handler(payload: ResearchQuery, metadata: HandlerMetadata) -> HandlerResponse:
    from xml_pipeline.platform.llm_api import complete

    response = await complete(
        model="grok-4.1",
        messages=[
            {"role": "system", "content": metadata.usage_instructions},
            {"role": "user", "content": payload.query},
        ],
    )

    return HandlerResponse(
        payload=ResearchResult(answer=response.content),
        to="summarizer",
    )
```

### Self-Iterating Agent

```python
async def thinking_agent(payload: ThinkPayload, metadata: HandlerMetadata) -> HandlerResponse:
    if payload.iteration >= 5:
        # Done thinking - respond to caller
        return HandlerResponse.respond(
            payload=FinalAnswer(answer=payload.current_answer)
        )

    # Continue thinking by calling self
    return HandlerResponse(
        payload=ThinkPayload(
            iteration=payload.iteration + 1,
            current_answer=f"Refined: {payload.current_answer}",
        ),
        to=metadata.own_name,  # Self-call
    )
```

### Terminal Handler

```python
async def console_output(payload: TextOutput, metadata: HandlerMetadata) -> None:
    print(f"[{payload.source}] {payload.text}")
    return None  # Chain ends
```

### Error Handling

```python
async def safe_handler(payload: MyPayload, metadata: HandlerMetadata) -> HandlerResponse:
    try:
        result = await risky_operation(payload)
        return HandlerResponse.respond(payload=SuccessResult(data=result))
    except ValidationError as e:
        return HandlerResponse.respond(payload=ErrorResult(error=str(e)))
    except Exception:
        logger.exception("Handler failed")
        return HandlerResponse.respond(payload=ErrorResult(error="Internal error"))
```

## Security Properties

Handlers are **untrusted code**. Even compromised handlers cannot:

- Forge sender identity
- Access other threads
- Discover organism topology
- Route to undeclared peers
- Modify message history

## See Also

- [[Writing Handlers]] — Practical guide
- [[Configuration]] — Registering handlers
- [[Architecture Overview]] — System architecture
