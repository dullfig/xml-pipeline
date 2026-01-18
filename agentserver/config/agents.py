"""
Agent configuration management.

Each agent has its own YAML config file in ~/.xml-pipeline/agents/
containing behavior settings (prompt, model, temperature, etc.)

The main organism.yaml defines wiring (listeners, peers, routing).
Agent YAML files define behavior.
"""

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional, Dict, Any, List
import yaml


CONFIG_DIR = Path.home() / ".xml-pipeline"
AGENTS_DIR = CONFIG_DIR / "agents"


@dataclass
class AgentConfig:
    """
    Configuration for an individual agent.

    Stored in ~/.xml-pipeline/agents/{name}.yaml
    """
    name: str

    # System prompt for the LLM
    prompt: str = ""

    # Model selection
    model: str = "default"  # "default", "claude-sonnet", "claude-opus", "gpt-4", etc.

    # Generation parameters
    temperature: float = 0.7
    max_tokens: int = 4096

    # Behavior flags
    verbose: bool = False          # Log detailed reasoning
    confirm_actions: bool = False  # Ask before tool calls

    # Tool permissions (if empty, uses defaults from wiring)
    allowed_tools: List[str] = field(default_factory=list)
    blocked_tools: List[str] = field(default_factory=list)

    # Custom metadata
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict for YAML serialization."""
        d = asdict(self)
        # Remove name from dict (it's the filename)
        del d["name"]
        return d

    @classmethod
    def from_dict(cls, name: str, data: Dict[str, Any]) -> "AgentConfig":
        """Create from dict (loaded from YAML)."""
        return cls(
            name=name,
            prompt=data.get("prompt", ""),
            model=data.get("model", "default"),
            temperature=data.get("temperature", 0.7),
            max_tokens=data.get("max_tokens", 4096),
            verbose=data.get("verbose", False),
            confirm_actions=data.get("confirm_actions", False),
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
    def from_yaml(cls, name: str, yaml_str: str) -> "AgentConfig":
        """Parse from YAML string."""
        data = yaml.safe_load(yaml_str) or {}
        return cls.from_dict(name, data)


class AgentConfigStore:
    """
    Manages agent configuration files.

    Usage:
        store = AgentConfigStore()

        # Load or create config
        config = store.get("greeter")

        # Modify and save
        config.prompt = "You are a friendly greeter."
        store.save(config)

        # Get path for editing
        path = store.path_for("greeter")
    """

    def __init__(self, agents_dir: Path = AGENTS_DIR):
        self.agents_dir = agents_dir
        self._ensure_dir()

    def _ensure_dir(self) -> None:
        """Create agents directory if needed."""
        self.agents_dir.mkdir(parents=True, exist_ok=True)

    def path_for(self, name: str) -> Path:
        """Get path to agent's config file."""
        return self.agents_dir / f"{name}.yaml"

    def exists(self, name: str) -> bool:
        """Check if agent config exists."""
        return self.path_for(name).exists()

    def get(self, name: str) -> AgentConfig:
        """
        Load agent config, creating default if not exists.
        """
        path = self.path_for(name)

        if path.exists():
            with open(path) as f:
                data = yaml.safe_load(f) or {}
            return AgentConfig.from_dict(name, data)

        # Return default config (not saved yet)
        return AgentConfig(name=name)

    def save(self, config: AgentConfig) -> Path:
        """
        Save agent config to file.

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
        Save raw YAML content for an agent.

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
        """Generate default YAML template for new agent."""
        return f"""# Agent configuration for: {name}
# This file defines behavior settings for the agent.
# The wiring (peers, routing) is in organism.yaml.

# System prompt - instructions for the LLM
prompt: |
  You are {name}, an AI assistant.

  Respond helpfully and concisely.

# Model selection: "default", "claude-sonnet", "claude-opus", "gpt-4", etc.
model: default

# Generation parameters
temperature: 0.7
max_tokens: 4096

# Behavior flags
verbose: false          # Log detailed reasoning
confirm_actions: false  # Ask before tool calls

# Tool permissions (empty = use defaults from wiring)
allowed_tools: []
blocked_tools: []

# Custom metadata (available to handler)
metadata: {{}}
"""

    def list_agents(self) -> List[str]:
        """List all configured agents."""
        return [
            p.stem for p in self.agents_dir.glob("*.yaml")
        ]

    def delete(self, name: str) -> bool:
        """Delete agent config file."""
        path = self.path_for(name)
        if path.exists():
            path.unlink()
            return True
        return False


# Global instance
_store: Optional[AgentConfigStore] = None


def get_agent_config_store() -> AgentConfigStore:
    """Get the global agent config store."""
    global _store
    if _store is None:
        _store = AgentConfigStore()
    return _store
