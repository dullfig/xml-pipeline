"""
Configuration management for xml-pipeline.

Handles:
- Agent configs (~/.xml-pipeline/agents/*.yaml)
- Organism config (organism.yaml)
"""

from .agents import (
    AgentConfig,
    AgentConfigStore,
    get_agent_config_store,
    CONFIG_DIR,
    AGENTS_DIR,
)

__all__ = [
    "AgentConfig",
    "AgentConfigStore",
    "get_agent_config_store",
    "CONFIG_DIR",
    "AGENTS_DIR",
]
