/**
 * Nextra API Contract - TypeScript Types
 *
 * These types define the API contract between frontend and backend.
 * The FastAPI backend uses matching Pydantic models.
 *
 * Usage in frontend:
 *   import type { Flow, CreateFlowRequest } from '@/types/api';
 */

// =============================================================================
// Common Types
// =============================================================================

export type UUID = string;
export type ISODateTime = string;

// =============================================================================
// User (synced from Clerk)
// =============================================================================

export interface User {
  id: UUID;
  clerkId: string;
  email: string;
  name: string | null;
  avatarUrl: string | null;
  tier: 'free' | 'paid' | 'pro' | 'enterprise';
  createdAt: ISODateTime;
}

// =============================================================================
// Flows
// =============================================================================

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
  organismYaml?: string;  // Default template if not provided
}

export interface UpdateFlowRequest {
  name?: string;
  description?: string;
  organismYaml?: string;
  canvasState?: CanvasState;
}

// =============================================================================
// Canvas State (React Flow)
// =============================================================================

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

// =============================================================================
// Triggers
// =============================================================================

export type TriggerType = 'webhook' | 'schedule' | 'manual';

export interface Trigger {
  id: UUID;
  flowId: UUID;
  type: TriggerType;
  name: string;
  config: TriggerConfig;
  webhookToken?: string;  // Only for webhook triggers
  webhookUrl?: string;    // Full URL for webhook triggers
  createdAt: ISODateTime;
}

export type TriggerConfig =
  | WebhookTriggerConfig
  | ScheduleTriggerConfig
  | ManualTriggerConfig;

export interface WebhookTriggerConfig {
  type: 'webhook';
}

export interface ScheduleTriggerConfig {
  type: 'schedule';
  cron: string;           // Cron expression
  timezone?: string;      // Default: UTC
}

export interface ManualTriggerConfig {
  type: 'manual';
}

export interface CreateTriggerRequest {
  type: TriggerType;
  name: string;
  config: TriggerConfig;
}

// =============================================================================
// Executions (Run History)
// =============================================================================

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
  inputPayload: string | null;   // JSON string
  outputPayload: string | null;  // JSON string
}

export interface ExecutionSummary {
  id: UUID;
  status: ExecutionStatus;
  triggerType: TriggerType;
  startedAt: ISODateTime;
  durationMs: number | null;
}

// =============================================================================
// WASM Modules (Pro+)
// =============================================================================

export interface WasmModule {
  id: UUID;
  userId: UUID;
  name: string;
  description: string | null;
  witInterface: string | null;
  sizeBytes: number;
  createdAt: ISODateTime;
  updatedAt: ISODateTime;
}

export interface WasmModuleSummary {
  id: UUID;
  name: string;
  description: string | null;
  createdAt: ISODateTime;
}

export interface CreateWasmModuleRequest {
  name: string;
  description?: string;
  witInterface?: string;
  // Actual .wasm file uploaded separately via multipart
}

// =============================================================================
// Marketplace
// =============================================================================

export type MarketplaceListingType = 'tool' | 'flow';

export interface MarketplaceListing {
  id: UUID;
  authorId: UUID;
  authorName: string;
  type: MarketplaceListingType;
  name: string;
  description: string;
  category: string;
  tags: string[];
  downloads: number;
  rating: number | null;
  content: MarketplaceContent;
  createdAt: ISODateTime;
  updatedAt: ISODateTime;
}

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

export type MarketplaceContent = ToolContent | FlowTemplateContent;

export interface ToolContent {
  type: 'tool';
  wasmModuleId: UUID;
  witInterface: string;
  exampleUsage: string;
}

export interface FlowTemplateContent {
  type: 'flow';
  organismYaml: string;
  canvasState: CanvasState;
}

export interface PublishToMarketplaceRequest {
  type: MarketplaceListingType;
  name: string;
  description: string;
  category: string;
  tags: string[];
  content: MarketplaceContent;
}

// =============================================================================
// Project Memory (Pro+, opt-in)
// =============================================================================

export interface ProjectMemory {
  flowId: UUID;
  enabled: boolean;
  usedBytes: number;
  maxBytes: number;
  keys: string[];
}

export interface MemoryEntry {
  key: string;
  value: string;  // JSON string
  sizeBytes: number;
  updatedAt: ISODateTime;
}

// =============================================================================
// API Responses
// =============================================================================

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

// =============================================================================
// Flow Logs (streaming)
// =============================================================================

export interface LogEntry {
  timestamp: ISODateTime;
  level: 'debug' | 'info' | 'warning' | 'error';
  message: string;
  metadata?: Record<string, unknown>;
}

// =============================================================================
// Stats & Analytics
// =============================================================================

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
