"""
llm_api.py — Platform-controlled LLM interface.

The platform controls all LLM calls. Agents request completions via this API.
The platform assembles the full prompt (system + history + user message)
and enforces rate limits, caching, and cost controls.

Design principles:
- Agent-invisible prompts: agents never see their system prompt
- Thread-scoped history: only messages from the current thread
- Auditable: all calls can be logged/traced
- Rate-limited: platform controls costs

Usage (from handler):
    from agentserver.platform import complete

    async def handle_greeting(payload, metadata):
        response = await complete(
            agent_name=metadata.own_name,
            thread_id=metadata.thread_id,
            user_message=f"Greet {payload.name}",
            temperature=0.9,
        )
        return HandlerResponse(
            payload=GreetingResponse(message=response),
            to="shouter",
        )
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from agentserver.platform.prompt_registry import get_prompt_registry
from agentserver.memory import get_context_buffer

logger = logging.getLogger(__name__)


async def complete(
    agent_name: str,
    thread_id: str,
    user_message: str,
    *,
    temperature: float = 0.7,
    max_tokens: int = 1024,
    include_history: bool = True,
    **kwargs: Any,
) -> str:
    """
    Request an LLM completion for an agent.

    The platform assembles the full prompt:
    1. System prompt from PromptRegistry (invisible to agent)
    2. Peer schemas (what messages agent can send)
    3. Thread history from ContextBuffer
    4. User's message

    Args:
        agent_name: The calling agent's name (for prompt lookup)
        thread_id: Current thread UUID (for history lookup)
        user_message: The user/task message to complete
        temperature: LLM temperature (0.0-1.0)
        max_tokens: Maximum tokens in response
        include_history: Whether to include thread history
        **kwargs: Additional LLM parameters

    Returns:
        The LLM's text response

    Raises:
        KeyError: If agent has no registered prompt
        RuntimeError: If LLM call fails
    """
    # Get agent's prompt (agent cannot see this)
    prompt_registry = get_prompt_registry()
    prompt = prompt_registry.get_required(agent_name)

    # Build messages array
    messages: List[Dict[str, str]] = []

    # System prompt (from registry)
    if prompt.system_prompt:
        messages.append({
            "role": "system",
            "content": prompt.system_prompt,
        })

    # Peer schemas (what messages agent can send)
    if prompt.peer_schemas:
        messages.append({
            "role": "system",
            "content": prompt.peer_schemas,
        })

    # Thread history (agent can read, not modify)
    if include_history and thread_id:
        context_buffer = get_context_buffer()
        history = context_buffer.get_thread(thread_id)

        for slot in history:
            # Determine role: assistant if from this agent, user otherwise
            role = "assistant" if slot.from_id == agent_name else "user"

            # Serialize payload for LLM context
            content = _serialize_for_llm(slot.payload, slot.from_id)
            messages.append({
                "role": role,
                "content": content,
            })

    # Current user message
    messages.append({
        "role": "user",
        "content": user_message,
    })

    # Make LLM call via router
    try:
        from agentserver.llm import generate

        response = await generate(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )

        logger.debug(
            f"platform.complete: agent={agent_name} thread={thread_id[:8]}... "
            f"messages={len(messages)} response_len={len(response)}"
        )

        return response

    except Exception as e:
        logger.error(f"LLM call failed for {agent_name}: {e}")
        raise RuntimeError(f"LLM completion failed: {e}") from e


def _serialize_for_llm(payload: Any, from_id: str) -> str:
    """
    Serialize a payload for LLM context.

    Converts structured payloads to a readable format for the LLM.
    """
    # Try XML serialization first (for xmlify classes)
    if hasattr(payload, 'xml_value'):
        from lxml import etree
        try:
            class_name = type(payload).__name__
            tree = payload.xml_value(class_name)
            xml_str = etree.tostring(tree, encoding='unicode', pretty_print=True)
            return f"[From {from_id}]\n{xml_str}"
        except Exception:
            pass

    # Try to_xml for custom classes
    if hasattr(payload, 'to_xml'):
        try:
            return f"[From {from_id}]\n{payload.to_xml()}"
        except Exception:
            pass

    # Fallback to repr
    return f"[From {from_id}] {repr(payload)}"


# Alias for cleaner imports
platform_complete = complete
