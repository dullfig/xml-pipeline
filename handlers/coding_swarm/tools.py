"""
tools.py — Workspace and build tool handlers for the coding swarm.

Three scoped tools with security constraints:
- workspace-read:  Read artifacts from KV-backed storage
- workspace-write: Write artifacts to KV-backed storage
- build-run:       Execute whitelisted commands only

Security:
- Artifact name validation (no paths, traversal, or bad extensions)
- Command whitelist enforcement
- File size limits (10 MB)
- Command timeout (15 s)
- Per-thread isolation via KV key prefixing
"""

from __future__ import annotations

import asyncio
import os
import re
import shlex
import tempfile
from pathlib import Path
from typing import Optional

from xml_pipeline.message_bus.message_state import HandlerMetadata, HandlerResponse
from xml_pipeline.tools.keyvalue import KVBackend, MemoryBackend

from handlers.coding_swarm.payloads import SwarmMessage

# ── Configuration ──────────────────────────────────────────────────────────

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB
COMMAND_TIMEOUT = 15  # seconds
ALLOWED_COMMANDS = {"asc", "npm", "npx", "node", "pytest"}

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
        return HandlerResponse.respond(
            payload=SwarmMessage(
                role="read-result",
                tool_name=payload.tool_name,
                content="",
                status="error",
                error=str(exc),
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
        return HandlerResponse.respond(
            payload=SwarmMessage(
                role="write-result",
                tool_name=payload.tool_name,
                content="",
                status="error",
                error=str(exc),
                iteration=payload.iteration,
                phase=payload.phase,
            ),
        )


async def handle_build_run(
    payload: SwarmMessage,
    metadata: HandlerMetadata,
) -> Optional[HandlerResponse]:
    """Run a whitelisted command against thread artifacts.

    Expected ``content`` format: ``command arg1 arg2 ...``

    Artifacts are extracted to a temporary directory, the command runs there,
    and any new/modified files are stored back in KV.
    """
    try:
        parts = shlex.split(payload.content.strip())
        if not parts:
            raise ValueError("Empty command")

        cmd = parts[0]
        if cmd not in ALLOWED_COMMANDS:
            raise ValueError(
                f"Command '{cmd}' not in whitelist: {sorted(ALLOWED_COMMANDS)}"
            )

        backend = get_swarm_backend()
        prefix = f"{metadata.thread_id}:"

        # Fetch all thread artifacts
        all_keys = await backend.keys(f"{prefix}*")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            # Extract artifacts to tmpdir
            for key in all_keys:
                artifact_name = key[len(prefix):]
                content = await backend.get(key)
                if content is not None:
                    target = tmpdir_path / artifact_name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(content, encoding="utf-8")

            # Snapshot mtimes before run
            before_mtimes = {}
            for f in tmpdir_path.iterdir():
                if f.is_file():
                    before_mtimes[f.name] = f.stat().st_mtime

            proc = await asyncio.create_subprocess_exec(
                *parts,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(tmpdir_path),
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=COMMAND_TIMEOUT,
                )
            except asyncio.TimeoutError:
                proc.kill()
                raise TimeoutError(f"Command timed out after {COMMAND_TIMEOUT}s")

            # Scan for new/modified files and store back
            for f in tmpdir_path.iterdir():
                if f.is_file():
                    old_mtime = before_mtimes.get(f.name)
                    if old_mtime is None or f.stat().st_mtime > old_mtime:
                        new_content = f.read_text(encoding="utf-8")
                        await backend.set(f"{prefix}{f.name}", new_content)

            output = stdout.decode("utf-8", errors="replace")
            if proc.returncode != 0:
                err_output = stderr.decode("utf-8", errors="replace")
                return HandlerResponse.respond(
                    payload=SwarmMessage(
                        role="build-result",
                        tool_name=payload.tool_name,
                        content=output,
                        status="error",
                        error=f"Exit code {proc.returncode}: {err_output}",
                        iteration=payload.iteration,
                        phase=payload.phase,
                    ),
                )

            return HandlerResponse.respond(
                payload=SwarmMessage(
                    role="build-result",
                    tool_name=payload.tool_name,
                    content=output,
                    status="success",
                    error="",
                    iteration=payload.iteration,
                    phase=payload.phase,
                ),
            )
    except Exception as exc:
        return HandlerResponse.respond(
            payload=SwarmMessage(
                role="build-result",
                tool_name=payload.tool_name,
                content="",
                status="error",
                error=str(exc),
                iteration=payload.iteration,
                phase=payload.phase,
            ),
        )
