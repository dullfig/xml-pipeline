"""
sequence.py — Sequence orchestration primitives.

Sequences chain multiple listeners in order, feeding the output of one step
as input to the next. Steps remain transparent - they don't know they're
part of a sequence.

Usage by an agent:
    # Start a sequence: add two numbers, then multiply
    return HandlerResponse(
        payload=SequenceStart(
            steps="calculator.add,calculator.multiply",
            payload='<AddPayload><a>5</a><b>3</b></AddPayload>',
            return_to="my-agent",
        ),
        to="system.sequence",
    )

Flow:
    1. system.sequence receives SequenceStart
    2. Creates ephemeral listener sequence_{id} to receive step results
    3. Sends initial payload to first step FROM sequence_{id}
    4. Step processes and responds → routes to sequence_{id}
    5. Ephemeral handler advances, sends to next step
    6. When all steps complete, sends SequenceComplete to return_to
    7. Cleans up ephemeral listener

Key insight: Steps use normal .respond() - the ephemeral listener IS the
caller in the thread chain, so responses naturally route back to it.
"""

from dataclasses import dataclass
from typing import Optional
import uuid as uuid_module
import logging

from lxml import etree
from third_party.xmlable import xmlify
from xml_pipeline.message_bus.message_state import (
    HandlerMetadata,
    HandlerResponse,
    MessageState,
)
from xml_pipeline.message_bus.sequence_registry import get_sequence_registry

logger = logging.getLogger(__name__)


# ============================================================================
# Payloads
# ============================================================================

@xmlify
@dataclass
class SequenceStart:
    """
    Start a new sequence execution.

    Sent to system.sequence to begin chaining steps.
    """
    steps: str = ""           # Comma-separated listener names
    payload: str = ""         # Initial XML payload for first step
    return_to: str = ""       # Where to send final result
    sequence_id: str = ""     # Auto-generated if empty


@xmlify
@dataclass
class SequenceComplete:
    """
    Sequence completed successfully.

    Sent to the return_to listener when all steps finish.
    """
    sequence_id: str = ""
    final_result: str = ""    # XML result from last step
    step_count: int = 0       # How many steps were executed


@xmlify
@dataclass
class SequenceError:
    """
    Sequence failed at a step.

    Sent to return_to when a step fails.
    """
    sequence_id: str = ""
    failed_step: str = ""     # Which step failed
    step_index: int = 0       # 0-based index of failed step
    error: str = ""           # Error message


# ============================================================================
# Handlers
# ============================================================================

async def handle_sequence_start(
    payload: SequenceStart,
    metadata: HandlerMetadata,
) -> Optional[HandlerResponse]:
    """
    Handle SequenceStart — begin a sequence execution.

    Creates an ephemeral listener for this sequence, stores state,
    and kicks off the first step.
    """
    from xml_pipeline.message_bus.stream_pump import get_stream_pump

    # Parse and validate
    steps = [s.strip() for s in payload.steps.split(",") if s.strip()]
    if not steps:
        logger.error("SequenceStart with no steps")
        return HandlerResponse(
            payload=SequenceError(
                sequence_id=payload.sequence_id or "unknown",
                failed_step="",
                step_index=0,
                error="No steps specified",
            ),
            to=payload.return_to or metadata.from_id,
        )

    # Generate sequence ID if not provided
    seq_id = payload.sequence_id or str(uuid_module.uuid4())[:8]

    # Validate all steps exist
    pump = get_stream_pump()
    for step in steps:
        if step not in pump.listeners:
            logger.error(f"SequenceStart: unknown step '{step}'")
            return HandlerResponse(
                payload=SequenceError(
                    sequence_id=seq_id,
                    failed_step=step,
                    step_index=steps.index(step),
                    error=f"Unknown listener: {step}",
                ),
                to=payload.return_to or metadata.from_id,
            )

    # Create sequence state
    registry = get_sequence_registry()
    state = registry.create(
        sequence_id=seq_id,
        steps=steps,
        return_to=payload.return_to or metadata.from_id,
        thread_id=metadata.thread_id,
        from_id=metadata.from_id,
        initial_payload=payload.payload,
    )

    # Create ephemeral handler for this sequence
    ephemeral_name = f"sequence_{seq_id}"

    async def sequence_handler(
        payload_tree: etree._Element,
        meta: HandlerMetadata,
    ) -> Optional[HandlerResponse]:
        """Ephemeral handler that processes step results."""
        return await _handle_sequence_step_result(seq_id, payload_tree, meta)

    # Register ephemeral listener (generic mode - accepts any payload)
    pump.register_generic_listener(
        name=ephemeral_name,
        handler=sequence_handler,
        description=f"Ephemeral sequence handler for {seq_id}",
    )

    logger.info(
        f"Sequence {seq_id} started: {len(steps)} steps, "
        f"return_to={state.return_to}"
    )

    # Kick off first step
    first_step = steps[0]
    return _create_step_message(
        seq_id=seq_id,
        target=first_step,
        payload_xml=payload.payload,
        from_name=ephemeral_name,
    )


async def _handle_sequence_step_result(
    seq_id: str,
    payload_tree: etree._Element,
    metadata: HandlerMetadata,
) -> Optional[HandlerResponse]:
    """
    Handle a step result in the sequence.

    Called by the ephemeral listener when a step responds.
    """
    from xml_pipeline.message_bus.stream_pump import get_stream_pump

    registry = get_sequence_registry()
    state = registry.get(seq_id)

    if state is None:
        logger.error(f"Sequence {seq_id} not found in registry")
        return None

    # Serialize the result for storage
    result_xml = etree.tostring(payload_tree, encoding="unicode")

    # Check for error responses
    if payload_tree.tag.lower() in ("huh", "systemerror"):
        # Step failed
        error_text = payload_tree.text or etree.tostring(payload_tree, encoding="unicode")
        registry.mark_failed(seq_id, state.current_step or "unknown", error_text)

        # Clean up and send error
        pump = get_stream_pump()
        pump.unregister_listener(f"sequence_{seq_id}")
        registry.remove(seq_id)

        logger.warning(f"Sequence {seq_id} failed at step {state.current_index}")
        return HandlerResponse(
            payload=SequenceError(
                sequence_id=seq_id,
                failed_step=state.current_step or "unknown",
                step_index=state.current_index,
                error=error_text[:200],  # Truncate long errors
            ),
            to=state.return_to,
        )

    # Advance to next step
    state = registry.advance(seq_id, result_xml)

    if state.is_complete:
        # All steps done - send completion
        pump = get_stream_pump()
        pump.unregister_listener(f"sequence_{seq_id}")
        registry.remove(seq_id)

        logger.info(f"Sequence {seq_id} completed: {len(state.steps)} steps")
        return HandlerResponse(
            payload=SequenceComplete(
                sequence_id=seq_id,
                final_result=result_xml,
                step_count=len(state.steps),
            ),
            to=state.return_to,
        )

    # More steps to go - send to next step
    next_step = state.current_step
    logger.debug(
        f"Sequence {seq_id} advancing to step {state.current_index}: {next_step}"
    )

    return _create_step_message(
        seq_id=seq_id,
        target=next_step,
        payload_xml=result_xml,
        from_name=f"sequence_{seq_id}",
    )


def _create_step_message(
    seq_id: str,
    target: str,
    payload_xml: str,
    from_name: str,
) -> HandlerResponse:
    """
    Create a HandlerResponse to send payload to a step.

    We need to inject the message with the ephemeral listener as the sender,
    so that .respond() routes back to us.
    """
    from xml_pipeline.primitives.sequence import _RawPayloadCarrier

    # Return a special carrier that tells the pump to:
    # 1. Use the raw XML bytes directly
    # 2. Set from_id to from_name (the ephemeral listener)
    return HandlerResponse(
        payload=_RawPayloadCarrier(xml=payload_xml, from_override=from_name),
        to=target,
    )


class _RawPayloadCarrier:
    """
    Internal carrier for raw XML that bypasses normal serialization.

    When the pump sees this, it uses the raw XML directly instead of
    serializing a dataclass.
    """

    def __init__(self, xml: str, from_override: Optional[str] = None):
        self.xml = xml
        self.from_override = from_override

    def to_xml(self) -> str:
        """Return raw XML for envelope wrapping."""
        return self.xml
