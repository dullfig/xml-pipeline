"""
WASM Listener support (STUB).

Enables custom listeners implemented in WebAssembly/AssemblyScript.
See docs/wasm-listeners.md for specification.

Status: NOT IMPLEMENTED - interface documented, awaiting implementation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class WasmNotImplementedError(NotImplementedError):
    """WASM listener support is not yet implemented."""

    def __init__(self):
        super().__init__(
            "WASM listener support is not yet implemented. "
            "See docs/wasm-listeners.md for the planned interface. "
            "For now, implement listeners in Python."
        )


@dataclass
class WasmListenerConfig:
    """Configuration for a WASM listener."""

    name: str
    wasm_path: Path
    wit_path: Path
    memory_limit_mb: int = 64
    timeout_seconds: float = 5.0
    keep_hot: bool = True  # Keep instance loaded between calls


@dataclass
class WasmInstance:
    """A loaded WASM module instance (STUB)."""

    config: WasmListenerConfig
    # Future: wasmtime.Instance or wasmer.Instance
    _module: Any = field(default=None, repr=False)
    _instance: Any = field(default=None, repr=False)

    def call(self, handler: str, input_json: str) -> str:
        """Call a handler with JSON input, return JSON output."""
        raise WasmNotImplementedError()

    def close(self) -> None:
        """Release WASM instance resources."""
        pass


class WasmListenerRegistry:
    """
    Registry for WASM listeners (STUB).

    Usage:
        from xml_pipeline.listeners.wasm_listener import wasm_registry

        wasm_registry.register(
            name="calculator",
            wasm_path=Path("/uploads/calculator.wasm"),
            wit_path=Path("/uploads/calculator.wit"),
        )
    """

    def __init__(self):
        self._listeners: dict[str, WasmListenerConfig] = {}
        self._instances: dict[str, WasmInstance] = {}  # thread_id -> instance

    def register(
        self,
        name: str,
        wasm_path: Path,
        wit_path: Path,
        **config,
    ) -> None:
        """
        Register a WASM listener.

        Args:
            name: Listener name (must be unique)
            wasm_path: Path to .wasm file
            wit_path: Path to .wit interface file
            **config: Additional config (memory_limit_mb, timeout_seconds, etc.)
        """
        raise WasmNotImplementedError()

    def unregister(self, name: str) -> None:
        """Remove a WASM listener."""
        raise WasmNotImplementedError()

    def get_instance(self, name: str, thread_id: str) -> WasmInstance:
        """Get or create a WASM instance for a thread."""
        raise WasmNotImplementedError()

    def prune_thread(self, thread_id: str) -> None:
        """Release WASM instances for a pruned thread."""
        # This will be called by thread registry on cleanup
        instances_to_remove = [
            key for key in self._instances
            if key.endswith(f":{thread_id}")
        ]
        for key in instances_to_remove:
            instance = self._instances.pop(key, None)
            if instance:
                instance.close()

    def list_listeners(self) -> list[str]:
        """List registered WASM listener names."""
        return list(self._listeners.keys())


# Global registry instance
wasm_registry = WasmListenerRegistry()


def register_wasm_listener(
    name: str,
    wasm_path: str | Path,
    wit_path: str | Path,
    **config,
) -> None:
    """
    Convenience function to register a WASM listener.

    See docs/wasm-listeners.md for full specification.

    Args:
        name: Unique listener name
        wasm_path: Path to .wasm module
        wit_path: Path to .wit interface definition
        **config: Optional config overrides

    Raises:
        WasmNotImplementedError: WASM support not yet implemented
    """
    wasm_registry.register(
        name=name,
        wasm_path=Path(wasm_path),
        wit_path=Path(wit_path),
        **config,
    )


async def create_wasm_handler(config: WasmListenerConfig):
    """
    Create an async handler function for a WASM listener.

    Returns a handler compatible with the standard listener interface:
        async def handler(payload: DataClass, metadata: HandlerMetadata) -> HandlerResponse
    """
    raise WasmNotImplementedError()
