"""
Nextra API Contract - Pydantic Models

These models define the API contract between frontend and backend.
They match the TypeScript types in types.ts.

Usage:
    from nextra.api.models import Flow, CreateFlowRequest
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Generic, Literal, TypeVar
from uuid import UUID

from pydantic import BaseModel, Field


# =============================================================================
# Common Types
# =============================================================================

T = TypeVar("T")


class PaginatedResponse(BaseModel, Generic[T]):
    """Paginated list response."""

    items: list[T]
    total: int
    page: int
    page_size: int = Field(alias="pageSize")
    has_more: bool = Field(alias="hasMore")

    class Config:
        populate_by_name = True


class ApiError(BaseModel):
    """API error response."""

    code: str
    message: str
    details: dict[str, Any] | None = None


# =============================================================================
# User (synced from Clerk)
# =============================================================================

class Tier(str, Enum):
    FREE = "free"
    PAID = "paid"
    PRO = "pro"
    ENTERPRISE = "enterprise"


class User(BaseModel):
    """User account (synced from Clerk)."""

    id: UUID
    clerk_id: str = Field(alias="clerkId")
    email: str
    name: str | None = None
    avatar_url: str | None = Field(default=None, alias="avatarUrl")
    tier: Tier = Tier.FREE
    created_at: datetime = Field(alias="createdAt")

    class Config:
        populate_by_name = True


# =============================================================================
# Flows
# =============================================================================

class FlowStatus(str, Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    ERROR = "error"


class CanvasNode(BaseModel):
    """A node in the React Flow canvas."""

    id: str
    type: str
    position: dict[str, float]
    data: dict[str, Any]


class CanvasEdge(BaseModel):
    """An edge connecting nodes in the canvas."""

    id: str
    source: str
    target: str
    source_handle: str | None = Field(default=None, alias="sourceHandle")
    target_handle: str | None = Field(default=None, alias="targetHandle")

    class Config:
        populate_by_name = True


class CanvasState(BaseModel):
    """React Flow canvas state."""

    nodes: list[CanvasNode]
    edges: list[CanvasEdge]
    viewport: dict[str, float]


class Flow(BaseModel):
    """A user's workflow/flow."""

    id: UUID
    user_id: UUID = Field(alias="userId")
    name: str
    description: str | None = None
    organism_yaml: str = Field(alias="organismYaml")
    canvas_state: CanvasState | None = Field(default=None, alias="canvasState")
    status: FlowStatus = FlowStatus.STOPPED
    container_id: str | None = Field(default=None, alias="containerId")
    error_message: str | None = Field(default=None, alias="errorMessage")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")

    class Config:
        populate_by_name = True


class FlowSummary(BaseModel):
    """Abbreviated flow for list views."""

    id: UUID
    name: str
    description: str | None = None
    status: FlowStatus
    updated_at: datetime = Field(alias="updatedAt")

    class Config:
        populate_by_name = True


class CreateFlowRequest(BaseModel):
    """Request to create a new flow."""

    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    organism_yaml: str | None = Field(default=None, alias="organismYaml")

    class Config:
        populate_by_name = True


class UpdateFlowRequest(BaseModel):
    """Request to update a flow."""

    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    organism_yaml: str | None = Field(default=None, alias="organismYaml")
    canvas_state: CanvasState | None = Field(default=None, alias="canvasState")

    class Config:
        populate_by_name = True


# =============================================================================
# Triggers
# =============================================================================

class TriggerType(str, Enum):
    WEBHOOK = "webhook"
    SCHEDULE = "schedule"
    MANUAL = "manual"


class WebhookTriggerConfig(BaseModel):
    """Config for webhook triggers."""

    type: Literal["webhook"] = "webhook"


class ScheduleTriggerConfig(BaseModel):
    """Config for scheduled triggers."""

    type: Literal["schedule"] = "schedule"
    cron: str = Field(description="Cron expression")
    timezone: str = "UTC"


class ManualTriggerConfig(BaseModel):
    """Config for manual triggers."""

    type: Literal["manual"] = "manual"


TriggerConfig = WebhookTriggerConfig | ScheduleTriggerConfig | ManualTriggerConfig


class Trigger(BaseModel):
    """A trigger that can start a flow."""

    id: UUID
    flow_id: UUID = Field(alias="flowId")
    type: TriggerType
    name: str
    config: TriggerConfig
    webhook_token: str | None = Field(default=None, alias="webhookToken")
    webhook_url: str | None = Field(default=None, alias="webhookUrl")
    created_at: datetime = Field(alias="createdAt")

    class Config:
        populate_by_name = True


class CreateTriggerRequest(BaseModel):
    """Request to create a trigger."""

    type: TriggerType
    name: str = Field(min_length=1, max_length=100)
    config: TriggerConfig


# =============================================================================
# Executions
# =============================================================================

class ExecutionStatus(str, Enum):
    RUNNING = "running"
    SUCCESS = "success"
    ERROR = "error"
    TIMEOUT = "timeout"


class Execution(BaseModel):
    """A single execution/run of a flow."""

    id: UUID
    flow_id: UUID = Field(alias="flowId")
    trigger_id: UUID | None = Field(default=None, alias="triggerId")
    trigger_type: TriggerType = Field(alias="triggerType")
    status: ExecutionStatus
    started_at: datetime = Field(alias="startedAt")
    completed_at: datetime | None = Field(default=None, alias="completedAt")
    duration_ms: int | None = Field(default=None, alias="durationMs")
    error_message: str | None = Field(default=None, alias="errorMessage")
    input_payload: str | None = Field(default=None, alias="inputPayload")
    output_payload: str | None = Field(default=None, alias="outputPayload")

    class Config:
        populate_by_name = True


class ExecutionSummary(BaseModel):
    """Abbreviated execution for list views."""

    id: UUID
    status: ExecutionStatus
    trigger_type: TriggerType = Field(alias="triggerType")
    started_at: datetime = Field(alias="startedAt")
    duration_ms: int | None = Field(default=None, alias="durationMs")

    class Config:
        populate_by_name = True


# =============================================================================
# WASM Modules (Pro+)
# =============================================================================

class WasmModule(BaseModel):
    """A custom WASM module."""

    id: UUID
    user_id: UUID = Field(alias="userId")
    name: str
    description: str | None = None
    wit_interface: str | None = Field(default=None, alias="witInterface")
    size_bytes: int = Field(alias="sizeBytes")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")

    class Config:
        populate_by_name = True


class WasmModuleSummary(BaseModel):
    """Abbreviated module for list views."""

    id: UUID
    name: str
    description: str | None = None
    created_at: datetime = Field(alias="createdAt")

    class Config:
        populate_by_name = True


class CreateWasmModuleRequest(BaseModel):
    """Request to create a WASM module (file uploaded separately)."""

    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    wit_interface: str | None = Field(default=None, alias="witInterface")

    class Config:
        populate_by_name = True


# =============================================================================
# Marketplace
# =============================================================================

class MarketplaceListingType(str, Enum):
    TOOL = "tool"
    FLOW = "flow"


class ToolContent(BaseModel):
    """Content for a tool listing."""

    type: Literal["tool"] = "tool"
    wasm_module_id: UUID = Field(alias="wasmModuleId")
    wit_interface: str = Field(alias="witInterface")
    example_usage: str = Field(alias="exampleUsage")

    class Config:
        populate_by_name = True


class FlowTemplateContent(BaseModel):
    """Content for a flow template listing."""

    type: Literal["flow"] = "flow"
    organism_yaml: str = Field(alias="organismYaml")
    canvas_state: CanvasState = Field(alias="canvasState")

    class Config:
        populate_by_name = True


MarketplaceContent = ToolContent | FlowTemplateContent


class MarketplaceListing(BaseModel):
    """A marketplace listing (tool or flow template)."""

    id: UUID
    author_id: UUID = Field(alias="authorId")
    author_name: str = Field(alias="authorName")
    type: MarketplaceListingType
    name: str
    description: str
    category: str
    tags: list[str]
    downloads: int = 0
    rating: float | None = None
    content: MarketplaceContent
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")

    class Config:
        populate_by_name = True


class MarketplaceListingSummary(BaseModel):
    """Abbreviated listing for browse views."""

    id: UUID
    author_name: str = Field(alias="authorName")
    type: MarketplaceListingType
    name: str
    description: str
    category: str
    downloads: int
    rating: float | None = None

    class Config:
        populate_by_name = True


class PublishToMarketplaceRequest(BaseModel):
    """Request to publish to marketplace."""

    type: MarketplaceListingType
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=10, max_length=2000)
    category: str
    tags: list[str] = Field(default_factory=list, max_length=10)
    content: MarketplaceContent


# =============================================================================
# Project Memory (Pro+)
# =============================================================================

class ProjectMemory(BaseModel):
    """Project memory status for a flow."""

    flow_id: UUID = Field(alias="flowId")
    enabled: bool = False
    used_bytes: int = Field(default=0, alias="usedBytes")
    max_bytes: int = Field(alias="maxBytes")
    keys: list[str] = Field(default_factory=list)

    class Config:
        populate_by_name = True


class MemoryEntry(BaseModel):
    """A single memory entry."""

    key: str
    value: str  # JSON string
    size_bytes: int = Field(alias="sizeBytes")
    updated_at: datetime = Field(alias="updatedAt")

    class Config:
        populate_by_name = True


# =============================================================================
# Logs
# =============================================================================

class LogLevel(str, Enum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class LogEntry(BaseModel):
    """A log entry from a running flow."""

    timestamp: datetime
    level: LogLevel
    message: str
    metadata: dict[str, Any] | None = None


# =============================================================================
# Stats
# =============================================================================

class FlowStats(BaseModel):
    """Statistics for a single flow."""

    flow_id: UUID = Field(alias="flowId")
    executions_total: int = Field(alias="executionsTotal")
    executions_success: int = Field(alias="executionsSuccess")
    executions_error: int = Field(alias="executionsError")
    avg_duration_ms: float = Field(alias="avgDurationMs")
    last_executed_at: datetime | None = Field(default=None, alias="lastExecutedAt")

    class Config:
        populate_by_name = True


class UsageStats(BaseModel):
    """Usage statistics for billing/limits."""

    user_id: UUID = Field(alias="userId")
    period: Literal["day", "month"]
    flow_count: int = Field(alias="flowCount")
    execution_count: int = Field(alias="executionCount")
    execution_limit: int = Field(alias="executionLimit")

    class Config:
        populate_by_name = True
