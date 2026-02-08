"""
Shell tool - sandboxed command execution.

Provides controlled command execution with security restrictions.

DISABLED: This tool is disabled pending security audit. All @tool functions
return an error immediately. Validation logic remains testable.
See docs/readiness-gaps.md for details.
"""

from __future__ import annotations

import asyncio
import shlex
from typing import Optional, List

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
