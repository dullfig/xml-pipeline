"""
Configuration management for xml-pipeline.

Handles:
- Agent configs (~/.xml-pipeline/agents/*.yaml)
- Listener configs (~/.xml-pipeline/listeners/*.yaml)
- Organism config (organism.yaml)
"""

from .agents import (
    AgentConfig,
    AgentConfigStore,
    get_agent_config_store,
    CONFIG_DIR,
    AGENTS_DIR,
)
from .listeners import (
    ListenerConfigStore,
    get_listener_config_store,
    LISTENERS_DIR,
)

__all__ = [
    # Agent config
    "AgentConfig",
    "AgentConfigStore",
    "get_agent_config_store",
    "CONFIG_DIR",
    "AGENTS_DIR",
    # Listener config
    "ListenerConfigStore",
    "get_listener_config_store",
    "LISTENERS_DIR",
]
