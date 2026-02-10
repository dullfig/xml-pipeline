"""
test_runner.py — WASM test runner for the coding swarm.

Loads compiled .wasm from KV storage, executes structured JSON test
cases against its exports, and returns pass/fail results. No subprocess
involved — everything runs inside wasmtime sandboxes.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from xml_pipeline.message_bus.message_state import HandlerMetadata, HandlerResponse

from handlers.coding_swarm.payloads import SwarmMessage
from handlers.coding_swarm.tools import get_swarm_backend

logger = logging.getLogger(__name__)

# Guarded import — wasmtime is optional
try:
    import wasmtime
    WASMTIME_AVAILABLE = True
except ImportError:
    WASMTIME_AVAILABLE = False

TEST_TIMEOUT = 30.0  # seconds per test suite run


@dataclass
class TestCaseResult:
    """Result of a single test case execution."""
    function: str
    passed: bool
    expected: Dict[str, str] = field(default_factory=dict)
    actual: Dict[str, str] = field(default_factory=dict)
    error: str = ""


@dataclass
class TestRunResult:
    """Aggregate result of all test cases."""
    total: int = 0
    passed: int = 0
    failed: int = 0
    errors: int = 0
    results: List[TestCaseResult] = field(default_factory=list)
    error: str = ""

    @property
    def success(self) -> bool:
        return self.error == "" and self.failed == 0 and self.errors == 0


def _parse_test_cases(raw: str) -> List[Dict[str, Any]]:
    """
    Parse JSON test case array from string.

    Expected format:
    [
      {"function": "calculate", "input": {...}, "expected": {...}},
      ...
    ]

    Raises ValueError on invalid format.
    """
    try:
        cases = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON: {exc}")

    if not isinstance(cases, list):
        raise ValueError("Test cases must be a JSON array")

    for i, case in enumerate(cases):
        if not isinstance(case, dict):
            raise ValueError(f"Test case {i} must be an object")
        if "function" not in case:
            raise ValueError(f"Test case {i} missing 'function' field")
        if "expected" not in case:
            raise ValueError(f"Test case {i} missing 'expected' field")

    return cases


def _run_wasm_tests(
    wasm_bytes: bytes,
    test_cases: List[Dict[str, Any]],
) -> TestRunResult:
    """
    Load a .wasm module and execute test cases against its exports.

    This is a blocking method — call via asyncio.to_thread().

    For each test case, creates a fresh Store+Instance, calls the
    exported function with serialized input, and compares the result
    to the expected output.
    """
    if not WASMTIME_AVAILABLE:
        return TestRunResult(error="wasmtime not installed")

    result = TestRunResult(total=len(test_cases))

    try:
        engine = wasmtime.Engine()
        module = wasmtime.Module(engine, wasm_bytes)
    except Exception as exc:
        return TestRunResult(error=f"Failed to load WASM module: {exc}")

    for case in test_cases:
        func_name = case["function"]
        input_data = case.get("input", {})
        expected = case.get("expected", {})

        tc_result = TestCaseResult(
            function=func_name,
            passed=False,
            expected=expected,
        )

        try:
            # Fresh store per test case for isolation
            store = wasmtime.Store(engine)
            linker = wasmtime.Linker(engine)
            instance = linker.instantiate(store, module)

            # Look for handle_{function} or {function} export
            exports = instance.exports(store)
            handler_name = f"handle_{func_name.replace('-', '_')}"
            fn = exports.get(handler_name) or exports.get(func_name)
            if fn is None:
                tc_result.error = f"Export '{handler_name}' not found"
                result.errors += 1
                result.results.append(tc_result)
                continue

            # Get memory and alloc for string I/O
            memory = exports.get("memory")
            alloc_fn = exports.get("alloc")

            if memory is None or alloc_fn is None:
                tc_result.error = "Module missing 'memory' or 'alloc' export"
                result.errors += 1
                result.results.append(tc_result)
                continue

            # Serialize input as JSON, write to WASM memory
            input_json = json.dumps(input_data)
            input_bytes = input_json.encode("utf-8")
            input_len = len(input_bytes)
            input_ptr = alloc_fn(store, input_len)

            # Write input bytes to memory
            memory.write(store, input_bytes, input_ptr)

            # Call the function — returns ptr to result
            result_ptr = fn(store, input_ptr, input_len)

            # Read result length (first 4 bytes at result_ptr)
            result_len_bytes = memory.read(store, result_ptr, result_ptr + 4)
            result_len = int.from_bytes(bytes(result_len_bytes), "little")

            # Read result string
            result_data = memory.read(
                store, result_ptr + 4, result_ptr + 4 + result_len
            )
            actual_json = bytes(result_data).decode("utf-8")
            actual = json.loads(actual_json)

            tc_result.actual = actual

            # Compare expected vs actual
            match = True
            for key, exp_val in expected.items():
                act_val = str(actual.get(key, ""))
                if act_val != str(exp_val):
                    match = False
                    break

            tc_result.passed = match
            if match:
                result.passed += 1
            else:
                result.failed += 1

        except Exception as exc:
            tc_result.error = str(exc)
            result.errors += 1

        result.results.append(tc_result)

    return result


async def handle_test_run(
    payload: SwarmMessage,
    metadata: HandlerMetadata,
) -> Optional[HandlerResponse]:
    """Execute JSON test cases against a compiled .wasm from KV storage."""
    if payload.role != "test-run-request":
        return HandlerResponse.respond(
            payload=SwarmMessage(
                role="test-run-result",
                tool_name=payload.tool_name,
                content="",
                status="error",
                error=f"Unexpected role: {payload.role}",
                iteration=payload.iteration,
                phase=payload.phase,
            ),
        )

    try:
        # Fetch compiled .wasm from KV (stored as base64)
        backend = get_swarm_backend()
        wasm_key = f"{metadata.thread_id}:{payload.tool_name}.wasm"
        wasm_b64 = await backend.get(wasm_key)
        if wasm_b64 is None:
            raise FileNotFoundError(
                f"Compiled .wasm not found in KV: {payload.tool_name}.wasm"
            )

        wasm_bytes = base64.b64decode(wasm_b64)

        # Parse test cases from content
        test_cases = _parse_test_cases(payload.content)

        # Run tests in a thread (wasmtime is blocking)
        run_result = await asyncio.wait_for(
            asyncio.to_thread(_run_wasm_tests, wasm_bytes, test_cases),
            timeout=TEST_TIMEOUT,
        )

        if run_result.success:
            summary = (
                f"All {run_result.total} tests passed"
            )
            return HandlerResponse.respond(
                payload=SwarmMessage(
                    role="test-run-result",
                    tool_name=payload.tool_name,
                    content=summary,
                    status="success",
                    error="",
                    iteration=payload.iteration,
                    phase=payload.phase,
                ),
            )
        else:
            # Build failure details
            failures = []
            for tc in run_result.results:
                if not tc.passed:
                    if tc.error:
                        failures.append(f"{tc.function}: ERROR {tc.error}")
                    else:
                        failures.append(
                            f"{tc.function}: expected {tc.expected}, "
                            f"got {tc.actual}"
                        )
            detail = "\n".join(failures)
            error_msg = run_result.error or detail
            return HandlerResponse.respond(
                payload=SwarmMessage(
                    role="test-run-result",
                    tool_name=payload.tool_name,
                    content=detail,
                    status="error",
                    error=error_msg,
                    iteration=payload.iteration,
                    phase=payload.phase,
                ),
            )

    except asyncio.TimeoutError:
        return HandlerResponse.respond(
            payload=SwarmMessage(
                role="test-run-result",
                tool_name=payload.tool_name,
                content="",
                status="error",
                error=f"Test execution timed out after {TEST_TIMEOUT}s",
                iteration=payload.iteration,
                phase=payload.phase,
            ),
        )
    except Exception as exc:
        logger.exception("Test runner error for tool '%s'", payload.tool_name)
        return HandlerResponse.respond(
            payload=SwarmMessage(
                role="test-run-result",
                tool_name=payload.tool_name,
                content="",
                status="error",
                error=str(exc),
                iteration=payload.iteration,
                phase=payload.phase,
            ),
        )
