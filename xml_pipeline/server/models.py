"""
models.py — Pydantic models for AgentServer API.

These models define the JSON structure for API responses.
Uses camelCase for JSON keys (JavaScript convention).
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


def to_camel(string: str) -> str:
    """Convert snake_case to camelCase."""
    components = string.split("_")
    return components[0] + "".join(x.title() for x in components[1:])


class CamelModel(BaseModel):
    """Base model with camelCase JSON serialization."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )


# =============================================================================
# Enums
# =============================================================================


class AgentState(str, Enum):
    """Agent processing state."""

    IDLE = "idle"
    PROCESSING = "processing"
    WAITING = "waiting"
    ERROR = "error"
    PAUSED = "paused"


class ThreadStatus(str, Enum):
    """Thread lifecycle status."""

    ACTIVE = "active"
    COMPLETED = "completed"
    ERROR = "error"
    KILLED = "killed"


class OrganismStatus(str, Enum):
    """Organism running status."""

    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"


# =============================================================================
# API Response Models
# =============================================================================


class AgentInfo(CamelModel):
    """Agent information for API response."""

    name: str
    description: str
    is_agent: bool = Field(alias="isAgent")
    peers: List[str] = Field(default_factory=list)
    payload_class: str = Field(alias="payloadClass")
    state: AgentState = AgentState.IDLE
    current_thread: Optional[str] = Field(None, alias="currentThread")
    queue_depth: int = Field(0, alias="queueDepth")
    last_activity: Optional[datetime] = Field(None, alias="lastActivity")
    message_count: int = Field(0, alias="messageCount")


class MessageInfo(CamelModel):
    """Message information for API response."""

    id: str
    thread_id: str = Field(alias="threadId")
    from_id: str = Field(alias="from")
    to_id: str = Field(alias="to")
    payload_type: str = Field(alias="payloadType")
    payload: Dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime
    slot_index: Optional[int] = Field(None, alias="slotIndex")


class ThreadInfo(CamelModel):
    """Thread information for API response."""

    id: str
    status: ThreadStatus = ThreadStatus.ACTIVE
    participants: List[str] = Field(default_factory=list)
    message_count: int = Field(0, alias="messageCount")
    created_at: datetime = Field(alias="createdAt")
    last_activity: Optional[datetime] = Field(None, alias="lastActivity")
    error: Optional[str] = None


class OrganismInfo(CamelModel):
    """Organism overview for API response."""

    name: str
    status: OrganismStatus = OrganismStatus.RUNNING
    uptime_seconds: float = Field(0.0, alias="uptimeSeconds")
    agent_count: int = Field(0, alias="agentCount")
    active_threads: int = Field(0, alias="activeThreads")
    total_messages: int = Field(0, alias="totalMessages")
    identity_configured: bool = Field(False, alias="identityConfigured")


class OrganismConfig(CamelModel):
    """Sanitized organism configuration for API response."""

    name: str
    port: int = 8765
    thread_scheduling: str = Field("breadth-first", alias="threadScheduling")
    max_concurrent_pipelines: int = Field(50, alias="maxConcurrentPipelines")
    max_concurrent_handlers: int = Field(20, alias="maxConcurrentHandlers")
    listeners: List[str] = Field(default_factory=list)
    # Note: Secrets like API keys are never exposed


# =============================================================================
# Request Models
# =============================================================================


class InjectRequest(CamelModel):
    """Request body for POST /inject."""

    to: str
    payload: Dict[str, Any]
    thread_id: Optional[str] = Field(None, alias="threadId")


class InjectResponse(CamelModel):
    """Response body for POST /inject."""

    thread_id: str = Field(alias="threadId")
    message_id: str = Field(alias="messageId")


class SubscribeRequest(CamelModel):
    """WebSocket subscription filter."""

    threads: Optional[List[str]] = None
    agents: Optional[List[str]] = None
    payload_types: Optional[List[str]] = Field(None, alias="payloadTypes")
    events: Optional[List[str]] = None


# =============================================================================
# WebSocket Event Models
# =============================================================================


class WSEvent(CamelModel):
    """Base WebSocket event."""

    event: str


class WSConnectedEvent(WSEvent):
    """Sent on WebSocket connection with full state snapshot."""

    event: str = "connected"
    organism: OrganismInfo
    agents: List[AgentInfo]
    threads: List[ThreadInfo]


class WSAgentStateEvent(WSEvent):
    """Agent state changed."""

    event: str = "agent_state"
    agent: str
    state: AgentState
    current_thread: Optional[str] = Field(None, alias="currentThread")


class WSMessageEvent(WSEvent):
    """New message in the system."""

    event: str = "message"
    message: MessageInfo


class WSThreadCreatedEvent(WSEvent):
    """New thread started."""

    event: str = "thread_created"
    thread: ThreadInfo


class WSThreadUpdatedEvent(WSEvent):
    """Thread status changed."""

    event: str = "thread_updated"
    thread_id: str = Field(alias="threadId")
    status: ThreadStatus
    message_count: int = Field(alias="messageCount")


class WSErrorEvent(WSEvent):
    """Error occurred."""

    event: str = "error"
    thread_id: Optional[str] = Field(None, alias="threadId")
    agent: Optional[str] = None
    error: str
    timestamp: datetime


# =============================================================================
# List Response Models
# =============================================================================


class AgentListResponse(CamelModel):
    """Response for GET /agents."""

    agents: List[AgentInfo]
    count: int


class ThreadListResponse(CamelModel):
    """Response for GET /threads."""

    threads: List[ThreadInfo]
    count: int
    total: int
    offset: int
    limit: int


class MessageListResponse(CamelModel):
    """Response for GET /messages or /threads/{id}/messages."""

    messages: List[MessageInfo]
    count: int
    total: int
    offset: int
    limit: int


class ErrorResponse(CamelModel):
    """Error response."""

    error: str
    detail: Optional[str] = None
