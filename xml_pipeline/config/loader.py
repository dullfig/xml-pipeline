"""
Configuration loader for xml-pipeline.

Loads and validates organism.yaml configuration files.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class ConfigError(Exception):
    """Configuration validation error."""

    pass


@dataclass
class OrganismMeta:
    """Organism metadata."""

    name: str
    version: str = "0.1.0"
    description: str = ""


@dataclass
class LLMBackendConfig:
    """LLM backend configuration."""

    name: str
    provider: str  # xai, anthropic, openai, ollama
    model: str
    api_key_env: str | None = None
    base_url: str | None = None
    priority: int = 0


@dataclass
class ListenerConfig:
    """Listener configuration."""

    name: str
    description: str = ""

    # Type flags
    agent: bool = False
    tool: bool = False
    gateway: bool = False

    # Handler (for tools)
    handler: str | None = None
    payload_class: str | None = None

    # Agent config
    prompt: str | None = None
    model: str | None = None

    # Routing
    peers: list[str] = field(default_factory=list)

    # Tool permissions (for agents)
    allowed_tools: list[str] = field(default_factory=list)
    blocked_tools: list[str] = field(default_factory=list)


@dataclass
class ServerConfig:
    """WebSocket server configuration (optional)."""

    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 8765


@dataclass
class AuthConfig:
    """Authentication configuration (optional)."""

    enabled: bool = False
    totp_secret_env: str = "ORGANISM_TOTP_SECRET"


@dataclass
class OrganismConfig:
    """Complete organism configuration."""

    organism: OrganismMeta
    listeners: list[ListenerConfig] = field(default_factory=list)
    llm_backends: list[LLMBackendConfig] = field(default_factory=list)
    server: ServerConfig | None = None
    auth: AuthConfig | None = None


def load_config(path: Path) -> OrganismConfig:
    """Load and validate organism configuration from YAML file."""
    with open(path) as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict):
        raise ConfigError(f"Config must be a YAML mapping, got {type(raw)}")

    # Parse organism metadata
    org_raw = raw.get("organism", {})
    if not org_raw.get("name"):
        raise ConfigError("organism.name is required")

    organism = OrganismMeta(
        name=org_raw["name"],
        version=org_raw.get("version", "0.1.0"),
        description=org_raw.get("description", ""),
    )

    # Parse LLM backends
    llm_backends = []
    for backend_raw in raw.get("llm_backends", []):
        if not backend_raw.get("name"):
            raise ConfigError("llm_backends[].name is required")
        if not backend_raw.get("provider"):
            raise ConfigError(f"llm_backends[{backend_raw['name']}].provider is required")

        llm_backends.append(
            LLMBackendConfig(
                name=backend_raw["name"],
                provider=backend_raw["provider"],
                model=backend_raw.get("model", ""),
                api_key_env=backend_raw.get("api_key_env"),
                base_url=backend_raw.get("base_url"),
                priority=backend_raw.get("priority", 0),
            )
        )

    # Parse listeners
    listeners = []
    for listener_raw in raw.get("listeners", []):
        if not listener_raw.get("name"):
            raise ConfigError("listeners[].name is required")

        listeners.append(
            ListenerConfig(
                name=listener_raw["name"],
                description=listener_raw.get("description", ""),
                agent=listener_raw.get("agent", False),
                tool=listener_raw.get("tool", False),
                gateway=listener_raw.get("gateway", False),
                handler=listener_raw.get("handler"),
                payload_class=listener_raw.get("payload_class"),
                prompt=listener_raw.get("prompt"),
                model=listener_raw.get("model"),
                peers=listener_raw.get("peers", []),
                allowed_tools=listener_raw.get("allowed_tools", []),
                blocked_tools=listener_raw.get("blocked_tools", []),
            )
        )

    # Parse optional server config
    server = None
    if "server" in raw:
        server_raw = raw["server"]
        server = ServerConfig(
            enabled=server_raw.get("enabled", True),
            host=server_raw.get("host", "127.0.0.1"),
            port=server_raw.get("port", 8765),
        )

    # Parse optional auth config
    auth = None
    if "auth" in raw:
        auth_raw = raw["auth"]
        auth = AuthConfig(
            enabled=auth_raw.get("enabled", True),
            totp_secret_env=auth_raw.get("totp_secret_env", "ORGANISM_TOTP_SECRET"),
        )

    return OrganismConfig(
        organism=organism,
        listeners=listeners,
        llm_backends=llm_backends,
        server=server,
        auth=auth,
    )


def validate_config(config: OrganismConfig) -> list[str]:
    """
    Validate config for common issues.

    Returns list of warning messages (empty if valid).
    """
    warnings = []

    # Check for at least one listener
    if not config.listeners:
        warnings.append("No listeners defined")

    # Check for LLM backend if agents exist
    agents = [l for l in config.listeners if l.agent]
    if agents and not config.llm_backends:
        warnings.append(
            f"Config has {len(agents)} agent(s) but no llm_backends defined"
        )

    # Check peer references
    listener_names = {l.name for l in config.listeners}
    for listener in config.listeners:
        for peer in listener.peers:
            # Peer can be "listener_name" or "listener_name.payload_type"
            peer_name = peer.split(".")[0]
            if peer_name not in listener_names:
                warnings.append(
                    f"Listener '{listener.name}' references unknown peer '{peer_name}'"
                )

    return warnings
