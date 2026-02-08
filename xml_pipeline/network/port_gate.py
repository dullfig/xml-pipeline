"""
port_gate.py — Network port allocation gate.

Enforces that only ports explicitly declared in organism.yaml can be opened.
Secure default: if no ports are declared, all requests are denied.

System-level ports (main bus, OOB channel) are exempt — they are managed
directly by StreamPump and OOBServer, not through this gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass(frozen=True)
class PortAllocation:
    """A declared port that a listener is allowed to open."""
    port: int
    bind: str = "127.0.0.1"
    listener: str = ""
    protocol: str = "tcp"


class PortDeniedError(Exception):
    """Raised when a port request doesn't match any declared allocation."""
    pass


class PortGate:
    """
    Firewall gate for listener port usage.

    Only ports declared in organism.yaml ``network.ports`` can be opened.
    Empty allocations = zero ports allowed (secure default).
    """

    def __init__(self, allocations: List[PortAllocation]) -> None:
        self._allocations = list(allocations)
        self._active: Dict[int, PortAllocation] = {}

    def request(
        self,
        port: int,
        bind: str,
        listener: str,
        protocol: str = "tcp",
    ) -> PortAllocation:
        """
        Request permission to open a port.

        Validates against the declared allocations. All four fields must match.

        Returns:
            The matching PortAllocation.

        Raises:
            PortDeniedError: If no matching allocation exists or port already active.
        """
        if port in self._active:
            raise PortDeniedError(
                f"Port {port} is already in use by '{self._active[port].listener}'"
            )

        for alloc in self._allocations:
            if (alloc.port == port
                    and alloc.bind == bind
                    and alloc.listener == listener
                    and alloc.protocol == protocol):
                self._active[port] = alloc
                return alloc

        raise PortDeniedError(
            f"Port {port} (bind={bind}, listener={listener}, protocol={protocol}) "
            f"not declared in network.ports"
        )

    def release(self, port: int) -> None:
        """Release a previously acquired port. No-op if not active."""
        self._active.pop(port, None)

    def active_ports(self) -> List[PortAllocation]:
        """Return list of currently active port allocations."""
        return list(self._active.values())

    def shutdown(self) -> None:
        """Release all active ports."""
        self._active.clear()


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_port_gate: Optional[PortGate] = None


def get_port_gate() -> PortGate:
    """Get the global PortGate singleton."""
    if _port_gate is None:
        raise RuntimeError(
            "PortGate not initialized. Call set_port_gate() or pump.start() first."
        )
    return _port_gate


def set_port_gate(gate: PortGate) -> None:
    """Set the global PortGate singleton."""
    global _port_gate
    _port_gate = gate


def reset_port_gate() -> None:
    """Reset the global PortGate singleton (for tests)."""
    global _port_gate
    _port_gate = None
