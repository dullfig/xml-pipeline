"""
test_test_runner.py — Unit tests for the WASM test runner.

Tests cover:
- _parse_test_cases: valid, not array, invalid JSON, missing keys
- TestRunResult defaults and success property
- handle_test_run error paths (no .wasm, invalid JSON)
"""

from __future__ import annotations

import json
from typing import Optional

import pytest

from xml_pipeline.message_bus.message_state import HandlerMetadata, HandlerResponse

from handlers.coding_swarm.test_runner import (
    _parse_test_cases,
    _run_wasm_tests,
    TestCaseResult,
    TestRunResult,
    handle_test_run,
    WASMTIME_AVAILABLE,
)
from handlers.coding_swarm.payloads import SwarmMessage
from handlers.coding_swarm.tools import set_swarm_backend, _reset_backend, get_swarm_backend
from xml_pipeline.tools.keyvalue import MemoryBackend


# ============================================================================
# Fixture
# ============================================================================


@pytest.fixture(autouse=True)
def _reset():
    _reset_backend()
    yield
    _reset_backend()


# ============================================================================
# _parse_test_cases
# ============================================================================


class TestParseTestCases:

    def test_valid_cases(self):
        raw = json.dumps([
            {"function": "calc", "input": {"expr": "2+2"}, "expected": {"result": "4"}},
            {"function": "calc", "input": {"expr": "bad"}, "expected": {"error": "parse"}},
        ])
        cases = _parse_test_cases(raw)
        assert len(cases) == 2
        assert cases[0]["function"] == "calc"
        assert cases[1]["expected"]["error"] == "parse"

    def test_invalid_json(self):
        with pytest.raises(ValueError, match="Invalid JSON"):
            _parse_test_cases("not json at all")

    def test_not_array(self):
        with pytest.raises(ValueError, match="must be a JSON array"):
            _parse_test_cases('{"function": "calc"}')

    def test_missing_function(self):
        with pytest.raises(ValueError, match="missing 'function'"):
            _parse_test_cases('[{"expected": {"result": "4"}}]')

    def test_missing_expected(self):
        with pytest.raises(ValueError, match="missing 'expected'"):
            _parse_test_cases('[{"function": "calc", "input": {}}]')

    def test_non_object_item(self):
        with pytest.raises(ValueError, match="must be an object"):
            _parse_test_cases('["not an object"]')

    def test_empty_array(self):
        """Empty array is valid (zero test cases)."""
        cases = _parse_test_cases("[]")
        assert cases == []


# ============================================================================
# TestRunResult
# ============================================================================


class TestTestRunResult:

    def test_defaults(self):
        r = TestRunResult()
        assert r.total == 0
        assert r.passed == 0
        assert r.failed == 0
        assert r.errors == 0
        assert r.results == []
        assert r.error == ""

    def test_success_property_all_pass(self):
        r = TestRunResult(total=2, passed=2, failed=0, errors=0)
        assert r.success is True

    def test_success_property_with_failure(self):
        r = TestRunResult(total=2, passed=1, failed=1, errors=0)
        assert r.success is False

    def test_success_property_with_error(self):
        r = TestRunResult(total=1, passed=0, failed=0, errors=1)
        assert r.success is False

    def test_success_property_with_top_level_error(self):
        r = TestRunResult(error="Module load failed")
        assert r.success is False


# ============================================================================
# TestCaseResult
# ============================================================================


class TestTestCaseResult:

    def test_defaults(self):
        r = TestCaseResult(function="calc", passed=True)
        assert r.function == "calc"
        assert r.passed is True
        assert r.expected == {}
        assert r.actual == {}
        assert r.error == ""


# ============================================================================
# _run_wasm_tests (no wasmtime)
# ============================================================================


class TestRunWasmTestsNoWasmtime:

    def test_without_wasmtime(self):
        """If wasmtime is not available, returns error."""
        if WASMTIME_AVAILABLE:
            pytest.skip("wasmtime is installed")
        result = _run_wasm_tests(b"\x00wasm", [{"function": "f", "expected": {}}])
        assert result.success is False
        assert "wasmtime" in result.error.lower()


# ============================================================================
# handle_test_run error paths
# ============================================================================


class TestHandleTestRun:

    @pytest.mark.asyncio
    async def test_unexpected_role(self):
        """Non test-run-request role returns error."""
        msg = SwarmMessage(
            role="wrong-role", tool_name="calc", content="",
            status="pending", error="", iteration=0, phase="",
        )
        meta = HandlerMetadata(
            thread_id="t1", from_id="coord", own_name="test-runner",
            is_self_call=False, usage_instructions="", todo_nudge="",
        )
        resp = await handle_test_run(msg, meta)
        assert resp is not None
        assert resp.payload.status == "error"
        assert "Unexpected role" in resp.payload.error

    @pytest.mark.asyncio
    async def test_missing_wasm_in_kv(self):
        """If .wasm is not in KV, returns error."""
        set_swarm_backend(MemoryBackend())
        msg = SwarmMessage(
            role="test-run-request", tool_name="calc",
            content='[{"function": "f", "expected": {}}]',
            status="pending", error="", iteration=0, phase="test-running",
        )
        meta = HandlerMetadata(
            thread_id="t1", from_id="coord", own_name="test-runner",
            is_self_call=False, usage_instructions="", todo_nudge="",
        )
        resp = await handle_test_run(msg, meta)
        assert resp is not None
        assert resp.payload.status == "error"
        assert "not found" in resp.payload.error.lower()

    @pytest.mark.asyncio
    async def test_invalid_json_in_content(self):
        """If content is not valid JSON, returns error."""
        backend = MemoryBackend()
        set_swarm_backend(backend)
        # Store a fake .wasm
        import base64
        await backend.set("t1:calc.wasm", base64.b64encode(b"fakewasm").decode())

        msg = SwarmMessage(
            role="test-run-request", tool_name="calc",
            content="not json",
            status="pending", error="", iteration=0, phase="test-running",
        )
        meta = HandlerMetadata(
            thread_id="t1", from_id="coord", own_name="test-runner",
            is_self_call=False, usage_instructions="", todo_nudge="",
        )
        resp = await handle_test_run(msg, meta)
        assert resp is not None
        assert resp.payload.status == "error"
        assert "Invalid JSON" in resp.payload.error
