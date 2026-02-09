# Writing Handlers

Handlers are async Python functions that process messages. This guide covers everything you need to know to write effective handlers.

## Basic Handler Structure

Every handler follows this pattern:

```python
from dataclasses import dataclass
from third_party.xmlable import xmlify
from xml_pipeline.message_bus.message_state import HandlerMetadata, HandlerResponse

# 1. Define your payload (input)
@xmlify
@dataclass
class MyPayload:
    """Description of what this payload represents."""
    field1: str
    field2: int

# 2. Define your response (output)
@xmlify
@dataclass
class MyResponse:
    """Description of the response."""
    result: str

# 3. Write the handler
async def my_handler(payload: MyPayload, metadata: HandlerMetadata) -> HandlerResponse:
    """Process the payload and return a response."""
    result = f"Processed {payload.field1} with {payload.field2}"

    return HandlerResponse(
        payload=MyResponse(result=result),
        to="next-listener",  # Where to send the response
    )
```

## The @xmlify Decorator

The `@xmlify` decorator enables automatic XML serialization and XSD generation:

```python
from dataclasses import dataclass
from third_party.xmlable import xmlify

@xmlify
@dataclass
class Greeting:
    name: str                    # Required field
    formal: bool = False         # Optional with default
    count: int = 1               # Optional with default
```

This generates XML like:
```xml
<greeting>
  <name>Alice</name>
  <formal>true</formal>
  <count>3</count>
</greeting>
```

### Supported Types

| Python Type | XML Representation |
|-------------|-------------------|
| `str` | Text content |
| `int` | Integer text |
| `float` | Decimal text |
| `bool` | `true` / `false` |
| `list[T]` | Repeated elements |
| `Optional[T]` | Optional element |
| `@xmlify` class | Nested element |

### Nested Payloads

```python
@xmlify
@dataclass
class Address:
    street: str
    city: str

@xmlify
@dataclass
class Person:
    name: str
    address: Address  # Nested payload
```

## HandlerMetadata

The `metadata` parameter provides context about the message:

```python
@dataclass
class HandlerMetadata:
    thread_id: str              # Opaque thread UUID
    from_id: str                # Who sent this message
    own_name: str | None        # This listener's name (agents only)
    is_self_call: bool          # True if message is from self
    usage_instructions: str     # Peer schemas for LLM prompts
    todo_nudge: str             # System reminders about pending todos
```

### Usage Examples

```python
async def my_handler(payload: MyPayload, metadata: HandlerMetadata) -> HandlerResponse:
    # Log who sent the message
    print(f"Received from: {metadata.from_id}")

    # Check if this is a self-call (agent iterating)
    if metadata.is_self_call:
        print("This is a self-call")

    # For agents: use peer schemas in LLM prompts
    if metadata.usage_instructions:
        system_prompt = metadata.usage_instructions + "\n\nYour custom instructions..."
```

## Response Types

### Forward to Target

Send the response to a specific listener:

```python
return HandlerResponse(
    payload=MyResponse(result="done"),
    to="next-listener",
)
```

### Respond to Caller

Return to whoever sent the message:

```python
return HandlerResponse.respond(
    payload=ResultPayload(value=42)
)
```

This uses the thread registry to route back up the call chain.

### Terminate Chain

End processing with no response:

```python
return None
```

Use this for terminal handlers (logging, display, etc.).

## Handler Patterns

### Simple Tool

A stateless transformation:

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

An agent that uses language models:

```python
async def research_handler(payload: ResearchQuery, metadata: HandlerMetadata) -> HandlerResponse:
    from xml_pipeline.llm import complete

    # Build prompt with peer awareness
    system_prompt = f"""
{metadata.usage_instructions}

You are a research agent. Answer the query using available tools.
"""

    response = await complete(
        model="grok-4.1",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": payload.query},
        ],
    )

    return HandlerResponse(
        payload=ResearchResult(answer=response.content),
        to="summarizer",
    )
```

### Terminal Handler

A handler that displays output and ends the chain:

```python
async def console_output(payload: TextOutput, metadata: HandlerMetadata) -> None:
    print(f"[{payload.source}] {payload.text}")
    return None  # Chain ends here
```

### Self-Iterating Agent

An agent that calls itself to continue reasoning:

```python
async def thinking_agent(payload: ThinkPayload, metadata: HandlerMetadata) -> HandlerResponse:
    # Check if we should continue thinking
    if payload.iteration >= 5:
        return HandlerResponse(
            payload=FinalAnswer(answer=payload.current_answer),
            to="output",
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

## Error Handling

Handlers should handle errors gracefully:

```python
async def safe_handler(payload: MyPayload, metadata: HandlerMetadata) -> HandlerResponse:
    try:
        result = await risky_operation(payload)
        return HandlerResponse.respond(payload=SuccessResult(data=result))
    except ValidationError as e:
        return HandlerResponse.respond(payload=ErrorResult(error=str(e)))
    except Exception as e:
        # Log and return generic error
        logger.exception("Handler failed")
        return HandlerResponse.respond(payload=ErrorResult(error="Internal error"))
```

## Registration

Register handlers in `organism.yaml`:

```yaml
listeners:
  - name: calculator.add
    payload_class: handlers.calc.AddPayload
    handler: handlers.calc.add_handler
    description: "Adds two numbers and returns the sum"
```

The `description` is important—it's used in auto-generated tool prompts for LLM agents.

## Peer Tables

By default, agents can only call peers declared in their `peers` list at registration time. **Peer tables** allow overriding this per-thread, enabling different privilege tiers (e.g., premium vs basic users).

When a thread is created with `routing_table="premium"`, the pump uses the table's peer definitions instead of the static `listener.peers`. Tables are mutable — changes take effect immediately on all threads using that table.

Agents in tabled threads automatically receive table-specific `usage_instructions` reflecting their actual permissions.

See [Peer Tables](../api.md#peer-tables) in the API reference for details.

## Security Notes

Handlers are **untrusted code**. The system enforces:

1. **Identity injection** — `<from>` is always set by the pump, never by handlers
2. **Thread isolation** — Handlers see only opaque UUIDs
3. **Peer constraints** — Agents can only send to effective peers (peer table override or static `listener.peers`)
4. **Peer tables** — Table name embedded in thread chain (behind opaque UUID), invisible to agents

Even compromised handlers cannot:
- Forge sender identity
- Access other threads
- Discover organism topology
- Route to undeclared peers

## See Also

- [[Handler Contract]] — Complete handler specification
- [[Configuration]] — Registering handlers
- [[LLM Router]] — Using language models
