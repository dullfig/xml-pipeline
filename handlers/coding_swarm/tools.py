"""
tools.py — Workspace and build tool handlers for the coding swarm.

Three scoped tools with security constraints:
- workspace-read:  Read artifacts from KV-backed storage
- workspace-write: Write artifacts to KV-backed storage
- compile:         WASI-sandboxed AssemblyScript compilation

Security:
- Artifact name validation (no paths, traversal, or bad extensions)
- WASI sandbox for compilation (no subprocess)
- File size limits (10 MB)
- Per-thread isolation via KV key prefixing
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import re
from typing import Optional

_tools_logger = logging.getLogger(__name__)

from xml_pipeline.message_bus.message_state import HandlerMetadata, HandlerResponse
from xml_pipeline.tools.keyvalue import KVBackend, MemoryBackend

from handlers.coding_swarm.payloads import SwarmMessage

# ── Configuration ──────────────────────────────────────────────────────────

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB
COMPILE_TIMEOUT = 60.0  # seconds

# Artifact name validation
ARTIFACT_NAME_RE = re.compile(r'^[a-zA-Z0-9_.-]+$')
ALLOWED_EXTENSIONS = {".wit", ".ts", ".py", ".json", ".wasm"}


# ── Backend management ────────────────────────────────────────────────────

_backend: Optional[KVBackend] = None


def set_swarm_backend(backend: KVBackend) -> None:
    """Set the KV backend for swarm artifact storage."""
    global _backend
    _backend = backend


def get_swarm_backend() -> KVBackend:
    """Return the current backend, creating MemoryBackend if unset."""
    global _backend
    if _backend is None:
        _backend = MemoryBackend()
    return _backend


def _reset_backend() -> None:
    """Reset backend to unconfigured (for tests)."""
    global _backend
    _backend = None


# ── Artifact name validation ──────────────────────────────────────────────

def _validate_artifact_name(name: str) -> None:
    """Validate an artifact name for storage.

    Raises ValueError if the name is unsafe.
    """
    if not name or name.strip() == "":
        raise ValueError("Empty artifact name")

    if len(name) > 255:
        raise ValueError(f"Artifact name too long ({len(name)} > 255)")

    # Reject slashes, backslashes, absolute paths
    if "/" in name or "\\" in name:
        raise ValueError(f"Slashes not allowed in artifact name: {name}")

    # Reject traversal
    if ".." in name:
        raise ValueError(f"Path traversal not allowed: {name}")

    # Reject absolute paths (Windows drive letters, Unix root)
    if os.path.isabs(name):
        raise ValueError(f"Absolute paths not allowed: {name}")

    # Must match safe pattern
    if not ARTIFACT_NAME_RE.match(name):
        raise ValueError(f"Invalid artifact name (only alphanumeric, _, ., - allowed): {name}")

    # Check extension
    _, ext = os.path.splitext(name)
    if ext and ext.lower() not in ALLOWED_EXTENSIONS:
        raise ValueError(f"Extension '{ext}' not in allowed set: {sorted(ALLOWED_EXTENSIONS)}")


# ── Handlers ───────────────────────────────────────────────────────────────

async def handle_workspace_read(
    payload: SwarmMessage,
    metadata: HandlerMetadata,
) -> Optional[HandlerResponse]:
    """Read an artifact from KV-backed storage.

    Expected ``content`` format: ``path:artifact_name``
    """
    try:
        if not payload.content.startswith("path:"):
            raise ValueError("Content must start with 'path:'")
        name = payload.content[len("path:"):].strip()
        _validate_artifact_name(name)

        backend = get_swarm_backend()
        key = f"{metadata.thread_id}:{name}"
        content = await backend.get(key)

        if content is None:
            raise FileNotFoundError(f"Artifact not found: {name}")

        if len(content.encode("utf-8")) > MAX_FILE_SIZE:
            raise ValueError(f"Artifact exceeds {MAX_FILE_SIZE} byte limit")

        return HandlerResponse.respond(
            payload=SwarmMessage(
                role="read-result",
                tool_name=payload.tool_name,
                content=content,
                status="success",
                error="",
                iteration=payload.iteration,
                phase=payload.phase,
            ),
        )
    except Exception as exc:
        _tools_logger.exception("Workspace read failed for '%s'", payload.tool_name)
        return HandlerResponse.respond(
            payload=SwarmMessage(
                role="read-result",
                tool_name=payload.tool_name,
                content="",
                status="error",
                error=f"Read failed: {type(exc).__name__}",
                iteration=payload.iteration,
                phase=payload.phase,
            ),
        )


async def handle_workspace_write(
    payload: SwarmMessage,
    metadata: HandlerMetadata,
) -> Optional[HandlerResponse]:
    """Write an artifact to KV-backed storage.

    Expected ``content`` format::

        path:artifact_name
        <file content follows on remaining lines>
    """
    try:
        if not payload.content.startswith("path:"):
            raise ValueError("Content must start with 'path:'")

        # Split first line (path) from rest (file content)
        first_newline = payload.content.index("\n")
        name = payload.content[len("path:"):first_newline].strip()
        file_content = payload.content[first_newline + 1:]

        if len(file_content.encode("utf-8")) > MAX_FILE_SIZE:
            raise ValueError(f"File exceeds {MAX_FILE_SIZE} byte limit")

        _validate_artifact_name(name)

        backend = get_swarm_backend()
        key = f"{metadata.thread_id}:{name}"
        await backend.set(key, file_content)

        return HandlerResponse.respond(
            payload=SwarmMessage(
                role="write-result",
                tool_name=payload.tool_name,
                content=name,
                status="success",
                error="",
                iteration=payload.iteration,
                phase=payload.phase,
            ),
        )
    except Exception as exc:
        _tools_logger.exception("Workspace write failed for '%s'", payload.tool_name)
        return HandlerResponse.respond(
            payload=SwarmMessage(
                role="write-result",
                tool_name=payload.tool_name,
                content="",
                status="error",
                error=f"Write failed: {type(exc).__name__}",
                iteration=payload.iteration,
                phase=payload.phase,
            ),
        )


async def handle_compile(
    payload: SwarmMessage,
    metadata: HandlerMetadata,
) -> Optional[HandlerResponse]:
    """Compile AssemblyScript source to WASM via WASI-sandboxed asc.wasm.

    Reads .ts source from KV storage, compiles via WasiCompiler, and
    stores the resulting .wasm as base64 in KV.
    """
    try:
        from xml_pipeline.wasm.compiler import get_compiler

        backend = get_swarm_backend()
        prefix = f"{metadata.thread_id}:"

        # Read source from KV
        src_key = f"{prefix}{payload.tool_name}.ts"
        source = await backend.get(src_key)
        if source is None:
            raise FileNotFoundError(
                f"Source not found in KV: {payload.tool_name}.ts"
            )

        compiler = get_compiler()

        # Run compilation in a thread with timeout
        compile_result = await asyncio.wait_for(
            asyncio.to_thread(
                compiler.compile, {"main.ts": source}
            ),
            timeout=COMPILE_TIMEOUT,
        )

        if compile_result.success:
            # Store compiled .wasm as base64
            wasm_b64 = base64.b64encode(compile_result.wasm_bytes).decode("ascii")
            await backend.set(f"{prefix}{payload.tool_name}.wasm", wasm_b64)

            return HandlerResponse.respond(
                payload=SwarmMessage(
                    role="compile-result",
                    tool_name=payload.tool_name,
                    content=compile_result.stdout,
                    status="success",
                    error="",
                    iteration=payload.iteration,
                    phase=payload.phase,
                ),
            )
        else:
            error_detail = compile_result.error
            if compile_result.stderr:
                error_detail = f"{error_detail}\n{compile_result.stderr}"
            return HandlerResponse.respond(
                payload=SwarmMessage(
                    role="compile-result",
                    tool_name=payload.tool_name,
                    content=compile_result.stdout,
                    status="error",
                    error=error_detail,
                    iteration=payload.iteration,
                    phase=payload.phase,
                ),
            )

    except asyncio.TimeoutError:
        # Interrupt the compiler to free resources
        try:
            from xml_pipeline.wasm.compiler import get_compiler
            get_compiler().interrupt()
        except Exception:
            pass
        return HandlerResponse.respond(
            payload=SwarmMessage(
                role="compile-result",
                tool_name=payload.tool_name,
                content="",
                status="error",
                error=f"Compilation timed out after {COMPILE_TIMEOUT}s",
                iteration=payload.iteration,
                phase=payload.phase,
            ),
        )
    except Exception as exc:
        _tools_logger.exception("Compilation failed for '%s'", payload.tool_name)
        return HandlerResponse.respond(
            payload=SwarmMessage(
                role="compile-result",
                tool_name=payload.tool_name,
                content="",
                status="error",
                error=f"Compilation failed: {type(exc).__name__}",
                iteration=payload.iteration,
                phase=payload.phase,
            ),
        )
