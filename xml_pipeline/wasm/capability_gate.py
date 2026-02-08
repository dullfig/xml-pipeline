"""
capability_gate.py — Declare-then-use enforcement for WASM host capabilities.

WASM modules can import host functions (fetch, kv, log) but only if
declared in the tool's YAML configuration. Unknown capabilities are
rejected at config time.

Follows the PortGate pattern — declare up front, enforce at instantiation.
"""

from __future__ import annotations

from typing import List


# Known host capabilities that can be linked into WASM modules.
KNOWN_CAPABILITIES = frozenset({"fetch", "kv", "log"})


class CapabilityDeniedError(Exception):
    """Raised when a WASM tool requests an unknown capability."""


def validate_capabilities(declared: List[str]) -> None:
    """
    Validate that all declared capabilities are known.

    Args:
        declared: List of capability names from YAML config.

    Raises:
        CapabilityDeniedError: If any capability is not in KNOWN_CAPABILITIES.
    """
    for cap in declared:
        if cap not in KNOWN_CAPABILITIES:
            raise CapabilityDeniedError(
                f"Unknown WASM capability: {cap!r}. "
                f"Known capabilities: {sorted(KNOWN_CAPABILITIES)}"
            )
