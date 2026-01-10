# xml_pipeline/__init__.py
"""
xml-pipeline
============
Secure, XML-centric multi-listener organism server.

Stream-based message pump with aiostream for fan-out handling.
"""

from agentserver.message_bus import (
    StreamPump,
    ConfigLoader,
    Listener,
    MessageState,
    HandlerMetadata,
    bootstrap,
)

__all__ = [
    "StreamPump",
    "ConfigLoader",
    "Listener",
    "MessageState",
    "HandlerMetadata",
    "bootstrap",
]

__version__ = "0.2.0"  # Bumped for aiostream pump