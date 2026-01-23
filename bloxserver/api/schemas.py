"""
Pydantic schemas for API request/response validation.

These match the TypeScript types in types.ts for frontend compatibility.
Uses camelCase aliases for JSON serialization.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Generic, Literal, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# =============================================================================
# Config for camelCase serialization
# =============================================================================


def to_camel(string: str) -> str:
    """Convert snake_case to camelCase."""
    components = string.split("_")
    return components[0] + "".join(x.title() for x in components[1:])


class CamelModel(BaseModel):
    """Base model with camelCase JSON serialization."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


# =============================================================================
# Common Types
# =============================================================================

T = TypeVar("T")


class PaginatedResponse(CamelModel, Generic[T]):
    """Paginated list response."""

    items: list[T]
    total: int
    page: int
    page_size: int
    has_more: bool


class ApiError(CamelModel):
    """API error response."""

    code: str
    message: str
    details: dict[str, Any] | None = None


# =============================================================================
# Enums
# =============================================================================


class Tier(str, Enum):
    FREE = "free"
    PRO = "pro"
    ENTERPRISE = "enterprise"
    HIGH_FREQUENCY = "high_frequency"


class FlowStatus(str, Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    ERROR = "error"


class TriggerType(str, Enum):
    WEBHOOK = "webhook"
    SCHEDULE = "schedule"
    MANUAL = "manual"


class ExecutionStatus(str, Enum):
    RUNNING = "running"
    SUCCESS = "success"
    ERROR = "error"
    TIMEOUT = "timeout"


# =============================================================================
# User
# =============================================================================


class User(CamelModel):
    """User account (synced from Clerk)."""

    id: UUID
    clerk_id: str
    email: str
    name: str | None = None
    avatar_url: str | None = None
    tier: Tier = Tier.FREE
    created_at: datetime


# =============================================================================
# Canvas State (React Flow)
# =============================================================================


class CanvasNode(CamelModel):
    """A node in the React Flow canvas."""

    id: str
    type: str
    position: dict[str, float]
    data: dict[str, Any]


class CanvasEdge(CamelModel):
    """An edge connecting nodes in the canvas."""

    id: str
    source: str
    target: str
    source_handle: str | None = None
    target_handle: str | None = None


class CanvasState(CamelModel):
    """React Flow canvas state."""

    nodes: list[CanvasNode]
    edges: list[CanvasEdge]
    viewport: dict[str, float]


# =============================================================================
# Flows
# =============================================================================


class Flow(CamelModel):
    """A user's workflow/flow."""

    id: UUID
    user_id: UUID
    name: str
    description: str | None = None
    organism_yaml: str
    canvas_state: CanvasState | None = None
    status: FlowStatus = FlowStatus.STOPPED
    container_id: str | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime


class FlowSummary(CamelModel):
    """Abbreviated flow for list views."""

    id: UUID
    name: str
    description: str | None = None
    status: FlowStatus
    updated_at: datetime


class CreateFlowRequest(CamelModel):
    """Request to create a new flow."""

    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    organism_yaml: str | None = None


class UpdateFlowRequest(CamelModel):
    """Request to update a flow."""

    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    organism_yaml: str | None = None
    canvas_state: CanvasState | None = None


# =============================================================================
# Triggers
# =============================================================================


class WebhookTriggerConfig(CamelModel):
    """Config for webhook triggers."""

    type: Literal["webhook"] = "webhook"


class ScheduleTriggerConfig(CamelModel):
    """Config for scheduled triggers."""

    type: Literal["schedule"] = "schedule"
    cron: str = Field(description="Cron expression")
    timezone: str = "UTC"


class ManualTriggerConfig(CamelModel):
    """Config for manual triggers."""

    type: Literal["manual"] = "manual"


TriggerConfig = WebhookTriggerConfig | ScheduleTriggerConfig | ManualTriggerConfig


class Trigger(CamelModel):
    """A trigger that can start a flow."""

    id: UUID
    flow_id: UUID
    type: TriggerType
    name: str
    config: dict[str, Any]
    webhook_token: str | None = None
    webhook_url: str | None = None
    created_at: datetime


class CreateTriggerRequest(CamelModel):
    """Request to create a trigger."""

    type: TriggerType
    name: str = Field(min_length=1, max_length=100)
    config: dict[str, Any]


# =============================================================================
# Executions
# =============================================================================


class Execution(CamelModel):
    """A single execution/run of a flow."""

    id: UUID
    flow_id: UUID
    trigger_id: UUID | None = None
    trigger_type: TriggerType
    status: ExecutionStatus
    started_at: datetime
    completed_at: datetime | None = None
    duration_ms: int | None = None
    error_message: str | None = None
    input_payload: str | None = None
    output_payload: str | None = None


class ExecutionSummary(CamelModel):
    """Abbreviated execution for list views."""

    id: UUID
    status: ExecutionStatus
    trigger_type: TriggerType
    started_at: datetime
    duration_ms: int | None = None


# =============================================================================
# Usage & Stats
# =============================================================================


class UsageDashboard(CamelModel):
    """Current usage for user dashboard."""

    period_start: datetime
    period_end: datetime | None
    runs_used: int
    runs_limit: int
    runs_percentage: float
    tokens_used: int
    estimated_overage: float
    days_remaining: int


class FlowStats(CamelModel):
    """Statistics for a single flow."""

    flow_id: UUID
    executions_total: int
    executions_success: int
    executions_error: int
    avg_duration_ms: float
    last_executed_at: datetime | None = None


# =============================================================================
# API Keys (BYOK)
# =============================================================================


class ApiKeyInfo(CamelModel):
    """Info about a stored API key (never exposes the key itself)."""

    provider: str
    key_hint: str | None  # Last few chars: "...abc123"
    is_valid: bool
    last_used_at: datetime | None
    created_at: datetime


class AddApiKeyRequest(CamelModel):
    """Request to add a user's API key."""

    provider: str = Field(description="Provider name: openai, anthropic, xai")
    api_key: str = Field(min_length=10, description="The API key")
