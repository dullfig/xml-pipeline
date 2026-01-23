"""
Node types for BloxServer flows.

Nodes are the building blocks of flows:
- AgentNode: LLM-powered agents with prompts and reasoning
- ToolNode: Built-in tools (calculate, fetch, shell, etc.)
- GatewayNode: External APIs or federated organisms
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import UUID, uuid4


class NodeType(str, Enum):
    """Types of nodes in a flow."""

    AGENT = "agent"
    TOOL = "tool"
    GATEWAY = "gateway"


# =============================================================================
# Built-in Tool Types
# =============================================================================


class ToolType(str, Enum):
    """Built-in tool types available in BloxServer."""

    CALCULATE = "calculate"
    FETCH = "fetch"
    READ_FILE = "read_file"
    WRITE_FILE = "write_file"
    LIST_DIR = "list_dir"
    SHELL = "shell"
    WEB_SEARCH = "web_search"
    KEY_VALUE = "key_value"
    SEND_EMAIL = "send_email"
    WEBHOOK = "webhook"
    LIBRARIAN = "librarian"
    CUSTOM = "custom"


# =============================================================================
# Base Node
# =============================================================================


@dataclass
class Node:
    """Base class for all node types."""

    id: UUID
    name: str
    description: str
    node_type: NodeType

    # Canvas position (for frontend rendering)
    position_x: float = 0.0
    position_y: float = 0.0

    # Metadata for UI
    color: str | None = None
    icon: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": str(self.id),
            "name": self.name,
            "description": self.description,
            "nodeType": self.node_type.value,
            "position": {"x": self.position_x, "y": self.position_y},
            "color": self.color,
            "icon": self.icon,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Node:
        """Create node from dictionary. Dispatches to appropriate subclass."""
        node_type = NodeType(data.get("nodeType", data.get("node_type")))

        if node_type == NodeType.AGENT:
            return AgentNode.from_dict(data)
        elif node_type == NodeType.TOOL:
            return ToolNode.from_dict(data)
        elif node_type == NodeType.GATEWAY:
            return GatewayNode.from_dict(data)
        else:
            raise ValueError(f"Unknown node type: {node_type}")


# =============================================================================
# Agent Node
# =============================================================================


@dataclass
class AgentNode(Node):
    """
    LLM-powered agent node.

    Agents have prompts, can reason, and communicate with other nodes.
    They are the "brains" of a flow.
    """

    node_type: NodeType = field(default=NodeType.AGENT, init=False)

    # Agent configuration
    prompt: str = ""
    model: str | None = None  # None = use default from org settings

    # Advanced settings
    temperature: float = 0.7
    max_tokens: int | None = None
    system_prompt_append: str | None = None  # Extra instructions

    # Handler paths (auto-generated if not specified)
    handler_path: str | None = None
    payload_class_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        base = super().to_dict()
        base.update({
            "prompt": self.prompt,
            "model": self.model,
            "temperature": self.temperature,
            "maxTokens": self.max_tokens,
            "systemPromptAppend": self.system_prompt_append,
            "handlerPath": self.handler_path,
            "payloadClassPath": self.payload_class_path,
        })
        return base

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentNode:
        position = data.get("position", {})
        return cls(
            id=UUID(data["id"]) if isinstance(data.get("id"), str) else data.get("id", uuid4()),
            name=data["name"],
            description=data.get("description", ""),
            position_x=position.get("x", 0.0),
            position_y=position.get("y", 0.0),
            color=data.get("color"),
            icon=data.get("icon"),
            prompt=data.get("prompt", ""),
            model=data.get("model"),
            temperature=data.get("temperature", 0.7),
            max_tokens=data.get("maxTokens") or data.get("max_tokens"),
            system_prompt_append=data.get("systemPromptAppend") or data.get("system_prompt_append"),
            handler_path=data.get("handlerPath") or data.get("handler_path"),
            payload_class_path=data.get("payloadClassPath") or data.get("payload_class_path"),
        )

    def to_listener_config(self, peers: list[str]) -> dict[str, Any]:
        """
        Convert to organism.yaml listener configuration.

        Args:
            peers: List of peer names derived from outgoing edges.
        """
        config: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "agent": True,
            "prompt": self.prompt,
        }

        if peers:
            config["peers"] = peers

        if self.handler_path:
            config["handler"] = self.handler_path
        if self.payload_class_path:
            config["payload_class"] = self.payload_class_path

        # LLM settings as metadata (for future use)
        if self.model:
            config["model"] = self.model
        if self.temperature != 0.7:
            config["temperature"] = self.temperature
        if self.max_tokens:
            config["max_tokens"] = self.max_tokens

        return config


# =============================================================================
# Tool Node
# =============================================================================


# Tool handler/payload mappings for built-in tools
BUILTIN_TOOLS: dict[ToolType, dict[str, str]] = {
    ToolType.CALCULATE: {
        "handler": "xml_pipeline.tools.calculate.handle_calculate",
        "payload_class": "xml_pipeline.tools.calculate.CalculatePayload",
    },
    ToolType.FETCH: {
        "handler": "xml_pipeline.tools.fetch.handle_fetch",
        "payload_class": "xml_pipeline.tools.fetch.FetchPayload",
    },
    ToolType.READ_FILE: {
        "handler": "xml_pipeline.tools.files.handle_read",
        "payload_class": "xml_pipeline.tools.files.ReadFilePayload",
    },
    ToolType.WRITE_FILE: {
        "handler": "xml_pipeline.tools.files.handle_write",
        "payload_class": "xml_pipeline.tools.files.WriteFilePayload",
    },
    ToolType.LIST_DIR: {
        "handler": "xml_pipeline.tools.files.handle_list",
        "payload_class": "xml_pipeline.tools.files.ListDirPayload",
    },
    ToolType.SHELL: {
        "handler": "xml_pipeline.tools.shell.handle_shell",
        "payload_class": "xml_pipeline.tools.shell.ShellPayload",
    },
    ToolType.WEB_SEARCH: {
        "handler": "xml_pipeline.tools.search.handle_search",
        "payload_class": "xml_pipeline.tools.search.SearchPayload",
    },
    ToolType.KEY_VALUE: {
        "handler": "xml_pipeline.tools.keyvalue.handle_keyvalue",
        "payload_class": "xml_pipeline.tools.keyvalue.KeyValuePayload",
    },
    ToolType.LIBRARIAN: {
        "handler": "xml_pipeline.tools.librarian.handle_librarian",
        "payload_class": "xml_pipeline.tools.librarian.LibrarianPayload",
    },
}


@dataclass
class ToolNode(Node):
    """
    Built-in or custom tool node.

    Tools are stateless functions that perform specific operations
    like calculations, HTTP requests, file operations, etc.
    """

    node_type: NodeType = field(default=NodeType.TOOL, init=False)

    # Tool configuration
    tool_type: ToolType = ToolType.CUSTOM
    config: dict[str, Any] = field(default_factory=dict)

    # Custom tool paths (only for CUSTOM type)
    handler_path: str | None = None
    payload_class_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        base = super().to_dict()
        base.update({
            "toolType": self.tool_type.value,
            "config": self.config,
            "handlerPath": self.handler_path,
            "payloadClassPath": self.payload_class_path,
        })
        return base

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ToolNode:
        position = data.get("position", {})
        tool_type_str = data.get("toolType") or data.get("tool_type", "custom")
        return cls(
            id=UUID(data["id"]) if isinstance(data.get("id"), str) else data.get("id", uuid4()),
            name=data["name"],
            description=data.get("description", ""),
            position_x=position.get("x", 0.0),
            position_y=position.get("y", 0.0),
            color=data.get("color"),
            icon=data.get("icon"),
            tool_type=ToolType(tool_type_str),
            config=data.get("config", {}),
            handler_path=data.get("handlerPath") or data.get("handler_path"),
            payload_class_path=data.get("payloadClassPath") or data.get("payload_class_path"),
        )

    def to_listener_config(self) -> dict[str, Any]:
        """Convert to organism.yaml listener configuration."""
        # Get handler/payload from built-in mapping or custom paths
        if self.tool_type in BUILTIN_TOOLS:
            tool_info = BUILTIN_TOOLS[self.tool_type]
            handler = tool_info["handler"]
            payload_class = tool_info["payload_class"]
        else:
            handler = self.handler_path
            payload_class = self.payload_class_path

        if not handler or not payload_class:
            raise ValueError(
                f"Custom tool '{self.name}' requires handler_path and payload_class_path"
            )

        config: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "handler": handler,
            "payload_class": payload_class,
        }

        # Include tool-specific config if present
        if self.config:
            config["tool_config"] = self.config

        return config


# =============================================================================
# Gateway Node
# =============================================================================


@dataclass
class GatewayNode(Node):
    """
    External API or federated organism gateway.

    Gateways connect flows to:
    - External REST APIs
    - Federated xml-pipeline organisms
    - Third-party services (Slack, Discord, etc.)
    """

    node_type: NodeType = field(default=NodeType.GATEWAY, init=False)

    # Federation (other xml-pipeline organisms)
    remote_url: str | None = None
    trusted_identity: str | None = None  # Path to public key

    # REST API gateway
    api_endpoint: str | None = None
    api_method: str = "POST"
    api_headers: dict[str, str] = field(default_factory=dict)
    api_auth_type: str | None = None  # bearer, api_key, basic
    api_auth_env_var: str | None = None  # Env var containing the secret

    # Response mapping
    response_mapping: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        base = super().to_dict()
        base.update({
            "remoteUrl": self.remote_url,
            "trustedIdentity": self.trusted_identity,
            "apiEndpoint": self.api_endpoint,
            "apiMethod": self.api_method,
            "apiHeaders": self.api_headers,
            "apiAuthType": self.api_auth_type,
            "apiAuthEnvVar": self.api_auth_env_var,
            "responseMapping": self.response_mapping,
        })
        return base

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GatewayNode:
        position = data.get("position", {})
        return cls(
            id=UUID(data["id"]) if isinstance(data.get("id"), str) else data.get("id", uuid4()),
            name=data["name"],
            description=data.get("description", ""),
            position_x=position.get("x", 0.0),
            position_y=position.get("y", 0.0),
            color=data.get("color"),
            icon=data.get("icon"),
            remote_url=data.get("remoteUrl") or data.get("remote_url"),
            trusted_identity=data.get("trustedIdentity") or data.get("trusted_identity"),
            api_endpoint=data.get("apiEndpoint") or data.get("api_endpoint"),
            api_method=data.get("apiMethod") or data.get("api_method", "POST"),
            api_headers=data.get("apiHeaders") or data.get("api_headers", {}),
            api_auth_type=data.get("apiAuthType") or data.get("api_auth_type"),
            api_auth_env_var=data.get("apiAuthEnvVar") or data.get("api_auth_env_var"),
            response_mapping=data.get("responseMapping") or data.get("response_mapping", {}),
        )

    def to_listener_config(self) -> dict[str, Any]:
        """Convert to organism.yaml listener/gateway configuration."""
        if self.remote_url:
            # Federation gateway
            return {
                "name": self.name,
                "remote_url": self.remote_url,
                "trusted_identity": self.trusted_identity,
                "description": self.description,
            }
        elif self.api_endpoint:
            # REST API gateway (custom handler)
            return {
                "name": self.name,
                "description": self.description,
                "handler": "bloxserver.gateways.rest.handle_rest_gateway",
                "payload_class": "bloxserver.gateways.rest.RestGatewayPayload",
                "gateway_config": {
                    "endpoint": self.api_endpoint,
                    "method": self.api_method,
                    "headers": self.api_headers,
                    "auth_type": self.api_auth_type,
                    "auth_env_var": self.api_auth_env_var,
                    "response_mapping": self.response_mapping,
                },
            }
        else:
            raise ValueError(
                f"Gateway '{self.name}' requires either remote_url (federation) "
                "or api_endpoint (REST)"
            )
