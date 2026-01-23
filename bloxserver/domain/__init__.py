"""
BloxServer Domain Model.

This module contains the core domain classes that represent flows (teams),
nodes (agents/tools), edges (connections), and triggers.

The domain model serves as the bridge between:
- Frontend canvas JSON (React Flow)
- Database storage (FlowRecord)
- Execution engine (organism.yaml)
"""

from bloxserver.domain.nodes import (
    Node,
    NodeType,
    AgentNode,
    ToolNode,
    GatewayNode,
)
from bloxserver.domain.edges import Edge, EdgeCondition
from bloxserver.domain.triggers import (
    Trigger,
    TriggerType,
    TriggerConfig,
    create_webhook_trigger,
    create_schedule_trigger,
    create_manual_trigger,
)
from bloxserver.domain.flow import Flow, FlowSettings, LLMSettings, ValidationError

__all__ = [
    # Nodes
    "Node",
    "NodeType",
    "AgentNode",
    "ToolNode",
    "GatewayNode",
    # Edges
    "Edge",
    "EdgeCondition",
    # Triggers
    "Trigger",
    "TriggerType",
    "TriggerConfig",
    "create_webhook_trigger",
    "create_schedule_trigger",
    "create_manual_trigger",
    # Flow
    "Flow",
    "FlowSettings",
    "LLMSettings",
    "ValidationError",
]
