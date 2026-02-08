"""
xml-pipeline: Tamper-proof nervous system for multi-agent organisms.
"""

__version__ = "0.4.0"

# Public API re-exports — stable symbols for downstream consumers.
# Use these instead of reaching into submodules directly.
from xml_pipeline.message_bus.message_state import HandlerResponse, HandlerMetadata
from xml_pipeline.message_bus.stream_pump import StreamPump
from xml_pipeline.message_bus.events import (
    PumpEvent,
    MessageReceivedEvent,
    MessageSentEvent,
    AgentStateEvent,
    ThreadEvent,
    ReloadEvent,
)
from xml_pipeline.workers import get_worker_registry
from xml_pipeline.network import get_port_gate
from xml_pipeline.wasm import get_wasm_registry

__all__ = [
    "__version__",
    # Handler contract
    "HandlerResponse",
    "HandlerMetadata",
    # Pump
    "StreamPump",
    # Events
    "PumpEvent",
    "MessageReceivedEvent",
    "MessageSentEvent",
    "AgentStateEvent",
    "ThreadEvent",
    "ReloadEvent",
    # Workers
    "get_worker_registry",
    # Network
    "get_port_gate",
    # WASM
    "get_wasm_registry",
]
