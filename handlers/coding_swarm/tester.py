"""
tester.py — LLM agent that writes pytest test cases for WASM tools.

Receives ``test-request`` messages containing a WIT interface and source
code, calls the LLM to produce test cases, and returns a ``test-result``.
A ``status`` of ``"error"`` signals test failure (triggers coordinator retry).
"""

from __future__ import annotations

import logging
from typing import Optional

from xml_pipeline.message_bus.message_state import HandlerMetadata, HandlerResponse

from handlers.coding_swarm.payloads import SwarmMessage

logger = logging.getLogger(__name__)


async def handle_tester(
    payload: SwarmMessage,
    metadata: HandlerMetadata,
) -> Optional[HandlerResponse]:
    """Generate pytest tests for the tool's source code."""
    if payload.role != "test-request":
        return HandlerResponse.respond(
            payload=SwarmMessage(
                role="test-result",
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
            agent_name=metadata.own_name or "tester",
            thread_id=metadata.thread_id,
            user_message=(
                f"Write pytest test cases for the WASM tool "
                f"'{payload.tool_name}'.\n\n"
                f"{payload.content}\n\n"
                "Respond with ONLY the pytest test code, no explanation."
            ),
            temperature=0.2,
        )

        return HandlerResponse.respond(
            payload=SwarmMessage(
                role="test-result",
                tool_name=payload.tool_name,
                content=llm_response,
                status="success",
                error="",
                iteration=payload.iteration,
                phase=payload.phase,
            ),
        )
    except Exception as exc:
        logger.exception("Tester agent error for tool '%s'", payload.tool_name)
        return HandlerResponse.respond(
            payload=SwarmMessage(
                role="test-result",
                tool_name=payload.tool_name,
                content="",
                status="error",
                error="Agent error (details logged)",
                iteration=payload.iteration,
                phase=payload.phase,
            ),
        )
