"""
Flow domain model - the central aggregation point.

A Flow (also called a Team) is the complete definition of an agent workflow:
- Nodes (agents, tools, gateways)
- Edges (connections between nodes)
- Triggers (how the flow is started)
- Settings (LLM config, rate limits, etc.)

The Flow class provides:
- to_organism_yaml(): Convert to xml-pipeline organism configuration
- to_canvas_json(): Convert to frontend canvas format
- from_canvas_json(): Parse from frontend canvas format
- validate(): Check for errors before execution
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import yaml

from bloxserver.domain.nodes import (
    Node,
    NodeType,
    AgentNode,
    ToolNode,
    GatewayNode,
)
from bloxserver.domain.edges import Edge, compute_peers, find_entry_nodes, detect_cycles
from bloxserver.domain.triggers import Trigger, TriggerType


# =============================================================================
# Validation
# =============================================================================


@dataclass
class ValidationError:
    """A validation error in a flow."""

    code: str
    message: str
    node_id: UUID | None = None
    edge_id: UUID | None = None
    trigger_id: UUID | None = None
    severity: str = "error"  # error, warning, info

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "nodeId": str(self.node_id) if self.node_id else None,
            "edgeId": str(self.edge_id) if self.edge_id else None,
            "triggerId": str(self.trigger_id) if self.trigger_id else None,
            "severity": self.severity,
        }


# =============================================================================
# Flow Settings
# =============================================================================


@dataclass
class LLMSettings:
    """LLM configuration for the flow."""

    # Default model for agents without explicit model
    default_model: str = "grok-4.1"

    # Strategy for backend selection
    strategy: str = "failover"

    # Rate limits (override org-level limits)
    max_tokens_per_minute: int | None = None
    max_requests_per_minute: int | None = None

    # Retry settings
    retries: int = 3
    retry_base_delay: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "defaultModel": self.default_model,
            "strategy": self.strategy,
            "maxTokensPerMinute": self.max_tokens_per_minute,
            "maxRequestsPerMinute": self.max_requests_per_minute,
            "retries": self.retries,
            "retryBaseDelay": self.retry_base_delay,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LLMSettings:
        return cls(
            default_model=data.get("defaultModel") or data.get("default_model", "grok-4.1"),
            strategy=data.get("strategy", "failover"),
            max_tokens_per_minute=data.get("maxTokensPerMinute") or data.get("max_tokens_per_minute"),
            max_requests_per_minute=data.get("maxRequestsPerMinute") or data.get("max_requests_per_minute"),
            retries=data.get("retries", 3),
            retry_base_delay=data.get("retryBaseDelay") or data.get("retry_base_delay", 1.0),
        )


@dataclass
class FlowSettings:
    """Settings for a flow."""

    # LLM configuration
    llm: LLMSettings = field(default_factory=LLMSettings)

    # Execution settings
    timeout_seconds: int = 300  # Max execution time
    max_iterations: int = 100  # Max message loops (prevent infinite recursion)

    # Logging
    log_level: str = "info"
    log_payloads: bool = False  # Log full message payloads

    # Thread settings
    thread_ttl_seconds: int = 86400  # 24 hours

    def to_dict(self) -> dict[str, Any]:
        return {
            "llm": self.llm.to_dict(),
            "timeoutSeconds": self.timeout_seconds,
            "maxIterations": self.max_iterations,
            "logLevel": self.log_level,
            "logPayloads": self.log_payloads,
            "threadTtlSeconds": self.thread_ttl_seconds,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FlowSettings:
        llm_data = data.get("llm", {})
        return cls(
            llm=LLMSettings.from_dict(llm_data) if llm_data else LLMSettings(),
            timeout_seconds=data.get("timeoutSeconds") or data.get("timeout_seconds", 300),
            max_iterations=data.get("maxIterations") or data.get("max_iterations", 100),
            log_level=data.get("logLevel") or data.get("log_level", "info"),
            log_payloads=data.get("logPayloads") or data.get("log_payloads", False),
            thread_ttl_seconds=data.get("threadTtlSeconds") or data.get("thread_ttl_seconds", 86400),
        )


# =============================================================================
# Flow
# =============================================================================


@dataclass
class Flow:
    """
    The complete definition of an agent workflow.

    This is the domain model that bridges:
    - Frontend canvas JSON
    - Database storage
    - Organism YAML for execution
    """

    # Identity
    id: UUID
    name: str
    description: str = ""

    # Owner
    owner_id: UUID | None = None

    # Components
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    triggers: list[Trigger] = field(default_factory=list)

    # Configuration
    settings: FlowSettings = field(default_factory=FlowSettings)

    # State
    is_active: bool = False
    version: int = 1

    # Metadata
    created_at: datetime | None = None
    updated_at: datetime | None = None

    # ==========================================================================
    # Node Helpers
    # ==========================================================================

    def get_node(self, node_id: UUID) -> Node | None:
        """Get a node by ID."""
        for node in self.nodes:
            if node.id == node_id:
                return node
        return None

    def get_node_by_name(self, name: str) -> Node | None:
        """Get a node by name."""
        for node in self.nodes:
            if node.name == name:
                return node
        return None

    def get_agents(self) -> list[AgentNode]:
        """Get all agent nodes."""
        return [n for n in self.nodes if isinstance(n, AgentNode)]

    def get_tools(self) -> list[ToolNode]:
        """Get all tool nodes."""
        return [n for n in self.nodes if isinstance(n, ToolNode)]

    def get_gateways(self) -> list[GatewayNode]:
        """Get all gateway nodes."""
        return [n for n in self.nodes if isinstance(n, GatewayNode)]

    def _build_node_map(self) -> dict[UUID, str]:
        """Build a mapping of node IDs to names."""
        return {node.id: node.name for node in self.nodes}

    # ==========================================================================
    # Validation
    # ==========================================================================

    def validate(self) -> list[ValidationError]:
        """
        Validate the flow for errors.

        Returns a list of validation errors (empty if valid).
        """
        errors: list[ValidationError] = []
        node_map = self._build_node_map()
        node_ids = set(node_map.keys())

        # Check for unique node names
        names_seen: set[str] = set()
        for node in self.nodes:
            if node.name in names_seen:
                errors.append(ValidationError(
                    code="duplicate_node_name",
                    message=f"Duplicate node name: {node.name}",
                    node_id=node.id,
                ))
            names_seen.add(node.name)

        # Check for valid node names (no spaces, special chars)
        for node in self.nodes:
            if not node.name or not node.name.replace("_", "").replace("-", "").isalnum():
                errors.append(ValidationError(
                    code="invalid_node_name",
                    message=f"Invalid node name: {node.name}. Use alphanumeric, underscore, or hyphen.",
                    node_id=node.id,
                ))

        # Check agents have prompts
        for agent in self.get_agents():
            if not agent.prompt or not agent.prompt.strip():
                errors.append(ValidationError(
                    code="missing_agent_prompt",
                    message=f"Agent '{agent.name}' has no prompt",
                    node_id=agent.id,
                ))

        # Check custom tools have handler paths
        for tool in self.get_tools():
            from bloxserver.domain.nodes import ToolType, BUILTIN_TOOLS
            if tool.tool_type == ToolType.CUSTOM:
                if not tool.handler_path or not tool.payload_class_path:
                    errors.append(ValidationError(
                        code="missing_custom_tool_paths",
                        message=f"Custom tool '{tool.name}' requires handler_path and payload_class_path",
                        node_id=tool.id,
                    ))

        # Check gateways have required config
        for gateway in self.get_gateways():
            if not gateway.remote_url and not gateway.api_endpoint:
                errors.append(ValidationError(
                    code="invalid_gateway_config",
                    message=f"Gateway '{gateway.name}' requires remote_url or api_endpoint",
                    node_id=gateway.id,
                ))

        # Check edges reference valid nodes
        for edge in self.edges:
            if edge.source_node_id not in node_ids:
                errors.append(ValidationError(
                    code="invalid_edge_source",
                    message=f"Edge source node not found: {edge.source_node_id}",
                    edge_id=edge.id,
                ))
            if edge.target_node_id not in node_ids:
                errors.append(ValidationError(
                    code="invalid_edge_target",
                    message=f"Edge target node not found: {edge.target_node_id}",
                    edge_id=edge.id,
                ))

        # Check triggers reference valid nodes
        for trigger in self.triggers:
            for target_id in trigger.target_node_ids:
                if target_id not in node_ids:
                    errors.append(ValidationError(
                        code="invalid_trigger_target",
                        message=f"Trigger '{trigger.name}' targets unknown node: {target_id}",
                        trigger_id=trigger.id,
                    ))

        # Check for entry points (nodes reachable from triggers)
        entry_nodes = find_entry_nodes(list(node_ids), self.edges)
        trigger_targets = set()
        for trigger in self.triggers:
            trigger_targets.update(trigger.target_node_ids)

        orphan_entries = [nid for nid in entry_nodes if nid not in trigger_targets]
        for nid in orphan_entries:
            node = self.get_node(nid)
            if node:
                errors.append(ValidationError(
                    code="unreachable_entry_node",
                    message=f"Node '{node.name}' has no incoming edges and no trigger",
                    node_id=nid,
                    severity="warning",
                ))

        # Check for cycles (warning, not error - agents can self-loop)
        cycles = detect_cycles(list(node_ids), self.edges)
        for cycle in cycles:
            cycle_names = [node_map.get(nid, str(nid)) for nid in cycle]
            errors.append(ValidationError(
                code="cycle_detected",
                message=f"Cycle detected: {' -> '.join(cycle_names)}",
                severity="info",
            ))

        return errors

    # ==========================================================================
    # Serialization: Canvas JSON
    # ==========================================================================

    def to_canvas_json(self) -> dict[str, Any]:
        """
        Convert to React Flow canvas format.

        Returns a dict with 'nodes', 'edges', 'triggers', 'settings'.
        """
        return {
            "id": str(self.id),
            "name": self.name,
            "description": self.description,
            "nodes": [node.to_dict() for node in self.nodes],
            "edges": [edge.to_dict() for edge in self.edges],
            "triggers": [trigger.to_dict() for trigger in self.triggers],
            "settings": self.settings.to_dict(),
            "isActive": self.is_active,
            "version": self.version,
        }

    @classmethod
    def from_canvas_json(
        cls,
        data: dict[str, Any],
        flow_id: UUID | None = None,
        owner_id: UUID | None = None,
    ) -> Flow:
        """
        Create a Flow from React Flow canvas JSON.

        Args:
            data: Canvas JSON with nodes, edges, triggers, settings.
            flow_id: Override the flow ID (for updates).
            owner_id: Owner user ID.
        """
        # Parse nodes
        nodes: list[Node] = []
        for node_data in data.get("nodes", []):
            nodes.append(Node.from_dict(node_data))

        # Parse edges
        edges: list[Edge] = []
        for edge_data in data.get("edges", []):
            edges.append(Edge.from_dict(edge_data))

        # Parse triggers
        triggers: list[Trigger] = []
        for trigger_data in data.get("triggers", []):
            triggers.append(Trigger.from_dict(trigger_data))

        # Parse settings
        settings_data = data.get("settings", {})
        settings = FlowSettings.from_dict(settings_data) if settings_data else FlowSettings()

        # Determine flow ID
        if flow_id:
            fid = flow_id
        elif "id" in data:
            fid = UUID(data["id"]) if isinstance(data["id"], str) else data["id"]
        else:
            fid = uuid4()

        return cls(
            id=fid,
            name=data.get("name", "Untitled Flow"),
            description=data.get("description", ""),
            owner_id=owner_id,
            nodes=nodes,
            edges=edges,
            triggers=triggers,
            settings=settings,
            is_active=data.get("isActive") or data.get("is_active", False),
            version=data.get("version", 1),
        )

    # ==========================================================================
    # Serialization: Organism YAML
    # ==========================================================================

    def to_organism_yaml(self, port: int = 0) -> str:
        """
        Convert to xml-pipeline organism.yaml format.

        Args:
            port: Port for the organism (0 = auto-assign).

        Returns:
            YAML string ready for the xml-pipeline runner.
        """
        node_map = self._build_node_map()

        # Build listeners list
        listeners: list[dict[str, Any]] = []
        gateways: list[dict[str, Any]] = []

        for node in self.nodes:
            if isinstance(node, AgentNode):
                peers = compute_peers(node.id, self.edges, node_map)
                listeners.append(node.to_listener_config(peers))

            elif isinstance(node, ToolNode):
                listeners.append(node.to_listener_config())

            elif isinstance(node, GatewayNode):
                config = node.to_listener_config()
                if node.remote_url:
                    # Federation gateway goes in gateways section
                    gateways.append(config)
                else:
                    # REST gateway is a listener
                    listeners.append(config)

        # Build organism config
        organism_config: dict[str, Any] = {
            "organism": {
                "name": f"flow-{self.id}",
                "port": port,
            },
            "listeners": listeners,
        }

        # Add gateways if any
        if gateways:
            organism_config["gateways"] = gateways

        # Add LLM config
        organism_config["llm"] = {
            "strategy": self.settings.llm.strategy,
            "retries": self.settings.llm.retries,
            "retry_base_delay": self.settings.llm.retry_base_delay,
        }

        # Add execution settings as metadata
        organism_config["execution"] = {
            "timeout_seconds": self.settings.timeout_seconds,
            "max_iterations": self.settings.max_iterations,
            "log_level": self.settings.log_level,
            "log_payloads": self.settings.log_payloads,
        }

        # Add trigger metadata for runtime
        if self.triggers:
            organism_config["triggers"] = [
                {
                    "id": str(t.id),
                    "name": t.name,
                    "type": t.trigger_type.value,
                    "target_nodes": [node_map.get(nid, str(nid)) for nid in t.target_node_ids],
                    "enabled": t.enabled,
                }
                for t in self.triggers
            ]

        return yaml.dump(
            organism_config,
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
        )

    # ==========================================================================
    # Database Serialization
    # ==========================================================================

    def to_db_dict(self) -> dict[str, Any]:
        """
        Convert to dictionary for database storage.

        This is stored in FlowRecord.canvas_data as JSON.
        """
        return {
            "nodes": [node.to_dict() for node in self.nodes],
            "edges": [edge.to_dict() for edge in self.edges],
            "triggers": [trigger.to_dict() for trigger in self.triggers],
            "settings": self.settings.to_dict(),
        }

    @classmethod
    def from_db_record(cls, record: Any) -> Flow:
        """
        Create a Flow from a database FlowRecord.

        Args:
            record: FlowRecord ORM instance.
        """
        canvas_data = record.canvas_data or {}

        # Parse components from canvas_data
        nodes: list[Node] = []
        for node_data in canvas_data.get("nodes", []):
            nodes.append(Node.from_dict(node_data))

        edges: list[Edge] = []
        for edge_data in canvas_data.get("edges", []):
            edges.append(Edge.from_dict(edge_data))

        triggers: list[Trigger] = []
        for trigger_data in canvas_data.get("triggers", []):
            triggers.append(Trigger.from_dict(trigger_data))

        settings_data = canvas_data.get("settings", {})
        settings = FlowSettings.from_dict(settings_data) if settings_data else FlowSettings()

        return cls(
            id=record.id,
            name=record.name,
            description=record.description or "",
            owner_id=record.owner_id,
            nodes=nodes,
            edges=edges,
            triggers=triggers,
            settings=settings,
            is_active=record.status.value == "active" if hasattr(record.status, "value") else record.status == "active",
            version=record.version,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )
