# Shared Backend

The Shared Backend enables cross-process state sharing for multiprocess deployments. It provides storage for the Context Buffer and Thread Registry.

## Overview

By default, xml-pipeline uses in-memory storage (single process). For CPU-bound handlers running in separate processes, you need shared state:

```
┌────────────────────┐     ┌────────────────────┐
│   Main Process     │     │  Worker Process    │
│   (StreamPump)     │     │  (cpu_bound)       │
└─────────┬──────────┘     └──────────┬─────────┘
          │                           │
          └───────────┬───────────────┘
                      │
                      ▼
          ┌─────────────────────┐
          │   Shared Backend    │
          │  (Redis/Manager)    │
          └─────────────────────┘
```

## Backend Types

### InMemoryBackend (Default)

Single-process, thread-safe storage using Python dictionaries.

```python
from xml_pipeline.memory import get_shared_backend, BackendConfig

config = BackendConfig(backend_type="memory")
backend = get_shared_backend(config)
```

**Use when:**
- Single process deployment
- Development/testing
- No CPU-bound handlers

### ManagerBackend

Uses `multiprocessing.Manager` for local multi-process sharing.

```python
config = BackendConfig(backend_type="manager")
backend = get_shared_backend(config)
```

**Use when:**
- Local deployment with CPU-bound handlers
- No Redis available
- Single machine, multiple processes

### RedisBackend

Distributed storage with TTL-based auto-cleanup.

```python
config = BackendConfig(
    backend_type="redis",
    redis_url="redis://localhost:6379",
    redis_prefix="xp:",
    redis_ttl=86400,  # 24 hours
)
backend = get_shared_backend(config)
```

**Use when:**
- Distributed deployment
- Multiple machines
- Need persistence
- Production environments

## Configuration

### Via organism.yaml

```yaml
backend:
  type: redis                          # memory | manager | redis
  redis_url: "redis://localhost:6379"  # Redis connection URL
  redis_prefix: "xp:"                  # Key prefix for multi-tenancy
  redis_ttl: 86400                     # Key TTL in seconds
```

### Programmatic

```python
from xml_pipeline.memory import get_shared_backend, BackendConfig

config = BackendConfig(
    backend_type="redis",
    redis_url="redis://localhost:6379",
    redis_prefix="myapp:",
    redis_ttl=3600,
)
backend = get_shared_backend(config)
```

## Storage Schema

### Context Buffer

Stores message history per thread.

**In-Memory/Manager:**
```python
_buffers = {
    "thread-uuid-1": [slot_bytes_0, slot_bytes_1, ...],
    "thread-uuid-2": [...],
}
```

**Redis:**
```
{prefix}buffer:{thread_id} → LIST of pickled BufferSlots
```

### Thread Registry

Maps UUIDs to call chains.

**In-Memory/Manager:**
```python
_chain_to_uuid = {"console.greeter": "uuid-123"}
_uuid_to_chain = {"uuid-123": "console.greeter"}
```

**Redis:**
```
{prefix}chain:{chain} → {uuid}
{prefix}uuid:{uuid} → {chain}
```

## API

### Buffer Operations

```python
# Append a slot
index = backend.buffer_append(thread_id, slot_bytes)

# Get all slots for thread
slots = backend.buffer_get_thread(thread_id)

# Get specific slot
slot = backend.buffer_get_slot(thread_id, index)

# Check thread exists
exists = backend.buffer_thread_exists(thread_id)

# Delete thread
deleted = backend.buffer_delete_thread(thread_id)

# List all threads
threads = backend.buffer_list_threads()

# Clear all (testing)
backend.buffer_clear()
```

### Registry Operations

```python
# Set chain ↔ UUID mapping
backend.registry_set(chain, uuid)

# Get UUID from chain
uuid = backend.registry_get_uuid(chain)

# Get chain from UUID
chain = backend.registry_get_chain(uuid)

# Delete mapping
deleted = backend.registry_delete(uuid)

# List all mappings
all_mappings = backend.registry_list_all()

# Clear all (testing)
backend.registry_clear()
```

### Serialization

Slots are serialized using pickle:

```python
from xml_pipeline.memory import serialize_slot, deserialize_slot

# Serialize for storage
slot_bytes = serialize_slot(buffer_slot)

# Deserialize after retrieval
slot = deserialize_slot(slot_bytes)
```

## Integration

### With ContextBuffer

```python
from xml_pipeline.memory import get_context_buffer

# Uses shared backend automatically if configured
buffer = get_context_buffer(backend=backend)

# Check if using shared storage
print(buffer.is_shared)  # True
```

### With ThreadRegistry

```python
from xml_pipeline.message_bus.thread_registry import get_registry

registry = get_registry(backend=backend)

# Check if using shared storage
print(registry.is_shared)  # True
```

### With StreamPump

The pump automatically uses the configured backend:

```yaml
backend:
  type: redis
  redis_url: "redis://localhost:6379"

process_pool:
  workers: 4

listeners:
  - name: analyzer
    cpu_bound: true  # Uses shared backend for data exchange
```

## Worker Data Flow

For CPU-bound handlers, data flows through the backend:

```
1. Main Process
   ├── Serialize payload + metadata
   ├── Store in backend (payload_uuid, metadata_uuid)
   └── Submit WorkerTask to ProcessPool

2. Worker Process
   ├── Fetch payload + metadata from backend
   ├── Execute handler
   ├── Store response in backend (response_uuid)
   └── Return WorkerResult

3. Main Process
   ├── Fetch response from backend
   ├── Clean up temporary data
   └── Process response normally
```

## TTL and Cleanup

### Redis TTL

Redis keys automatically expire:

```yaml
backend:
  redis_ttl: 86400  # Keys expire after 24 hours
```

### Manual Cleanup

```python
# Delete specific thread
backend.buffer_delete_thread(thread_id)
backend.registry_delete(uuid)

# Clear all (testing only)
backend.buffer_clear()
backend.registry_clear()
```

## Multi-Tenancy

Use prefixes to isolate different organisms:

```yaml
# Organism A
backend:
  type: redis
  redis_prefix: "orgA:"

# Organism B
backend:
  type: redis
  redis_prefix: "orgB:"
```

## Monitoring

### Redis Info

```python
info = backend.info()
# {'buffer_threads': 5, 'registry_entries': 12}
```

### Health Check

```python
is_healthy = backend.ping()  # True if connected
```

## Testing

```python
import pytest
from xml_pipeline.memory import InMemoryBackend

@pytest.fixture
def backend():
    backend = InMemoryBackend()
    yield backend
    backend.close()

def test_buffer_operations(backend):
    backend.buffer_append("thread-1", b"data")
    assert backend.buffer_thread_exists("thread-1")
```

## See Also

- [[Architecture Overview]] — High-level architecture
- [[Message Pump]] — How the pump uses backends
- [[Configuration]] — Backend configuration options
