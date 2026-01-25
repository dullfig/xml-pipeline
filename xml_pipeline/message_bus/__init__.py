"""
message_bus — Stream-based message pump for AgentServer v2.1

The message pump handles message flow through the organism:
- YAML config → bootstrap → pump → handlers → responses → loop

Key classes:
    StreamPump      Main pump class (queue-backed, aiostream-powered)
    SystemPipeline  Entry point for external messages (console, webhook)
    ConfigLoader    Load organism.yaml and resolve imports
    Listener        Runtime listener with handler and routing info
    MessageState    Message flowing through pipeline steps

Usage:
    from xml_pipeline.message_bus import StreamPump, SystemPipeline, bootstrap

    pump = await bootstrap("config/organism.yaml")
    system = SystemPipeline(pump)

    # Inject from console
    thread_id = await system.inject_console("@greeter Dan", user="admin")

    await pump.run()
"""

from xml_pipeline.message_bus.stream_pump import (
    StreamPump,
    ConfigLoader,
    Listener,
    ListenerConfig,
    OrganismConfig,
    bootstrap,
    get_stream_pump,
    set_stream_pump,
    reset_stream_pump,
)

from xml_pipeline.message_bus.message_state import (
    MessageState,
    HandlerMetadata,
)

from xml_pipeline.message_bus.system_pipeline import (
    SystemPipeline,
    ExternalMessage,
)

from xml_pipeline.message_bus.sequence_registry import (
    SequenceState,
    SequenceRegistry,
    get_sequence_registry,
    reset_sequence_registry,
)

from xml_pipeline.message_bus.buffer_registry import (
    BufferState,
    BufferItemResult,
    BufferRegistry,
    get_buffer_registry,
    reset_buffer_registry,
)

__all__ = [
    # Pump
    "StreamPump",
    "ConfigLoader",
    "Listener",
    "ListenerConfig",
    "OrganismConfig",
    "bootstrap",
    "get_stream_pump",
    "set_stream_pump",
    "reset_stream_pump",
    # Message state
    "MessageState",
    "HandlerMetadata",
    # System pipeline
    "SystemPipeline",
    "ExternalMessage",
    # Sequence registry
    "SequenceState",
    "SequenceRegistry",
    "get_sequence_registry",
    "reset_sequence_registry",
    # Buffer registry
    "BufferState",
    "BufferItemResult",
    "BufferRegistry",
    "get_buffer_registry",
    "reset_buffer_registry",
]
