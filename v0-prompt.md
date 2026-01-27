# BloxServer Frontend — v0 Prompt

Build a Next.js 14+ frontend for BloxServer, a visual AI agent workflow builder. The UI paradigm is like **Excel meets n8n** — toolbar at top, tabs at bottom, flow canvas in the middle.

## Tech Stack
- **Framework:** Next.js 14 (App Router)
- **Components:** shadcn/ui + Tailwind CSS
- **Flow Canvas:** React Flow (@xyflow/react)
- **Code Editor:** Monaco Editor (for YAML and WASM/AssemblyScript)
- **State:** Zustand
- **Auth:** Clerk
- **API:** REST (see types below)

## Pages

| Route | Purpose |
|-------|---------|
| `/` | Landing page (marketing) |
| `/dashboard` | Flow list, usage stats, quick actions |
| `/flow/[id]` | Main flow editor (canvas + YAML tabs) |
| `/flow/[id]/runs` | Execution history for a flow |
| `/marketplace` | Browse/install tools and flow templates |
| `/settings` | Account, billing, API keys |

## Flow Editor Layout

```
┌─────────────────────────────────────────────────────────────────┐
│  TOOLBAR: [Save] [Run ▶] [Stop ■]          [Canvas|YAML|Split]  │
├─────────────────────────────────────────────────────────────────┤
│ ┌─────────┐                                                     │
│ │ PALETTE │    ┌─────────┐      ┌─────────┐      ┌─────────┐   │
│ │         │    │ Webhook │ ───▶ │   LLM   │ ───▶ │ Output  │   │
│ │[Triggers]    │ Trigger │      │  Agent  │      │         │   │
│ │[LLM]    │    └─────────┘      └─────────┘      └─────────┘   │
│ │[HTTP]   │                                                     │
│ │[Code]   │    Drag nodes from palette onto canvas.            │
│ │[Branch] │    Connect outputs to inputs.                       │
│ │[Output] │    Canvas auto-saves.                               │
│ │         │                                                     │
│ │[Market] │    YAML tab shows generated config.                 │
│ │ ↳ search│    Edits in YAML reflect back on canvas.           │
│ └─────────┘                                                     │
├─────────────────────────────────────────────────────────────────┤
│  STATUS BAR: Nodes: 3 │ Status: Saved │ Last run: 2m ago       │
└─────────────────────────────────────────────────────────────────┘
```

## Node Types

| Type | Icon | Purpose |
|------|------|---------|
| `trigger` | 🎯 | Entry point (webhook, schedule, manual) |
| `llmCall` | 🤖 | LLM agent (Claude, GPT, Grok) |
| `httpRequest` | 🌐 | External API call |
| `codeBlock` | 📝 | Custom WASM code (Pro) |
| `conditional` | 🔀 | Branch based on condition |
| `output` | 📤 | Terminal node / response |

## Dashboard Features
- List of user's flows (cards or table)
- Flow status indicator (stopped/running/error)
- Quick actions: New Flow, Import, Run
- Usage stats (executions this month, limit)

## Key UX Patterns
1. **Bidirectional sync:** Canvas changes update YAML, YAML changes update canvas
2. **Auto-save:** Changes saved automatically (debounced)
3. **Run/Stop:** Single click to deploy/undeploy a flow
4. **Execution history:** Click a run to see logs, input/output payloads
5. **Node inspector:** Click a node to edit its config in a side panel

## API Base URL
`https://api.openblox.ai/v1` (placeholder — backend on Render)

---

## TypeScript API Types

```typescript
// Common
export type UUID = string;
export type ISODateTime = string;

// User (synced from Clerk)
export interface User {
  id: UUID;
  clerkId: string;
  email: string;
  name: string | null;
  avatarUrl: string | null;
  tier: 'free' | 'paid' | 'pro' | 'enterprise';
  createdAt: ISODateTime;
}

// Flow
export type FlowStatus = 'stopped' | 'starting' | 'running' | 'stopping' | 'error';

export interface Flow {
  id: UUID;
  userId: UUID;
  name: string;
  description: string | null;
  organismYaml: string;
  canvasState: CanvasState | null;
  status: FlowStatus;
  containerId: string | null;
  errorMessage: string | null;
  createdAt: ISODateTime;
  updatedAt: ISODateTime;
}

export interface FlowSummary {
  id: UUID;
  name: string;
  description: string | null;
  status: FlowStatus;
  updatedAt: ISODateTime;
}

export interface CreateFlowRequest {
  name: string;
  description?: string;
  organismYaml?: string;
}

export interface UpdateFlowRequest {
  name?: string;
  description?: string;
  organismYaml?: string;
  canvasState?: CanvasState;
}

// Canvas State (React Flow)
export interface CanvasState {
  nodes: CanvasNode[];
  edges: CanvasEdge[];
  viewport: { x: number; y: number; zoom: number };
}

export interface CanvasNode {
  id: string;
  type: NodeType;
  position: { x: number; y: number };
  data: NodeData;
}

export type NodeType =
  | 'trigger'
  | 'llmCall'
  | 'httpRequest'
  | 'codeBlock'
  | 'conditional'
  | 'output'
  | 'custom';

export interface NodeData {
  name: string;
  label: string;
  description?: string;
  handler?: string;
  payloadClass?: string;
  isAgent?: boolean;
  config?: Record<string, unknown>;
}

export interface CanvasEdge {
  id: string;
  source: string;
  target: string;
  sourceHandle?: string;
  targetHandle?: string;
}

// Triggers
export type TriggerType = 'webhook' | 'schedule' | 'manual';

export interface Trigger {
  id: UUID;
  flowId: UUID;
  type: TriggerType;
  name: string;
  config: TriggerConfig;
  webhookToken?: string;
  webhookUrl?: string;
  createdAt: ISODateTime;
}

export type TriggerConfig =
  | { type: 'webhook' }
  | { type: 'schedule'; cron: string; timezone?: string }
  | { type: 'manual' };

export interface CreateTriggerRequest {
  type: TriggerType;
  name: string;
  config: TriggerConfig;
}

// Executions (Run History)
export type ExecutionStatus = 'running' | 'success' | 'error' | 'timeout';

export interface Execution {
  id: UUID;
  flowId: UUID;
  triggerId: UUID | null;
  triggerType: TriggerType;
  status: ExecutionStatus;
  startedAt: ISODateTime;
  completedAt: ISODateTime | null;
  durationMs: number | null;
  errorMessage: string | null;
  inputPayload: string | null;
  outputPayload: string | null;
}

export interface ExecutionSummary {
  id: UUID;
  status: ExecutionStatus;
  triggerType: TriggerType;
  startedAt: ISODateTime;
  durationMs: number | null;
}

// Marketplace
export type MarketplaceListingType = 'tool' | 'flow';

export interface MarketplaceListingSummary {
  id: UUID;
  authorName: string;
  type: MarketplaceListingType;
  name: string;
  description: string;
  category: string;
  downloads: number;
  rating: number | null;
}

// API Responses
export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page: number;
  pageSize: number;
  hasMore: boolean;
}

export interface ApiError {
  code: string;
  message: string;
  details?: Record<string, unknown>;
}

// Stats
export interface FlowStats {
  flowId: UUID;
  executionsTotal: number;
  executionsSuccess: number;
  executionsError: number;
  avgDurationMs: number;
  lastExecutedAt: ISODateTime | null;
}

export interface UsageStats {
  userId: UUID;
  period: 'day' | 'month';
  flowCount: number;
  executionCount: number;
  executionLimit: number;
}
```

---

## API Endpoints

### Flows
- `GET /flows` — List user's flows (returns `FlowSummary[]`)
- `POST /flows` — Create flow
- `GET /flows/:id` — Get full flow
- `PATCH /flows/:id` — Update flow
- `DELETE /flows/:id` — Delete flow
- `POST /flows/:id/start` — Start flow (deploy container)
- `POST /flows/:id/stop` — Stop flow

### Triggers
- `GET /flows/:id/triggers` — List triggers
- `POST /flows/:id/triggers` — Create trigger
- `DELETE /triggers/:id` — Delete trigger

### Executions
- `GET /flows/:id/executions` — List runs (paginated)
- `GET /executions/:id` — Get run details
- `GET /executions/:id/logs` — Stream logs (WebSocket)

### Marketplace
- `GET /marketplace` — Browse listings
- `GET /marketplace/:id` — Get listing details
- `POST /marketplace/:id/install` — Install to user's library

### User
- `GET /me` — Current user info + usage stats

---

## Design Notes

1. **Dark mode default** — Developer audience prefers dark
2. **Keyboard shortcuts** — Cmd+S save, Cmd+Enter run, etc.
3. **Toast notifications** — For save/run/error feedback
4. **Loading states** — Skeleton loaders, not spinners
5. **Mobile:** Dashboard is responsive, flow editor is desktop-only (show message on mobile)

---

## Start With
1. Dashboard page with flow list
2. Flow editor with React Flow canvas
3. Basic YAML tab (Monaco, read-only first)
4. Run/Stop buttons that call API

Then iterate: node inspector panel, execution history, marketplace.
