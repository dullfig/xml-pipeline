"""
test_coding_swarm.py — End-to-end integration tests for the coding swarm.

All tests use deterministic stub handlers (no LLM). The coordinator FSM
is real; agents and tools are replaced with predictable stubs that return
canned responses.

Run with: pytest tests/test_coding_swarm.py -v
"""

from __future__ import annotations

import asyncio
import shlex
import time
from dataclasses import dataclass
from typing import List, Optional

import pytest
from third_party.xmlable import xmlify

from xml_pipeline import (
    StreamPump,
    HandlerResponse,
    HandlerMetadata,
    PumpEvent,
    MessageReceivedEvent,
    MessageSentEvent,
    AgentStateEvent,
    ThreadEvent,
)
from xml_pipeline.message_bus.thread_registry import reset_registry
from xml_pipeline.message_bus.singleton import reset_stream_pump
from xml_pipeline.message_bus.budget_registry import reset_budget_registry
from xml_pipeline.memory import reset_context_buffer
from xml_pipeline.platform.prompt_registry import get_prompt_registry
from xml_pipeline.tools.keyvalue import MemoryBackend

from handlers.coding_swarm.payloads import SwarmMessage
from handlers.coding_swarm.coordinator import (
    handle_coordinator,
    get_states,
    _validate_tool_name,
    _validate_wit_content,
    _validate_python_content,
    _validate_code_content,
    _evict_expired_states,
    CoordinatorState,
    MAX_STATES,
    STATE_TTL,
)
from handlers.coding_swarm.tools import (
    handle_workspace_read,
    handle_workspace_write,
    handle_build_run,
    get_swarm_backend,
    set_swarm_backend,
    _reset_backend,
    _validate_artifact_name,
    ALLOWED_EXTENSIONS,
    ARTIFACT_NAME_RE,
)


# ============================================================================
# Canned content for stubs
# ============================================================================

CANNED_WIT = """\
interface calculator {
    record calculate-request {
        expression: string,
    }
    record calculate-response {
        result: string,
        error: string,
    }
    calculate: func(req: calculate-request) -> calculate-response
}"""

CANNED_CODE = """\
import { CalculateRequest, CalculateResponse } from "./bindings";
export function calculate(req: CalculateRequest): CalculateResponse {
    return { result: "42", error: "" };
}"""

CANNED_TESTS = """\
import pytest
def test_calculate_basic():
    assert True
def test_calculate_error():
    assert True"""


# ============================================================================
# Deterministic stub handlers
# ============================================================================

async def stub_architect(
    payload: SwarmMessage, metadata: HandlerMetadata,
) -> Optional[HandlerResponse]:
    """Always returns a valid WIT design."""
    return HandlerResponse.respond(
        payload=SwarmMessage(
            role="design-result",
            tool_name=payload.tool_name,
            content=CANNED_WIT,
            status="success",
            error="",
            iteration=payload.iteration,
            phase=payload.phase,
        ),
    )


async def stub_coder(
    payload: SwarmMessage, metadata: HandlerMetadata,
) -> Optional[HandlerResponse]:
    """Always returns valid AssemblyScript code."""
    return HandlerResponse.respond(
        payload=SwarmMessage(
            role="code-result",
            tool_name=payload.tool_name,
            content=CANNED_CODE,
            status="success",
            error="",
            iteration=payload.iteration,
            phase=payload.phase,
        ),
    )


async def stub_tester_pass(
    payload: SwarmMessage, metadata: HandlerMetadata,
) -> Optional[HandlerResponse]:
    """Always returns passing tests."""
    return HandlerResponse.respond(
        payload=SwarmMessage(
            role="test-result",
            tool_name=payload.tool_name,
            content=CANNED_TESTS,
            status="success",
            error="",
            iteration=payload.iteration,
            phase=payload.phase,
        ),
    )


_test_fail_count = []


async def stub_tester_fail_then_pass(
    payload: SwarmMessage, metadata: HandlerMetadata,
) -> Optional[HandlerResponse]:
    """Fails on first call, passes on second."""
    _test_fail_count.append(1)
    if len(_test_fail_count) == 1:
        return HandlerResponse.respond(
            payload=SwarmMessage(
                role="test-result",
                tool_name=payload.tool_name,
                content="",
                status="error",
                error="AssertionError: expected 42 got 0",
                iteration=payload.iteration,
                phase=payload.phase,
            ),
        )
    return HandlerResponse.respond(
        payload=SwarmMessage(
            role="test-result",
            tool_name=payload.tool_name,
            content=CANNED_TESTS,
            status="success",
            error="",
            iteration=payload.iteration,
            phase=payload.phase,
        ),
    )


_test_always_fail_count = []


async def stub_tester_always_fail(
    payload: SwarmMessage, metadata: HandlerMetadata,
) -> Optional[HandlerResponse]:
    """Always fails — triggers max retry exhaustion."""
    _test_always_fail_count.append(1)
    return HandlerResponse.respond(
        payload=SwarmMessage(
            role="test-result",
            tool_name=payload.tool_name,
            content="",
            status="error",
            error=f"Test failure #{len(_test_always_fail_count)}",
            iteration=payload.iteration,
            phase=payload.phase,
        ),
    )


async def stub_reviewer_approve(
    payload: SwarmMessage, metadata: HandlerMetadata,
) -> Optional[HandlerResponse]:
    """Always approves."""
    return HandlerResponse.respond(
        payload=SwarmMessage(
            role="review-result",
            tool_name=payload.tool_name,
            content="APPROVED",
            status="success",
            error="",
            iteration=payload.iteration,
            phase=payload.phase,
        ),
    )


_review_reject_count = []


async def stub_reviewer_reject_then_approve(
    payload: SwarmMessage, metadata: HandlerMetadata,
) -> Optional[HandlerResponse]:
    """Rejects first, approves second."""
    _review_reject_count.append(1)
    if len(_review_reject_count) == 1:
        return HandlerResponse.respond(
            payload=SwarmMessage(
                role="review-result",
                tool_name=payload.tool_name,
                content="",
                status="error",
                error="REJECTED: Missing error handling in calculate function",
                iteration=payload.iteration,
                phase=payload.phase,
            ),
        )
    return HandlerResponse.respond(
        payload=SwarmMessage(
            role="review-result",
            tool_name=payload.tool_name,
            content="APPROVED",
            status="success",
            error="",
            iteration=payload.iteration,
            phase=payload.phase,
        ),
    )


# ============================================================================
# Helpers
# ============================================================================

async def _drain_queue(pump):
    """Drain unprocessed items from queue so shutdown won't block."""
    while not pump.queue.empty():
        try:
            pump.queue.get_nowait()
            pump.queue.task_done()
        except asyncio.QueueEmpty:
            break


async def run_for_duration(pump, *, duration=3.0):
    """Run pump for a fixed duration, collecting all events."""
    all_events: List[PumpEvent] = []

    def on_event(e):
        all_events.append(e)

    pump.subscribe_events(on_event)
    task = asyncio.create_task(pump.run())
    await asyncio.sleep(duration)
    pump._running = False
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    await _drain_queue(pump)
    return all_events


async def run_until_quiet(pump, *, idle_timeout=1.5, max_duration=15.0):
    """Run pump until no events fire for ``idle_timeout`` seconds."""
    all_events: List[PumpEvent] = []
    last_event_time = asyncio.get_event_loop().time()

    def on_event(e):
        nonlocal last_event_time
        all_events.append(e)
        last_event_time = asyncio.get_event_loop().time()

    pump.subscribe_events(on_event)
    task = asyncio.create_task(pump.run())
    start = asyncio.get_event_loop().time()

    while True:
        await asyncio.sleep(0.2)
        now = asyncio.get_event_loop().time()
        if now - last_event_time > idle_timeout:
            break
        if now - start > max_duration:
            break

    pump._running = False
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    await _drain_queue(pump)
    return all_events


def _make_task_message(tool_name: str = "calculator", requirements: str = "A simple calculator") -> SwarmMessage:
    """Create the initial task message to inject into the coordinator."""
    return SwarmMessage(
        role="task",
        tool_name=tool_name,
        content=requirements,
        status="pending",
        error="",
        iteration=0,
        phase="",
    )


# ============================================================================
# Fixture: fresh state before each test
# ============================================================================

@pytest.fixture(autouse=True)
def _reset_globals():
    """Reset all global singletons and KV backend before each test."""
    reset_registry()
    reset_stream_pump()
    reset_budget_registry()
    reset_context_buffer()
    get_prompt_registry().clear()
    get_states().clear()
    _test_fail_count.clear()
    _test_always_fail_count.clear()
    _review_reject_count.clear()
    _reset_backend()
    yield
    reset_registry()
    reset_stream_pump()
    reset_budget_registry()
    reset_context_buffer()
    get_prompt_registry().clear()
    get_states().clear()
    _reset_backend()


def _build_pump(
    *,
    tester_handler=stub_tester_pass,
    reviewer_handler=stub_reviewer_approve,
    name="test-swarm",
) -> StreamPump:
    """Register all coding-swarm listeners with deterministic stubs."""
    pump = StreamPump(name=name)

    # Coordinator (real FSM)
    pump.register(
        "coordinator", handle_coordinator, SwarmMessage,
        description="Coordinator FSM", agent=True,
        peers=["architect", "coder", "tester", "reviewer",
               "workspace-read", "workspace-write", "build-run"],
    )
    # Stubs
    pump.register(
        "architect", stub_architect, SwarmMessage,
        description="Architect stub", agent=True, peers=[],
    )
    pump.register(
        "coder", stub_coder, SwarmMessage,
        description="Coder stub", agent=True, peers=[],
    )
    pump.register(
        "tester", tester_handler, SwarmMessage,
        description="Tester stub", agent=True, peers=[],
    )
    pump.register(
        "reviewer", reviewer_handler, SwarmMessage,
        description="Reviewer stub", agent=True, peers=[],
    )
    # Real tools (KV-backed via fixture reset)
    pump.register(
        "workspace-read", handle_workspace_read, SwarmMessage,
        description="Workspace read",
    )
    pump.register(
        "workspace-write", handle_workspace_write, SwarmMessage,
        description="Workspace write",
    )
    pump.register(
        "build-run", handle_build_run, SwarmMessage,
        description="Build runner",
    )
    return pump


# ============================================================================
# 1. Happy Path — Full Workflow
# ============================================================================


class TestHappyPath:

    @pytest.mark.asyncio
    async def test_full_workflow_completes(self):
        """Task -> architect -> write WIT -> coder -> write src -> tester -> write tests -> reviewer -> done."""
        pump = _build_pump()
        await pump.start()
        await pump.inject("coordinator", _make_task_message())

        events = await run_until_quiet(pump, idle_timeout=2.0, max_duration=15.0)

        # Coordinator state should be cleaned up (workflow complete)
        assert len(get_states()) == 0, "Coordinator state should be cleaned up after completion"

        # Artifacts should be stored in KV backend (keyed by workspace-write's thread_id)
        backend = get_swarm_backend()
        all_keys = await backend.keys("*")
        wit_keys = [k for k in all_keys if k.endswith(":calculator.wit")]
        src_keys = [k for k in all_keys if k.endswith(":calculator.ts")]
        test_keys = [k for k in all_keys if k.endswith(":test_calculator.py")]

        assert len(wit_keys) == 1, "WIT artifact should be stored"
        assert len(src_keys) == 1, "Source artifact should be stored"
        assert len(test_keys) == 1, "Test artifact should be stored"

        assert await backend.get(wit_keys[0]) == CANNED_WIT
        assert await backend.get(src_keys[0]) == CANNED_CODE
        assert await backend.get(test_keys[0]) == CANNED_TESTS

    @pytest.mark.asyncio
    async def test_events_emitted_for_each_hop(self):
        """Verify MessageReceivedEvents fire for all participants."""
        pump = _build_pump()
        await pump.start()
        await pump.inject("coordinator", _make_task_message())

        events = await run_until_quiet(pump, idle_timeout=2.0, max_duration=15.0)

        recv_events = [e for e in events if isinstance(e, MessageReceivedEvent)]
        targets_hit = {e.to_id for e in recv_events}

        # All participants should have received at least one message
        expected = {"coordinator", "architect", "coder", "tester",
                    "reviewer", "workspace-write"}
        assert expected.issubset(targets_hit), (
            f"Missing targets: {expected - targets_hit}"
        )


# ============================================================================
# 2. Test Retry — Tester Fails Then Passes
# ============================================================================


class TestTestRetry:

    @pytest.mark.asyncio
    async def test_retry_on_test_failure(self):
        """Tester fails once -> coordinator retries coder -> tester passes -> workflow completes."""
        pump = _build_pump(tester_handler=stub_tester_fail_then_pass)
        await pump.start()
        await pump.inject("coordinator", _make_task_message())

        events = await run_until_quiet(pump, idle_timeout=2.0, max_duration=20.0)

        # State should be cleaned up
        assert len(get_states()) == 0

        # Coder should have been called twice (original + retry)
        recv_events = [e for e in events if isinstance(e, MessageReceivedEvent)]
        coder_calls = [e for e in recv_events if e.to_id == "coder"]
        assert len(coder_calls) >= 2, "Coder should be called at least twice on test retry"

        # Artifacts should still be stored in KV
        backend = get_swarm_backend()
        all_keys = await backend.keys("*")
        assert any(k.endswith(":calculator.wit") for k in all_keys)
        assert any(k.endswith(":calculator.ts") for k in all_keys)
        assert any(k.endswith(":test_calculator.py") for k in all_keys)


# ============================================================================
# 3. Review Rejection — Reviewer Rejects Then Approves
# ============================================================================


class TestReviewRejection:

    @pytest.mark.asyncio
    async def test_retry_on_review_rejection(self):
        """Reviewer rejects once -> coordinator retries coder -> reviewer approves."""
        pump = _build_pump(reviewer_handler=stub_reviewer_reject_then_approve)
        await pump.start()
        await pump.inject("coordinator", _make_task_message())

        events = await run_until_quiet(pump, idle_timeout=2.0, max_duration=20.0)

        assert len(get_states()) == 0

        recv_events = [e for e in events if isinstance(e, MessageReceivedEvent)]
        coder_calls = [e for e in recv_events if e.to_id == "coder"]
        assert len(coder_calls) >= 2, "Coder should be called at least twice on review rejection"

        reviewer_calls = [e for e in recv_events if e.to_id == "reviewer"]
        assert len(reviewer_calls) >= 2, "Reviewer should be called twice"


# ============================================================================
# 4. Max Retries Exhaustion
# ============================================================================


class TestMaxRetries:

    @pytest.mark.asyncio
    async def test_max_test_retries_terminates(self):
        """3 test failures -> workflow terminates without writing tests."""
        pump = _build_pump(tester_handler=stub_tester_always_fail)
        await pump.start()
        await pump.inject("coordinator", _make_task_message())

        events = await run_until_quiet(pump, idle_timeout=2.0, max_duration=20.0)

        # State should be cleaned up (terminated)
        assert len(get_states()) == 0

        # Test artifact should NOT be stored (never passed)
        backend = get_swarm_backend()
        all_keys = await backend.keys("*")
        assert not any(k.endswith(":test_calculator.py") for k in all_keys), \
            "Test artifact should not exist after max retries"

        # WIT should be stored (that phase succeeded)
        assert any(k.endswith(":calculator.wit") for k in all_keys)


# ============================================================================
# 5. Tool Error — Workspace Write Failure
# ============================================================================


class TestToolError:

    @pytest.mark.asyncio
    async def test_write_error_terminates(self):
        """If workspace-write returns an error, coordinator terminates gracefully."""
        async def stub_write_error(
            payload: SwarmMessage, metadata: HandlerMetadata,
        ) -> Optional[HandlerResponse]:
            return HandlerResponse.respond(
                payload=SwarmMessage(
                    role="write-result",
                    tool_name=payload.tool_name,
                    content="",
                    status="error",
                    error="Disk full",
                    iteration=payload.iteration,
                    phase=payload.phase,
                ),
            )

        pump = StreamPump(name="test-write-err")
        pump.register(
            "coordinator", handle_coordinator, SwarmMessage,
            description="Coordinator FSM", agent=True,
            peers=["architect", "coder", "tester", "reviewer",
                   "workspace-read", "workspace-write", "build-run"],
        )
        pump.register("architect", stub_architect, SwarmMessage,
                       description="Architect", agent=True, peers=[])
        pump.register("coder", stub_coder, SwarmMessage,
                       description="Coder", agent=True, peers=[])
        pump.register("tester", stub_tester_pass, SwarmMessage,
                       description="Tester", agent=True, peers=[])
        pump.register("reviewer", stub_reviewer_approve, SwarmMessage,
                       description="Reviewer", agent=True, peers=[])
        pump.register("workspace-read", handle_workspace_read, SwarmMessage,
                       description="Read")
        pump.register("workspace-write", stub_write_error, SwarmMessage,
                       description="Write")
        pump.register("build-run", handle_build_run, SwarmMessage,
                       description="Build")
        await pump.start()
        await pump.inject("coordinator", _make_task_message())

        await run_until_quiet(pump, idle_timeout=2.0, max_duration=10.0)

        # Coordinator should have terminated (cleaned up state)
        assert len(get_states()) == 0


# ============================================================================
# 6. Artifact Name Validation (replaces Path Traversal)
# ============================================================================


class TestArtifactNameValidation:

    def test_slashes_rejected(self):
        with pytest.raises(ValueError, match="Slashes"):
            _validate_artifact_name("../../../etc/passwd")

    def test_backslashes_rejected(self):
        with pytest.raises(ValueError, match="Slashes"):
            _validate_artifact_name("..\\..\\etc\\passwd")

    def test_traversal_rejected(self):
        with pytest.raises(ValueError, match="traversal"):
            _validate_artifact_name("..foo")

    def test_empty_rejected(self):
        with pytest.raises(ValueError, match="Empty"):
            _validate_artifact_name("")

    def test_whitespace_only_rejected(self):
        with pytest.raises(ValueError, match="Empty"):
            _validate_artifact_name("   ")

    def test_too_long_rejected(self):
        with pytest.raises(ValueError, match="too long"):
            _validate_artifact_name("a" * 256 + ".py")

    def test_special_chars_rejected(self):
        with pytest.raises(ValueError, match="Invalid"):
            _validate_artifact_name("calc ulator.ts")

    def test_valid_name_accepted(self):
        _validate_artifact_name("calculator.wit")

    def test_valid_name_with_dash(self):
        _validate_artifact_name("my-tool.ts")

    def test_valid_name_with_underscore(self):
        _validate_artifact_name("test_calc.py")


class TestExtensionWhitelist:

    def test_allowed_wit(self):
        _validate_artifact_name("foo.wit")

    def test_allowed_ts(self):
        _validate_artifact_name("foo.ts")

    def test_allowed_py(self):
        _validate_artifact_name("foo.py")

    def test_allowed_json(self):
        _validate_artifact_name("foo.json")

    def test_allowed_wasm(self):
        _validate_artifact_name("foo.wasm")

    def test_rejected_exe(self):
        with pytest.raises(ValueError, match="Extension"):
            _validate_artifact_name("evil.exe")

    def test_rejected_sh(self):
        with pytest.raises(ValueError, match="Extension"):
            _validate_artifact_name("evil.sh")

    def test_rejected_bat(self):
        with pytest.raises(ValueError, match="Extension"):
            _validate_artifact_name("evil.bat")

    def test_no_extension_allowed(self):
        """Names without extensions are allowed (e.g. Makefile)."""
        _validate_artifact_name("Makefile")


# ============================================================================
# 7. KV Storage — Write Then Read
# ============================================================================


class TestKVStorage:

    @pytest.mark.asyncio
    async def test_write_then_read(self):
        """Write an artifact, then read it back."""
        backend = get_swarm_backend()
        key = "test-thread:hello.py"
        await backend.set(key, "print('hello')")
        result = await backend.get(key)
        assert result == "print('hello')"

    @pytest.mark.asyncio
    async def test_thread_isolation(self):
        """Artifacts from different threads don't collide."""
        backend = get_swarm_backend()
        await backend.set("thread-a:foo.py", "a-content")
        await backend.set("thread-b:foo.py", "b-content")
        assert await backend.get("thread-a:foo.py") == "a-content"
        assert await backend.get("thread-b:foo.py") == "b-content"

    @pytest.mark.asyncio
    async def test_keys_by_prefix(self):
        """Keys can be listed by thread prefix."""
        backend = get_swarm_backend()
        await backend.set("thread-x:a.py", "1")
        await backend.set("thread-x:b.py", "2")
        await backend.set("thread-y:c.py", "3")
        keys = await backend.keys("thread-x:*")
        assert sorted(keys) == ["thread-x:a.py", "thread-x:b.py"]

    @pytest.mark.asyncio
    async def test_overwrite(self):
        """Writing to same key overwrites."""
        backend = get_swarm_backend()
        await backend.set("t:f.py", "v1")
        await backend.set("t:f.py", "v2")
        assert await backend.get("t:f.py") == "v2"


# ============================================================================
# 8. WIT Validation
# ============================================================================


class TestWitValidation:

    def test_valid_wit_passes(self):
        assert _validate_wit_content(CANNED_WIT) is None

    def test_invalid_wit_rejected(self):
        err = _validate_wit_content("this is not valid WIT")
        assert err is not None
        assert "Invalid WIT" in err

    def test_empty_wit_rejected(self):
        err = _validate_wit_content("")
        assert err is not None
        assert "Empty" in err

    def test_whitespace_wit_rejected(self):
        err = _validate_wit_content("   \n  ")
        assert err is not None
        assert "Empty" in err


# ============================================================================
# 9. Python Validation
# ============================================================================


class TestPythonValidation:

    def test_valid_python_passes(self):
        assert _validate_python_content("def test(): pass") is None

    def test_syntax_error_rejected(self):
        err = _validate_python_content("def test( missing paren")
        assert err is not None
        assert "syntax error" in err.lower()

    def test_empty_python_rejected(self):
        err = _validate_python_content("")
        assert err is not None
        assert "Empty" in err

    def test_complex_python_passes(self):
        code = """
import pytest

class TestFoo:
    def test_one(self):
        assert 1 + 1 == 2

    @pytest.mark.parametrize("x", [1, 2, 3])
    def test_param(self, x):
        assert x > 0
"""
        assert _validate_python_content(code) is None


# ============================================================================
# 10. Tool Name Validation
# ============================================================================


class TestToolNameValidation:

    def test_valid_lowercase(self):
        assert _validate_tool_name("calculator") is None

    def test_valid_with_dash(self):
        assert _validate_tool_name("my-tool") is None

    def test_valid_with_underscore(self):
        assert _validate_tool_name("my_tool") is None

    def test_valid_with_digits(self):
        assert _validate_tool_name("tool2") is None

    def test_uppercase_rejected(self):
        err = _validate_tool_name("Calculator")
        assert err is not None

    def test_starts_with_digit_rejected(self):
        err = _validate_tool_name("2tool")
        assert err is not None

    def test_special_chars_rejected(self):
        err = _validate_tool_name("tool.name")
        assert err is not None

    def test_empty_rejected(self):
        err = _validate_tool_name("")
        assert err is not None

    def test_too_long_rejected(self):
        err = _validate_tool_name("a" * 65)
        assert err is not None
        assert "too long" in err


# ============================================================================
# 11. State TTL and Max States
# ============================================================================


class TestStateTTL:

    def test_expired_states_evicted(self):
        states = get_states()
        states["old-thread"] = CoordinatorState(
            created_at=time.time() - STATE_TTL - 10,
        )
        states["new-thread"] = CoordinatorState(
            created_at=time.time(),
        )
        _evict_expired_states()
        assert "old-thread" not in states
        assert "new-thread" in states

    def test_max_states_enforced(self):
        """When MAX_STATES is reached, new tasks are rejected."""
        states = get_states()
        for i in range(MAX_STATES):
            states[f"thread-{i}"] = CoordinatorState()
        assert len(states) == MAX_STATES

    def test_non_expired_kept(self):
        states = get_states()
        states["fresh"] = CoordinatorState(created_at=time.time())
        _evict_expired_states()
        assert "fresh" in states


# ============================================================================
# 12. Shlex Tokenization
# ============================================================================


class TestShlexTokenization:

    def test_simple_split(self):
        parts = shlex.split("npm run build")
        assert parts == ["npm", "run", "build"]

    def test_quoted_args(self):
        parts = shlex.split('pytest -k "test_foo and test_bar"')
        assert parts == ["pytest", "-k", "test_foo and test_bar"]

    def test_str_split_differs(self):
        """str.split would break quoted args incorrectly."""
        cmd = 'npm run "my script"'
        str_parts = cmd.split()
        shlex_parts = shlex.split(cmd)
        assert str_parts != shlex_parts
        assert shlex_parts == ["npm", "run", "my script"]


# ============================================================================
# 13. Command Whitelist Enforced
# ============================================================================


class TestCommandWhitelist:

    @pytest.mark.asyncio
    async def test_disallowed_command_rejected(self):
        """Attempting to run 'rm -rf /' is rejected."""
        pump = StreamPump(name="test-whitelist")
        pump.register("build-run", handle_build_run, SwarmMessage,
                       description="Build")
        await pump.start()

        evil_msg = SwarmMessage(
            role="build-request",
            tool_name="evil",
            content="rm -rf /",
            status="pending",
            error="",
            iteration=0,
            phase="",
        )

        sent_events: List[MessageSentEvent] = []

        def on_event(e):
            if isinstance(e, MessageSentEvent):
                sent_events.append(e)

        pump.subscribe_events(on_event)
        task = asyncio.create_task(pump.run())
        await pump.inject("build-run", evil_msg)
        await asyncio.sleep(1.5)
        pump._running = False
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await _drain_queue(pump)

        build_results = [e for e in sent_events if e.payload_type == "SwarmMessage"]
        assert len(build_results) >= 1
        result = build_results[0].payload
        assert isinstance(result, SwarmMessage)
        assert result.status == "error"
        assert "whitelist" in result.error.lower() or "not in" in result.error.lower()


# ============================================================================
# 14. Code Content Validation
# ============================================================================


class TestCodeContentValidation:

    def test_valid_code_passes(self):
        assert _validate_code_content("some code here") is None

    def test_empty_code_rejected(self):
        err = _validate_code_content("")
        assert err is not None
        assert "Empty" in err

    def test_whitespace_only_rejected(self):
        err = _validate_code_content("   \n  ")
        assert err is not None
        assert "Empty" in err

    def test_oversized_code_rejected(self):
        big = "x" * (1024 * 1024 + 1)
        err = _validate_code_content(big)
        assert err is not None
        assert "limit" in err.lower()


# ============================================================================
# 15. Event Emission at Each Phase
# ============================================================================


class TestEventEmission:

    @pytest.mark.asyncio
    async def test_agent_state_events(self):
        """Verify AgentStateEvents fire for coordinator and agents."""
        pump = _build_pump()
        await pump.start()
        await pump.inject("coordinator", _make_task_message())

        events = await run_until_quiet(pump, idle_timeout=2.0, max_duration=15.0)

        agent_events = [e for e in events if isinstance(e, AgentStateEvent)]
        agent_names = {e.agent_name for e in agent_events}

        # Coordinator and at least architect + coder should have state transitions
        assert "coordinator" in agent_names
        assert "architect" in agent_names
        assert "coder" in agent_names

        # Each agent should have processing->idle transitions
        for name in ["coordinator", "architect", "coder"]:
            states = [e.state for e in agent_events if e.agent_name == name]
            assert "processing" in states, f"{name} should have 'processing' state"
            assert "idle" in states, f"{name} should have 'idle' state"
