"""
boot.py — System boot primitive.

The <boot> message is the first message in every organism's lifetime.
It establishes the root thread from which all other threads descend.

The boot handler:
1. Logs organism startup
2. Initializes any system-level state

All external messages that arrive without a known thread parent
will be registered as children of the boot thread.

Note: The SecureConsole (v3.0) handles the console directly, so the boot
handler no longer sends to a console listener.
"""

from dataclasses import dataclass
import logging

from third_party.xmlable import xmlify
from agentserver.message_bus.message_state import HandlerMetadata, HandlerResponse

logger = logging.getLogger(__name__)


@xmlify
@dataclass
class Boot:
    """
    System boot message — first message in organism lifetime.

    Injected automatically at startup. Establishes root thread context.
    """
    organism_name: str = ""
    timestamp: str = ""
    listener_count: int = 0


async def handle_boot(payload: Boot, metadata: HandlerMetadata) -> None:
    """
    Handle the system boot message.

    Logs the boot event. The SecureConsole handles user interaction directly.
    """
    logger.info(
        f"Organism '{payload.organism_name}' booted at {payload.timestamp} "
        f"with {payload.listener_count} listeners. "
        f"Root thread: {metadata.thread_id}"
    )

    # Could initialize system state here:
    # - Warm up LLM connections
    # - Load cached schemas
    # - Pre-populate routing caches

    # No response needed - SecureConsole handles user interaction
    return None
