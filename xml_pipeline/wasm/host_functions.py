"""
host_functions.py — Host function implementations for WASM modules.

Provides log, fetch, and kv host functions that get linked into WASM
instances based on declared capabilities.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Try to import wasmtime — all host function linking is guarded
try:
    import wasmtime
    WASMTIME_AVAILABLE = True
except ImportError:
    WASMTIME_AVAILABLE = False


# ============================================================================
# Memory helpers
# ============================================================================

def read_string(store: Any, memory: Any, ptr: int, length: int) -> str:
    """Read a UTF-8 string from WASM linear memory."""
    if ptr < 0 or length < 0:
        raise ValueError(f"Invalid memory access: ptr={ptr}, length={length}")
    mem_size = memory.data_len(store)
    if ptr + length > mem_size:
        raise ValueError(
            f"Out-of-bounds read: ptr={ptr}, length={length}, memory_size={mem_size}"
        )
    data = memory.read(store, ptr, ptr + length)
    return bytes(data).decode("utf-8")


def write_string(
    store: Any, memory: Any, alloc_fn: Any, text: str
) -> tuple[int, int]:
    """
    Write a UTF-8 string to WASM linear memory.

    Calls the module's alloc() export to get space, then copies data in.
    Returns (ptr, len).
    """
    encoded = text.encode("utf-8")
    length = len(encoded)
    ptr = alloc_fn(store, length)
    if ptr <= 0:
        raise ValueError(f"WASM alloc returned invalid pointer: {ptr}")
    mem_size = memory.data_len(store)
    if ptr + length > mem_size:
        raise ValueError(
            f"WASM alloc returned out-of-bounds pointer: ptr={ptr}, "
            f"length={length}, memory_size={mem_size}"
        )
    memory.write(store, encoded, ptr)
    return ptr, length


# ============================================================================
# Host function implementations
# ============================================================================

def _host_log(store: Any, memory: Any, level: int, ptr: int, length: int) -> None:
    """Log a message from WASM at the specified level."""
    try:
        msg = read_string(store, memory, ptr, length)
    except Exception:
        logger.warning("WASM host_log: failed to read string from memory")
        return

    level_map = {0: logging.DEBUG, 1: logging.INFO, 2: logging.WARNING, 3: logging.ERROR}
    log_level = level_map.get(level, logging.INFO)
    logger.log(log_level, f"[WASM] {msg}")


def _host_fetch(
    store: Any, memory: Any, alloc_fn: Any,
    url_ptr: int, url_len: int,
    method_ptr: int, method_len: int,
) -> int:
    """
    Synchronous HTTP fetch from WASM.

    Returns a pointer to JSON result string in WASM memory.
    The result is a packed (ptr << 32 | len) i64 value.
    """
    try:
        url = read_string(store, memory, url_ptr, url_len)
        method = read_string(store, memory, method_ptr, method_len)
    except Exception as e:
        logger.warning(f"WASM host_fetch: failed to read args: {e}")
        result = json.dumps({"error": "Failed to read fetch arguments"})
        ptr, length = write_string(store, memory, alloc_fn, result)
        return (ptr << 32) | length

    try:
        from xml_pipeline.tools.fetch import _do_fetch
        import asyncio

        # Run async fetch synchronously (wasmtime calls are sync)
        loop = asyncio.new_event_loop()
        try:
            fetch_result = loop.run_until_complete(
                _do_fetch(url=url, method=method)
            )
        finally:
            loop.close()

        result = json.dumps({
            "status_code": fetch_result.get("status_code", 0),
            "body": fetch_result.get("body", "")[:65536],  # Cap at 64KB
            "content_type": fetch_result.get("content_type", ""),
            "error": "",
        })
    except Exception as e:
        logger.warning(f"WASM host_fetch error for url={url!r}: {e}")
        result = json.dumps({"error": "Fetch request failed"})

    ptr, length = write_string(store, memory, alloc_fn, result)
    return (ptr << 32) | length


def _host_kv_get(
    store: Any, memory: Any, alloc_fn: Any,
    key_ptr: int, key_len: int,
    kv_namespace: str = "wasm",
) -> int:
    """Get a value from keyvalue store. Returns packed (ptr << 32 | len)."""
    try:
        key = read_string(store, memory, key_ptr, key_len)
    except Exception as e:
        logger.warning(f"WASM host_kv_get: failed to read key: {e}")
        result = json.dumps({"error": "Failed to read key"})
        ptr, length = write_string(store, memory, alloc_fn, result)
        return (ptr << 32) | length

    try:
        from xml_pipeline.tools.keyvalue import kv_get
        import asyncio

        loop = asyncio.new_event_loop()
        try:
            value = loop.run_until_complete(kv_get(key, namespace=kv_namespace))
        finally:
            loop.close()

        result = json.dumps({"value": value})
    except Exception as e:
        logger.warning(f"WASM host_kv_get error for key={key!r}: {e}")
        result = json.dumps({"error": "KV get failed"})

    ptr, length = write_string(store, memory, alloc_fn, result)
    return (ptr << 32) | length


def _host_kv_set(
    store: Any, memory: Any,
    key_ptr: int, key_len: int,
    val_ptr: int, val_len: int,
    kv_namespace: str = "wasm",
) -> None:
    """Set a value in keyvalue store."""
    try:
        key = read_string(store, memory, key_ptr, key_len)
        value = read_string(store, memory, val_ptr, val_len)
    except Exception:
        logger.warning("WASM host_kv_set: failed to read strings from memory")
        return

    try:
        from xml_pipeline.tools.keyvalue import kv_set
        import asyncio

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(kv_set(key, value, namespace=kv_namespace))
        finally:
            loop.close()
    except Exception as e:
        logger.warning(f"WASM host_kv_set error: {e}")


# ============================================================================
# Linker setup
# ============================================================================

def link_host_functions(
    linker: Any,
    capabilities: list[str],
    memory_ref: list,
    alloc_ref: list,
    store_ref: list,
    *,
    module_name_for_kv: str = "wasm",
) -> None:
    """
    Link host functions into a wasmtime Linker based on declared capabilities.

    The memory_ref, alloc_ref, and store_ref are mutable lists used as
    late-binding references — populated after instantiation when we know
    the actual memory/alloc exports.

    Args:
        linker: wasmtime.Linker
        capabilities: list of declared capability names
        memory_ref: [memory] — populated after instantiation
        alloc_ref: [alloc_fn] — populated after instantiation
        store_ref: [store] — populated after instantiation
        module_name_for_kv: module name for KV namespace isolation (default "wasm")
    """
    if not WASMTIME_AVAILABLE:
        return

    module_name = "host"

    if "log" in capabilities:
        @wasmtime.Func  # type: ignore[misc]
        def host_log_wrapper(caller: Any, level: int, ptr: int, length: int) -> None:
            if memory_ref and store_ref:
                _host_log(store_ref[0], memory_ref[0], level, ptr, length)

        linker.define_func(
            module_name, "host_log",
            wasmtime.FuncType(
                [wasmtime.ValType.i32(), wasmtime.ValType.i32(), wasmtime.ValType.i32()],
                [],
            ),
            host_log_wrapper,
        )

    if "fetch" in capabilities:
        def host_fetch_wrapper(
            caller: Any,
            url_ptr: int, url_len: int,
            method_ptr: int, method_len: int,
        ) -> int:
            if memory_ref and alloc_ref and store_ref:
                return _host_fetch(
                    store_ref[0], memory_ref[0], alloc_ref[0],
                    url_ptr, url_len, method_ptr, method_len,
                )
            return 0

        linker.define_func(
            module_name, "host_fetch",
            wasmtime.FuncType(
                [wasmtime.ValType.i32()] * 4,
                [wasmtime.ValType.i64()],
            ),
            host_fetch_wrapper,
        )

    if "kv" in capabilities:
        _kv_ns = f"wasm:{module_name_for_kv}"

        def host_kv_get_wrapper(
            caller: Any, key_ptr: int, key_len: int,
        ) -> int:
            if memory_ref and alloc_ref and store_ref:
                return _host_kv_get(
                    store_ref[0], memory_ref[0], alloc_ref[0],
                    key_ptr, key_len, kv_namespace=_kv_ns,
                )
            return 0

        linker.define_func(
            module_name, "host_kv_get",
            wasmtime.FuncType(
                [wasmtime.ValType.i32(), wasmtime.ValType.i32()],
                [wasmtime.ValType.i64()],
            ),
            host_kv_get_wrapper,
        )

        def host_kv_set_wrapper(
            caller: Any,
            key_ptr: int, key_len: int,
            val_ptr: int, val_len: int,
        ) -> None:
            if memory_ref and store_ref:
                _host_kv_set(
                    store_ref[0], memory_ref[0],
                    key_ptr, key_len, val_ptr, val_len,
                    kv_namespace=_kv_ns,
                )

        linker.define_func(
            module_name, "host_kv_set",
            wasmtime.FuncType(
                [wasmtime.ValType.i32()] * 4,
                [],
            ),
            host_kv_set_wrapper,
        )
