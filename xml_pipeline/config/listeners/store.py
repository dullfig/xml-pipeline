"""
Listener configuration storage.

Each listener can have its own YAML config file in ~/.xml-pipeline/listeners/
containing listener-specific settings (handler, peers, prompt, etc.)

The main organism.yaml defines which listeners to load and can reference
these individual files or inline the config.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional, Any

import yaml


CONFIG_DIR = Path.home() / ".xml-pipeline"
LISTENERS_DIR = CONFIG_DIR / "listeners"


@dataclass
class ListenerConfigData:
    """
    Configuration for an individual listener.

    Stored in ~/.xml-pipeline/listeners/{name}.yaml
    """

    name: str

    # Description (required for tool prompt generation)
    description: str = ""

    # Type flags
    agent: bool = False
    tool: bool = False
    gateway: bool = False

    # Handler configuration
    handler: Optional[str] = None
    payload_class: Optional[str] = None

    # Agent configuration
    prompt: Optional[str] = None
    model: Optional[str] = None

    # Routing
    peers: list[str] = field(default_factory=list)

    # Tool permissions (for agents)
    allowed_tools: list[str] = field(default_factory=list)
    blocked_tools: list[str] = field(default_factory=list)

    # Custom metadata
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict for YAML serialization."""
        d = asdict(self)
        # Remove None values and empty lists/dicts for cleaner YAML
        result = {}
        for key, value in d.items():
            if value is None:
                continue
            if isinstance(value, list) and not value:
                continue
            if isinstance(value, dict) and not value:
                continue
            result[key] = value
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ListenerConfigData":
        """Create from dict (loaded from YAML)."""
        return cls(
            name=data.get("name", ""),
            description=data.get("description", ""),
            agent=data.get("agent", False),
            tool=data.get("tool", False),
            gateway=data.get("gateway", False),
            handler=data.get("handler"),
            payload_class=data.get("payload_class"),
            prompt=data.get("prompt"),
            model=data.get("model"),
            peers=data.get("peers", []),
            allowed_tools=data.get("allowed_tools", []),
            blocked_tools=data.get("blocked_tools", []),
            metadata=data.get("metadata", {}),
        )

    def to_yaml(self) -> str:
        """Serialize to YAML string."""
        return yaml.dump(
            self.to_dict(),
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
        )

    @classmethod
    def from_yaml(cls, yaml_str: str) -> "ListenerConfigData":
        """Parse from YAML string."""
        data = yaml.safe_load(yaml_str) or {}
        return cls.from_dict(data)


class ListenerConfigStore:
    """
    Manages listener configuration files.

    Usage:
        store = ListenerConfigStore()

        # Load or create config
        config = store.get("greeter")

        # Modify and save
        config.prompt = "You are a friendly greeter."
        store.save(config)

        # Get raw YAML for editing
        yaml_content = store.load_yaml("greeter")

        # Save edited YAML
        store.save_yaml("greeter", yaml_content)
    """

    def __init__(self, listeners_dir: Path = LISTENERS_DIR):
        self.listeners_dir = listeners_dir
        self._ensure_dir()

    def _ensure_dir(self) -> None:
        """Create listeners directory if needed."""
        self.listeners_dir.mkdir(parents=True, exist_ok=True)

    def path_for(self, name: str) -> Path:
        """Get path to listener's config file."""
        return self.listeners_dir / f"{name}.yaml"

    def exists(self, name: str) -> bool:
        """Check if listener config exists."""
        return self.path_for(name).exists()

    def get(self, name: str) -> ListenerConfigData:
        """
        Load listener config, creating default if not exists.
        """
        path = self.path_for(name)

        if path.exists():
            with open(path) as f:
                data = yaml.safe_load(f) or {}
            # Ensure name is set
            data["name"] = name
            return ListenerConfigData.from_dict(data)

        # Return default config (not saved yet)
        return ListenerConfigData(name=name)

    def save(self, config: ListenerConfigData) -> Path:
        """
        Save listener config to file.

        Returns path to saved file.
        """
        path = self.path_for(config.name)

        with open(path, "w") as f:
            yaml.dump(
                config.to_dict(),
                f,
                default_flow_style=False,
                sort_keys=False,
                allow_unicode=True,
            )

        return path

    def save_yaml(self, name: str, yaml_content: str) -> Path:
        """
        Save raw YAML content for a listener.

        Used when saving from editor.
        """
        path = self.path_for(name)

        # Validate YAML before saving
        yaml.safe_load(yaml_content)  # Raises on invalid YAML

        with open(path, "w") as f:
            f.write(yaml_content)

        return path

    def load_yaml(self, name: str) -> str:
        """
        Load raw YAML content for editing.

        Returns default template if file doesn't exist.
        """
        path = self.path_for(name)

        if path.exists():
            with open(path) as f:
                return f.read()

        # Return default template
        return self._default_template(name)

    def _default_template(self, name: str) -> str:
        """Generate default YAML template for new listener."""
        return f"""# yaml-language-server: $schema=~/.xml-pipeline/schemas/listener.schema.json
# Listener configuration for: {name}

name: {name}
description: "Description of what this listener does"

# Listener type (set one to true)
agent: false      # LLM-powered agent
tool: false       # Simple tool/function
gateway: false    # Federation gateway

# Handler configuration
handler: "handlers.{name}.handle_{name}"
payload_class: "handlers.{name}.{name.title()}Payload"

# Agent configuration (only if agent: true)
# prompt: |
#   You are an AI assistant.
#
#   Respond helpfully and concisely.
# model: default

# Routing - which listeners this can send to
peers: []

# Tool permissions (for agents)
# allowed_tools: []
# blocked_tools: []

# Custom metadata (available to handler)
# metadata: {{}}
"""

    def list_listeners(self) -> list[str]:
        """List all configured listeners."""
        return [p.stem for p in self.listeners_dir.glob("*.yaml")]

    def delete(self, name: str) -> bool:
        """Delete listener config file."""
        path = self.path_for(name)
        if path.exists():
            path.unlink()
            return True
        return False


# Global instance
_store: Optional[ListenerConfigStore] = None


def get_listener_config_store() -> ListenerConfigStore:
    """Get the global listener config store."""
    global _store
    if _store is None:
        _store = ListenerConfigStore()
    return _store
