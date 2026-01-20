"""
todo.py — TodoUntil and TodoComplete system primitives.

These payloads allow agents to register watchers that monitor the thread
for specific conditions, and to close those watchers when done.

Usage by an agent:
    # Issue a todo - "wait for ShoutedResponse from shouter"
    return HandlerResponse(
        payload=TodoUntil(
            wait_for="ShoutedResponse",
            from_listener="shouter",
            description="waiting for shouter to respond",
        ),
        to="system.todo",
    )

    # Later, when nagged about a raised eyebrow, close it
    return HandlerResponse(
        payload=TodoComplete(id="..."),
        to="system.todo",
    )

The system.todo listener handles these messages:
- TodoUntil: registers a watcher in the TodoRegistry
- TodoComplete: closes the watcher
"""

from dataclasses import dataclass
from typing import Optional
import logging

from third_party.xmlable import xmlify
from xml_pipeline.message_bus.message_state import HandlerMetadata, HandlerResponse
from xml_pipeline.message_bus.todo_registry import get_todo_registry

logger = logging.getLogger(__name__)


@xmlify
@dataclass
class TodoUntil:
    """
    Register a todo watcher on the current thread.

    The agent will be nagged when the condition appears satisfied,
    until it explicitly closes with TodoComplete.
    """
    wait_for: str = ""              # Payload type to watch for
    from_listener: str = ""         # Optional: must be from this listener
    description: str = ""           # Human-readable description


@xmlify
@dataclass
class TodoComplete:
    """
    Close a todo watcher.

    Sent by an agent when it acknowledges the todo is complete.
    """
    id: str = ""                    # Watcher ID to close


@xmlify
@dataclass
class TodoRegistered:
    """
    Acknowledgment that a todo was registered.

    Sent back to the issuing agent with the watcher ID.
    """
    id: str = ""                    # Watcher ID for later close
    wait_for: str = ""              # What we're watching for
    description: str = ""           # Echo back the description


@xmlify
@dataclass
class TodoClosed:
    """
    Acknowledgment that a todo was closed.
    """
    id: str = ""                    # Watcher ID that was closed
    was_raised: bool = False        # Whether the eyebrow was raised when closed


async def handle_todo_until(payload: TodoUntil, metadata: HandlerMetadata) -> HandlerResponse:
    """
    Handle TodoUntil — register a watcher for this thread.

    Returns TodoRegistered to acknowledge.
    """
    registry = get_todo_registry()

    watcher_id = registry.register(
        thread_id=metadata.thread_id,
        issuer=metadata.from_id,
        wait_for=payload.wait_for,
        from_listener=payload.from_listener or None,
        description=payload.description,
    )

    logger.info(
        f"TodoUntil registered: {watcher_id} on thread {metadata.thread_id[:8]}... "
        f"by {metadata.from_id}, waiting for {payload.wait_for}"
    )

    return HandlerResponse(
        payload=TodoRegistered(
            id=watcher_id,
            wait_for=payload.wait_for,
            description=payload.description,
        ),
        to=metadata.from_id,
    )


async def handle_todo_complete(payload: TodoComplete, metadata: HandlerMetadata) -> Optional[HandlerResponse]:
    """
    Handle TodoComplete — close a watcher.

    Returns TodoClosed to acknowledge, or None if not found.
    """
    registry = get_todo_registry()

    # Get watcher info before closing (for the response)
    watcher = registry._by_id.get(payload.id)
    was_raised = watcher.eyebrow_raised if watcher else False

    if registry.close(payload.id):
        logger.info(f"TodoComplete: closed {payload.id} (was_raised={was_raised})")
        return HandlerResponse(
            payload=TodoClosed(id=payload.id, was_raised=was_raised),
            to=metadata.from_id,
        )
    else:
        logger.warning(f"TodoComplete: watcher {payload.id} not found")
        return None
