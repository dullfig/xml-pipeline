# Thread Registry

The Thread Registry maps opaque UUIDs to call chains, enabling thread tracking while hiding topology from handlers.

## Purpose

When agents communicate, they form call chains:

```
console → greeter → calculator → back to greeter → shouter
```

The registry:
1. **Tracks call chains** for routing responses
2. **Provides opaque UUIDs** to handlers (hiding topology)
3. **Manages chain pruning** when handlers respond

## Concepts

### Call Chain

A dot-separated path showing message flow:

```
system.organism.console.greeter.calculator
│      │        │       │       │
│      │        │       │       └─ Current position
│      │        │       └─ Greeter called calculator
│      │        └─ Console called greeter
│      └─ Organism name
└─ Root
```

### Opaque UUID

What handlers actually see:

```
550e8400-e29b-41d4-a716-446655440000
```

Handlers never see the actual chain. This prevents:
- Topology probing
- Call chain forgery
- Thread hijacking

## API

### Initialize Root

At boot time:

```python
from xml_pipeline.message_bus.thread_registry import get_registry

registry = get_registry()
root_uuid = registry.initialize_root("my-organism")
# Creates: system.my-organism → uuid
```

### Get or Create

Get UUID for a chain (creates if needed):

```python
uuid = registry.get_or_create("console.greeter")
# Returns: existing UUID or creates new one
```

### Lookup

Get chain for a UUID:

```python
chain = registry.lookup(uuid)
# Returns: "console.greeter" or None
```

### Extend Chain

When forwarding to a new handler:

```python
new_uuid = registry.extend_chain(current_uuid, "calculator")
# Before: console.greeter (uuid-123)
# After: console.greeter.calculator (uuid-456)
```

### Prune for Response

When a handler returns `.respond()`:

```python
target, new_uuid = registry.prune_for_response(current_uuid)
# Before: console.greeter.calculator (uuid-456)
# After: console.greeter (uuid-123)
# target: "greeter"
```

### Register External Thread

For messages arriving with pre-assigned UUIDs:

```python
registry.register_thread(
    thread_id="external-uuid",
    initiator="console",
    target="greeter"
)
# Creates: system.organism.console.greeter → external-uuid
```

## Thread Lifecycle

### Creation

```
1. External message arrives without thread
   │
   ▼
2. thread_assignment_step generates UUID
   │
   ▼
3. Registry maps: chain → UUID
```

### Extension

```
1. Handler A forwards to Handler B
   │
   ▼
2. Pump calls extend_chain(uuid_A, "B")
   │
   ▼
3. Registry creates: chain.B → uuid_B
```

### Pruning

```
1. Handler B calls .respond()
   │
   ▼
2. Pump calls prune_for_response(uuid_B)
   │
   ▼
3. Registry:
   - Looks up chain: "...A.B"
   - Prunes last segment: "...A"
   - Returns target "A" and uuid_A
   │
   ▼
4. Response routed to Handler A
```

### Cleanup

```
1. Chain exhausted (root reached) or
   Handler returns None
   │
   ▼
2. UUID mapping removed
   │
   ▼
3. Context buffer for thread deleted
```

## Peer Table Roots

The thread registry also manages **peer table roots** — named routing tables for thread-scoped privilege enforcement. When a thread is created with a routing table, the chain prefix carries the table name instead of `system`.

### API

```python
# Create a root chain for a peer table
root_uuid = registry.initialize_table_root("premium", "my-organism")
# Creates: premium.my-organism → uuid

# Get root UUID for a named table
root_uuid = registry.get_table_root("premium")
# Returns: uuid or None

# Extract table name from a thread's chain
table_name = registry.get_table_for_thread(thread_uuid)
# Returns: "premium" (or None for "system.*" chains)
```

### How It Works

Default threads have chains like `system.organism.console.greeter`. Tabled threads replace the `system` prefix with the table name:

```
Default thread:  system.organism.external.concierge
                 │
                 └─ Uses listener.peers (static)

Tabled thread:   premium.organism.external.concierge
                 │
                 └─ Uses _peer_tables["premium"] (dynamic)
```

The `get_table_for_thread()` method looks up the chain, splits by `.`, and returns the first segment if it's not `"system"`. This is O(1) and called on every dispatch.

### Instance Data

```python
_table_roots: Dict[str, str]  # table_name → root_uuid
```

## Shared Backend Support

For multiprocess deployments, the registry can use a shared backend:

```python
from xml_pipeline.memory.shared_backend import get_shared_backend, BackendConfig

# Use Redis for distributed deployments
config = BackendConfig(backend_type="redis", redis_url="redis://localhost:6379")
backend = get_shared_backend(config)
registry = get_registry(backend=backend)
```

### Storage Schema (Redis)

```
xp:chain:{chain} → {uuid}     # Chain to UUID
xp:uuid:{uuid} → {chain}      # UUID to Chain
```

## Security Properties

### What Handlers See

```python
metadata.thread_id = "550e8400-..."  # Opaque UUID
metadata.from_id = "greeter"         # Only immediate caller
```

### What Handlers Don't See

- Full call chain
- Other thread UUIDs
- Thread count or topology
- Parent/child relationships

### Why This Matters

Even compromised handlers cannot:
- **Forge thread IDs** — UUIDs are cryptographically random
- **Discover topology** — Chain hidden behind UUID
- **Hijack threads** — Registry validates all operations
- **Probe other threads** — No enumeration API

## Debugging

For operators (not exposed to handlers):

```python
# Dump all mappings
chains = registry.debug_dump()
# {'uuid-123': 'console.greeter', 'uuid-456': 'console.greeter.calc'}

# Clear (testing only)
registry.clear()
```

## Example Flow

```
1. Console sends @greeter hello
   ├── UUID assigned: uuid-1
   └── Chain: system.org.console.greeter

2. Greeter forwards to calculator
   ├── extend_chain(uuid-1, "calculator")
   ├── New UUID: uuid-2
   └── Chain: system.org.console.greeter.calculator

3. Calculator responds
   ├── prune_for_response(uuid-2)
   ├── Target: "greeter"
   └── UUID: uuid-1 (back to greeter's context)

4. Greeter responds
   ├── prune_for_response(uuid-1)
   ├── Target: "console"
   └── Chain exhausted → cleanup
```

## Configuration

No explicit configuration needed. The registry:
- Initializes automatically at pump startup
- Uses shared backend if configured
- Cleans up on thread termination

## See Also

- [[Architecture Overview]] — High-level architecture
- [[Message Pump]] — How the pump uses the registry
- [[Shared Backend]] — Cross-process storage
