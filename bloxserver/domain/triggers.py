"""
Trigger types for starting flow execution.

Triggers define how a flow is initiated:
- Webhook: External HTTP POST to a unique URL
- Schedule: Cron-based scheduled execution
- Manual: User-initiated from dashboard
- Event: Internal event subscription
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4


class TriggerType(str, Enum):
    """Types of triggers that can start a flow."""

    WEBHOOK = "webhook"
    SCHEDULE = "schedule"
    MANUAL = "manual"
    EVENT = "event"


# =============================================================================
# Trigger Configs
# =============================================================================


@dataclass
class WebhookConfig:
    """Configuration for webhook triggers."""

    # Auto-generated token for authentication
    token: str | None = None

    # Optional: require specific headers
    required_headers: dict[str, str] = field(default_factory=dict)

    # Optional: IP allowlist
    allowed_ips: list[str] = field(default_factory=list)

    # Payload transformation
    payload_template: str | None = None  # Jinja2 template for input transformation

    def to_dict(self) -> dict[str, Any]:
        return {
            "token": self.token,
            "requiredHeaders": self.required_headers,
            "allowedIps": self.allowed_ips,
            "payloadTemplate": self.payload_template,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WebhookConfig:
        return cls(
            token=data.get("token"),
            required_headers=data.get("requiredHeaders") or data.get("required_headers", {}),
            allowed_ips=data.get("allowedIps") or data.get("allowed_ips", []),
            payload_template=data.get("payloadTemplate") or data.get("payload_template"),
        )


@dataclass
class ScheduleConfig:
    """Configuration for scheduled triggers."""

    # Cron expression (e.g., "0 9 * * *" for 9 AM daily)
    cron: str

    # Timezone (e.g., "America/New_York")
    timezone: str = "UTC"

    # Optional: specific dates to skip
    skip_dates: list[str] = field(default_factory=list)

    # Optional: payload to inject on trigger
    default_payload: dict[str, Any] = field(default_factory=dict)

    # Execution window (skip if missed by more than N minutes)
    grace_period_minutes: int = 5

    def to_dict(self) -> dict[str, Any]:
        return {
            "cron": self.cron,
            "timezone": self.timezone,
            "skipDates": self.skip_dates,
            "defaultPayload": self.default_payload,
            "gracePeriodMinutes": self.grace_period_minutes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ScheduleConfig:
        return cls(
            cron=data["cron"],
            timezone=data.get("timezone", "UTC"),
            skip_dates=data.get("skipDates") or data.get("skip_dates", []),
            default_payload=data.get("defaultPayload") or data.get("default_payload", {}),
            grace_period_minutes=data.get("gracePeriodMinutes") or data.get("grace_period_minutes", 5),
        )


@dataclass
class ManualConfig:
    """Configuration for manual triggers."""

    # Form fields to show in dashboard
    input_fields: list[dict[str, Any]] = field(default_factory=list)

    # Default values
    default_payload: dict[str, Any] = field(default_factory=dict)

    # Confirmation required before execution
    require_confirmation: bool = False
    confirmation_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "inputFields": self.input_fields,
            "defaultPayload": self.default_payload,
            "requireConfirmation": self.require_confirmation,
            "confirmationMessage": self.confirmation_message,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ManualConfig:
        return cls(
            input_fields=data.get("inputFields") or data.get("input_fields", []),
            default_payload=data.get("defaultPayload") or data.get("default_payload", {}),
            require_confirmation=data.get("requireConfirmation") or data.get("require_confirmation", False),
            confirmation_message=data.get("confirmationMessage") or data.get("confirmation_message"),
        )


@dataclass
class EventConfig:
    """Configuration for internal event triggers."""

    # Event name to subscribe to
    event_name: str

    # Optional: filter expression
    filter_expression: str | None = None

    # Source flow ID (if triggered by another flow)
    source_flow_id: UUID | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "eventName": self.event_name,
            "filterExpression": self.filter_expression,
            "sourceFlowId": str(self.source_flow_id) if self.source_flow_id else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EventConfig:
        source_flow = data.get("sourceFlowId") or data.get("source_flow_id")
        return cls(
            event_name=data["eventName"] if "eventName" in data else data["event_name"],
            filter_expression=data.get("filterExpression") or data.get("filter_expression"),
            source_flow_id=UUID(source_flow) if source_flow else None,
        )


# Union type for trigger configs
TriggerConfig = WebhookConfig | ScheduleConfig | ManualConfig | EventConfig


# =============================================================================
# Trigger
# =============================================================================


@dataclass
class Trigger:
    """
    A trigger that initiates flow execution.

    Triggers are connected to entry nodes in the flow.
    When fired, they inject a message into the entry node(s).
    """

    id: UUID
    name: str
    trigger_type: TriggerType
    config: TriggerConfig

    # Which node(s) to send the initial message to
    target_node_ids: list[UUID] = field(default_factory=list)

    # Enabled state
    enabled: bool = True

    # Metadata
    created_at: datetime | None = None
    last_triggered_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "name": self.name,
            "triggerType": self.trigger_type.value,
            "config": self.config.to_dict(),
            "targetNodeIds": [str(nid) for nid in self.target_node_ids],
            "enabled": self.enabled,
            "createdAt": self.created_at.isoformat() if self.created_at else None,
            "lastTriggeredAt": self.last_triggered_at.isoformat() if self.last_triggered_at else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Trigger:
        trigger_type = TriggerType(data.get("triggerType") or data.get("trigger_type"))
        config_data = data.get("config", {})

        # Parse config based on trigger type
        config: TriggerConfig
        if trigger_type == TriggerType.WEBHOOK:
            config = WebhookConfig.from_dict(config_data)
        elif trigger_type == TriggerType.SCHEDULE:
            config = ScheduleConfig.from_dict(config_data)
        elif trigger_type == TriggerType.MANUAL:
            config = ManualConfig.from_dict(config_data)
        elif trigger_type == TriggerType.EVENT:
            config = EventConfig.from_dict(config_data)
        else:
            raise ValueError(f"Unknown trigger type: {trigger_type}")

        # Parse target node IDs
        target_ids_raw = data.get("targetNodeIds") or data.get("target_node_ids", [])
        target_node_ids = [
            UUID(nid) if isinstance(nid, str) else nid
            for nid in target_ids_raw
        ]

        # Parse timestamps
        created_at = None
        if created := data.get("createdAt") or data.get("created_at"):
            created_at = datetime.fromisoformat(created) if isinstance(created, str) else created

        last_triggered = None
        if triggered := data.get("lastTriggeredAt") or data.get("last_triggered_at"):
            last_triggered = datetime.fromisoformat(triggered) if isinstance(triggered, str) else triggered

        return cls(
            id=UUID(data["id"]) if isinstance(data.get("id"), str) else data.get("id", uuid4()),
            name=data["name"],
            trigger_type=trigger_type,
            config=config,
            target_node_ids=target_node_ids,
            enabled=data.get("enabled", True),
            created_at=created_at,
            last_triggered_at=last_triggered,
        )

    def get_webhook_url(self, base_url: str, flow_id: UUID) -> str | None:
        """Get the webhook URL if this is a webhook trigger."""
        if self.trigger_type != TriggerType.WEBHOOK:
            return None

        if not isinstance(self.config, WebhookConfig):
            return None

        token = self.config.token or ""
        return f"{base_url}/webhooks/{flow_id}/{self.id}?token={token}"


def create_webhook_trigger(
    name: str,
    target_node_ids: list[UUID],
    token: str | None = None,
) -> Trigger:
    """Factory function to create a webhook trigger."""
    return Trigger(
        id=uuid4(),
        name=name,
        trigger_type=TriggerType.WEBHOOK,
        config=WebhookConfig(token=token),
        target_node_ids=target_node_ids,
        enabled=True,
    )


def create_schedule_trigger(
    name: str,
    cron: str,
    target_node_ids: list[UUID],
    timezone: str = "UTC",
) -> Trigger:
    """Factory function to create a scheduled trigger."""
    return Trigger(
        id=uuid4(),
        name=name,
        trigger_type=TriggerType.SCHEDULE,
        config=ScheduleConfig(cron=cron, timezone=timezone),
        target_node_ids=target_node_ids,
        enabled=True,
    )


def create_manual_trigger(
    name: str,
    target_node_ids: list[UUID],
    input_fields: list[dict[str, Any]] | None = None,
) -> Trigger:
    """Factory function to create a manual trigger."""
    return Trigger(
        id=uuid4(),
        name=name,
        trigger_type=TriggerType.MANUAL,
        config=ManualConfig(input_fields=input_fields or []),
        target_node_ids=target_node_ids,
        enabled=True,
    )
