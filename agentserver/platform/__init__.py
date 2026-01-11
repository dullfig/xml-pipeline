"""
platform — Trusted orchestration layer for the agent swarm.

The platform manages:
- Prompt registry: immutable system prompts per agent
- LLM call assembly: platform controls what goes to the LLM
- Context buffer access: controlled by platform

Agents are sandboxed. They receive messages and return responses.
They cannot see or modify prompts, and cannot directly access the LLM.
"""

from agentserver.platform.prompt_registry import (
    PromptRegistry,
    AgentPrompt,
    get_prompt_registry,
)

from agentserver.platform.llm_api import (
    complete,
    platform_complete,
)

__all__ = [
    "PromptRegistry",
    "AgentPrompt",
    "get_prompt_registry",
    "complete",
    "platform_complete",
]
