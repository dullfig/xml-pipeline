# Message Pump

The Message Pump (StreamPump) is the heart of xml-pipeline. It orchestrates message flow from ingress through processing to handler dispatch and response handling.

## Overview

The pump uses [aiostream](https://aiostream.readthedocs.io/) for stream-based processing with concurrent fan-out capabilities.

```python
from xml_pipeline import StreamPump

pump = StreamPump(name="my-organism")
pump.register("greeter", handle_greeting, Greeting,
              description="Greeting agent", peers=["shouter"])
await pump.start()

# Inject a message
await pump.inject("greeter", Greeting(name="Alice"))

await pump.shutdown()
```

## Architecture

```
                    ┌─────────────────────┐
                    │   Message Source    │
                    │ (Console, WebSocket)│
                    └──────────┬──────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────┐
│                      INGRESS PIPELINE                        │
│                                                              │
│  ┌─────────┐   ┌──────┐   ┌──────────┐   ┌─────────────┐     │
│  │ Repair  │ → │ C14N │ → │ Envelope │ → │   Payload   │     │
│  │  Step   │   │ Step │   │ Validate │   │  Extraction │     │
│  └─────────┘   └──────┘   └──────────┘   └─────────────┘     │
│                                                              │
│  ┌──────────┐   ┌─────────┐   ┌─────────────┐                │
│  │  Thread  │ → │   XSD   │ → │ Deserialize │                │
│  │  Assign  │   │ Validate│   │   to class  │                │
│  └──────────┘   └─────────┘   └─────────────┘                │
│                                                              │
└──────────────────────────────────────────────────────────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │   ROUTING TABLE     │
                    │                     │
                    │  root_tag → [       │
                    │    Listener1,       │
                    │    Listener2,       │
                    │  ]                  │
                    └──────────┬──────────┘
                               │
              ┌────────────────┼────────────────┐
              ▼                ▼                ▼
    ┌─────────────────┐ ┌─────────────┐ ┌─────────────────┐
    │   Handler A     │ │  Handler B  │ │   Handler C     │
    │  (async/main)   │ │ (cpu_bound) │ │   (async)       │
    └────────┬────────┘ └──────┬──────┘ └────────┬────────┘
             │                 │                  │
             └─────────────────┼──────────────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │  RESPONSE HANDLER   │
                    │                     │
                    │  • Serialize        │
                    │  • Wrap envelope    │
                    │  • Inject <from>    │
                    │  • Re-inject        │
                    └─────────────────────┘
```

## Pipeline Steps

### repair_step

Fixes malformed XML using lxml's recover mode:

```python
async def repair_step(state: MessageState) -> MessageState:
    parser = etree.XMLParser(recover=True)
    state.envelope_tree = etree.fromstring(state.raw_bytes, parser)
    return state
```

Handles:
- Missing closing tags
- Invalid characters
- Encoding issues

### c14n_step

Canonicalizes XML using Exclusive C14N:

```python
async def c14n_step(state: MessageState) -> MessageState:
    c14n_bytes = etree.tostring(state.envelope_tree, method='c14n')
    state.envelope_tree = etree.fromstring(c14n_bytes)
    return state
```

Ensures deterministic representation for signing.

### envelope_validation_step

Validates against `envelope.xsd`:

```xml
<message xmlns="https://xml-pipeline.org/ns/envelope/v1">
  <meta>
    <from>...</from>
    <to>...</to>        <!-- optional -->
    <thread>...</thread> <!-- optional -->
  </meta>
  <!-- payload element -->
</message>
```

### payload_extraction_step

Extracts the payload element:

```python
async def payload_extraction_step(state: MessageState) -> MessageState:
    # Find first non-meta child
    for child in state.envelope_tree:
        if child.tag != 'meta':
            state.payload_tree = child
            break
    return state
```

### thread_assignment_step

Assigns or inherits thread UUID:

```python
async def thread_assignment_step(state: MessageState) -> MessageState:
    meta = state.envelope_tree.find('meta')
    thread_elem = meta.find('thread')

    if thread_elem is not None:
        state.thread_id = thread_elem.text
    else:
        # New thread - assign UUID
        state.thread_id = str(uuid.uuid4())
    return state
```

### xsd_validation_step

Validates payload against listener's schema:

```python
async def xsd_validation_step(state: MessageState) -> MessageState:
    listener = find_listener_for_payload(state.payload_tree)
    schema = load_schema(listener.schema_path)

    if not schema.validate(state.payload_tree):
        state.error = "XSD validation failed"
    return state
```

### deserialization_step

Converts XML to typed dataclass:

```python
async def deserialization_step(state: MessageState) -> MessageState:
    listener = find_listener_for_payload(state.payload_tree)
    state.payload = xmlify_deserialize(
        state.payload_tree,
        listener.payload_class
    )
    return state
```

## Routing

### Root Tag Derivation

Root tag = `{listener_name}.{dataclass_name}` (lowercase):

```
Listener: greeter
Dataclass: Greeting
Root tag: greeter.greeting
```

### Routing Table

```python
routing_table = {
    "greeter.greeting": [greeter_listener],
    "calculator.add": [calculator_listener],
    "search.query": [google_listener, bing_listener],  # Broadcast
}
```

### Broadcast

Multiple listeners can share a root tag (`broadcast: true`):

```yaml
listeners:
  - name: search.google
    broadcast: true
    # ...

  - name: search.bing
    broadcast: true
    # ...
```

All matching listeners execute concurrently.

## Handler Dispatch

### Async Handlers (Default)

Run in the main event loop:

```python
async def _dispatch_async(state, listener):
    metadata = build_metadata(state, listener)
    response = await listener.handler(state.payload, metadata)
    await self._process_response(response, listener, state)
```

### CPU-Bound Handlers

Dispatched to ProcessPoolExecutor:

```python
async def _dispatch_to_process_pool(state, listener):
    # Store data in shared backend
    payload_uuid, metadata_uuid = store_task_data(
        self._backend, state.payload, metadata
    )

    # Submit to pool
    task = WorkerTask(
        thread_uuid=state.thread_id,
        payload_uuid=payload_uuid,
        handler_path=listener.handler_path,
        metadata_uuid=metadata_uuid,
    )

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        self._process_pool, execute_handler, task
    )

    # Fetch response from backend
    response = fetch_response(self._backend, result.response_uuid)
    await self._process_response(response, listener, state)
```

## Response Processing

When a handler returns `HandlerResponse`:

```python
async def _process_response(response, listener, state):
    if response is None:
        return  # Chain terminates

    # Resolve effective peers (peer table override or static listener.peers)
    table_name = registry.get_table_for_thread(state.thread_id)
    if table_name and table_name in self._peer_tables:
        effective_peers = self._peer_tables[table_name].get(listener.name, listener.peers)
    else:
        effective_peers = listener.peers

    # Validate target
    if listener.is_agent and effective_peers:
        if response.to not in effective_peers:
            await self._emit_error(state, "Routing error")
            return

    # Build new envelope
    payload_xml = xmlify_serialize(response.payload)
    new_state = MessageState(
        raw_bytes=wrap_in_envelope(
            payload_xml,
            from_id=listener.name,  # SYSTEM INJECTS
            thread_id=get_new_thread_id(response, state),
        ),
    )

    # Re-inject
    await self._process(new_state)
```

## Error Handling

### Pipeline Errors

If any step sets `state.error`, processing stops and `<huh>` is emitted:

```xml
<huh xmlns="https://xml-pipeline.org/ns/core/v1">
  <error>XSD validation failed</error>
  <original-attempt>base64-encoded-bytes</original-attempt>
</huh>
```

### Routing Errors

If an agent tries to route to an undeclared peer:

```xml
<SystemError>
  <code>routing</code>
  <message>Message could not be delivered.</message>
  <retry-allowed>true</retry-allowed>
</SystemError>
```

## Lifecycle

```python
from xml_pipeline import StreamPump

# Start
pump = StreamPump(name="my-organism")
pump.register("greeter", handle_greeting, Greeting,
              description="Greeting agent")
await pump.start()

# Inject messages
await pump.inject("greeter", Greeting(name="Alice"))

# Shutdown (graceful)
await pump.shutdown()
```

## Configuration

```yaml
process_pool:
  workers: 4
  max_tasks_per_child: 100

backend:
  type: redis
  redis_url: "redis://localhost:6379"
```

## See Also

- [[Architecture Overview]] — High-level view
- [[Thread Registry]] — Thread tracking
- [[Shared Backend]] — Cross-process state
- [[Writing Handlers]] — Handler patterns
