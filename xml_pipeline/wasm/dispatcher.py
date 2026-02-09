"""
dispatcher.py — Generic WASM-to-listener dispatcher (Option B).

Fixed Python code that dynamically creates payload dataclasses from WIT
definitions, applies xmlify(), and registers them as pump listeners.
Zero codegen, zero self-modifying code.

One WASM export = one listener.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import make_dataclass
from typing import Any, Dict, List

from xml_pipeline.message_bus.message_state import HandlerMetadata, HandlerResponse
from xml_pipeline.wasm.wit_parser import WitInterface, WitRecord
from xml_pipeline.wasm.loader import WasmToolConfig, WasmModule, load_wasm_module, WASMTIME_AVAILABLE
from xml_pipeline.wasm.capability_gate import validate_capabilities
from xml_pipeline.wasm.registry import WasmRegistry, WasmInstanceHandle

logger = logging.getLogger(__name__)


# ============================================================================
# Dynamic dataclass creation
# ============================================================================

_PASCAL_RE = re.compile(r"(?:^|[-_])(\w)")


def _to_pascal(name: str) -> str:
    """Convert kebab-case or snake_case to PascalCase."""
    return _PASCAL_RE.sub(lambda m: m.group(1).upper(), name)


def _create_payload_class(tool_name: str, record: WitRecord) -> type:
    """
    Create an @xmlify dataclass from a WIT record definition.

    Args:
        tool_name: Tool name prefix for disambiguation
        record: WIT record with field definitions

    Returns:
        An @xmlify-decorated dataclass type
    """
    from third_party.xmlable import xmlify

    # Build field list: (field_name, python_type)
    fields = [
        (f.name, f.python_type, f.python_type())
        for f in record.fields
    ]

    cls_name = _to_pascal(record.name)

    # make_dataclass with defaults
    cls = make_dataclass(cls_name, fields)
    return xmlify(cls)


# ============================================================================
# Serialization helpers
# ============================================================================

def _payload_to_json(payload: Any, record: WitRecord) -> str:
    """Serialize an @xmlify dataclass payload to JSON for WASM."""
    data: Dict[str, Any] = {}
    for f in record.fields:
        data[f.name] = getattr(payload, f.name, f.python_type())
    return json.dumps(data)


def _json_to_payload(
    result_json: str, response_cls: type, record: WitRecord
) -> Any:
    """Deserialize JSON from WASM into a response dataclass."""
    data = json.loads(result_json)
    kwargs: Dict[str, Any] = {}
    for f in record.fields:
        raw = data.get(f.name, f.python_type())
        kwargs[f.name] = f.python_type(raw)
    return response_cls(**kwargs)


# ============================================================================
# Handler factory
# ============================================================================

def _make_handler(
    module: WasmModule,
    interface: WitInterface,
    request_record: WitRecord,
    response_cls: type,
    response_record: WitRecord,
    registry: WasmRegistry,
    timeout: float,
):
    """
    Create an async handler closure that calls a WASM export.

    The handler:
    1. Serializes payload to JSON
    2. Gets or creates a WASM instance (thread-scoped)
    3. Calls WASM: alloc → copy → handle → read → free
    4. Deserializes JSON to response dataclass
    5. Routes based on _to field in result
    """
    handler_export_name = f"handle_{interface.name.replace('-', '_')}"

    async def handler(payload: Any, metadata: HandlerMetadata) -> HandlerResponse:
        # 1. Serialize payload to JSON
        input_json = _payload_to_json(payload, request_record)

        # 2. Get or create WASM instance
        handle = registry.get_instance(module.name, metadata.thread_id)
        if handle is None:
            # Create new instance for this thread
            caps = module.config.capabilities if module.config else []
            store, instance, memory, alloc_fn, free_fn = module.create_instance(caps)
            handle = WasmInstanceHandle(
                module_name=module.name,
                thread_id=metadata.thread_id,
                store=store,
                instance=instance,
                memory=memory,
                alloc_fn=alloc_fn,
                free_fn=free_fn,
            )
            registry.store_instance(module.name, metadata.thread_id, handle)

        # 3. Call WASM in a thread (wasmtime is synchronous)
        def _call_wasm() -> str:
            from xml_pipeline.wasm.host_functions import read_string, write_string

            # Allocate and write input
            input_ptr, input_len = write_string(
                handle.store, handle.memory, handle.alloc_fn, input_json
            )

            # Call handler
            handler_fn = handle.instance.exports(handle.store).get(handler_export_name)
            result_ptr = handler_fn(handle.store, input_ptr, input_len)

            # Read result: ptr is packed as (ptr << 32 | len) for i64,
            # or just ptr for i32 (length read from first 4 bytes)
            if isinstance(result_ptr, int) and result_ptr > 0xFFFFFFFF:
                # Packed i64: high 32 bits = ptr, low 32 bits = len
                r_ptr = (result_ptr >> 32) & 0xFFFFFFFF
                r_len = result_ptr & 0xFFFFFFFF
            else:
                # i32 ptr — read length from first 4 bytes at ptr
                r_ptr = result_ptr
                len_bytes = handle.memory.read(handle.store, r_ptr, r_ptr + 4)
                r_len = int.from_bytes(bytes(len_bytes), "little")
                r_ptr += 4

            result_str = read_string(handle.store, handle.memory, r_ptr, r_len)

            # Free input memory
            if handle.free_fn:
                handle.free_fn(handle.store, input_ptr)

            return result_str

        try:
            result_json = await asyncio.wait_for(
                asyncio.to_thread(_call_wasm),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            # Force-stop the WASM execution via epoch interrupt
            # and evict the poisoned instance so next call is fresh
            registry.interrupt_instance(module.name, metadata.thread_id)
            raise

        # 4. Deserialize response
        result_dict = json.loads(result_json)

        # Build response payload
        response = _json_to_payload(result_json, response_cls, response_record)

        # 5. Route: check _to field for explicit routing
        to = result_dict.get("_to")
        if to:
            return HandlerResponse(payload=response, to=to)
        return HandlerResponse.respond(payload=response)

    return handler


# ============================================================================
# Public API
# ============================================================================

def register_wasm_tool(pump: Any, config: dict) -> List[str]:
    """
    Register all interfaces from a WASM tool as pump listeners.

    1. Parse WIT → List[WitInterface]
    2. Load WASM → WasmModule (validates exports)
    3. For each interface:
       a. make_dataclass() → request class, apply xmlify()
       b. make_dataclass() → response class, apply xmlify()
       c. Create handler closure (calls WASM)
       d. pump.register(name, handler, request_class, ...)

    Args:
        pump: StreamPump instance
        config: Tool config dict from YAML (or WasmToolConfig fields)

    Returns:
        List of registered listener names
    """
    from xml_pipeline.wasm.registry import get_wasm_registry

    # Build config object
    tool_config = WasmToolConfig(
        name=config["name"],
        wasm_path=config["wasm_path"],
        wit_path=config["wit_path"],
        description=config.get("description", ""),
        capabilities=config.get("capabilities", []),
        memory_limit_mb=config.get("memory_limit_mb", 64),
        timeout_seconds=float(config.get("timeout_seconds", 5.0)),
    )

    # Validate capabilities early (before loading WASM)
    validate_capabilities(tool_config.capabilities)

    # Parse WIT (always works — no wasmtime needed)
    from xml_pipeline.wasm.wit_parser import parse_wit_file
    interfaces = parse_wit_file(tool_config.wit_path)

    # Load WASM module (requires wasmtime)
    if WASMTIME_AVAILABLE:
        module = load_wasm_module(tool_config)
    else:
        # Create a placeholder module for WIT-only registration
        module = WasmModule(
            name=tool_config.name,
            interfaces=interfaces,
            config=tool_config,
        )

    # Register module in registry
    registry = get_wasm_registry()
    registry.register_module(tool_config.name, module)

    # Register each interface as a pump listener
    registered: List[str] = []
    for iface in interfaces:
        # Create dynamic payload classes
        request_cls = _create_payload_class(tool_config.name, iface.request)
        response_cls = _create_payload_class(tool_config.name, iface.response)

        # Create handler
        handler = _make_handler(
            module=module,
            interface=iface,
            request_record=iface.request,
            response_cls=response_cls,
            response_record=iface.response,
            registry=registry,
            timeout=tool_config.timeout_seconds,
        )

        # Listener name: tool_name.interface_name (dots for hierarchy)
        listener_name = f"{tool_config.name}.{iface.name.replace('-', '_')}"

        pump.register(
            listener_name,
            handler,
            request_cls,
            description=tool_config.description or f"WASM tool: {tool_config.name}.{iface.name}",
            timeout=tool_config.timeout_seconds,
        )
        registered.append(listener_name)

        logger.info(
            f"WASM listener registered: {listener_name} "
            f"(request={request_cls.__name__}, response={response_cls.__name__})"
        )

    return registered
