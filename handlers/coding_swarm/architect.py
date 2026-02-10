"""
architect.py — LLM agent that designs WIT interfaces for WASM tools.

Receives ``design-request`` messages, calls the LLM to produce a WIT
interface definition, and returns a ``design-result``.
"""

from __future__ import annotations

import logging
from typing import Optional

from xml_pipeline.message_bus.message_state import HandlerMetadata, HandlerResponse

from handlers.coding_swarm.payloads import SwarmMessage

logger = logging.getLogger(__name__)


async def handle_architect(
    payload: SwarmMessage,
    metadata: HandlerMetadata,
) -> Optional[HandlerResponse]:
    """Design a WIT interface based on requirements."""
    if payload.role != "design-request":
        return HandlerResponse.respond(
            payload=SwarmMessage(
                role="design-result",
                tool_name=payload.tool_name,
                content="",
                status="error",
                error=f"Unexpected role: {payload.role}",
                iteration=payload.iteration,
                phase=payload.phase,
            ),
        )

    try:
        from xml_pipeline.platform import complete

        llm_response = await complete(
            agent_name=metadata.own_name or "architect",
            thread_id=metadata.thread_id,
            user_message=(
                f"Design a WIT interface for a WASM tool called '{payload.tool_name}'.\n\n"
                f"Requirements:\n{payload.content}\n\n"
                "Respond with ONLY the WIT interface definition, no explanation."
            ),
            temperature=0.3,
        )

        return HandlerResponse.respond(
            payload=SwarmMessage(
                role="design-result",
                tool_name=payload.tool_name,
                content=llm_response,
                status="success",
                error="",
                iteration=payload.iteration,
                phase=payload.phase,
            ),
        )
    except Exception as exc:
        logger.exception("Architect agent error for tool '%s'", payload.tool_name)
        return HandlerResponse.respond(
            payload=SwarmMessage(
                role="design-result",
                tool_name=payload.tool_name,
                content="",
                status="error",
                error="Agent error (details logged)",
                iteration=payload.iteration,
                phase=payload.phase,
            ),
        )
