"""
network — Network port allocation and security gate.

Enforces that only ports declared in organism.yaml can be opened by listeners.
"""

from xml_pipeline.network.port_gate import (
    PortAllocation,
    PortDeniedError,
    PortGate,
    get_port_gate,
    set_port_gate,
    reset_port_gate,
)

__all__ = [
    "PortAllocation",
    "PortDeniedError",
    "PortGate",
    "get_port_gate",
    "set_port_gate",
    "reset_port_gate",
]
