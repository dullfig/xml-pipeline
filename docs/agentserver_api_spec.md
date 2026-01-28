# Agentserver API Specification

The agentserver is the backend for monitoring and controlling an xml-pipeline organism. It exposes a GUI-agnostic API that any frontend can consume - whether a 3D visualization, flow diagram, web dashboard, or CLI tool.

**Design principle:** The API is the product. GUIs are just consumers.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        Agentserver                          │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
│  │  REST API   │  │  WebSocket  │  │    Message Pump     │  │
│  │  (queries)  │  │   (push)    │  │    (organism)       │  │
│  └─────────────┘  └─────────────┘  └─────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
         │                  │
         ▼                  ▼
┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│  3D Office  │    │  n8n-style  │    │  Dashboard  │
│     GUI     │    │    flow     │    │     CLI     │
└─────────────┘    └─────────────┘    └─────────────┘
```

## Data Model

### Agent
```json
{
  "name": "greeter",
  "description": "Greeting agent - forwards to shouter",
  "is_agent": true,
  "peers": ["shouter"],
  "payload_class": "handlers.hello.Greeting",
  "state": "idle",
  "current_thread": null,
  "queue_depth": 0,
  "last_activity": "2024-01-15T10:30:00Z"
}
```

**States:** `idle`, `processing`, `waiting`, `error`

### Message
```json
{
  "id": "msg-uuid",
  "thread_id": "thread-uuid",
  "from": "greeter",
  "to": "shouter",
  "payload_type": "GreetingResponse",
  "payload": { "message": "Hello!", "original_sender": "console" },
  "timestamp": "2024-01-15T10:30:01Z"
}
```

### Thread
```json
{
  "id": "thread-uuid",
  "status": "active",
  "participants": ["console", "greeter", "shouter", "response-handler"],
  "message_count": 4,
  "created_at": "2024-01-15T10:30:00Z",
  "last_activity": "2024-01-15T10:30:03Z",
  "error": null
}
```

**Statuses:** `active`, `completed`, `error`, `killed`

### Organism
```json
{
  "name": "hello-world",
  "status": "running",
  "uptime_seconds": 3600,
  "agent_count": 4,
  "active_threads": 2,
  "total_messages": 150
}
```

### Library (Codebase Intelligence)
```json
{
  "library_id": "fastapi-abc12345",
  "name": "fastapi",
  "source_url": "https://github.com/tiangolo/fastapi.git",
  "created_at": "2024-01-15T10:00:00Z",
  "total_files": 142,
  "total_chunks": 890,
  "stats": {
    "functions": 234,
    "classes": 45,
    "lang_python": 890
  }
}
```

### Chunk (Code Fragment)
```json
{
  "chunk_id": "fastapi/routing.py:APIRouter:a1b2c3d4",
  "library_id": "fastapi-abc12345",
  "file_path": "fastapi/routing.py",
  "name": "APIRouter",
  "chunk_type": "class",
  "language": "python",
  "start_line": 150,
  "end_line": 320,
  "signature": "class APIRouter(routing.Router)",
  "docstring": "A router for grouping path operations."
}
```

**Chunk types:** `function`, `method`, `class`, `module`, `section`, `block`

**Languages:** `python`, `javascript`, `typescript`, `cpp`, `c`, `rust`, `go`, `java`, `markdown`, `unknown`

---

## REST API

Base URL: `https://host:443/api/v1`

### Topology & Config

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/organism` | GET | Organism info and stats |
| `/organism/config` | GET | Full YAML config (sanitized) |
| `/agents` | GET | List all agents with current state |
| `/agents/{name}` | GET | Single agent details |
| `/agents/{name}/config` | GET | Agent's YAML config section |
| `/agents/{name}/schema` | GET | Agent's payload XML schema |

### Threads & Messages

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/threads` | GET | List threads (paginated, filterable) |
| `/threads/{id}` | GET | Thread details with message history |
| `/threads/{id}/messages` | GET | Messages in thread (paginated) |
| `/messages` | GET | Global message history (paginated) |

**Query params for lists:**
- `?limit=50` - page size
- `?offset=0` - pagination offset
- `?status=active` - filter by status
- `?agent=greeter` - filter by participant
- `?since=2024-01-15T10:00:00Z` - filter by time

### Control

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/inject` | POST | Send message to an agent |
| `/threads/{id}/kill` | POST | Terminate a thread |
| `/agents/{name}/pause` | POST | Pause an agent |
| `/agents/{name}/resume` | POST | Resume a paused agent |
| `/organism/reload` | POST | Hot-reload config |
| `/organism/stop` | POST | Graceful shutdown |

#### POST /inject
```json
{
  "to": "greeter",
  "payload": { "name": "Dan" },
  "thread_id": "optional-existing-thread"
}
```

Response:
```json
{
  "thread_id": "new-or-existing-uuid",
  "message_id": "msg-uuid"
}
```

### Librarian (Codebase Intelligence)

RAG-powered codebase intelligence. Ingest git repositories, chunk code intelligently, and query with natural language.

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/librarian/libraries` | GET | List all ingested libraries |
| `/librarian/libraries` | POST | Ingest a new codebase |
| `/librarian/libraries/{id}` | GET | Get library details and index |
| `/librarian/libraries/{id}` | DELETE | Delete a library |
| `/librarian/libraries/{id}/query` | POST | Query library with natural language |
| `/librarian/libraries/{id}/chunks/{chunk_id}` | GET | Get specific code chunk |

#### GET /librarian/libraries

List all ingested libraries.

Response:
```json
{
  "libraries": [
    {
      "library_id": "fastapi-abc12345",
      "name": "fastapi",
      "source_url": "https://github.com/tiangolo/fastapi.git",
      "created_at": "2024-01-15T10:00:00Z",
      "total_files": 142,
      "total_chunks": 890
    }
  ],
  "count": 1
}
```

#### POST /librarian/libraries

Ingest a git repository.

Request:
```json
{
  "git_url": "https://github.com/tiangolo/fastapi.git",
  "branch": "main",
  "library_name": "fastapi"
}
```

Response:
```json
{
  "library_id": "fastapi-abc12345",
  "library_name": "fastapi",
  "files_processed": 142,
  "chunks_created": 890,
  "index_built": true,
  "errors": []
}
```

#### GET /librarian/libraries/{id}

Get library index with structural information.

Response:
```json
{
  "library_id": "fastapi-abc12345",
  "name": "fastapi",
  "source_url": "https://github.com/tiangolo/fastapi.git",
  "created_at": "2024-01-15T10:00:00Z",
  "files": ["fastapi/main.py", "fastapi/routing.py", ...],
  "functions": {
    "get_swagger_ui_html": "fastapi/openapi/docs.py",
    "get_redoc_html": "fastapi/openapi/docs.py"
  },
  "classes": {
    "FastAPI": "fastapi/applications.py",
    "APIRouter": "fastapi/routing.py"
  },
  "stats": {
    "chunks": 890,
    "files": 142,
    "functions": 234,
    "classes": 45,
    "lang_python": 890
  }
}
```

#### POST /librarian/libraries/{id}/query

Query a library with natural language. Uses RAG to search relevant chunks and synthesize an answer.

Request:
```json
{
  "question": "How does dependency injection work in FastAPI?",
  "max_chunks": 20,
  "model": "grok-4.1"
}
```

Response:
```json
{
  "answer": "FastAPI implements dependency injection through the `Depends` function...",
  "sources": [
    {
      "file_path": "fastapi/dependencies/utils.py",
      "name": "solve_dependencies",
      "chunk_type": "function",
      "start_line": 45,
      "end_line": 120,
      "relevance_score": 0.95,
      "snippet": "async def solve_dependencies(..."
    },
    {
      "file_path": "fastapi/params.py",
      "name": "Depends",
      "chunk_type": "class",
      "start_line": 10,
      "end_line": 35,
      "relevance_score": 0.88,
      "snippet": "class Depends:..."
    }
  ],
  "tokens_used": 2450,
  "chunks_examined": 15
}
```

#### GET /librarian/libraries/{id}/chunks/{chunk_id}

Retrieve a specific code chunk (for viewing full source).

Response:
```json
{
  "chunk_id": "fastapi/routing.py:APIRouter:a1b2c3d4",
  "file_path": "fastapi/routing.py",
  "name": "APIRouter",
  "chunk_type": "class",
  "language": "python",
  "start_line": 150,
  "end_line": 320,
  "content": "class APIRouter:\n    \"\"\"...",
  "docstring": "A router for grouping path operations.",
  "signature": "class APIRouter(routing.Router)"
}
```

---

## WebSocket API

### Main Control Channel

Endpoint: `wss://host:443/ws`

#### Connection
1. Client connects to `/ws`
2. Server sends `connected` event with current state snapshot
3. Server pushes events as they occur
4. Client can send control commands

---

### Message Log Stream

Endpoint: `wss://host:443/ws/messages`

Dedicated stream of all messages flowing through the organism. Clients can filter by agent to reconstruct individual message buffers in real-time.

#### Connection
```json
{
  "cmd": "subscribe",
  "filter": {
    "agents": ["greeter", "shouter"],  // optional, empty = all
    "threads": ["thread-uuid"],         // optional, empty = all
    "payload_types": ["GreetingResponse"] // optional
  }
}
```

#### Message Event
Every message that flows through the pump:
```json
{
  "id": "msg-uuid",
  "thread_id": "thread-uuid",
  "from": "greeter",
  "to": "shouter",
  "payload_type": "GreetingResponse",
  "payload": {
    "message": "Hello Dan!",
    "original_sender": "console"
  },
  "timestamp": "2024-01-15T10:30:01.123Z",
  "slot_index": 3
}
```

#### Use Case: Reconstructing Agent Buffers

Client-side, filter messages by agent to build each agent's context buffer:

```javascript
const buffers = {};  // agent_name -> messages[]

ws.onmessage = (event) => {
  const msg = JSON.parse(event.data);

  // Add to sender's outbox
  buffers[msg.from] = buffers[msg.from] || [];
  buffers[msg.from].push({ direction: 'out', ...msg });

  // Add to receiver's inbox
  buffers[msg.to] = buffers[msg.to] || [];
  buffers[msg.to].push({ direction: 'in', ...msg });
};
```

This allows the GUI to show each agent's "conversation view" in real-time.

---

### Event Types

#### connected
Sent immediately on connection with full state snapshot.
```json
{
  "event": "connected",
  "organism": { ... },
  "agents": [ ... ],
  "threads": [ ... ]
}
```

#### agent_state
Agent state changed.
```json
{
  "event": "agent_state",
  "agent": "greeter",
  "state": "processing",
  "current_thread": "thread-uuid"
}
```

#### message
New message in the system.
```json
{
  "event": "message",
  "message": {
    "id": "msg-uuid",
    "thread_id": "thread-uuid",
    "from": "greeter",
    "to": "shouter",
    "payload_type": "GreetingResponse",
    "payload": { ... },
    "timestamp": "2024-01-15T10:30:01Z"
  }
}
```

#### thread_created
New thread started.
```json
{
  "event": "thread_created",
  "thread": {
    "id": "thread-uuid",
    "status": "active",
    "participants": ["console", "greeter"],
    "created_at": "2024-01-15T10:30:00Z"
  }
}
```

#### thread_updated
Thread status changed.
```json
{
  "event": "thread_updated",
  "thread_id": "thread-uuid",
  "status": "completed",
  "message_count": 4
}
```

#### error
Error occurred.
```json
{
  "event": "error",
  "thread_id": "thread-uuid",
  "agent": "greeter",
  "error": "LLM timeout after 30s",
  "timestamp": "2024-01-15T10:30:05Z"
}
```

### Client Commands

Clients can send commands over the WebSocket:

#### subscribe
Filter which events to receive.
```json
{
  "cmd": "subscribe",
  "threads": ["thread-uuid"],
  "agents": ["greeter", "shouter"],
  "events": ["message", "agent_state"]
}
```

#### inject
Same as REST inject.
```json
{
  "cmd": "inject",
  "to": "greeter",
  "payload": { "name": "Dan" }
}
```

---

## Authentication

TBD - options:
- API key in header (`X-API-Key`)
- Bearer token (JWT)
- WebSocket auth via initial message

---

## Example: 3D Office GUI

The 3D office visualization would use this API as follows:

1. **Connect** to WebSocket, receive `connected` with all agents
2. **Render** cubicles for each agent, light cones based on state
3. **On `agent_state`** - update mannequin pose, light intensity
4. **On `message`** - show thought bubble, animate from→to
5. **On click** - dim non-peer lights, fetch `/agents/{name}/config` for popup
6. **User injects message** - send `inject` command via WebSocket

---

## Implementation Notes

- Agentserver wraps the existing `StreamPump`
- Hooks into pump events to generate WebSocket pushes
- REST endpoints query `ContextBuffer` and pump state
- Consider rate limiting WebSocket events for large swarms
- Payload content may need sanitization (no secrets in API responses)
