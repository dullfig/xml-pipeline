"""
Shell worker — long-lived worker process for OS-isolated shell execution.

Follows the worker registry contract: target(inbox, outbox, **kwargs).
Communicates with xp-exec (setuid helper binary) via JSON lines over
stdin/stdout. Each request spawns one xp-exec subprocess.

Security:
- TOTP secret held only in this process (never in pump process)
- PR_SET_DUMPABLE=0 on Linux blocks /proc/pid/mem reads
- xp-exec is stateless: one subprocess.run() per request
"""

from __future__ import annotations

import json
import subprocess
import sys
import uuid
from typing import Any, Dict, Optional


# Constants
PR_SET_DUMPABLE = 4
MAX_OUTPUT_SIZE = 1024 * 1024  # 1 MB
MAX_TIMEOUT = 300

# Environment variables that must never be overridden by untrusted callers.
# These can influence dynamic linking, library loading, or privilege escalation.
BLOCKED_ENV_VARS = frozenset({
    "LD_PRELOAD", "LD_LIBRARY_PATH", "LD_AUDIT",
    "DYLD_INSERT_LIBRARIES", "DYLD_LIBRARY_PATH",
    "PATH", "HOME", "USER", "SHELL", "LOGNAME",
    "PYTHONPATH", "PYTHONSTARTUP", "PYTHONHOME",
    "NODE_OPTIONS", "NODE_PATH",
    "BASH_ENV", "ENV", "CDPATH", "GLOBIGNORE",
    "IFS", "SHELLOPTS", "BASHOPTS",
})


def _set_undumpable() -> None:
    """Block /proc/pid/mem reads on Linux via PR_SET_DUMPABLE=0."""
    if sys.platform != "linux":
        return
    try:
        import ctypes
        libc = ctypes.CDLL("libc.so.6")
        libc.prctl(PR_SET_DUMPABLE, 0)
    except Exception:
        pass  # Best-effort; not fatal


def _execute_via_xp_exec(
    xp_exec_path: str,
    totp_token: str,
    os_user: str,
    command: str,
    timeout: int,
    cwd: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Execute a command via the xp-exec helper binary.

    Writes a JSON request to xp-exec's stdin, reads a JSON response
    from its stdout. One subprocess.run() per request (stateless).

    Returns:
        Response dict with exit_code, stdout, stderr, timed_out, error.
    """
    request = {
        "totp": totp_token,
        "user": os_user,
        "command": command,
        "timeout": timeout,
    }
    if cwd:
        request["cwd"] = cwd
    if env:
        request["env"] = env

    request_line = json.dumps(request) + "\n"

    try:
        result = subprocess.run(
            [xp_exec_path],
            input=request_line,
            capture_output=True,
            text=True,
            timeout=timeout + 5,  # Grace period beyond command timeout
        )
    except FileNotFoundError:
        return {
            "exit_code": -1,
            "stdout": "",
            "stderr": "",
            "timed_out": False,
            "error": f"xp-exec not found: {xp_exec_path}",
        }
    except subprocess.TimeoutExpired:
        return {
            "exit_code": -1,
            "stdout": "",
            "stderr": "",
            "timed_out": True,
            "error": "xp-exec process timed out",
        }
    except PermissionError:
        return {
            "exit_code": -1,
            "stdout": "",
            "stderr": "",
            "timed_out": False,
            "error": f"Permission denied: {xp_exec_path}",
        }

    # Parse xp-exec's JSON response from stdout
    try:
        response = json.loads(result.stdout.strip())
    except (json.JSONDecodeError, ValueError):
        return {
            "exit_code": -1,
            "stdout": result.stdout[:MAX_OUTPUT_SIZE],
            "stderr": result.stderr[:MAX_OUTPUT_SIZE],
            "timed_out": False,
            "error": f"Invalid xp-exec response (exit {result.returncode})",
        }

    # Truncate output if needed
    if "stdout" in response:
        response["stdout"] = response["stdout"][:MAX_OUTPUT_SIZE]
    if "stderr" in response:
        response["stderr"] = response["stderr"][:MAX_OUTPUT_SIZE]

    return response


def shell_worker_main(
    inbox: Any,
    outbox: Any,
    *,
    totp_secret: str = "",
    xp_exec_path: str = "/usr/local/bin/xp-exec",
) -> None:
    """
    Shell worker entry point (worker registry contract).

    Args:
        inbox: multiprocessing.Queue — receives request dicts
        outbox: multiprocessing.Queue — sends response dicts
        totp_secret: TOTP secret for authenticating with xp-exec
        xp_exec_path: Path to the xp-exec binary
    """
    _set_undumpable()

    while True:
        try:
            request = inbox.get()
        except Exception:
            break

        # Sentinel: None means stop
        if request is None:
            break

        request_id = request.get("request_id", str(uuid.uuid4()))
        command = request.get("command", "")
        os_user = request.get("os_user", "")
        timeout = min(max(1, request.get("timeout", 30)), MAX_TIMEOUT)
        cwd = request.get("cwd")
        raw_env = request.get("env")

        # Sanitize env: strip blocked variables that could escalate privileges
        env = None
        if raw_env and isinstance(raw_env, dict):
            env = {
                k: v for k, v in raw_env.items()
                if k.upper() not in BLOCKED_ENV_VARS
            }

        if not command:
            outbox.put({
                "request_id": request_id,
                "exit_code": -1,
                "stdout": "",
                "stderr": "",
                "timed_out": False,
                "error": "Empty command",
            })
            continue

        if not os_user:
            outbox.put({
                "request_id": request_id,
                "exit_code": -1,
                "stdout": "",
                "stderr": "",
                "timed_out": False,
                "error": "No os_user specified",
            })
            continue

        # Compute TOTP token for this request
        totp_token = ""
        if totp_secret:
            try:
                from xml_pipeline.crypto.totp import compute_totp
                totp_token = compute_totp(totp_secret)
            except Exception as e:
                outbox.put({
                    "request_id": request_id,
                    "exit_code": -1,
                    "stdout": "",
                    "stderr": "",
                    "timed_out": False,
                    "error": f"TOTP computation failed: {e}",
                })
                continue

        # Execute via xp-exec
        result = _execute_via_xp_exec(
            xp_exec_path=xp_exec_path,
            totp_token=totp_token,
            os_user=os_user,
            command=command,
            timeout=timeout,
            cwd=cwd,
            env=env,
        )
        result["request_id"] = request_id
        outbox.put(result)
