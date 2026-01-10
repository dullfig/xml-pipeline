"""
message_bus — Stream-based message pump for AgentServer v2.1

The message pump handles message flow through the organism:
- YAML config → bootstrap → pump → handlers → responses → loop

Key classes:
    StreamPump      Main pump class (queue-backed, aiostream-powered)
    ConfigLoader    Load organism.yaml and resolve imports
    Listener        Runtime listener with handler and routing info
    MessageState    Message flowing through pipeline steps

Usage:
    from agentserver.message_bus import StreamPump, bootstrap

    pump = await bootstrap("config/organism.yaml")
    await pump.inject(initial_message, thread_id, from_id)
    await pump.run()
"""

from agentserver.message_bus.stream_pump import (
    StreamPump,
    ConfigLoader,
    Listener,
    ListenerConfig,
    OrganismConfig,
    bootstrap,
)

from agentserver.message_bus.message_state import (
    MessageState,
    HandlerMetadata,
)

__all__ = [
    "StreamPump",
    "ConfigLoader",
    "Listener",
    "ListenerConfig",
    "OrganismConfig",
    "MessageState",
    "HandlerMetadata",
    "bootstrap",
]
