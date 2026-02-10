"""
reviewer.py — LLM agent that reviews code quality.

Receives ``review-request`` messages, calls the LLM to assess code quality,
and returns a ``review-result``. A ``status`` of ``"error"`` signals rejection
(triggers coordinator retry).
"""

from __future__ import annotations

import logging
from typing import Optional

from xml_pipeline.message_bus.message_state import HandlerMetadata, HandlerResponse

from handlers.coding_swarm.payloads import SwarmMessage

logger = logging.getLogger(__name__)


async def handle_reviewer(
    payload: SwarmMessage,
    metadata: HandlerMetadata,
) -> Optional[HandlerResponse]:
    """Review code quality — approve or reject."""
    if payload.role != "review-request":
        return HandlerResponse.respond(
            payload=SwarmMessage(
                role="review-result",
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
            agent_name=metadata.own_name or "reviewer",
            thread_id=metadata.thread_id,
            user_message=(
                f"Review the following WASM tool '{payload.tool_name}' "
                f"for code quality.\n\n"
                f"{payload.content}\n\n"
                "Respond with APPROVED if the code is acceptable, or "
                "REJECTED followed by specific feedback."
            ),
            temperature=0.1,
        )

        if llm_response.strip().upper().startswith("APPROVED"):
            return HandlerResponse.respond(
                payload=SwarmMessage(
                    role="review-result",
                    tool_name=payload.tool_name,
                    content=llm_response,
                    status="success",
                    error="",
                    iteration=payload.iteration,
                    phase=payload.phase,
                ),
            )
        else:
            return HandlerResponse.respond(
                payload=SwarmMessage(
                    role="review-result",
                    tool_name=payload.tool_name,
                    content="",
                    status="error",
                    error=llm_response,
                    iteration=payload.iteration,
                    phase=payload.phase,
                ),
            )
    except Exception as exc:
        logger.exception("Reviewer agent error for tool '%s'", payload.tool_name)
        return HandlerResponse.respond(
            payload=SwarmMessage(
                role="review-result",
                tool_name=payload.tool_name,
                content="",
                status="error",
                error="Agent error (details logged)",
                iteration=payload.iteration,
                phase=payload.phase,
            ),
        )
