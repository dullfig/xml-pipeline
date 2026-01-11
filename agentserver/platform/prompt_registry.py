"""
prompt_registry.py — Immutable prompt storage for agents.

The PromptRegistry is the trusted store for agent system prompts.
Prompts are loaded at startup and cannot be modified at runtime.

Design principles:
- Immutable: prompts set at startup, never modified
- Invisible: agents cannot see their own prompts
- Auditable: prompts hashed for tracking
- Per-agent: each agent has one system prompt

Usage:
    registry = get_prompt_registry()

    # At startup (from config loader)
    registry.register("greeter", "You are a friendly greeter...")

    # At LLM call assembly (platform only)
    prompt = registry.get("greeter")
    messages = [{"role": "system", "content": prompt.system_prompt}, ...]
"""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Optional


@dataclass(frozen=True)
class AgentPrompt:
    """
    Immutable prompt for an agent.

    frozen=True ensures prompts cannot be modified after creation.
    """
    agent_name: str
    system_prompt: str
    prompt_hash: str              # SHA256 of system_prompt
    peer_schemas: str = ""        # Generated from peer XSDs
    created_at: str = ""          # ISO timestamp

    @classmethod
    def create(
        cls,
        agent_name: str,
        system_prompt: str,
        peer_schemas: str = "",
    ) -> AgentPrompt:
        """Create a new AgentPrompt with computed hash."""
        prompt_hash = hashlib.sha256(system_prompt.encode()).hexdigest()[:16]
        created_at = datetime.now(timezone.utc).isoformat()

        return cls(
            agent_name=agent_name,
            system_prompt=system_prompt,
            prompt_hash=prompt_hash,
            peer_schemas=peer_schemas,
            created_at=created_at,
        )

    @property
    def full_prompt(self) -> str:
        """Combined system prompt + peer schemas."""
        if self.peer_schemas:
            return f"{self.system_prompt}\n\n{self.peer_schemas}"
        return self.system_prompt


class PromptRegistry:
    """
    Immutable registry of agent prompts.

    Thread-safe. Prompts can only be registered once per agent.
    """

    def __init__(self):
        self._prompts: Dict[str, AgentPrompt] = {}
        self._lock = threading.Lock()
        self._frozen = False

    def register(
        self,
        agent_name: str,
        system_prompt: str,
        peer_schemas: str = "",
    ) -> AgentPrompt:
        """
        Register a prompt for an agent.

        Can only be called during startup (before freeze).
        Raises RuntimeError if called after freeze or if agent already registered.
        """
        with self._lock:
            if self._frozen:
                raise RuntimeError(
                    f"Cannot register prompt for '{agent_name}': registry is frozen"
                )

            if agent_name in self._prompts:
                raise RuntimeError(
                    f"Cannot register prompt for '{agent_name}': already registered"
                )

            prompt = AgentPrompt.create(
                agent_name=agent_name,
                system_prompt=system_prompt,
                peer_schemas=peer_schemas,
            )

            self._prompts[agent_name] = prompt
            return prompt

    def freeze(self) -> None:
        """
        Freeze the registry. No more registrations allowed.

        Call this after all prompts are loaded from config.
        """
        with self._lock:
            self._frozen = True

    def get(self, agent_name: str) -> Optional[AgentPrompt]:
        """Get prompt for an agent (None if not registered)."""
        with self._lock:
            return self._prompts.get(agent_name)

    def get_required(self, agent_name: str) -> AgentPrompt:
        """Get prompt for an agent (raises if not found)."""
        prompt = self.get(agent_name)
        if prompt is None:
            raise KeyError(f"No prompt registered for agent: {agent_name}")
        return prompt

    def has(self, agent_name: str) -> bool:
        """Check if an agent has a registered prompt."""
        with self._lock:
            return agent_name in self._prompts

    def list_agents(self) -> list[str]:
        """List all agents with registered prompts."""
        with self._lock:
            return list(self._prompts.keys())

    def get_stats(self) -> dict:
        """Get registry statistics."""
        with self._lock:
            return {
                "agent_count": len(self._prompts),
                "frozen": self._frozen,
                "agents": list(self._prompts.keys()),
            }

    def clear(self) -> None:
        """Clear all prompts (for testing only)."""
        with self._lock:
            self._prompts.clear()
            self._frozen = False


# ============================================================================
# Singleton
# ============================================================================

_registry: Optional[PromptRegistry] = None
_registry_lock = threading.Lock()


def get_prompt_registry() -> PromptRegistry:
    """Get the global PromptRegistry singleton."""
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                _registry = PromptRegistry()
    return _registry
