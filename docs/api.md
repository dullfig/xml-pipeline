# AgentServer — Public Python API
**February 5, 2026**

This document is the canonical reference for the stable programmatic API of `xml-pipeline`.
Downstream consumers (OpenBlox, AgentOS, custom integrations) should import only from
the symbols documented here. Internal modules may change without notice.

## Top-Level Imports

All stable symbols are re-exported from the package root:

```python
from xml_pipeline import (
    # Handler contract
    HandlerResponse,
    HandlerMetadata,

    # Pump
    StreamPump,
    bootstrap,

    # Events
    PumpEvent,
    MessageReceivedEvent,
    MessageSentEvent,
    AgentStateEvent,
    ThreadEvent,
    ReloadEvent,
)
```

Internal paths like `xml_pipeline.message_bus.stream_pump` still work but are
not part of the stable API.

---

## Quick Start

### Minimal programmatic setup

```python
from xml_pipeline import StreamPump

pump = StreamPump(name="my-organism")
pump.register("greeter", handle_greeting, Greeting,
              description="Greeting agent", agent=True,
              peers=["shouter"], prompt="You are a friendly greeter.")
pump.register("shouter", handle_shout, GreetingResponse,
              description="Shouter tool")

await pump.start()
await pump.inject("greeter", Greeting(name="Alice"))
await pump.run()
```

### From YAML config

```python
from xml_pipeline import StreamPump

pump = await StreamPump.from_yaml("config/organism.yaml")
await pump.inject("greeter", Greeting(name="Alice"))
await pump.run()
```

### Legacy bootstrap (backward compat)

```python
from xml_pipeline import bootstrap

pump = await bootstrap("config/organism.yaml")
await pump.run()
```

---

## StreamPump

The central message pump. Manages listener registration, message routing,
handler dispatch, and the aiostream processing pipeline.

### Constructor

```python
StreamPump(
    name: str = "organism",
    *,
    port: int = 8765,
    max_tokens_per_thread: int = 100_000,
    max_concurrent_handlers: int = 20,
    max_concurrent_per_agent: int = 5,
    llm_config: dict | None = None,
    identity_path: str = "",
)
```

Creates a pump from keyword arguments. An `OrganismConfig` is built internally.

For advanced use or YAML-loaded configs, pass `config=` directly:

```python
StreamPump(config=organism_config, config_path="config/organism.yaml")
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `name` | `"organism"` | Human identifier, used in logs and root thread |
| `port` | `8765` | Main message bus port |
| `max_tokens_per_thread` | `100_000` | Token budget per conversation thread |
| `max_concurrent_handlers` | `20` | Max concurrent handler invocations |
| `max_concurrent_per_agent` | `5` | Per-agent rate limit |
| `llm_config` | `None` | LLM router config dict (strategy, backends) |
| `identity_path` | `""` | Path to Ed25519 private key for signing |

### register()

```python
pump.register(
    name: str,
    handler: Callable,
    payload_class: type,
    *,
    description: str = "",
    agent: bool = False,
    peers: list[str] | None = None,
    broadcast: bool = False,
    prompt: str = "",
    cpu_bound: bool = False,
) -> Listener
```

Register a listener using plain arguments. Internally constructs a `ListenerConfig`
and delegates to the existing `register_listener()` machinery.

| Parameter | Description |
|-----------|-------------|
| `name` | Unique listener name (e.g., `"greeter"`, `"calculator.add"`) |
| `handler` | Async handler function — see [Handler Contract](handler-contract-v2.1.md) |
| `payload_class` | `@xmlify` dataclass defining the message contract |
| `description` | Human-readable blurb for auto-generated tool prompts |
| `agent` | Whether this is an LLM agent (gets `own_name` in metadata, unique root tag) |
| `peers` | Allowed call targets for agents (enforced by pump) |
| `broadcast` | Allow multiple listeners to share the same root tag |
| `prompt` | System prompt for LLM agents (loaded into PromptRegistry) |
| `cpu_bound` | Dispatch to ProcessPoolExecutor instead of event loop |

**Example:**

```python
pump.register("greeter", handle_greeting, Greeting,
              description="Greeting agent",
              agent=True,
              peers=["shouter", "calculator"],
              prompt="You are a friendly greeter. Keep responses short.")
```

The lower-level `register_listener(lc: ListenerConfig)` is still available for
YAML-loaded configs and hot-reload.

### start()

```python
await pump.start() -> None
```

Runs the full bootstrap ceremony. Call this after registering all user listeners
and before `run()`.

**Steps performed automatically:**
1. Register system listeners (Boot, Todo, Sequence, Buffer) — skips if already present
2. Build `usage_instructions` for all agents (peer schema documentation)
3. Load prompts into PromptRegistry and freeze it
4. Configure LLM router (if `llm_config` provided)
5. Configure ThreadBudgetRegistry
6. Initialize root thread
7. Inject boot message
8. Set global pump singleton

**Idempotent:** Safe to call twice — system listeners are only registered once.

### inject()

```python
await pump.inject(
    target: str,
    payload: Any,
    *,
    from_id: str = "external",
    thread_id: str | None = None,
) -> str
```

Inject a message into the pump. The pump wraps the payload in a `<message>` envelope
automatically — no need to call `_wrap_in_envelope()`.

| Parameter | Description |
|-----------|-------------|
| `target` | Target listener name |
| `payload` | `@xmlify` dataclass instance |
| `from_id` | Sender identity (default `"external"`) |
| `thread_id` | Thread UUID; created automatically from root thread if not provided |

**Returns:** The `thread_id` used for the message.

**Example:**

```python
thread = await pump.inject("greeter", Greeting(name="Alice"))
print(f"Started thread: {thread}")
```

**Backward compatibility:** Passing raw bytes as the first argument still works but
emits a `DeprecationWarning`:

```python
# Deprecated — use inject(target, payload) instead
await pump.inject(envelope_bytes, thread_id, from_id="system")
```

### from_yaml()

```python
pump = await StreamPump.from_yaml(config_path: str = "config/organism.yaml") -> StreamPump
```

Highest-level entry point. Loads config, creates pump, registers all listeners
from YAML, and calls `start()`. Ready for `run()` immediately.

```python
pump = await StreamPump.from_yaml("config/organism.yaml")
await pump.inject("greeter", Greeting(name="Alice"))
await pump.run()
```

### run()

```python
await pump.run() -> None
```

Start the aiostream processing pipeline. Blocks until `shutdown()` is called
or the task is cancelled. Messages injected before `run()` (including the
boot message from `start()`) are processed immediately.

### shutdown()

```python
await pump.shutdown() -> None
```

Graceful shutdown — drains the message queue and closes resources (process pool, etc.).

### subscribe_events() / unsubscribe_events()

```python
pump.subscribe_events(callback: Callable[[PumpEvent], None]) -> None
pump.unsubscribe_events(callback: Callable[[PumpEvent], None]) -> None
```

Observe message flow, agent state changes, and thread lifecycle. See [Events](#events).

---

## HandlerResponse

Clean return type for handlers. The pump handles envelope wrapping.

```python
@dataclass
class HandlerResponse:
    payload: Any    # @xmlify dataclass instance
    to: str         # Target listener name
```

### Forward to named target

```python
return HandlerResponse(
    payload=GreetingResponse(message="Hello!"),
    to="shouter",
)
```

### Respond to caller (prunes call chain)

```python
return HandlerResponse.respond(
    payload=ResultPayload(value=42)
)
```

### No response (terminate chain)

```python
return None
```

See [Handler Contract](handler-contract-v2.1.md) for full details.

---

## HandlerMetadata

Trustworthy context passed to every handler by the pump.

```python
@dataclass
class HandlerMetadata:
    thread_id: str              # Opaque UUID for thread-scoped storage
    from_id: str                # Who sent this message (previous hop)
    own_name: str | None        # This listener's name (only if agent: true)
    is_self_call: bool          # True if message is from self
    usage_instructions: str     # Auto-generated peer schemas for LLM prompts
    todo_nudge: str             # System note about pending todos
```

See [Handler Contract](handler-contract-v2.1.md) for field details.

---

## Events

Subscribe to pump events for UI, monitoring, or debugging:

```python
def on_event(event: PumpEvent):
    if isinstance(event, MessageReceivedEvent):
        print(f"{event.from_id} -> {event.to_id}: {event.payload_type}")
    elif isinstance(event, AgentStateEvent):
        print(f"{event.agent_name}: {event.state}")

pump.subscribe_events(on_event)
```

| Event | Fields | When |
|-------|--------|------|
| `MessageReceivedEvent` | `thread_id`, `from_id`, `to_id`, `payload_type`, `payload` | Handler receives a message |
| `MessageSentEvent` | `thread_id`, `from_id`, `to_id`, `payload_type`, `payload` | Handler sends a response |
| `AgentStateEvent` | `agent_name`, `state`, `thread_id` | Agent transitions: `"idle"`, `"processing"`, `"error"` |
| `ThreadEvent` | `thread_id`, `status`, `participants`, `error` | Thread created/completed/killed |
| `ReloadEvent` | `success`, `added_listeners`, `removed_listeners`, `updated_listeners`, `error` | Hot-reload |

---

## Payload Classes

Define message contracts using `@xmlify` dataclasses:

```python
from dataclasses import dataclass
from third_party.xmlable import xmlify

@xmlify
@dataclass
class Greeting:
    """Incoming greeting request."""
    name: str
```

The pump automatically generates XSD schemas, example XML, and tool prompt
fragments from the dataclass definition.

---

## Handler Signature

```python
async def handle_greeting(
    payload: Greeting,
    metadata: HandlerMetadata,
) -> HandlerResponse | None:
    return HandlerResponse(
        payload=GreetingResponse(message=f"Hello, {payload.name}!"),
        to="shouter",
    )
```

Handlers must be `async def`. Return `HandlerResponse` to route a message,
or `None` to terminate the chain.

See [Handler Contract](handler-contract-v2.1.md) for the full specification.

---

## Consumer Examples

### AgentOS (handler authors)

```python
from xml_pipeline import HandlerMetadata, HandlerResponse

async def my_handler(payload: MyPayload, metadata: HandlerMetadata) -> HandlerResponse:
    return HandlerResponse(payload=Result(value=42), to=metadata.from_id)
```

### OpenBlox (orchestrator)

```python
from xml_pipeline import StreamPump, HandlerResponse
from xml_pipeline.llm import complete
from xml_pipeline.platform import get_prompt_registry

pump = StreamPump(name="my-flow", llm_config={"strategy": "failover", "backends": [...]})
pump.register("greeter", handler, Greeting,
              agent=True, peers=["shouter"], prompt="You are helpful.")
pump.register("shouter", shout_handler, GreetingResponse,
              description="Converts to uppercase")
await pump.start()
await pump.inject("greeter", Greeting(name="Alice"))
await pump.run()
```

### Minimal test harness

```python
from xml_pipeline import StreamPump

pump = StreamPump(name="test")
pump.register("echo", echo_handler, EchoPayload, description="Echo tool")
await pump.start()

# Inject and drain boot
await pump.queue.get()

# Inject test message
await pump.inject("echo", EchoPayload(text="hello"))
```

---

## What NOT to Import

These are internal and may change without notice:

| Internal | Use instead |
|----------|-------------|
| `OrganismConfig` | `StreamPump(name=..., port=...)` |
| `ListenerConfig` | `pump.register(name, handler, cls, ...)` |
| `pump._wrap_in_envelope()` | `pump.inject(target, payload)` |
| `pump._inject_raw()` | `pump.inject(target, payload)` |
| `get_registry().initialize_root()` | `pump.start()` |
| `get_registry().extend_chain()` | `pump.inject()` handles this |

---

## Related Documentation

- [Handler Contract](handler-contract-v2.1.md) — Full handler specification
- [Configuration](configuration.md) — organism.yaml reference
- [LLM Router](llm-router-v2.1.md) — Multi-backend LLM abstraction
- [Message Pump](message-pump-v2.1.md) — Pipeline architecture internals
- [Core Principles](core-principles-v2.1.md) — Architectural invariants
