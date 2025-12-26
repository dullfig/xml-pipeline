# xml_pipeline/__init__.py
"""
xml-pipeline
============
Secure, XML-centric multi-listener organism server.
"""

from agentserver.agentserver import AgentServer as AgentServer
from agentserver.listeners.base import XMLListener as XMLListener
from agentserver.message_bus import MessageBus as MessageBus
from agentserver.message_bus import Session as Session


__all__ = [
    "AgentServer",
    "XMLListener",
    "MessageBus",
    "Session",
]

__version__ = "0.1.0"