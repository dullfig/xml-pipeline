"""
test_coding_swarm.py — End-to-end integration tests for the coding swarm.

All tests use deterministic stub handlers (no LLM). The coordinator FSM
is real; agents and tools are replaced with predictable stubs that return
canned responses.

Run with: pytest tests/test_coding_swarm.py -v
"""

from __future__ import annotations

import asyncio
import tempfile
from dataclasses import dataclass
from pathlib import Path
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

from handlers.coding_swarm.payloads import SwarmMessage
from handlers.coding_swarm.coordinator import (
    handle_coordinator,
    get_states,
)
from handlers.coding_swarm.tools import (
    handle_workspace_read,
    handle_workspace_write,
    handle_build_run,
    set_workspace_root,
    get_workspace_root,
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
def _reset_globals(tmp_path):
    """Reset all global singletons and workspace before each test."""
    reset_registry()
    reset_stream_pump()
    reset_budget_registry()
    reset_context_buffer()
    get_prompt_registry().clear()
    get_states().clear()
    _test_fail_count.clear()
    _test_always_fail_count.clear()
    _review_reject_count.clear()
    # Use a temp directory for workspace
    set_workspace_root(tmp_path)
    yield
    reset_registry()
    reset_stream_pump()
    reset_budget_registry()
    reset_context_buffer()
    get_prompt_registry().clear()
    get_states().clear()


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
    # Real tools (sandboxed to tmp_path via fixture)
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
        """Task → architect → write WIT → coder → write src → tester → write tests → reviewer → done."""
        pump = _build_pump()
        await pump.start()
        await pump.inject("coordinator", _make_task_message())

        events = await run_until_quiet(pump, idle_timeout=2.0, max_duration=15.0)

        # Coordinator state should be cleaned up (workflow complete)
        assert len(get_states()) == 0, "Coordinator state should be cleaned up after completion"

        # Files should be written to workspace
        ws = get_workspace_root()
        assert (ws / "calculator.wit").exists(), "WIT file should be written"
        assert (ws / "calculator.ts").exists(), "Source file should be written"
        assert (ws / "test_calculator.py").exists(), "Test file should be written"

        # Verify file contents
        assert (ws / "calculator.wit").read_text() == CANNED_WIT
        assert (ws / "calculator.ts").read_text() == CANNED_CODE
        assert (ws / "test_calculator.py").read_text() == CANNED_TESTS

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
        """Tester fails once → coordinator retries coder → tester passes → workflow completes."""
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

        # Files should still be written
        ws = get_workspace_root()
        assert (ws / "calculator.wit").exists()
        assert (ws / "calculator.ts").exists()
        assert (ws / "test_calculator.py").exists()


# ============================================================================
# 3. Review Rejection — Reviewer Rejects Then Approves
# ============================================================================


class TestReviewRejection:

    @pytest.mark.asyncio
    async def test_retry_on_review_rejection(self):
        """Reviewer rejects once → coordinator retries coder → reviewer approves."""
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
        """3 test failures → workflow terminates without writing tests."""
        pump = _build_pump(tester_handler=stub_tester_always_fail)
        await pump.start()
        await pump.inject("coordinator", _make_task_message())

        events = await run_until_quiet(pump, idle_timeout=2.0, max_duration=20.0)

        # State should be cleaned up (terminated)
        assert len(get_states()) == 0

        # Test file should NOT be written (never passed)
        ws = get_workspace_root()
        assert not (ws / "test_calculator.py").exists(), "Test file should not exist after max retries"

        # WIT and source should be written (those phases succeeded)
        assert (ws / "calculator.wit").exists()


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
# 6. Path Traversal Blocked
# ============================================================================


class TestPathTraversal:

    @pytest.mark.asyncio
    async def test_path_traversal_rejected(self, tmp_path):
        """Attempting to write ../../../etc/passwd is rejected."""
        set_workspace_root(tmp_path)

        pump = StreamPump(name="test-traversal")
        pump.register("workspace-write", handle_workspace_write, SwarmMessage,
                       description="Write")
        await pump.start()

        # Inject a traversal attempt directly
        traversal_msg = SwarmMessage(
            role="write-request",
            tool_name="evil",
            content="path:../../../etc/passwd\nmalicious content",
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
        await pump.inject("workspace-write", traversal_msg)
        await asyncio.sleep(1.5)
        pump._running = False
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await _drain_queue(pump)

        # Should have a write-result with error status
        write_results = [e for e in sent_events if e.payload_type == "SwarmMessage"]
        assert len(write_results) >= 1
        result = write_results[0].payload
        assert isinstance(result, SwarmMessage)
        assert result.status == "error"
        assert "traversal" in result.error.lower() or "escape" in result.error.lower()

        # File should NOT exist
        assert not (tmp_path / ".." / ".." / ".." / "etc" / "passwd").exists()


# ============================================================================
# 7. Command Whitelist Enforced
# ============================================================================


class TestCommandWhitelist:

    @pytest.mark.asyncio
    async def test_disallowed_command_rejected(self, tmp_path):
        """Attempting to run 'rm -rf /' is rejected."""
        set_workspace_root(tmp_path)

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
# 8. Event Emission at Each Phase
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

        # Each agent should have processing→idle transitions
        for name in ["coordinator", "architect", "coder"]:
            states = [e.state for e in agent_events if e.agent_name == name]
            assert "processing" in states, f"{name} should have 'processing' state"
            assert "idle" in states, f"{name} should have 'idle' state"
