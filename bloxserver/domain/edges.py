"""
Edge model for connections between nodes.

Edges define how messages flow between nodes in a flow.
They can optionally have conditions for conditional routing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import UUID, uuid4


class EdgeCondition(str, Enum):
    """Types of edge conditions."""

    ALWAYS = "always"  # Always route (default)
    ON_SUCCESS = "on_success"  # Route only if source succeeds
    ON_ERROR = "on_error"  # Route only if source fails
    CONDITIONAL = "conditional"  # Custom condition expression


@dataclass
class Edge:
    """
    Connection between two nodes.

    Edges define the flow of messages between nodes. In organism.yaml terms,
    they define the `peers` list for agents and the routing paths.
    """

    id: UUID
    source_node_id: UUID
    target_node_id: UUID

    # Optional label for UI
    label: str | None = None

    # Condition for when this edge is followed
    condition: EdgeCondition = EdgeCondition.ALWAYS

    # Custom condition expression (when condition == CONDITIONAL)
    # Example: "payload.status == 'approved'"
    condition_expression: str | None = None

    # Visual properties for canvas
    source_handle: str | None = None  # Which output port
    target_handle: str | None = None  # Which input port
    animated: bool = False
    edge_type: str = "default"  # default, smoothstep, step, straight

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization (React Flow format)."""
        return {
            "id": str(self.id),
            "source": str(self.source_node_id),
            "target": str(self.target_node_id),
            "label": self.label,
            "data": {
                "condition": self.condition.value,
                "conditionExpression": self.condition_expression,
            },
            "sourceHandle": self.source_handle,
            "targetHandle": self.target_handle,
            "animated": self.animated,
            "type": self.edge_type,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Edge:
        """Create edge from dictionary (React Flow format)."""
        edge_data = data.get("data", {})

        # Handle condition
        condition_str = edge_data.get("condition", "always")
        try:
            condition = EdgeCondition(condition_str)
        except ValueError:
            condition = EdgeCondition.ALWAYS

        return cls(
            id=UUID(data["id"]) if isinstance(data.get("id"), str) else data.get("id", uuid4()),
            source_node_id=UUID(data["source"]) if isinstance(data.get("source"), str) else data["source"],
            target_node_id=UUID(data["target"]) if isinstance(data.get("target"), str) else data["target"],
            label=data.get("label"),
            condition=condition,
            condition_expression=edge_data.get("conditionExpression") or edge_data.get("condition_expression"),
            source_handle=data.get("sourceHandle") or data.get("source_handle"),
            target_handle=data.get("targetHandle") or data.get("target_handle"),
            animated=data.get("animated", False),
            edge_type=data.get("type", "default"),
        )


def compute_peers(
    node_id: UUID,
    edges: list[Edge],
    node_map: dict[UUID, str],
) -> list[str]:
    """
    Compute the peers list for a node based on outgoing edges.

    Args:
        node_id: The node to compute peers for.
        edges: All edges in the flow.
        node_map: Mapping of node IDs to node names.

    Returns:
        List of peer names (target nodes this node can send to).
    """
    peers: list[str] = []

    for edge in edges:
        if edge.source_node_id == node_id:
            target_name = node_map.get(edge.target_node_id)
            if target_name and target_name not in peers:
                peers.append(target_name)

    return peers


def compute_incoming(
    node_id: UUID,
    edges: list[Edge],
    node_map: dict[UUID, str],
) -> list[str]:
    """
    Compute the list of nodes that can send to this node.

    Args:
        node_id: The target node.
        edges: All edges in the flow.
        node_map: Mapping of node IDs to node names.

    Returns:
        List of source node names.
    """
    incoming: list[str] = []

    for edge in edges:
        if edge.target_node_id == node_id:
            source_name = node_map.get(edge.source_node_id)
            if source_name and source_name not in incoming:
                incoming.append(source_name)

    return incoming


def find_entry_nodes(
    nodes: list[UUID],
    edges: list[Edge],
) -> list[UUID]:
    """
    Find nodes with no incoming edges (entry points).

    These are typically where external triggers connect.
    """
    nodes_with_incoming = {edge.target_node_id for edge in edges}
    return [node_id for node_id in nodes if node_id not in nodes_with_incoming]


def find_exit_nodes(
    nodes: list[UUID],
    edges: list[Edge],
) -> list[UUID]:
    """
    Find nodes with no outgoing edges (exit points).

    These are typically terminal handlers or response nodes.
    """
    nodes_with_outgoing = {edge.source_node_id for edge in edges}
    return [node_id for node_id in nodes if node_id not in nodes_with_outgoing]


def detect_cycles(
    nodes: list[UUID],
    edges: list[Edge],
) -> list[list[UUID]]:
    """
    Detect cycles in the flow graph.

    Returns a list of cycles (each cycle is a list of node IDs).
    Cycles are allowed (agents can self-loop) but should be flagged for review.
    """
    cycles: list[list[UUID]] = []

    # Build adjacency list
    adj: dict[UUID, list[UUID]] = {node_id: [] for node_id in nodes}
    for edge in edges:
        if edge.source_node_id in adj:
            adj[edge.source_node_id].append(edge.target_node_id)

    # DFS for cycle detection
    visited: set[UUID] = set()
    rec_stack: set[UUID] = set()
    path: list[UUID] = []

    def dfs(node: UUID) -> None:
        visited.add(node)
        rec_stack.add(node)
        path.append(node)

        for neighbor in adj.get(node, []):
            if neighbor not in visited:
                dfs(neighbor)
            elif neighbor in rec_stack:
                # Found a cycle
                cycle_start = path.index(neighbor)
                cycles.append(path[cycle_start:] + [neighbor])

        path.pop()
        rec_stack.remove(node)

    for node_id in nodes:
        if node_id not in visited:
            dfs(node_id)

    return cycles
