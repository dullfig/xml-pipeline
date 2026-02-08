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

    # Workers
    get_worker_registry,
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
    timeout: float = 30.0,
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
| `timeout` | Handler execution timeout in seconds (default 30). On timeout, `SystemError(code="timeout")` sent back to caller; thread stays alive |

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
    routing_table: str | None = None,
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
| `routing_table` | Named peer table for this thread (see [Peer Tables](#peer-tables)) |

**Returns:** The `thread_id` used for the message.

**Example:**

```python
thread = await pump.inject("greeter", Greeting(name="Alice"))
print(f"Started thread: {thread}")

# Inject with a peer table for privilege-scoped dispatch
thread = await pump.inject("concierge", Request(query="help"),
                           routing_table="premium")
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

### register_peer_table()

```python
pump.register_peer_table(
    name: str,
    peers: dict[str, list[str]],
    *,
    parent: str | None = None,
) -> None
```

Register a named peer table (privilege tier). Peer tables override the static
`Listener.peers` for all threads using this table. Tables are mutable — modifying
a table immediately affects all threads using it.

**Ceiling enforcement:** Every peer in the table must exist in the ceiling — the
parent table's peers (if `parent` is set) or the YAML `listener.peers` (if no parent).
Tables can only **restrict** permissions, never expand beyond the ceiling.

| Parameter | Description |
|-----------|-------------|
| `name` | Unique table name (e.g., `"premium"`, `"basic"`) |
| `peers` | Mapping of `{ listener_name: [allowed_peers] }` |
| `parent` | Optional parent table name. Ceiling comes from parent instead of YAML `listener.peers` |

**Example:**

```python
# Register root table — ceiling is YAML listener.peers
pump.register_peer_table("admin", {
    "concierge": ["calculator", "search", "billing"],
})

# Register child table — ceiling is admin's peers
pump.register_peer_table("operator", {
    "concierge": ["calculator", "search"],
}, parent="admin")

# Register grandchild — ceiling is operator's peers
pump.register_peer_table("viewer", {
    "concierge": ["calculator"],
}, parent="operator")
```

**Raises:**
- `KeyError` if the table name already exists or parent not found.
- `ValueError` if any peer exceeds the ceiling.

### modify_peer_table()

```python
pump.modify_peer_table(
    name: str,
    listener_name: str,
    *,
    grant: list[str] | None = None,
    revoke: list[str] | None = None,
) -> list[str]
```

Modify a single listener's peers within a named table. Returns the updated peers list.
Changes take effect immediately on all threads using this table.

**Ceiling enforcement:** Grants are validated against the ceiling (parent table or YAML
`listener.peers`). You can restore previously revoked peers up to the ceiling, but never
exceed it. Revocations always succeed (subtraction is always valid).

| Parameter | Description |
|-----------|-------------|
| `name` | Table name (must already exist) |
| `listener_name` | Listener to modify within the table |
| `grant` | Peers to add (must be within ceiling) |
| `revoke` | Peers to remove (always valid) |

**Returns:** Updated list of peers for the listener in this table.

**Example:**

```python
# Grant billing access to concierge in premium tier
updated = pump.modify_peer_table("premium", "concierge",
                                 grant=["billing"], revoke=["search"])
# updated == ["calculator", "billing"]

# Revocations always succeed
pump.modify_peer_table("premium", "concierge", revoke=["billing"])

# Grants exceeding ceiling raise ValueError
pump.modify_peer_table("viewer", "concierge",
                       grant=["billing"])  # ValueError if billing not in parent
```

**Raises:**
- `KeyError` if the table name doesn't exist.
- `ValueError` if any granted peer exceeds the ceiling.

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
| `AgentStateEvent` | `agent_name`, `state`, `thread_id` | Agent transitions: `"idle"`, `"processing"`, `"error"` (includes timeout) |
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

## Peer Tables

Peer tables provide thread-scoped privilege enforcement. Instead of relying solely
on the static `peers` list declared at registration time, peer tables allow different
threads to use different peer mappings — enabling use cases like premium vs basic
user tiers.

### How It Works

1. **Register a table** with `pump.register_peer_table(name, peers, parent=...)` or declare in YAML
2. **Inject with the table** using `pump.inject(target, payload, routing_table=name)`
3. **Dispatch enforcement** reads from the table on every message (not from `listener.peers`)
4. **Modify at runtime** with `pump.modify_peer_table()` — changes affect all threads immediately

### Ceiling Model (Subtract-Only Hierarchy)

Peer tables use a **subtract-only hierarchy** like Linux permissions. YAML `listener.peers`
is the root ceiling — tables can only restrict, never expand beyond it.

```
YAML listener.peers (root ceiling)
  └─ admin table    (= root, or subset)
       └─ operator  (⊆ admin)
            └─ viewer (⊆ operator)
```

**Key invariants:**
- `register_peer_table()` validates every peer exists in the ceiling
- `modify_peer_table(..., grant=...)` validates grants against the ceiling — you can restore revoked peers up to the ceiling, but never exceed it
- `modify_peer_table(..., revoke=...)` always succeeds (subtraction is always valid)
- Tables without a `parent` inherit directly from YAML `listener.peers`

### Thread Chain Encoding

The table name is embedded in the thread chain prefix:
- Default threads: `system.organism.external.concierge` (uses `listener.peers`)
- Tabled threads: `premium.organism.external.concierge` (uses `_peer_tables["premium"]`)

Since chains are behind opaque UUIDs, agents cannot see or tamper with which table
they are on.

### Example: Multi-Tier Concierge

```python
from xml_pipeline import StreamPump

pump = StreamPump(name="my-org")
pump.register("concierge", handle_concierge, Request,
              agent=True, peers=["calculator", "search", "billing"],
              description="Customer service agent")
pump.register("calculator", calc_handler, CalcPayload, description="Calculator")
pump.register("search", search_handler, SearchPayload, description="Search")
pump.register("billing", billing_handler, BillingPayload, description="Billing")

await pump.start()

# Define privilege tiers (subtract-only from listener.peers ceiling)
pump.register_peer_table("premium", {
    "concierge": ["calculator", "search", "billing"],
})
pump.register_peer_table("basic", {
    "concierge": ["calculator"],
}, parent="premium")  # Ceiling is premium's peers, not YAML

# Premium user — concierge can call all three tools
await pump.inject("concierge", Request(query="Check my bill"),
                  routing_table="premium")

# Basic user — concierge can only call calculator
await pump.inject("concierge", Request(query="What is 2+2?"),
                  routing_table="basic")

# Revoke search from premium mid-conversation
pump.modify_peer_table("premium", "concierge", revoke=["search"])
```

### YAML Declaration

Peer tables can be declared in `organism.yaml` for bootstrap-time registration:

```yaml
peer_tables:
  - name: admin
    entries:
      - listener: concierge
        peers: [calculator, search, billing]

  - name: operator
    parent: admin
    entries:
      - listener: concierge
        peers: [calculator, search]

  - name: viewer
    parent: operator
    entries:
      - listener: concierge
        peers: [calculator]
```

Tables are registered in topological order (parents before children) during `start()`.
Circular parent chains are detected and rejected.

See [Configuration](configuration.md#peer_tables) for full YAML reference.

### OOB Commands

Peer tables can also be managed via the OOB privileged channel:

```xml
<register-peer-table xmlns="https://xml-pipeline.org/privileged-msg">
  <name>premium</name>
  <parent>admin</parent>
  <entries>
    <entry>
      <listener>concierge</listener>
      <peers><peer>calculator</peer><peer>search</peer></peers>
    </entry>
  </entries>
</register-peer-table>
```

```xml
<modify-peer-table xmlns="https://xml-pipeline.org/privileged-msg">
  <name>premium</name>
  <listener>concierge</listener>
  <grant><peer>billing</peer></grant>
  <revoke><peer>search</peer></revoke>
</modify-peer-table>
```

### Usage Instructions

Agents in tabled threads automatically receive table-specific `usage_instructions`
in their `HandlerMetadata`. These reflect the table's peer list, not the listener's
static peers — so the LLM sees tool documentation matching its actual permissions.

---

## Worker Registry

Background worker processes for long-running or CPU-bound tasks that outlive a single
handler invocation. Workers are thread-scoped and automatically cleaned up when the
thread terminates.

### get_worker_registry()

```python
from xml_pipeline import get_worker_registry

registry = get_worker_registry()
```

Returns the global `WorkerRegistry` singleton. The registry is also available on
the pump as `pump._worker_registry` (internal).

### WorkerRegistry API

```python
# Spawn a background worker process
worker_id = registry.spawn(
    thread_id: str,
    listener_name: str,
    target: Callable,
    *,
    kwargs: dict[str, Any] | None = None,
) -> str

# Send a message to the worker's inbox
registry.send(worker_id: str, message: Any) -> None

# Drain the worker's outbox (non-blocking)
messages = registry.receive(worker_id: str) -> list[Any]

# Get worker status snapshot
status = registry.status(worker_id: str) -> WorkerStatus

# Gracefully stop a worker
registry.stop(worker_id: str, *, join_timeout: float = 5.0) -> None

# Stop all workers for a thread (called automatically on thread cleanup)
count = registry.cleanup_for_thread(thread_id: str, *, join_timeout: float = 5.0) -> int

# Stop all workers (called on pump shutdown)
count = registry.shutdown_all(*, join_timeout: float = 5.0) -> int
```

### WorkerStatus

```python
@dataclass
class WorkerStatus:
    worker_id: str
    alive: bool
    pid: int | None
    uptime: float
    listener_name: str
    thread_id: str
```

### Usage in Handlers

```python
from xml_pipeline import get_worker_registry

async def my_handler(payload, metadata):
    registry = get_worker_registry()

    # Spawn a background worker
    worker_id = registry.spawn(
        thread_id=metadata.thread_id,
        listener_name=metadata.own_name,
        target=long_running_task,
        kwargs={"data": payload.data},
    )

    # Send work to it
    registry.send(worker_id, {"command": "process"})

    # Check for results later
    results = registry.receive(worker_id)
```

Workers use `multiprocessing.Process` with inbox/outbox `Queue` pairs. Send `None`
as a sentinel to signal graceful stop from the worker side.

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
| `WorkerRegistry()` | `get_worker_registry()` |

---

## Related Documentation

- [Handler Contract](handler-contract-v2.1.md) — Full handler specification
- [Configuration](configuration.md) — organism.yaml reference
- [LLM Router](llm-router-v2.1.md) — Multi-backend LLM abstraction
- [Message Pump](message-pump-v2.1.md) — Pipeline architecture internals
- [Core Principles](core-principles-v2.1.md) — Architectural invariants
