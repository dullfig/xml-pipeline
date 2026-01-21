# Nextra SaaS Platform — Architecture Design Document

**Version:** 1.0 (Draft)
**Date:** January 2026
**Status:** Planning

## Executive Summary

Nextra is a SaaS platform for building AI agent workflows using the xml-pipeline library. Users visually design message flows on a canvas, which generates the underlying YAML configuration. Flows run on isolated container instances with support for built-in tools, marketplace components, and custom WASM modules.

### Key Differentiators

- **Visual flow builder** with real-time YAML synchronization
- **Turing-complete** message routing (self-iteration, conditionals, parallel execution)
- **WASM sandboxing** for custom code (no Python upload = secure)
- **Marketplace** for sharing tools and complete flows
- **Anti-paperclipper** design with user-controlled memory

---

## System Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                              USERS                                       │
│                    (Browser / API Clients)                               │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         VERCEL (Frontend)                                │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │                     Next.js Application                           │  │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐               │  │
│  │  │ Flow Canvas │  │  YAML Tab   │  │  Monaco     │               │  │
│  │  │ (React Flow)│  │  (Preview)  │  │  (WASM)     │               │  │
│  │  └─────────────┘  └─────────────┘  └─────────────┘               │  │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐               │  │
│  │  │ Dashboard   │  │ Marketplace │  │  Settings   │               │  │
│  │  └─────────────┘  └─────────────┘  └─────────────┘               │  │
│  └───────────────────────────────────────────────────────────────────┘  │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │ REST / GraphQL
                                ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         RENDER (Backend)                                 │
│                                                                          │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │                   Control Plane (FastAPI)                         │  │
│  │  • User management (via Clerk webhooks)                           │  │
│  │  • Flow CRUD (organism.yaml storage)                              │  │
│  │  • Pump orchestration (start/stop/scale)                          │  │
│  │  • Trigger routing (webhooks → pump injection)                    │  │
│  │  • Marketplace catalog                                            │  │
│  │  • WASM module registry                                           │  │
│  │  • Billing integration (Stripe)                                   │  │
│  └───────────────────────────────────────────────────────────────────┘  │
│                                │                                         │
│                                │ Orchestrates                            │
│                                ▼                                         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                  │
│  │   Pump       │  │   Pump       │  │   Pump       │                  │
│  │   Container  │  │   Container  │  │   Container  │                  │
│  │   (Flow A)   │  │   (Flow B)   │  │   (Flow C)   │                  │
│  │              │  │              │  │              │                  │
│  │ StreamPump   │  │ StreamPump   │  │ StreamPump   │                  │
│  │ + WASM RT    │  │ + WASM RT    │  │ + WASM RT    │                  │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘                  │
│         │                 │                 │                           │
│         └─────────────────┼─────────────────┘                           │
│                           ▼                                              │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │                      Redis (Shared State)                         │  │
│  │  • Context buffers: tenant:{id}:flow:{id}:buffer:*               │  │
│  │  • Thread registry: tenant:{id}:flow:{id}:registry:*             │  │
│  │  • Project memory: tenant:{id}:flow:{id}:memory:* (opt-in)       │  │
│  └───────────────────────────────────────────────────────────────────┘  │
│                                                                          │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │                     PostgreSQL (Persistent)                       │  │
│  │  • Users, organizations                                           │  │
│  │  • Flows (organism.yaml stored as text)                          │  │
│  │  • Marketplace listings                                          │  │
│  │  • WASM modules (metadata, S3 refs)                              │  │
│  │  • Billing records                                               │  │
│  └───────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                        EXTERNAL SERVICES                                 │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐               │
│  │  Clerk   │  │  Stripe  │  │ LLM APIs │  │    S3    │               │
│  │  (Auth)  │  │ (Billing)│  │ (xAI,etc)│  │  (WASM)  │               │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘               │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Tier Model

| Tier | Price | Flows | Tools | Custom Code | Features |
|------|-------|-------|-------|-------------|----------|
| **Free** | $0 | 1 | Built-in only | ❌ | Community support |
| **Paid** | $X/mo | Multiple | + Marketplace | ❌ | Email support |
| **Pro** | $XX/mo | Unlimited | + Marketplace | ✅ WASM/WIT | Priority support |
| **Enterprise** | Custom | Unlimited | + Private | ✅ WASM/WIT | SSO, roles, SLA |

### Limits (TBD)

| Resource | Free | Paid | Pro | Enterprise |
|----------|------|------|-----|------------|
| Flows | 1 | 10 | Unlimited | Unlimited |
| Executions/month | 1,000 | 10,000 | 100,000 | Custom |
| WASM modules | 0 | 0 | 10 | Unlimited |
| Project memory | ❌ | 10MB | 100MB | Custom |
| Team members | 1 | 1 | 1 | Unlimited |

---

## Component Architecture

### Frontend (Next.js on Vercel)

#### Tech Stack
- **Framework:** Next.js 14+ (App Router)
- **UI Generation:** Vercel v0
- **Components:** shadcn/ui + Tailwind CSS
- **Flow Canvas:** React Flow (Xyflow)
- **Code Editor:** Monaco Editor
- **State:** Zustand or Jotai
- **API Client:** tRPC or React Query

#### Key Pages

| Route | Purpose |
|-------|---------|
| `/` | Landing page |
| `/dashboard` | Flow list, usage stats |
| `/flow/[id]` | Flow canvas editor |
| `/flow/[id]/yaml` | YAML editor view |
| `/flow/[id]/runs` | Execution history |
| `/marketplace` | Browse tools/flows |
| `/settings` | Account, billing, API keys |

#### Flow Canvas Features

```
┌─────────────────────────────────────────────────────────────────┐
│  [Save] [Run] [Stop]                    [YAML] [Canvas] [Split] │
├─────────────────────────────────────────────────────────────────┤
│ ┌───────────┐                                                   │
│ │ Palette   │    ┌─────────┐      ┌─────────┐                  │
│ │           │    │ Webhook │ ───▶ │   LLM   │ ──┐              │
│ │ [Built-in]│    │ Trigger │      │  Call   │   │              │
│ │ [Market]  │    └─────────┘      └─────────┘   │              │
│ │ [Custom]  │                                    │              │
│ │           │                     ┌─────────┐   │              │
│ │ 📦 Trigger│                     │  Code   │ ◀─┘              │
│ │ 📦 LLM    │                     │  Block  │                  │
│ │ 📦 HTTP   │                     └────┬────┘                  │
│ │ 📦 Code   │                          │                       │
│ │ 📦 Branch │                          ▼                       │
│ │ ...       │                     ┌─────────┐                  │
│ └───────────┘                     │  Output │                  │
│                                   └─────────┘                  │
├─────────────────────────────────────────────────────────────────┤
│ Minimap │ Zoom: 100% │ Nodes: 4 │ Status: Saved               │
└─────────────────────────────────────────────────────────────────┘
```

#### Node Types

| Node | Visual | Maps To |
|------|--------|---------|
| Trigger | 🎯 Circle | Injection endpoint |
| LLM Call | 🤖 Box | Agent listener |
| HTTP Request | 🌐 Box | HTTP tool |
| Code Block | 📝 Box | WASM handler |
| Conditional | ◇ Diamond | Branch logic |
| Output | 📤 Box | Terminal handler |
| Loop | ↻ Arrow back | Self-iteration |

### Control Plane (FastAPI on Render)

#### Tech Stack
- **Framework:** FastAPI
- **ORM:** SQLAlchemy 2.0 + asyncpg
- **Validation:** Pydantic v2
- **Task Queue:** (Optional) Celery or arq
- **Container Orchestration:** Render Native (or Docker API)

#### API Endpoints

```
Authentication (via Clerk)
───────────────────────────
POST   /webhooks/clerk          # Clerk webhook for user sync

Flows
───────────────────────────
GET    /api/flows               # List user's flows
POST   /api/flows               # Create flow
GET    /api/flows/{id}          # Get flow details
PUT    /api/flows/{id}          # Update flow (canvas → YAML)
DELETE /api/flows/{id}          # Delete flow
POST   /api/flows/{id}/start    # Start pump container
POST   /api/flows/{id}/stop     # Stop pump container
GET    /api/flows/{id}/status   # Pump status
GET    /api/flows/{id}/logs     # Stream logs

Triggers
───────────────────────────
POST   /api/triggers/{flow_id}/webhook/{token}  # Webhook ingress
POST   /api/triggers/{flow_id}/inject           # Manual injection

Marketplace
───────────────────────────
GET    /api/marketplace/tools   # Browse tools
GET    /api/marketplace/flows   # Browse flow templates
POST   /api/marketplace/publish # Publish to marketplace

WASM Modules (Pro+)
───────────────────────────
GET    /api/modules             # List user's modules
POST   /api/modules             # Upload WASM module
DELETE /api/modules/{id}        # Delete module
```

#### Database Schema (PostgreSQL)

```sql
-- Users (synced from Clerk)
CREATE TABLE users (
    id UUID PRIMARY KEY,
    clerk_id TEXT UNIQUE NOT NULL,
    email TEXT NOT NULL,
    tier TEXT DEFAULT 'free',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Flows
CREATE TABLE flows (
    id UUID PRIMARY KEY,
    user_id UUID REFERENCES users(id),
    name TEXT NOT NULL,
    description TEXT,
    organism_yaml TEXT NOT NULL,      -- The actual config
    canvas_state JSONB,               -- React Flow state
    status TEXT DEFAULT 'stopped',    -- stopped, starting, running, error
    container_id TEXT,                -- Render container ID
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- WASM Modules
CREATE TABLE wasm_modules (
    id UUID PRIMARY KEY,
    user_id UUID REFERENCES users(id),
    name TEXT NOT NULL,
    description TEXT,
    s3_key TEXT NOT NULL,             -- S3 path to .wasm file
    wit_interface TEXT,               -- WIT definition
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Marketplace Listings
CREATE TABLE marketplace_listings (
    id UUID PRIMARY KEY,
    author_id UUID REFERENCES users(id),
    type TEXT NOT NULL,               -- 'tool' or 'flow'
    name TEXT NOT NULL,
    description TEXT,
    content JSONB NOT NULL,           -- Tool def or flow template
    downloads INT DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Execution History
CREATE TABLE executions (
    id UUID PRIMARY KEY,
    flow_id UUID REFERENCES flows(id),
    trigger_type TEXT,                -- webhook, manual, schedule
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    status TEXT,                      -- success, error
    error_message TEXT
);
```

### Pump Containers (Render)

Each flow gets its own container running:
- StreamPump (from xml-pipeline)
- WASM runtime (wasmtime)
- Redis connection (shared state)

#### Container Image

```dockerfile
FROM python:3.11-slim

# Install xml-pipeline
COPY requirements.txt .
RUN pip install -r requirements.txt

# Install wasmtime
RUN pip install wasmtime

# Copy entrypoint
COPY entrypoint.py .

# Environment variables provided by orchestrator:
# - FLOW_ID
# - ORGANISM_YAML (base64 encoded)
# - REDIS_URL
# - TENANT_PREFIX

CMD ["python", "entrypoint.py"]
```

#### Entrypoint

```python
# entrypoint.py
import os
import base64
import asyncio
from xml_pipeline.message_bus.stream_pump import StreamPump
from xml_pipeline.config.loader import load_config_from_string
from xml_pipeline.memory.shared_backend import get_shared_backend, BackendConfig

async def main():
    # Load config from environment
    yaml_content = base64.b64decode(os.environ["ORGANISM_YAML"]).decode()
    config = load_config_from_string(yaml_content)

    # Configure shared backend with tenant prefix
    backend_config = BackendConfig(
        backend_type="redis",
        redis_url=os.environ["REDIS_URL"],
        redis_prefix=os.environ["TENANT_PREFIX"],
    )
    backend = get_shared_backend(backend_config)

    # Start pump
    pump = StreamPump(config, backend=backend)
    await pump.start()

    # Keep running
    try:
        while True:
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        await pump.shutdown()

if __name__ == "__main__":
    asyncio.run(main())
```

---

## Trigger System

Triggers inject messages into running pumps.

### Trigger Types

| Trigger | Implementation |
|---------|----------------|
| **Webhook** | Control plane receives POST, forwards to pump via Redis pub/sub |
| **Schedule** | Celery beat or Render Cron, injects at scheduled times |
| **Manual** | "Run" button in UI, calls control plane API |
| **Email** | (Future) IMAP polling service |

### Webhook Flow

```
External Service
      │
      │ POST /api/triggers/{flow_id}/webhook/{token}
      ▼
┌─────────────────┐
│  Control Plane  │
│                 │
│  1. Validate    │
│  2. Find pump   │
│  3. Publish     │
└────────┬────────┘
         │ Redis PUBLISH trigger:{flow_id}
         ▼
┌─────────────────┐
│  Pump Container │
│                 │
│  1. Subscribe   │
│  2. Inject msg  │
│  3. Process     │
└─────────────────┘
```

---

## Security Model

### Multi-Tenancy Isolation

```
┌─────────────────────────────────────────────────────────────┐
│                    Tenant A                                  │
│  ┌────────────┐  ┌────────────┐                             │
│  │  Flow 1    │  │  Flow 2    │                             │
│  │            │  │            │                             │
│  │  Redis:    │  │  Redis:    │                             │
│  │  tenantA:  │  │  tenantA:  │                             │
│  │  flow1:*   │  │  flow2:*   │                             │
│  └────────────┘  └────────────┘                             │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│                    Tenant B                                  │
│  ┌────────────┐                                             │
│  │  Flow 3    │  ← Cannot access tenantA:* keys             │
│  │            │                                             │
│  │  Redis:    │                                             │
│  │  tenantB:  │                                             │
│  │  flow3:*   │                                             │
│  └────────────┘                                             │
└─────────────────────────────────────────────────────────────┘
```

### WASM Sandboxing

Custom code runs in WASM, which provides:
- **Memory isolation** — Cannot access host memory
- **No filesystem** — Only WIT-defined host functions
- **No network** — Must use provided HTTP tool
- **CPU limits** — Fuel-based execution limits
- **Deterministic** — Same input → same output

### Memory Safety (Anti-Paperclipper)

```
┌─────────────────────────────────────────────────────────────┐
│                    Memory Tiers                              │
├─────────────────────────────────────────────────────────────┤
│  Thread Memory (automatic)                                   │
│  ├── Per-execution context buffer                           │
│  ├── Pruned when thread completes                           │
│  └── Swarm cannot prevent deletion                          │
├─────────────────────────────────────────────────────────────┤
│  Project Memory (opt-in, Pro+)                              │
│  ├── User explicitly enables per flow                       │
│  ├── Size limits enforced                                   │
│  ├── User can view/delete anytime                           │
│  └── Cleared on flow deletion                               │
├─────────────────────────────────────────────────────────────┤
│  Cross-Flow Memory (FORBIDDEN)                              │
│  ├── Flow A cannot read Flow B's memory                     │
│  ├── Even same user, different flows = isolated             │
│  └── Prevents swarm coordination across boundaries          │
└─────────────────────────────────────────────────────────────┘
```

---

## Data Flow Examples

### User Creates Flow

```
1. User drags nodes on canvas
2. Frontend converts to organism.yaml
3. PUT /api/flows/{id} with YAML
4. Control plane validates YAML
5. Saves to PostgreSQL
6. Returns success
```

### User Starts Flow

```
1. User clicks "Start"
2. POST /api/flows/{id}/start
3. Control plane:
   a. Fetches YAML from DB
   b. Creates Render container
   c. Passes YAML + Redis config as env vars
   d. Updates flow.status = 'starting'
4. Container starts, pump initializes
5. Pump reports ready via Redis
6. Control plane updates flow.status = 'running'
7. Frontend shows green "Running" status
```

### Webhook Triggers Flow

```
1. External service POSTs to webhook URL
2. Control plane receives at /api/triggers/{flow_id}/webhook/{token}
3. Control plane validates token
4. Control plane publishes to Redis: PUBLISH trigger:{flow_id} {payload}
5. Pump container (subscribed) receives message
6. Pump injects message into StreamPump
7. Pipeline processes, handlers execute
8. Results logged to execution history
```

---

## Canvas ↔ YAML Synchronization

### Canvas → YAML

```javascript
// Frontend: Convert React Flow state to organism.yaml
function canvasToYaml(nodes, edges) {
  const listeners = nodes
    .filter(n => n.type !== 'trigger')
    .map(node => ({
      name: node.data.name,
      handler: node.data.handler,
      payload_class: node.data.payloadClass,
      description: node.data.description,
      agent: node.data.isAgent || false,
      peers: edges
        .filter(e => e.source === node.id)
        .map(e => findNode(e.target).data.name),
    }));

  return yaml.dump({
    organism: { name: flowName },
    listeners,
  });
}
```

### YAML → Canvas

```javascript
// Frontend: Convert organism.yaml to React Flow state
function yamlToCanvas(yamlContent) {
  const config = yaml.load(yamlContent);

  const nodes = config.listeners.map((listener, i) => ({
    id: listener.name,
    type: getNodeType(listener),
    position: calculatePosition(i),
    data: {
      name: listener.name,
      handler: listener.handler,
      payloadClass: listener.payload_class,
      description: listener.description,
      isAgent: listener.agent,
    },
  }));

  const edges = config.listeners.flatMap(listener =>
    (listener.peers || []).map(peer => ({
      id: `${listener.name}-${peer}`,
      source: listener.name,
      target: peer,
    }))
  );

  return { nodes, edges };
}
```

---

## Marketplace

### Publishing a Tool

```
1. User creates WASM module (Pro+)
2. User clicks "Publish to Marketplace"
3. Frontend sends:
   - Module metadata
   - Description, icon, category
   - Pricing (free or paid)
4. Control plane:
   - Validates module
   - Creates marketplace listing
   - Makes module available to others
```

### Installing a Tool

```
1. User browses marketplace
2. User clicks "Install" on tool
3. Control plane:
   - Adds tool to user's available tools
   - Copies WASM module reference
4. Tool appears in user's palette under "Marketplace" tab
```

### Publishing a Flow Template

```
1. User creates working flow
2. User clicks "Publish as Template"
3. Frontend sends:
   - Flow YAML (sanitized)
   - Description, use case
4. Control plane creates listing
5. Other users can "Use Template" to clone flow
```

---

## Monitoring & Observability

### Metrics (Prometheus/Grafana)

| Metric | Description |
|--------|-------------|
| `nextra_flows_total` | Total flows by status |
| `nextra_executions_total` | Executions by flow, status |
| `nextra_pump_memory_bytes` | Memory per pump container |
| `nextra_pump_messages_total` | Messages processed |
| `nextra_api_requests_total` | API requests by endpoint |

### Logging

- **Control Plane:** Structured JSON logs → CloudWatch/Datadog
- **Pump Containers:** Stream to Redis → Viewable in UI
- **Execution History:** Stored in PostgreSQL

### Alerting

| Alert | Condition |
|-------|-----------|
| Pump crash | Container exits unexpectedly |
| High error rate | >5% executions failing |
| Memory pressure | Pump using >80% memory |
| Stuck flow | No messages processed in 5min |

---

## Scaling Considerations

### Render Service Types

| Component | Render Service | Scaling |
|-----------|----------------|---------|
| Control Plane | Web Service | Horizontal (multiple instances) |
| Pump Containers | Private Services | Per-flow, scale-to-zero |
| Redis | Managed Redis | Vertical |
| PostgreSQL | Managed Postgres | Vertical |

### Scale-to-Zero (Cost Optimization)

```
Free tier flows:
- Auto-stop after 15 min idle
- Webhook triggers wake container (~5s cold start)
- User sees "Starting..." briefly

Paid tier flows:
- Keep-alive option
- Faster cold starts (warm pool)
```

### Future: Multi-Region

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   US-East    │     │   EU-West    │     │   AP-Tokyo   │
│              │     │              │     │              │
│ Control Plane│ ←───│ Control Plane│ ←───│ Control Plane│
│ Pump Pool    │     │ Pump Pool    │     │ Pump Pool    │
│ Redis        │     │ Redis        │     │ Redis        │
└──────────────┘     └──────────────┘     └──────────────┘
        │                   │                   │
        └───────────────────┼───────────────────┘
                            ▼
                  ┌──────────────────┐
                  │  Global Postgres │
                  │  (CockroachDB?)  │
                  └──────────────────┘
```

---

## Implementation Phases

### Phase 1: MVP (4-6 weeks)

- [ ] Control Plane basic CRUD
- [ ] Single pump container (manual start/stop)
- [ ] Canvas with basic nodes (LLM, HTTP, Output)
- [ ] YAML preview (read-only)
- [ ] Clerk authentication
- [ ] Free tier only

### Phase 2: Core Features (4-6 weeks)

- [ ] Automatic pump orchestration
- [ ] Webhook triggers
- [ ] Execution history
- [ ] Canvas ↔ YAML sync
- [ ] Paid tier + Stripe billing

### Phase 3: Pro Features (4-6 weeks)

- [ ] WASM module upload
- [ ] Monaco editor integration
- [ ] Project memory (opt-in)
- [ ] Pro tier

### Phase 4: Marketplace (4-6 weeks)

- [ ] Tool publishing
- [ ] Flow templates
- [ ] Browse/search/install
- [ ] Ratings/reviews

### Phase 5: Enterprise (TBD)

- [ ] Team/org management
- [ ] Role-based access
- [ ] SSO (SAML)
- [ ] SLA dashboard
- [ ] Private marketplace

---

## Open Questions

1. **Pricing specifics** — What are the actual price points?
2. **Execution metering** — How to count/limit executions fairly?
3. **WASM module review** — Manual review before marketplace publish?
4. **Cold start optimization** — Warm container pool for paid users?
5. **Mobile support** — Canvas on mobile, or just monitoring?

---

## Appendix: Technology Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Frontend Framework | Next.js | v0 generates it, Vercel hosts it |
| Canvas Library | React Flow | Most popular, good docs, n8n uses it |
| Control Plane | FastAPI | Matches xml-pipeline, async-native |
| Database | PostgreSQL | Render managed, reliable |
| Cache/Pubsub | Redis | Already needed for xml-pipeline shared backend |
| Auth | Clerk | Free to 10K, great DX, handles OAuth |
| Billing | Stripe | Standard, good APIs |
| Frontend Hosting | Vercel | Built for Next.js |
| Backend Hosting | Render | Simple, good DX, containers |
| WASM Runtime | wasmtime | Best WIT support |

---

*Document generated: January 2026*
*Next review: After Phase 1 completion*
