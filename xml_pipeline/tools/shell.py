"""
Shell tool - OS-isolated command execution.

Provides controlled command execution via xp-exec (setuid helper binary)
that drops privileges to a per-peer-table OS user before executing.

The @tool functions remain disabled (defense-in-depth). The handler
(handle_shell) delegates to the shell worker process which communicates
with xp-exec.

Note: Do NOT use `from __future__ import annotations` here — it breaks
xmlable's type introspection on @xmlify dataclasses.
"""

import asyncio
import shlex
import sys
from dataclasses import dataclass
from typing import Optional, List, Dict, Any

from third_party.xmlable import xmlify

from xml_pipeline.message_bus.message_state import HandlerMetadata, HandlerResponse

from .base import tool, ToolResult


# ── Security gate ────────────────────────────────────────────────────────
# Flip to True only after security audit confirms no attack vectors.
TOOL_ENABLED = False
_DISABLED_MSG = "Shell tool is disabled pending security audit. See docs/readiness-gaps.md."

# Security configuration
ALLOWED_COMMANDS: List[str] = []  # Empty = check blocklist only
BLOCKED_COMMANDS: List[str] = [
    # Destructive commands
    "rm", "rmdir", "del", "erase", "format", "mkfs", "dd",
    # System modification
    "shutdown", "reboot", "init", "systemctl",
    # Network tools that could be abused
    "nc", "netcat", "ncat",
    # Privilege escalation
    "sudo", "su", "doas", "runas",
    # Shell escapes
    "bash", "sh", "zsh", "fish", "cmd", "powershell", "pwsh",
]
DEFAULT_TIMEOUT = 30
MAX_TIMEOUT = 300
MAX_OUTPUT_SIZE = 1024 * 1024  # 1 MB


def configure_allowed_commands(commands: List[str]) -> None:
    """Set an allowlist of commands (empty = blocklist mode)."""
    global ALLOWED_COMMANDS
    ALLOWED_COMMANDS = commands


def configure_blocked_commands(commands: List[str]) -> None:
    """Set additional blocked commands."""
    global BLOCKED_COMMANDS
    BLOCKED_COMMANDS = commands


def _validate_command(command: str) -> Optional[str]:
    """Validate command against allow/block lists. Returns error or None."""
    try:
        # Parse command to get the executable
        parts = shlex.split(command)
        if not parts:
            return "Empty command"
        
        executable = parts[0].lower()

        # Strip path to get just the command name
        if "/" in executable or "\\" in executable:
            executable = executable.split("/")[-1].split("\\")[-1]

        # Strip common executable extensions
        for ext in (".exe", ".cmd", ".bat", ".com", ".ps1"):
            if executable.endswith(ext):
                executable = executable[:-len(ext)]
                break

        # Check allowlist first (if configured)
        if ALLOWED_COMMANDS:
            if executable not in ALLOWED_COMMANDS:
                return f"Command '{executable}' not in allowlist"

        # Check blocklist — both exact match and suffix match.
        # Suffix match catches mangled Windows paths where shlex.split
        # eats backslashes: C:\Windows\System32\cmd.exe → "windowssystem32cmd"
        for blocked in BLOCKED_COMMANDS:
            if executable == blocked or executable.endswith(blocked):
                return f"Command '{blocked}' is blocked for security"
        
        # Check for shell operators that could be dangerous
        dangerous_operators = [";", "&&", "||", "|", "`", "$(", "${"]
        for op in dangerous_operators:
            if op in command:
                return f"Shell operator '{op}' not allowed"
        
        return None
    except ValueError as e:
        return f"Invalid command syntax: {e}"


@tool
async def run_command(
    command: str,
    timeout: int = DEFAULT_TIMEOUT,
    cwd: Optional[str] = None,
    env: Optional[dict] = None,
) -> ToolResult:
    """
    Execute a shell command (sandboxed).

    Args:
        command: Command to execute
        timeout: Timeout in seconds (default: 30, max: 300)
        cwd: Working directory (optional)
        env: Environment variables to add (optional)

    Returns:
        exit_code: Process exit code
        stdout: Standard output
        stderr: Standard error
        timed_out: True if command was killed due to timeout

    Security:
        - Dangerous commands are blocked
        - Shell operators (;, &&, |, etc.) are blocked
        - Timeout enforced
        - Output size limited to 1 MB
    """
    if not TOOL_ENABLED:
        return ToolResult(success=False, error=_DISABLED_MSG)

    # Validate command
    if error := _validate_command(command):
        return ToolResult(success=False, error=error)
    
    # Clamp timeout
    timeout = min(max(1, timeout), MAX_TIMEOUT)
    
    try:
        # Parse command into args (no shell)
        args = shlex.split(command)
        
        # Create subprocess
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=env,
        )
        
        timed_out = False
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            timed_out = True
            stdout = b""
            stderr = b"Command timed out"
        
        # Decode output, truncating if too large
        stdout_str = stdout[:MAX_OUTPUT_SIZE].decode("utf-8", errors="replace")
        stderr_str = stderr[:MAX_OUTPUT_SIZE].decode("utf-8", errors="replace")
        
        truncated = len(stdout) > MAX_OUTPUT_SIZE or len(stderr) > MAX_OUTPUT_SIZE
        
        return ToolResult(
            success=proc.returncode == 0 and not timed_out,
            data={
                "exit_code": proc.returncode,
                "stdout": stdout_str,
                "stderr": stderr_str,
                "timed_out": timed_out,
                "truncated": truncated,
            }
        )
    except FileNotFoundError:
        return ToolResult(success=False, error=f"Command not found: {args[0]}")
    except PermissionError:
        return ToolResult(success=False, error=f"Permission denied: {args[0]}")
    except Exception as e:
        return ToolResult(success=False, error=f"Execution error: {e}")


# ── Payload classes (for listener mode) ─────────────────────────────────


@xmlify
@dataclass
class ShellCommand:
    """Request to execute a shell command via OS-isolated xp-exec."""
    command: str
    timeout: int = 30
    cwd: str = ""


@xmlify
@dataclass
class ShellResult:
    """Result of a shell command execution."""
    command: str
    exit_code: int
    stdout: str
    stderr: str
    timed_out: int = 0   # 0/1 (xmlify-safe bool)
    error: str = ""


# ── OS user resolution ──────────────────────────────────────────────────


def _resolve_os_user(metadata: HandlerMetadata) -> Optional[str]:
    """
    Resolve the OS user for this thread from the peer table config.

    Looks up the thread's peer table → os_user mapping from the pump's
    shell config. Falls back to default_os_user if no table mapping.
    Returns None if shell is not configured.
    """
    try:
        from xml_pipeline.message_bus.singleton import get_stream_pump
        pump = get_stream_pump()
    except Exception:
        return None

    if not pump or not pump._shell_config:
        return None

    shell_cfg = pump._shell_config
    table_os_users: Dict[str, str] = shell_cfg.get("table_os_users", {})

    # Resolve thread's peer table
    from xml_pipeline.message_bus.thread_registry import get_registry
    registry = get_registry()
    table_name = registry.get_table_for_thread(metadata.thread_id)

    if table_name and table_name in table_os_users:
        return table_os_users[table_name]

    return shell_cfg.get("default_os_user") or None


# ── Handler (listener mode) ─────────────────────────────────────────────


async def handle_shell(
    payload: ShellCommand, metadata: HandlerMetadata
) -> HandlerResponse:
    """Handler for the shell listener — delegates to shell worker via pump."""
    # Linux-only gate
    if sys.platform != "linux":
        return HandlerResponse.respond(
            payload=ShellResult(
                command=payload.command,
                exit_code=-1,
                stdout="",
                stderr="",
                error="Shell execution requires Linux (OS-level isolation via xp-exec)",
            )
        )

    # Resolve OS user
    os_user = _resolve_os_user(metadata)
    if not os_user:
        return HandlerResponse.respond(
            payload=ShellResult(
                command=payload.command,
                exit_code=-1,
                stdout="",
                stderr="",
                error="No OS user configured for this thread's peer table",
            )
        )

    # Delegate to pump's shell worker
    try:
        from xml_pipeline.message_bus.singleton import get_stream_pump
        pump = get_stream_pump()
        if not pump:
            raise RuntimeError("No pump available")

        result = await pump._shell_execute(
            command=payload.command,
            os_user=os_user,
            timeout=payload.timeout,
            cwd=payload.cwd or None,
        )

        return HandlerResponse.respond(
            payload=ShellResult(
                command=payload.command,
                exit_code=result.get("exit_code", -1),
                stdout=result.get("stdout", ""),
                stderr=result.get("stderr", ""),
                timed_out=1 if result.get("timed_out", False) else 0,
                error=result.get("error", ""),
            )
        )
    except Exception as e:
        return HandlerResponse.respond(
            payload=ShellResult(
                command=payload.command,
                exit_code=-1,
                stdout="",
                stderr="",
                error=f"Shell execution failed: {e}",
            )
        )
