"""
wasm — WASM tool integration for xml-pipeline.

Foreign tools (AssemblyScript, Rust, etc.) run in WASM sandboxes as
first-class listeners. WIT interfaces define the contract; dynamic
dataclasses bridge WASM ↔ XML.
"""

from xml_pipeline.wasm.registry import (
    WasmRegistry,
    WasmInstanceHandle,
    get_wasm_registry,
    set_wasm_registry,
    reset_wasm_registry,
)
from xml_pipeline.wasm.dispatcher import register_wasm_tool
from xml_pipeline.wasm.wit_parser import (
    WitInterface,
    WitRecord,
    WitField,
    WitValidationError,
)
from xml_pipeline.wasm.loader import WasmModule, WasmValidationError, WasmToolConfig
from xml_pipeline.wasm.capability_gate import CapabilityDeniedError, KNOWN_CAPABILITIES

__all__ = [
    # Registry
    "WasmRegistry",
    "WasmInstanceHandle",
    "get_wasm_registry",
    "set_wasm_registry",
    "reset_wasm_registry",
    # Dispatcher
    "register_wasm_tool",
    # WIT parser
    "WitInterface",
    "WitRecord",
    "WitField",
    "WitValidationError",
    # Loader
    "WasmModule",
    "WasmValidationError",
    "WasmToolConfig",
    # Capability gate
    "CapabilityDeniedError",
    "KNOWN_CAPABILITIES",
]
