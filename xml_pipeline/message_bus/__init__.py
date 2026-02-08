"""
message_bus — Stream-based message pump for AgentServer v2.1

The message pump handles message flow through the organism:
- YAML config → StreamPump.from_yaml() → pump → handlers → responses → loop

Modules:
    stream_pump     Main StreamPump class
    events          PumpEvent and subclasses (MessageReceived, AgentState, etc.)
    pump_config     ListenerConfig, OrganismConfig, Listener dataclasses
    config_loader   ConfigLoader — YAML parsing and import resolution
    pipeline        Pipeline helpers (extract_payloads, make_xsd_validation, etc.)
    singleton       Global pump singleton (get/set/reset_stream_pump)
    message_state   MessageState, HandlerMetadata, HandlerResponse
    system_pipeline External message injection (console, webhook)
    thread_registry Opaque UUID ↔ call chain mapping

Usage:
    from xml_pipeline.message_bus import StreamPump, SystemPipeline

    pump = await StreamPump.from_yaml("config/organism.yaml")
    system = SystemPipeline(pump)

    thread_id = await system.inject_console("@greeter Dan", user="admin")
    await pump.run()
"""

from xml_pipeline.message_bus.stream_pump import StreamPump

from xml_pipeline.message_bus.config_loader import ConfigLoader

from xml_pipeline.message_bus.pump_config import (
    Listener,
    ListenerConfig,
    OrganismConfig,
)

from xml_pipeline.message_bus.singleton import (
    get_stream_pump,
    set_stream_pump,
    reset_stream_pump,
)

from xml_pipeline.message_bus.events import (
    PumpEvent,
    MessageReceivedEvent,
    MessageSentEvent,
    AgentStateEvent,
    ThreadEvent,
    ReloadEvent,
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

from xml_pipeline.message_bus.budget_registry import (
    ThreadBudget,
    ThreadBudgetRegistry,
    BudgetExhaustedError,
    get_budget_registry,
    configure_budget_registry,
    reset_budget_registry,
)

__all__ = [
    # Pump
    "StreamPump",
    "ConfigLoader",
    "Listener",
    "ListenerConfig",
    "OrganismConfig",
    "get_stream_pump",
    "set_stream_pump",
    "reset_stream_pump",
    # Event hooks
    "PumpEvent",
    "MessageReceivedEvent",
    "MessageSentEvent",
    "AgentStateEvent",
    "ThreadEvent",
    "ReloadEvent",
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
    # Budget registry
    "ThreadBudget",
    "ThreadBudgetRegistry",
    "BudgetExhaustedError",
    "get_budget_registry",
    "configure_budget_registry",
    "reset_budget_registry",
]
