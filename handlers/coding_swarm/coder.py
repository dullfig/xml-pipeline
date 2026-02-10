"""
coder.py — LLM agent that writes AssemblyScript implementations.

Receives ``code-request`` messages containing a WIT interface and
requirements, calls the LLM to produce AssemblyScript source, and returns
a ``code-result``.
"""

from __future__ import annotations

from typing import Optional

from xml_pipeline.message_bus.message_state import HandlerMetadata, HandlerResponse

from handlers.coding_swarm.payloads import SwarmMessage


async def handle_coder(
    payload: SwarmMessage,
    metadata: HandlerMetadata,
) -> Optional[HandlerResponse]:
    """Write AssemblyScript implementing a WIT interface."""
    if payload.role != "code-request":
        return HandlerResponse.respond(
            payload=SwarmMessage(
                role="code-result",
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
            agent_name=metadata.own_name or "coder",
            thread_id=metadata.thread_id,
            user_message=(
                f"Write an AssemblyScript implementation for the WASM tool "
                f"'{payload.tool_name}'.\n\n"
                f"{payload.content}\n\n"
                "Respond with ONLY the AssemblyScript source code, no explanation."
            ),
            temperature=0.2,
        )

        return HandlerResponse.respond(
            payload=SwarmMessage(
                role="code-result",
                tool_name=payload.tool_name,
                content=llm_response,
                status="success",
                error="",
                iteration=payload.iteration,
                phase=payload.phase,
            ),
        )
    except Exception as exc:
        return HandlerResponse.respond(
            payload=SwarmMessage(
                role="code-result",
                tool_name=payload.tool_name,
                content="",
                status="error",
                error=str(exc),
                iteration=payload.iteration,
                phase=payload.phase,
            ),
        )
