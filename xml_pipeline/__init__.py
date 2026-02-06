"""
xml-pipeline: Tamper-proof nervous system for multi-agent organisms.
"""

__version__ = "0.4.0"

# Public API re-exports — stable symbols for downstream consumers.
# Use these instead of reaching into submodules directly.
from xml_pipeline.message_bus.message_state import HandlerResponse, HandlerMetadata
from xml_pipeline.message_bus.stream_pump import (
    StreamPump,
    bootstrap,
    PumpEvent,
    MessageReceivedEvent,
    MessageSentEvent,
    AgentStateEvent,
    ThreadEvent,
    ReloadEvent,
)

__all__ = [
    "__version__",
    # Handler contract
    "HandlerResponse",
    "HandlerMetadata",
    # Pump
    "StreamPump",
    "bootstrap",
    # Events
    "PumpEvent",
    "MessageReceivedEvent",
    "MessageSentEvent",
    "AgentStateEvent",
    "ThreadEvent",
    "ReloadEvent",
]
