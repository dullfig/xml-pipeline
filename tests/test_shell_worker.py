"""Tests for shell worker process and xp-exec reference implementation.

Tests the worker protocol, xp-exec JSON format, TOTP authentication,
and reference implementation command execution.
"""

import io
import json
import queue
import sys
from unittest.mock import patch, MagicMock

import pytest

from xml_pipeline.tools.shell_worker import (
    PR_SET_DUMPABLE,
    MAX_OUTPUT_SIZE,
    MAX_TIMEOUT,
    _set_undumpable,
    _execute_via_xp_exec,
    shell_worker_main,
)
from xml_pipeline.tools.xp_exec_ref import run_xp_exec, _write_error


# ═══════════════════════════════════════════════════════════════════════════
# Helper: Queue adapter for testing
# ═══════════════════════════════════════════════════════════════════════════

class QueueAdapter:
    """Simple queue for testing (matches multiprocessing.Queue interface)."""
    def __init__(self):
        self._q = queue.Queue()

    def put(self, item):
        self._q.put(item)

    def get(self, timeout=None):
        return self._q.get(timeout=timeout)

    def empty(self):
        return self._q.empty()


# ═══════════════════════════════════════════════════════════════════════════
# TestConstants
# ═══════════════════════════════════════════════════════════════════════════

class TestConstants:
    def test_pr_set_dumpable(self) -> None:
        assert PR_SET_DUMPABLE == 4

    def test_max_output_size(self) -> None:
        assert MAX_OUTPUT_SIZE == 1024 * 1024

    def test_max_timeout(self) -> None:
        assert MAX_TIMEOUT == 300


# ═══════════════════════════════════════════════════════════════════════════
# TestSetUndumpable
# ═══════════════════════════════════════════════════════════════════════════

class TestSetUndumpable:
    def test_skipped_on_non_linux(self) -> None:
        """Should be a no-op on Windows."""
        with patch("xml_pipeline.tools.shell_worker.sys") as mock_sys:
            mock_sys.platform = "win32"
            _set_undumpable()  # Should not raise

    @pytest.mark.skipif(sys.platform != "linux", reason="Linux only")
    def test_calls_prctl_on_linux(self) -> None:
        with patch("ctypes.CDLL") as mock_cdll:
            mock_libc = MagicMock()
            mock_cdll.return_value = mock_libc
            _set_undumpable()
            mock_cdll.assert_called_once_with("libc.so.6")
            mock_libc.prctl.assert_called_once_with(PR_SET_DUMPABLE, 0)


# ═══════════════════════════════════════════════════════════════════════════
# TestExecuteViaXpExec — xp-exec subprocess protocol
# ═══════════════════════════════════════════════════════════════════════════

class TestExecuteViaXpExec:
    """Test _execute_via_xp_exec subprocess communication."""

    def test_successful_execution(self) -> None:
        response_json = json.dumps({
            "exit_code": 0,
            "stdout": "hello world\n",
            "stderr": "",
            "timed_out": False,
            "error": "",
        })
        mock_result = MagicMock()
        mock_result.stdout = response_json
        mock_result.stderr = ""
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            result = _execute_via_xp_exec(
                xp_exec_path="/usr/local/bin/xp-exec",
                totp_token="123456",
                os_user="xp-sandbox",
                command="echo hello world",
                timeout=30,
            )

        assert result["exit_code"] == 0
        assert result["stdout"] == "hello world\n"
        assert result["error"] == ""

        # Verify subprocess.run was called correctly
        mock_run.assert_called_once()
        call_args = mock_run.call_args
        assert call_args[0][0] == ["/usr/local/bin/xp-exec"]
        request = json.loads(call_args[1]["input"])
        assert request["totp"] == "123456"
        assert request["user"] == "xp-sandbox"
        assert request["command"] == "echo hello world"

    def test_xp_exec_not_found(self) -> None:
        with patch("subprocess.run", side_effect=FileNotFoundError):
            result = _execute_via_xp_exec(
                "/nonexistent/xp-exec", "tok", "user", "cmd", 30,
            )
        assert result["exit_code"] == -1
        assert "not found" in result["error"]

    def test_xp_exec_timeout(self) -> None:
        import subprocess
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("xp-exec", 35)):
            result = _execute_via_xp_exec(
                "/usr/local/bin/xp-exec", "tok", "user", "cmd", 30,
            )
        assert result["timed_out"] is True
        assert "timed out" in result["error"]

    def test_xp_exec_permission_denied(self) -> None:
        with patch("subprocess.run", side_effect=PermissionError):
            result = _execute_via_xp_exec(
                "/usr/local/bin/xp-exec", "tok", "user", "cmd", 30,
            )
        assert "Permission denied" in result["error"]

    def test_invalid_json_response(self) -> None:
        mock_result = MagicMock()
        mock_result.stdout = "not json"
        mock_result.stderr = "error details"
        mock_result.returncode = 1

        with patch("subprocess.run", return_value=mock_result):
            result = _execute_via_xp_exec(
                "/usr/local/bin/xp-exec", "tok", "user", "cmd", 30,
            )
        assert result["exit_code"] == -1
        assert "Invalid xp-exec response" in result["error"]

    def test_output_truncated(self) -> None:
        big_stdout = "x" * (MAX_OUTPUT_SIZE + 100)
        response_json = json.dumps({
            "exit_code": 0,
            "stdout": big_stdout,
            "stderr": "",
            "timed_out": False,
            "error": "",
        })
        mock_result = MagicMock()
        mock_result.stdout = response_json
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result):
            result = _execute_via_xp_exec(
                "/usr/local/bin/xp-exec", "tok", "user", "cmd", 30,
            )
        assert len(result["stdout"]) == MAX_OUTPUT_SIZE

    def test_cwd_and_env_passed(self) -> None:
        response_json = json.dumps({
            "exit_code": 0, "stdout": "", "stderr": "",
            "timed_out": False, "error": "",
        })
        mock_result = MagicMock()
        mock_result.stdout = response_json
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            _execute_via_xp_exec(
                "/usr/local/bin/xp-exec", "tok", "user", "cmd", 30,
                cwd="/tmp", env={"FOO": "bar"},
            )
        request = json.loads(mock_run.call_args[1]["input"])
        assert request["cwd"] == "/tmp"
        assert request["env"] == {"FOO": "bar"}

    def test_timeout_grace_period(self) -> None:
        """xp-exec subprocess gets timeout + 5s grace period."""
        response_json = json.dumps({
            "exit_code": 0, "stdout": "", "stderr": "",
            "timed_out": False, "error": "",
        })
        mock_result = MagicMock()
        mock_result.stdout = response_json
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            _execute_via_xp_exec(
                "/usr/local/bin/xp-exec", "tok", "user", "cmd", 60,
            )
        assert mock_run.call_args[1]["timeout"] == 65


# ═══════════════════════════════════════════════════════════════════════════
# TestShellWorkerMain — worker process main loop
# ═══════════════════════════════════════════════════════════════════════════

class TestShellWorkerMain:
    """Test shell_worker_main entry point."""

    def test_sentinel_stops_worker(self) -> None:
        inbox = QueueAdapter()
        outbox = QueueAdapter()
        inbox.put(None)  # Sentinel
        shell_worker_main(inbox, outbox, totp_secret="", xp_exec_path="")
        assert outbox.empty()

    def test_empty_command_returns_error(self) -> None:
        inbox = QueueAdapter()
        outbox = QueueAdapter()
        inbox.put({"request_id": "r1", "command": "", "os_user": "u"})
        inbox.put(None)  # Stop after

        shell_worker_main(inbox, outbox, totp_secret="", xp_exec_path="")

        result = outbox.get()
        assert result["request_id"] == "r1"
        assert "Empty command" in result["error"]

    def test_no_os_user_returns_error(self) -> None:
        inbox = QueueAdapter()
        outbox = QueueAdapter()
        inbox.put({"request_id": "r1", "command": "ls", "os_user": ""})
        inbox.put(None)

        shell_worker_main(inbox, outbox, totp_secret="", xp_exec_path="")

        result = outbox.get()
        assert "No os_user" in result["error"]

    def test_successful_command_via_mock(self) -> None:
        """Worker delegates to _execute_via_xp_exec."""
        inbox = QueueAdapter()
        outbox = QueueAdapter()
        inbox.put({
            "request_id": "r1",
            "command": "echo hello",
            "os_user": "xp-sandbox",
            "timeout": 30,
        })
        inbox.put(None)

        mock_response = {
            "exit_code": 0, "stdout": "hello\n", "stderr": "",
            "timed_out": False, "error": "",
        }
        with patch(
            "xml_pipeline.tools.shell_worker._execute_via_xp_exec",
            return_value=mock_response,
        ):
            shell_worker_main(inbox, outbox, totp_secret="", xp_exec_path="/bin/xp-exec")

        result = outbox.get()
        assert result["request_id"] == "r1"
        assert result["exit_code"] == 0
        assert result["stdout"] == "hello\n"

    def test_request_id_correlation(self) -> None:
        """Each response carries the same request_id as its request."""
        inbox = QueueAdapter()
        outbox = QueueAdapter()
        inbox.put({"request_id": "abc", "command": "ls", "os_user": "u"})
        inbox.put({"request_id": "def", "command": "pwd", "os_user": "u"})
        inbox.put(None)

        def fresh_response(*args, **kwargs):
            return {
                "exit_code": 0, "stdout": "", "stderr": "",
                "timed_out": False, "error": "",
            }

        with patch(
            "xml_pipeline.tools.shell_worker._execute_via_xp_exec",
            side_effect=fresh_response,
        ):
            shell_worker_main(inbox, outbox, totp_secret="", xp_exec_path="/bin/xp-exec")

        r1 = outbox.get()
        r2 = outbox.get()
        assert r1["request_id"] == "abc"
        assert r2["request_id"] == "def"

    def test_timeout_clamped(self) -> None:
        """Timeout is clamped to [1, MAX_TIMEOUT]."""
        inbox = QueueAdapter()
        outbox = QueueAdapter()
        inbox.put({
            "request_id": "r1", "command": "ls", "os_user": "u",
            "timeout": 999,
        })
        inbox.put(None)

        with patch(
            "xml_pipeline.tools.shell_worker._execute_via_xp_exec",
            return_value={"exit_code": 0, "stdout": "", "stderr": "", "timed_out": False, "error": ""},
        ) as mock_exec:
            shell_worker_main(inbox, outbox, totp_secret="", xp_exec_path="/bin/xp-exec")

        # Verify timeout was clamped to MAX_TIMEOUT
        call_kwargs = mock_exec.call_args
        assert call_kwargs[1]["timeout"] == MAX_TIMEOUT

    def test_totp_computation_called(self) -> None:
        """When totp_secret is provided, compute_totp is called."""
        inbox = QueueAdapter()
        outbox = QueueAdapter()
        inbox.put({"request_id": "r1", "command": "ls", "os_user": "u"})
        inbox.put(None)

        mock_response = {
            "exit_code": 0, "stdout": "", "stderr": "",
            "timed_out": False, "error": "",
        }
        with patch(
            "xml_pipeline.tools.shell_worker._execute_via_xp_exec",
            return_value=mock_response,
        ) as mock_exec, patch(
            "xml_pipeline.crypto.totp.compute_totp",
            return_value="654321",
        ):
            shell_worker_main(inbox, outbox, totp_secret="JBSWY3DPEHPK3PXP", xp_exec_path="/bin/xp-exec")

        # Verify TOTP token was passed to _execute_via_xp_exec
        assert mock_exec.call_args[1]["totp_token"] == "654321"

    def test_multiple_requests_processed(self) -> None:
        inbox = QueueAdapter()
        outbox = QueueAdapter()
        for i in range(3):
            inbox.put({"request_id": f"r{i}", "command": f"echo {i}", "os_user": "u"})
        inbox.put(None)

        mock_response = {
            "exit_code": 0, "stdout": "", "stderr": "",
            "timed_out": False, "error": "",
        }
        with patch(
            "xml_pipeline.tools.shell_worker._execute_via_xp_exec",
            return_value=mock_response,
        ):
            shell_worker_main(inbox, outbox, totp_secret="", xp_exec_path="/bin/xp-exec")

        results = []
        while not outbox.empty():
            results.append(outbox.get())
        assert len(results) == 3


# ═══════════════════════════════════════════════════════════════════════════
# TestXpExecRef — reference implementation
# ═══════════════════════════════════════════════════════════════════════════

class TestXpExecRef:
    """Test the Python reference xp-exec implementation."""

    def test_successful_command(self) -> None:
        request = json.dumps({"user": "sandbox", "command": "echo hello", "timeout": 5})
        inp = io.StringIO(request + "\n")
        out = io.StringIO()

        run_xp_exec("", {"sandbox"}, input_stream=inp, output_stream=out)

        response = json.loads(out.getvalue())
        assert response["error"] == ""
        # On Windows echo is a shell builtin; we use shell=True so it should work
        assert response["exit_code"] == 0

    def test_bad_totp_rejected(self) -> None:
        request = json.dumps({"totp": "000000", "user": "sandbox", "command": "echo hi"})
        inp = io.StringIO(request + "\n")
        out = io.StringIO()

        with patch("xml_pipeline.crypto.totp.verify_totp", return_value=False):
            run_xp_exec("SECRETKEY", {"sandbox"}, input_stream=inp, output_stream=out)

        response = json.loads(out.getvalue())
        assert "Invalid TOTP" in response["error"]

    def test_valid_totp_accepted(self) -> None:
        request = json.dumps({"totp": "123456", "user": "sandbox", "command": "echo hi"})
        inp = io.StringIO(request + "\n")
        out = io.StringIO()

        with patch("xml_pipeline.crypto.totp.verify_totp", return_value=True):
            run_xp_exec("SECRETKEY", {"sandbox"}, input_stream=inp, output_stream=out)

        response = json.loads(out.getvalue())
        assert response["error"] == ""
        assert response["exit_code"] == 0

    def test_unknown_user_rejected(self) -> None:
        request = json.dumps({"user": "hacker", "command": "rm -rf /"})
        inp = io.StringIO(request + "\n")
        out = io.StringIO()

        run_xp_exec("", {"sandbox", "admin"}, input_stream=inp, output_stream=out)

        response = json.loads(out.getvalue())
        assert "not in allowed list" in response["error"]

    def test_empty_request(self) -> None:
        inp = io.StringIO("\n")
        out = io.StringIO()
        run_xp_exec("", set(), input_stream=inp, output_stream=out)
        response = json.loads(out.getvalue())
        assert "Empty request" in response["error"]

    def test_invalid_json(self) -> None:
        inp = io.StringIO("not json\n")
        out = io.StringIO()
        run_xp_exec("", set(), input_stream=inp, output_stream=out)
        response = json.loads(out.getvalue())
        assert "Invalid JSON" in response["error"]

    def test_no_command(self) -> None:
        request = json.dumps({"user": "sandbox"})
        inp = io.StringIO(request + "\n")
        out = io.StringIO()
        run_xp_exec("", {"sandbox"}, input_stream=inp, output_stream=out)
        response = json.loads(out.getvalue())
        assert "No command" in response["error"]

    def test_no_user(self) -> None:
        request = json.dumps({"command": "echo hi"})
        inp = io.StringIO(request + "\n")
        out = io.StringIO()
        run_xp_exec("", {"sandbox"}, input_stream=inp, output_stream=out)
        response = json.loads(out.getvalue())
        assert "No user" in response["error"]


# ═══════════════════════════════════════════════════════════════════════════
# TestWriteError — error response helper
# ═══════════════════════════════════════════════════════════════════════════

class TestWriteError:
    def test_error_format(self) -> None:
        out = io.StringIO()
        _write_error(out, "test error")
        response = json.loads(out.getvalue())
        assert response["exit_code"] == -1
        assert response["error"] == "test error"
        assert response["stdout"] == ""
        assert response["stderr"] == ""
        assert response["timed_out"] is False
