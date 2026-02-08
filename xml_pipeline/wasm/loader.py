"""
loader.py — WASM module loader and validator.

Loads .wasm binaries via wasmtime, validates exported functions match
the WIT interface contract, and links host functions.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional

from xml_pipeline.wasm.wit_parser import WitInterface
from xml_pipeline.wasm.capability_gate import validate_capabilities

logger = logging.getLogger(__name__)

# Guarded import — wasmtime is optional
try:
    import wasmtime
    WASMTIME_AVAILABLE = True
except ImportError:
    WASMTIME_AVAILABLE = False


class WasmValidationError(Exception):
    """Raised when a WASM module fails export validation."""


@dataclass
class WasmToolConfig:
    """Parsed configuration for a single WASM tool."""
    name: str
    wasm_path: str
    wit_path: str
    description: str = ""
    capabilities: List[str] = field(default_factory=list)
    memory_limit_mb: int = 64
    timeout_seconds: float = 5.0


@dataclass
class WasmModule:
    """A validated, ready-to-use WASM module."""
    name: str
    engine: Any = None          # wasmtime.Engine
    module: Any = None          # wasmtime.Module
    interfaces: List[WitInterface] = field(default_factory=list)
    config: Optional[WasmToolConfig] = None

    def create_instance(self, capabilities: List[str]) -> Any:
        """
        Create a new Store+Instance with capability-gated host imports.

        Returns a tuple of (store, instance, memory, alloc_fn, free_fn).
        """
        if not WASMTIME_AVAILABLE:
            raise WasmValidationError("wasmtime not installed")

        store = wasmtime.Store(self.engine)

        # Apply memory limit
        if self.config:
            limit_bytes = self.config.memory_limit_mb * 1024 * 1024
            store.set_limits(memory_size=limit_bytes)

        # Create linker with capability-gated host functions
        linker = wasmtime.Linker(self.engine)

        # Late-binding refs for host functions to access instance internals
        memory_ref: list = []
        alloc_ref: list = []
        store_ref: list = [store]

        from xml_pipeline.wasm.host_functions import link_host_functions
        link_host_functions(linker, capabilities, memory_ref, alloc_ref, store_ref)

        # Instantiate
        instance = linker.instantiate(store, self.module)

        # Extract standard exports
        memory = instance.exports(store).get("memory")
        alloc_fn = instance.exports(store).get("alloc")
        free_fn = instance.exports(store).get("free")

        # Populate late-binding refs
        if memory:
            memory_ref.append(memory)
        if alloc_fn:
            alloc_ref.append(alloc_fn)

        return store, instance, memory, alloc_fn, free_fn


def load_wasm_module(config: WasmToolConfig) -> WasmModule:
    """
    Load and validate a WASM module from config.

    1. Validate capabilities against known set
    2. Parse WIT file for interface definitions
    3. Load WASM binary via wasmtime
    4. Validate exports match WIT interfaces

    Args:
        config: Tool configuration from YAML

    Returns:
        A validated WasmModule ready for instantiation

    Raises:
        WasmValidationError: If WASM exports don't match WIT
        CapabilityDeniedError: If unknown capabilities declared
    """
    # Validate capabilities
    validate_capabilities(config.capabilities)

    # Parse WIT
    from xml_pipeline.wasm.wit_parser import parse_wit_file
    interfaces = parse_wit_file(config.wit_path)

    if not WASMTIME_AVAILABLE:
        raise WasmValidationError(
            "wasmtime is required for WASM tool support. "
            "Install with: pip install xml-pipeline[wasm]"
        )

    # Load WASM binary
    wasm_path = Path(config.wasm_path)
    if not wasm_path.exists():
        raise WasmValidationError(f"WASM file not found: {wasm_path}")

    engine = wasmtime.Engine()
    module = wasmtime.Module.from_file(engine, str(wasm_path))

    # Validate exports
    _validate_exports(module, interfaces)

    logger.info(
        f"WASM module loaded: {config.name} "
        f"({len(interfaces)} interface(s), "
        f"capabilities={config.capabilities})"
    )

    return WasmModule(
        name=config.name,
        engine=engine,
        module=module,
        interfaces=interfaces,
        config=config,
    )


def _validate_exports(module: Any, interfaces: List[WitInterface]) -> None:
    """
    Validate that the WASM module exports the required functions.

    For each WitInterface, the module must export:
    - handle_{name} (i32, i32) -> i32
    - alloc (i32) -> i32
    - free (i32) -> ()
    """
    if not WASMTIME_AVAILABLE:
        return

    export_names = {exp.name for exp in module.exports}

    # Check alloc/free (required once)
    if "alloc" not in export_names:
        raise WasmValidationError("WASM module missing 'alloc' export")
    if "free" not in export_names:
        raise WasmValidationError("WASM module missing 'free' export")

    # Check per-interface handler exports
    for iface in interfaces:
        handler_name = f"handle_{iface.name.replace('-', '_')}"
        if handler_name not in export_names:
            raise WasmValidationError(
                f"WASM module missing '{handler_name}' export "
                f"for interface '{iface.name}'"
            )
