# Architecture Overview

xml-pipeline implements a stream-based message pump where all communication flows through validated XML envelopes. The architecture enforces strict isolation between handlers (untrusted code) and the system (trusted zone).

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        TRUSTED ZONE (System)                        │
│  • Thread registry (UUID ↔ call chain mapping)                      │
│  • Listener registry (name → peers, schema)                         │
│  • Envelope injection (<from>, <thread>, <to>)                      │
│  • Peer constraint enforcement (static peers + peer tables)         │
│  • OOB privileged channel (hot-reload, introspection)               │
└─────────────────────────────────────────────────────────────────────┘
                               ↕
                    Coroutine Capture Boundary
                               ↕
┌─────────────────────────────────────────────────────────────────────┐
│                      UNTRUSTED ZONE (Handlers)                      │
│  • Receive typed payload + metadata                                 │
│  • Return HandlerResponse or None                                   │
│  • Cannot forge identity, escape thread, or probe topology          │
└─────────────────────────────────────────────────────────────────────┘
```

## Core Components

### Message Pump (StreamPump)

The central orchestrator that:
1. Receives raw XML bytes
2. Runs messages through preprocessing pipeline
3. Routes to appropriate handlers
4. Processes responses and re-injects

See [[Message Pump]] for details.

### Pipeline Steps

Messages flow through ordered processing stages:

```
Raw Bytes
    │
    ▼
┌─────────────────┐
│  repair_step    │  Fix malformed XML (lxml recover mode)
└────────┬────────┘
         ▼
┌─────────────────┐
│   c14n_step     │  Canonicalize XML (Exclusive C14N)
└────────┬────────┘
         ▼
┌─────────────────┐
│ envelope_valid  │  Validate against envelope.xsd
└────────┬────────┘
         ▼
┌─────────────────┐
│ payload_extract │  Extract payload from envelope
└────────┬────────┘
         ▼
┌─────────────────┐
│ thread_assign   │  Assign or inherit thread UUID
└────────┬────────┘
         ▼
┌─────────────────┐
│  xsd_validate   │  Validate against listener's XSD
└────────┬────────┘
         ▼
┌─────────────────┐
│ deserialize     │  XML → @xmlify dataclass
└────────┬────────┘
         ▼
┌─────────────────┐
│    routing      │  Match to listener(s)
└────────┬────────┘
         ▼
    Handler
```

### Thread Registry

Maps opaque UUIDs to call chains:

```
UUID: 550e8400-e29b-41d4-...
Chain: system.organism.console.greeter.calculator
        │       │        │       │        │
        │       │        │       │        └─ Current handler
        │       │        │       └─ Previous hop
        │       │        └─ Entry point
        │       └─ Organism name
        └─ Root
```

Handlers only see the UUID. The actual chain is private to the system.

See [[Thread Registry]] for details.

### Listener Registry

Tracks registered listeners:

```
name: "greeter"
  ├── payload_class: Greeting
  ├── handler: handle_greeting
  ├── description: "Friendly greeting handler"
  ├── agent: true
  ├── peers: [shouter, calculator]
  └── schema: schemas/greeter/v1.xsd
```

### OOB Privileged Channel

A separate localhost-only WebSocket server for trusted administration:

- Hot-reload (register/unregister listeners at runtime)
- Introspection (list listeners, get schemas, get status)
- Message injection and thread subscription
- Peer table management (register, modify)
- Ed25519 signature verification (skipped in dev mode)

Runs on a dedicated port (default 8766), separate from the main message bus. The main pump is completely oblivious to privileged operations.

### Context Buffer

Stores message history per thread:

```
Thread: uuid-123
  ├── Slot 0: Greeting(name="Alice") from console
  ├── Slot 1: GreetingResponse(message="Hello!") from greeter
  └── Slot 2: ShoutResponse(text="HELLO!") from shouter
```

Append-only, immutable slots. Auto-GC when thread is pruned.

## Message Flow

### 1. Message Arrival

External message arrives (console, WebSocket, etc.):

```xml
<message xmlns="https://xml-pipeline.org/ns/envelope/v1">
  <meta>
    <from>console</from>
    <to>greeter</to>
  </meta>
  <greeting>
    <name>Alice</name>
  </greeting>
</message>
```

### 2. Pipeline Processing

Message flows through pipeline steps. Each step transforms `MessageState`:

```python
@dataclass
class MessageState:
    raw_bytes: bytes | None          # Input
    envelope_tree: Element | None    # After repair
    payload_tree: Element | None     # After extraction
    payload: Any | None              # After deserialization
    thread_id: str | None            # After assignment
    from_id: str | None              # Sender
    target_listeners: list | None    # After routing
    error: str | None                # If step fails
```

### 3. Handler Dispatch

Handler receives typed payload + metadata:

```python
async def handle_greeting(payload: Greeting, metadata: HandlerMetadata):
    # payload.name == "Alice"
    # metadata.thread_id == "uuid-123"
    # metadata.from_id == "console"
```

### 4. Response Processing

Handler returns `HandlerResponse`:

```python
return HandlerResponse(
    payload=GreetingResponse(message="Hello, Alice!"),
    to="shouter",
)
```

System:
1. Validates `to` against peer list
2. Serializes payload to XML
3. Creates new envelope with injected `<from>`
4. Re-injects into pipeline

## Trust Boundaries

### What the System Controls

| Aspect | System Responsibility |
|--------|----------------------|
| `<from>` | Always injected from listener.name |
| `<thread>` | Managed by thread registry |
| `<to>` validation | Checked against peers list |
| Schema enforcement | XSD validation on every message |
| Call chain | Private, never exposed to handlers |

### What Handlers Control

| Aspect | Handler Capability |
|--------|-------------------|
| Payload content | Full control |
| Target selection | Via `HandlerResponse.to` (validated) |
| Response/no response | Return value |
| Self-iteration | Call own name |

### Peer Tables

For thread-scoped privilege enforcement, **peer tables** override static `listener.peers` on a per-thread basis. When a thread is created with a named routing table (e.g., `"premium"`), dispatch uses the table's peer definitions instead. Tables are mutable — changes take effect immediately on all threads using that table.

See [[Writing Handlers]] for usage details.

### What Handlers Cannot Do

- Forge sender identity
- Access other threads
- Discover topology
- Route to undeclared peers (static or table-enforced)
- Modify message history
- Access other handlers' state
- See which peer table governs their thread

## Dispatch Architecture

Handler dispatch paths:

```
┌─────────────────────────────────────────────────────────────────┐
│  Main Process (StreamPump)                                      │
│  - Ingress pipeline                                             │
│  - Routing decisions                                            │
│  - Response re-injection                                        │
└───────────────────────────┬─────────────────────────────────────┘
                            │
              ┌─────────────┼─────────────┐
              ▼             ▼             ▼
┌─────────────────┐ ┌─────────────┐ ┌─────────────────┐
│ Python Async    │ │ WASM Tools  │ │ Background      │
│ (event loop)    │ │ (wasmtime   │ │ Workers         │
│ - Default mode  │ │  + threads) │ │ (WorkerRegistry)│
└─────────────────┘ └─────────────┘ └─────────────────┘
```

- **Python Async**: Default path — handler runs as async coroutine in the event loop
- **WASM Tools**: Foreign code in wasmtime sandboxes, dispatched via `asyncio.to_thread()` with epoch interruption for timeout enforcement
- **Background Workers**: Long-running processes via `WorkerRegistry` (thread-scoped, auto-cleanup)

## See Also

- [[Message Pump]] — Detailed pump architecture
- [[Thread Registry]] — Call chain tracking
- [[Shared Backend]] — Cross-process state
- [[Handler Contract]] — Handler specification
- [[Writing Handlers]] — Peer tables and security
