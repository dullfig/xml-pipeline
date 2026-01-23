"""
SQLAlchemy ORM models for BloxServer.

These map to the Pydantic models in schemas.py and TypeScript types in types.ts.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bloxserver.api.models.database import Base


# =============================================================================
# Enums
# =============================================================================


class Tier(str, enum.Enum):
    """User subscription tier."""

    FREE = "free"
    PRO = "pro"
    ENTERPRISE = "enterprise"
    HIGH_FREQUENCY = "high_frequency"


class BillingStatus(str, enum.Enum):
    """Subscription billing status."""

    ACTIVE = "active"
    TRIALING = "trialing"
    PAST_DUE = "past_due"
    CANCELED = "canceled"
    CANCELING = "canceling"


class FlowStatus(str, enum.Enum):
    """Flow runtime status."""

    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    ERROR = "error"


class TriggerType(str, enum.Enum):
    """How a flow can be triggered."""

    WEBHOOK = "webhook"
    SCHEDULE = "schedule"
    MANUAL = "manual"


class ExecutionStatus(str, enum.Enum):
    """Status of a flow execution."""

    RUNNING = "running"
    SUCCESS = "success"
    ERROR = "error"
    TIMEOUT = "timeout"


# =============================================================================
# Users (synced from Clerk)
# =============================================================================


class UserRecord(Base):
    """User account, synced from Clerk."""

    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    clerk_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str | None] = mapped_column(String(255))
    avatar_url: Mapped[str | None] = mapped_column(Text)

    # Stripe integration
    stripe_customer_id: Mapped[str | None] = mapped_column(String(255), unique=True)
    stripe_subscription_id: Mapped[str | None] = mapped_column(String(255))
    stripe_subscription_item_id: Mapped[str | None] = mapped_column(String(255))

    # Billing state (cached from Stripe)
    tier: Mapped[Tier] = mapped_column(Enum(Tier), default=Tier.FREE)
    billing_status: Mapped[BillingStatus] = mapped_column(
        Enum(BillingStatus), default=BillingStatus.ACTIVE
    )
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    flows: Mapped[list[FlowRecord]] = relationship(back_populates="user", cascade="all, delete-orphan")
    api_keys: Mapped[list[UserApiKeyRecord]] = relationship(back_populates="user", cascade="all, delete-orphan")
    usage_records: Mapped[list[UsageRecord]] = relationship(back_populates="user", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_users_clerk_id", "clerk_id"),
        Index("idx_users_stripe_customer", "stripe_customer_id"),
    )


# =============================================================================
# Flows
# =============================================================================


class FlowRecord(Base):
    """A user's workflow/flow."""

    __tablename__ = "flows"

    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    user_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))

    # The actual workflow definition
    organism_yaml: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # React Flow canvas state (JSON)
    canvas_state: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    # Runtime state
    status: Mapped[FlowStatus] = mapped_column(Enum(FlowStatus), default=FlowStatus.STOPPED)
    container_id: Mapped[str | None] = mapped_column(String(255))
    error_message: Mapped[str | None] = mapped_column(Text)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    user: Mapped[UserRecord] = relationship(back_populates="flows")
    triggers: Mapped[list[TriggerRecord]] = relationship(back_populates="flow", cascade="all, delete-orphan")
    executions: Mapped[list[ExecutionRecord]] = relationship(back_populates="flow", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_flows_user_id", "user_id"),
        Index("idx_flows_status", "status"),
    )


# =============================================================================
# Triggers
# =============================================================================


class TriggerRecord(Base):
    """A trigger that can start a flow."""

    __tablename__ = "triggers"

    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    flow_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("flows.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[TriggerType] = mapped_column(Enum(TriggerType), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)

    # Trigger configuration (JSON)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    # Webhook-specific fields
    webhook_token: Mapped[str | None] = mapped_column(String(64), unique=True)
    webhook_url: Mapped[str | None] = mapped_column(Text)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Relationships
    flow: Mapped[FlowRecord] = relationship(back_populates="triggers")
    executions: Mapped[list[ExecutionRecord]] = relationship(back_populates="trigger")

    __table_args__ = (
        Index("idx_triggers_flow_id", "flow_id"),
        Index("idx_triggers_webhook_token", "webhook_token"),
    )


# =============================================================================
# Executions
# =============================================================================


class ExecutionRecord(Base):
    """A single execution/run of a flow."""

    __tablename__ = "executions"

    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    flow_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("flows.id", ondelete="CASCADE"), nullable=False
    )
    trigger_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("triggers.id", ondelete="SET NULL")
    )
    trigger_type: Mapped[TriggerType] = mapped_column(Enum(TriggerType), nullable=False)

    # Execution state
    status: Mapped[ExecutionStatus] = mapped_column(
        Enum(ExecutionStatus), default=ExecutionStatus.RUNNING
    )
    error_message: Mapped[str | None] = mapped_column(Text)

    # Payloads (JSON strings for flexibility)
    input_payload: Mapped[str | None] = mapped_column(Text)
    output_payload: Mapped[str | None] = mapped_column(Text)

    # Timing
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)

    # Relationships
    flow: Mapped[FlowRecord] = relationship(back_populates="executions")
    trigger: Mapped[TriggerRecord | None] = relationship(back_populates="executions")

    __table_args__ = (
        Index("idx_executions_flow_id", "flow_id"),
        Index("idx_executions_started_at", "started_at"),
        Index("idx_executions_status", "status"),
    )


# =============================================================================
# User API Keys (BYOK)
# =============================================================================


class UserApiKeyRecord(Base):
    """User's own API keys for BYOK (Bring Your Own Key)."""

    __tablename__ = "user_api_keys"

    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    user_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(50), nullable=False)

    # Encrypted API key
    encrypted_key: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_hint: Mapped[str | None] = mapped_column(String(20))  # Last few chars for display

    # Validation state
    is_valid: Mapped[bool] = mapped_column(Boolean, default=True)
    last_error: Mapped[str | None] = mapped_column(String(255))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Relationships
    user: Mapped[UserRecord] = relationship(back_populates="api_keys")

    __table_args__ = (
        Index("idx_user_api_keys_user_provider", "user_id", "provider", unique=True),
    )


# =============================================================================
# Usage Tracking
# =============================================================================


class UsageRecord(Base):
    """Usage tracking for billing."""

    __tablename__ = "usage_records"

    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    user_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    period_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # Metrics
    workflow_runs: Mapped[int] = mapped_column(Integer, default=0)
    llm_tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    llm_tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    wasm_cpu_seconds: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    storage_gb_hours: Mapped[float] = mapped_column(Numeric(10, 2), default=0)

    # Stripe sync state
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_synced_runs: Mapped[int] = mapped_column(Integer, default=0)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    user: Mapped[UserRecord] = relationship(back_populates="usage_records")

    __table_args__ = (
        Index("idx_usage_user_period", "user_id", "period_start", unique=True),
    )


# =============================================================================
# Stripe Events (Idempotency)
# =============================================================================


class StripeEventRecord(Base):
    """Processed Stripe webhook events for idempotency."""

    __tablename__ = "stripe_events"

    event_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    __table_args__ = (
        Index("idx_stripe_events_processed", "processed_at"),
    )
