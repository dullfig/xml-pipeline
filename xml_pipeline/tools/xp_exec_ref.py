"""
xp-exec reference implementation — Python version for testing.

NOT for production use. Production xp-exec is a compiled setuid C binary
that drops privileges to the target OS user before executing commands.

This reference implementation:
- Reads one JSON line from stdin
- Verifies TOTP against secret from XP_EXEC_TOTP_SECRET env var
- Verifies user against allowlist from XP_EXEC_ALLOWED_USERS env var
- Executes command via subprocess.run (as current user — no setuid)
- Writes one JSON line to stdout
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import IO, Any, Dict, Set


MAX_OUTPUT_SIZE = 1024 * 1024  # 1 MB


def run_xp_exec(
    totp_secret: str,
    allowed_users: Set[str],
    *,
    input_stream: IO[str] | None = None,
    output_stream: IO[str] | None = None,
) -> None:
    """
    Process one xp-exec request.

    Args:
        totp_secret: TOTP secret for verification (empty = skip TOTP check)
        allowed_users: Set of allowed OS usernames
        input_stream: Where to read the JSON request (default: sys.stdin)
        output_stream: Where to write the JSON response (default: sys.stdout)
    """
    inp = input_stream or sys.stdin
    out = output_stream or sys.stdout

    try:
        line = inp.readline()
        if not line.strip():
            _write_error(out, "Empty request")
            return

        try:
            request = json.loads(line)
        except json.JSONDecodeError as e:
            _write_error(out, f"Invalid JSON: {e}")
            return

        # Verify TOTP
        if totp_secret:
            token = request.get("totp", "")
            if not token:
                _write_error(out, "TOTP token required")
                return
            try:
                from xml_pipeline.crypto.totp import verify_totp
                if not verify_totp(totp_secret, token):
                    _write_error(out, "Invalid TOTP token")
                    return
            except Exception as e:
                _write_error(out, f"TOTP verification failed: {e}")
                return

        # Verify user
        user = request.get("user", "")
        if not user:
            _write_error(out, "No user specified")
            return
        if allowed_users and user not in allowed_users:
            _write_error(out, f"User '{user}' not in allowed list")
            return

        # Execute command
        command = request.get("command", "")
        if not command:
            _write_error(out, "No command specified")
            return

        timeout = min(max(1, request.get("timeout", 30)), 300)
        cwd = request.get("cwd")
        env = request.get("env")

        # Build environment
        exec_env = dict(os.environ)
        if env:
            exec_env.update(env)

        timed_out = False
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=cwd,
                env=exec_env,
            )
            stdout = result.stdout[:MAX_OUTPUT_SIZE]
            stderr = result.stderr[:MAX_OUTPUT_SIZE]
            exit_code = result.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
            stdout = ""
            stderr = "Command timed out"
            exit_code = -1
        except Exception as e:
            stdout = ""
            stderr = str(e)
            exit_code = -1

        response = {
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "timed_out": timed_out,
            "error": "",
        }
        out.write(json.dumps(response) + "\n")
        out.flush()

    except Exception as e:
        _write_error(out, f"Internal error: {e}")


def _write_error(out: IO[str], message: str) -> None:
    """Write an error response to the output stream."""
    response = {
        "exit_code": -1,
        "stdout": "",
        "stderr": "",
        "timed_out": False,
        "error": message,
    }
    out.write(json.dumps(response) + "\n")
    out.flush()


if __name__ == "__main__":
    secret = os.environ.get("XP_EXEC_TOTP_SECRET", "")
    users_str = os.environ.get("XP_EXEC_ALLOWED_USERS", "")
    users = set(users_str.split(",")) if users_str else set()
    run_xp_exec(secret, users)
