"""
tools.py — Workspace and build tool handlers for the coding swarm.

Three scoped tools with security constraints:
- workspace-read:  Read files from a sandboxed workspace directory
- workspace-write: Write files to the sandboxed workspace directory
- build-run:       Execute whitelisted commands only

Security:
- Path traversal prevention (rejects ``..``, absolute paths)
- Command whitelist enforcement
- File size limits (10 MB)
- Command timeout (60 s)
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Optional

from xml_pipeline.message_bus.message_state import HandlerMetadata, HandlerResponse

from handlers.coding_swarm.payloads import SwarmMessage

# ── Configuration ──────────────────────────────────────────────────────────

# Default workspace root — overridable for testing via set_workspace_root()
_workspace_root: Path = Path("examples/coding-swarm/workspace").resolve()

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB
COMMAND_TIMEOUT = 60  # seconds
ALLOWED_COMMANDS = {"asc", "npm", "npx", "node", "pytest"}


def set_workspace_root(path: Path) -> None:
    """Override workspace root (for tests)."""
    global _workspace_root
    _workspace_root = path.resolve()


def get_workspace_root() -> Path:
    """Return the current workspace root."""
    return _workspace_root


# ── Path validation ────────────────────────────────────────────────────────

def _validate_path(relative: str) -> Path:
    """Validate and resolve a relative path within the workspace.

    Raises ValueError if the path is unsafe.
    """
    if not relative or relative.strip() == "":
        raise ValueError("Empty path")

    # Reject absolute paths
    if os.path.isabs(relative):
        raise ValueError(f"Absolute paths not allowed: {relative}")

    # Reject path traversal components
    parts = Path(relative).parts
    if ".." in parts:
        raise ValueError(f"Path traversal not allowed: {relative}")

    resolved = (_workspace_root / relative).resolve()

    # Final containment check
    if not str(resolved).startswith(str(_workspace_root)):
        raise ValueError(f"Path escapes workspace: {relative}")

    return resolved


# ── Handlers ───────────────────────────────────────────────────────────────

async def handle_workspace_read(
    payload: SwarmMessage,
    metadata: HandlerMetadata,
) -> Optional[HandlerResponse]:
    """Read a file from the sandboxed workspace.

    Expected ``content`` format: ``path:relative/file.ext``
    """
    try:
        if not payload.content.startswith("path:"):
            raise ValueError("Content must start with 'path:'")
        relative = payload.content[len("path:"):].strip()
        target = _validate_path(relative)

        if not target.exists():
            raise FileNotFoundError(f"File not found: {relative}")
        if not target.is_file():
            raise ValueError(f"Not a file: {relative}")

        content = target.read_text(encoding="utf-8")
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
    """Write a file to the sandboxed workspace.

    Expected ``content`` format::

        path:relative/file.ext
        <file content follows on remaining lines>
    """
    try:
        if not payload.content.startswith("path:"):
            raise ValueError("Content must start with 'path:'")

        # Split first line (path) from rest (file content)
        first_newline = payload.content.index("\n")
        relative = payload.content[len("path:"):first_newline].strip()
        file_content = payload.content[first_newline + 1:]

        if len(file_content.encode("utf-8")) > MAX_FILE_SIZE:
            raise ValueError(f"File exceeds {MAX_FILE_SIZE} byte limit")

        target = _validate_path(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(file_content, encoding="utf-8")

        return HandlerResponse.respond(
            payload=SwarmMessage(
                role="write-result",
                tool_name=payload.tool_name,
                content=relative,
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
    """Run a whitelisted command in the workspace directory.

    Expected ``content`` format: ``command arg1 arg2 ...``
    """
    try:
        parts = payload.content.strip().split()
        if not parts:
            raise ValueError("Empty command")

        cmd = parts[0]
        if cmd not in ALLOWED_COMMANDS:
            raise ValueError(
                f"Command '{cmd}' not in whitelist: {sorted(ALLOWED_COMMANDS)}"
            )

        proc = await asyncio.create_subprocess_exec(
            *parts,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(_workspace_root),
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=COMMAND_TIMEOUT,
            )
        except asyncio.TimeoutError:
            proc.kill()
            raise TimeoutError(f"Command timed out after {COMMAND_TIMEOUT}s")

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
