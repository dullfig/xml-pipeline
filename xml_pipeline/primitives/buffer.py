"""
buffer.py — Buffer (fan-out) orchestration primitives.

Buffers fan-out to parallel workers, sending N items to the same listener
concurrently. Results are collected and returned when all complete.

Usage by an agent:
    # Fan-out search queries to web_search
    return HandlerResponse(
        payload=BufferStart(
            target="web_search",
            items="python async\\nrust memory\\ngo concurrency",
            return_to="my-agent",
            collect=True,
        ),
        to="system.buffer",
    )

Flow:
    1. system.buffer receives BufferStart with N items
    2. Creates ephemeral listener buffer_{id} to receive results
    3. Creates N sibling threads via ThreadRegistry
    4. Sends BufferItem to each worker FROM buffer_{id}
    5. Workers process and respond → routes to buffer_{id}
    6. Ephemeral handler collects results
    7. When all workers done, sends BufferComplete to return_to
    8. Cleans up ephemeral listener

Fire-and-forget mode (collect=False):
    - Returns immediately after dispatching
    - No result collection
    - Useful for async side effects
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
)
from xml_pipeline.message_bus.buffer_registry import get_buffer_registry
from xml_pipeline.message_bus.thread_registry import get_registry as get_thread_registry

logger = logging.getLogger(__name__)


# ============================================================================
# Payloads
# ============================================================================

@xmlify
@dataclass
class BufferStart:
    """
    Start a new buffer (fan-out) execution.

    Sent to system.buffer to begin parallel processing.
    """
    target: str = ""          # Listener to fan-out to
    items: str = ""           # Newline-separated payloads (raw XML)
    collect: bool = True      # Wait for all results?
    return_to: str = ""       # Where to send BufferComplete
    buffer_id: str = ""       # Auto-generated if empty


@xmlify
@dataclass
class BufferItem:
    """
    Individual item being processed by a worker.

    Wraps the actual payload with buffer metadata.
    Note: This is an internal type - workers receive the raw payload,
    not BufferItem directly.
    """
    buffer_id: str = ""
    index: int = 0
    payload: str = ""         # The actual XML payload


@xmlify
@dataclass
class BufferComplete:
    """
    Buffer completed - all workers finished.

    Sent to return_to when all items are processed.
    """
    buffer_id: str = ""
    total: int = 0
    successful: int = 0
    results: str = ""         # XML array of results


@xmlify
@dataclass
class BufferDispatched:
    """
    Buffer dispatched (fire-and-forget mode).

    Sent immediately after items are dispatched when collect=False.
    """
    buffer_id: str = ""
    total: int = 0


@xmlify
@dataclass
class BufferError:
    """
    Buffer failed to start.

    Sent when buffer initialization fails.
    """
    buffer_id: str = ""
    error: str = ""


# ============================================================================
# Handlers
# ============================================================================

async def handle_buffer_start(
    payload: BufferStart,
    metadata: HandlerMetadata,
) -> Optional[HandlerResponse]:
    """
    Handle BufferStart — begin a fan-out execution.

    Creates N sibling threads, dispatches items to workers,
    and sets up result collection.
    """
    from xml_pipeline.message_bus.stream_pump import get_stream_pump

    # Parse items
    items = [item.strip() for item in payload.items.split("\n") if item.strip()]
    if not items:
        logger.error("BufferStart with no items")
        return HandlerResponse(
            payload=BufferError(
                buffer_id=payload.buffer_id or "unknown",
                error="No items specified",
            ),
            to=payload.return_to or metadata.from_id,
        )

    # Validate target exists
    pump = get_stream_pump()
    if payload.target not in pump.listeners:
        logger.error(f"BufferStart: unknown target '{payload.target}'")
        return HandlerResponse(
            payload=BufferError(
                buffer_id=payload.buffer_id or "unknown",
                error=f"Unknown target listener: {payload.target}",
            ),
            to=payload.return_to or metadata.from_id,
        )

    # Generate buffer ID if not provided
    buf_id = payload.buffer_id or str(uuid_module.uuid4())[:8]

    # Create buffer state
    buffer_registry = get_buffer_registry()
    state = buffer_registry.create(
        buffer_id=buf_id,
        total_items=len(items),
        return_to=payload.return_to or metadata.from_id,
        thread_id=metadata.thread_id,
        from_id=metadata.from_id,
        target=payload.target,
        collect=payload.collect,
    )

    # For fire-and-forget, we still track but don't wait
    ephemeral_name = f"buffer_{buf_id}"

    if payload.collect:
        # Create ephemeral handler for result collection
        async def buffer_handler(
            payload_tree: etree._Element,
            meta: HandlerMetadata,
        ) -> Optional[HandlerResponse]:
            """Ephemeral handler that collects worker results."""
            return await _handle_buffer_result(buf_id, payload_tree, meta)

        # Register ephemeral listener (generic mode - accepts any payload)
        pump.register_generic_listener(
            name=ephemeral_name,
            handler=buffer_handler,
            description=f"Ephemeral buffer handler for {buf_id}",
        )

    logger.info(
        f"Buffer {buf_id} starting: {len(items)} items to {payload.target}, "
        f"collect={payload.collect}"
    )

    # Dispatch all items in parallel
    thread_registry = get_thread_registry()
    parent_chain = thread_registry.lookup(metadata.thread_id) or metadata.thread_id

    for i, item_payload in enumerate(items):
        # Create sibling thread for this worker
        worker_chain = f"{parent_chain}.{ephemeral_name}_w{i}"
        worker_uuid = thread_registry.get_or_create(worker_chain)

        # Inject the item to the target
        # The item is sent FROM the ephemeral listener so .respond() comes back
        await _inject_buffer_item(
            pump=pump,
            target=payload.target,
            payload_xml=item_payload,
            thread_id=worker_uuid,
            from_id=ephemeral_name,
        )

        logger.debug(f"Buffer {buf_id}: dispatched item {i} to {payload.target}")

    # Fire-and-forget: return immediately
    if not payload.collect:
        logger.info(f"Buffer {buf_id}: fire-and-forget mode, {len(items)} items dispatched")
        return HandlerResponse(
            payload=BufferDispatched(
                buffer_id=buf_id,
                total=len(items),
            ),
            to=payload.return_to or metadata.from_id,
        )

    # Collect mode: wait for results (handled by ephemeral listener)
    # Return None - the ephemeral listener will send BufferComplete
    return None


async def _handle_buffer_result(
    buf_id: str,
    payload_tree: etree._Element,
    metadata: HandlerMetadata,
) -> Optional[HandlerResponse]:
    """
    Handle a worker result in the buffer.

    Called by the ephemeral listener when a worker responds.
    """
    from xml_pipeline.message_bus.stream_pump import get_stream_pump

    buffer_registry = get_buffer_registry()
    state = buffer_registry.get(buf_id)

    if state is None:
        logger.warning(f"Buffer {buf_id} not found in registry (result dropped)")
        return None

    # Extract worker index from thread chain
    # Chain format: parent.buffer_xyz_wN where N is the index
    thread_registry = get_thread_registry()
    chain = thread_registry.lookup(metadata.thread_id) or ""

    worker_index = _extract_worker_index(chain, buf_id)
    if worker_index is None:
        logger.warning(f"Buffer {buf_id}: could not determine worker index from chain")
        worker_index = state.completed_count  # Fallback to count-based

    # Serialize the result
    result_xml = etree.tostring(payload_tree, encoding="unicode")

    # Check for errors
    is_error = payload_tree.tag.lower() in ("huh", "systemerror")

    # Record result
    state = buffer_registry.record_result(
        buffer_id=buf_id,
        index=worker_index,
        result=result_xml,
        success=not is_error,
        error=result_xml[:200] if is_error else None,
    )

    logger.debug(
        f"Buffer {buf_id}: received result {state.completed_count}/{state.total_items}"
    )

    # Check if all done
    if state.is_complete:
        # Clean up
        pump = get_stream_pump()
        pump.unregister_listener(f"buffer_{buf_id}")

        # Format results as XML array
        results_xml = _format_buffer_results(state)
        buffer_registry.remove(buf_id)

        logger.info(
            f"Buffer {buf_id} completed: {state.successful_count}/{state.total_items} successful"
        )

        return HandlerResponse(
            payload=BufferComplete(
                buffer_id=buf_id,
                total=state.total_items,
                successful=state.successful_count,
                results=results_xml,
            ),
            to=state.return_to,
        )

    # More results pending
    return None


def _extract_worker_index(chain: str, buf_id: str) -> Optional[int]:
    """Extract worker index from thread chain."""
    # Look for pattern: buffer_{id}_wN
    import re
    pattern = rf"buffer_{buf_id}_w(\d+)"
    match = re.search(pattern, chain)
    if match:
        return int(match.group(1))
    return None


def _format_buffer_results(state) -> str:
    """Format buffer results as XML array."""
    lines = ["<results>"]
    for i in range(state.total_items):
        result = state.results.get(i)
        if result:
            success = "true" if result.success else "false"
            lines.append(f'  <item index="{i}" success="{success}">')
            # Indent the result content
            for line in result.result.split("\n"):
                lines.append(f"    {line}")
            lines.append("  </item>")
        else:
            lines.append(f'  <item index="{i}" success="false">missing</item>')
    lines.append("</results>")
    return "\n".join(lines)


async def _inject_buffer_item(
    pump,
    target: str,
    payload_xml: str,
    thread_id: str,
    from_id: str,
) -> None:
    """Inject a buffer item directly into the pump."""
    # Wrap the payload in an envelope
    envelope = pump._wrap_in_envelope(
        payload=_RawXmlPayload(payload_xml),
        from_id=from_id,
        to_id=target,
        thread_id=thread_id,
    )

    # Inject into pump
    await pump._inject_raw(envelope, thread_id=thread_id, from_id=from_id)


class _RawXmlPayload:
    """Carrier for raw XML that bypasses serialization."""

    def __init__(self, xml: str):
        self.xml = xml

    def to_xml(self) -> str:
        """Return raw XML for envelope wrapping."""
        return self.xml
