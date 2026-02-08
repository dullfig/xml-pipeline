"""
registry.py — Thread-scoped WASM instance registry.

Tracks loaded WASM modules and per-thread instances. Follows the
WorkerRegistry/PortGate singleton pattern.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class WasmInstanceHandle:
    """A live WASM instance bound to a specific thread."""
    module_name: str
    thread_id: str
    store: Any = None       # wasmtime.Store
    instance: Any = None    # wasmtime.Instance
    memory: Any = None      # wasmtime.Memory
    alloc_fn: Any = None    # wasmtime export
    free_fn: Any = None     # wasmtime export


class WasmRegistry:
    """
    Registry for loaded WASM modules and per-thread instances.

    Modules are loaded once and reused across threads. Each thread
    gets its own Store+Instance for isolation.
    """

    def __init__(self) -> None:
        # name → loaded WasmModule
        self._modules: Dict[str, Any] = {}
        # "name:thread_id" → WasmInstanceHandle
        self._instances: Dict[str, WasmInstanceHandle] = {}

    def register_module(self, name: str, module: Any) -> None:
        """Register a loaded WASM module."""
        if name in self._modules:
            raise KeyError(f"WASM module '{name}' already registered")
        self._modules[name] = module
        logger.info(f"WASM module registered: {name}")

    def get_module(self, name: str) -> Any:
        """Get a registered WASM module by name."""
        module = self._modules.get(name)
        if module is None:
            raise KeyError(f"WASM module '{name}' not registered")
        return module

    def has_module(self, name: str) -> bool:
        """Check if a module is registered."""
        return name in self._modules

    def store_instance(
        self, name: str, thread_id: str, handle: WasmInstanceHandle
    ) -> None:
        """Store a thread-scoped WASM instance."""
        key = f"{name}:{thread_id}"
        self._instances[key] = handle

    def get_instance(self, name: str, thread_id: str) -> Optional[WasmInstanceHandle]:
        """Get an existing instance for a module+thread, or None."""
        return self._instances.get(f"{name}:{thread_id}")

    def cleanup_for_thread(self, thread_id: str) -> int:
        """
        Remove all WASM instances for a thread.

        Returns the number of instances cleaned up.
        """
        keys_to_remove = [
            key for key in self._instances
            if key.endswith(f":{thread_id}")
        ]
        for key in keys_to_remove:
            del self._instances[key]

        if keys_to_remove:
            logger.debug(
                f"Cleaned up {len(keys_to_remove)} WASM instance(s) "
                f"for thread {thread_id[:8]}..."
            )
        return len(keys_to_remove)

    def shutdown_all(self) -> int:
        """Remove all instances and modules. Returns instance count."""
        count = len(self._instances)
        self._instances.clear()
        self._modules.clear()
        if count:
            logger.info(f"WASM registry shutdown: {count} instance(s) released")
        return count

    @property
    def module_count(self) -> int:
        return len(self._modules)

    @property
    def instance_count(self) -> int:
        return len(self._instances)


# ============================================================================
# Singleton
# ============================================================================

_registry: Optional[WasmRegistry] = None


def get_wasm_registry() -> WasmRegistry:
    """Get the global WASM registry singleton."""
    global _registry
    if _registry is None:
        _registry = WasmRegistry()
    return _registry


def set_wasm_registry(registry: WasmRegistry) -> None:
    """Set the global WASM registry singleton."""
    global _registry
    _registry = registry


def reset_wasm_registry() -> None:
    """Reset the global WASM registry (for tests)."""
    global _registry
    _registry = None
