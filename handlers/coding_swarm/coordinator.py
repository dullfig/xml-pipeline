"""
coordinator.py — Deterministic FSM coordinator for the coding swarm.

Routes messages between architect, coder, tester, reviewer, and workspace
tools. No LLM calls — pure state-machine logic. Marked ``agent: true`` only
for peer-enforcement.

State Machine Phases
--------------------
init       → receive task          → send design-request to architect
designing  → receive design-result → send write-request (WIT) to workspace-write
wit-saved  → receive write-result  → send code-request to coder
coding     → receive code-result   → send write-request (AS) to workspace-write
src-saved  → receive write-result  → send test-request to tester
testing    → receive test-result   → if fail & retries<3: back to coding
                                     else: send write-request (tests)
tests-saved→ receive write-result  → send review-request to reviewer
reviewing  → receive review-result → if reject & retries<2: back to coding
                                     else: return None (complete)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional

from xml_pipeline.message_bus.message_state import HandlerMetadata, HandlerResponse

from handlers.coding_swarm.payloads import SwarmMessage

MAX_TEST_RETRIES = 3
MAX_REVIEW_RETRIES = 2


@dataclass
class CoordinatorState:
    """Mutable state for one coordinator thread."""
    phase: str = "init"
    tool_name: str = ""
    requirements: str = ""
    wit_content: str = ""
    code_content: str = ""
    test_content: str = ""
    test_retries: int = 0
    review_retries: int = 0
    last_error: str = ""


# Thread-scoped state: thread_id -> CoordinatorState
_states: Dict[str, CoordinatorState] = {}


def get_states() -> Dict[str, CoordinatorState]:
    """Expose state dict for testing."""
    return _states


def cleanup_thread(thread_id: str) -> None:
    """Remove state for a finished thread."""
    _states.pop(thread_id, None)


async def handle_coordinator(
    payload: SwarmMessage,
    metadata: HandlerMetadata,
) -> Optional[HandlerResponse]:
    """Deterministic FSM — dispatches based on phase + incoming role."""
    tid = metadata.thread_id
    state = _states.get(tid)

    # ── Phase: init ────────────────────────────────────────────────
    if state is None:
        if payload.role != "task":
            return None  # Unexpected message without an active task
        state = CoordinatorState(
            phase="designing",
            tool_name=payload.tool_name,
            requirements=payload.content,
        )
        _states[tid] = state
        return HandlerResponse(
            payload=SwarmMessage(
                role="design-request",
                tool_name=state.tool_name,
                content=state.requirements,
                status="pending",
                error="",
                iteration=0,
                phase="designing",
            ),
            to="architect",
        )

    # ── Phase: designing ───────────────────────────────────────────
    if state.phase == "designing":
        if payload.role != "design-result":
            return None
        if payload.status == "error":
            cleanup_thread(tid)
            return None
        state.wit_content = payload.content
        state.phase = "wit-saving"
        return HandlerResponse(
            payload=SwarmMessage(
                role="write-request",
                tool_name=state.tool_name,
                content=f"path:{state.tool_name}.wit\n{state.wit_content}",
                status="pending",
                error="",
                iteration=0,
                phase="wit-saving",
            ),
            to="workspace-write",
        )

    # ── Phase: wit-saving (waiting for workspace-write confirmation) ─
    if state.phase == "wit-saving":
        if payload.role != "write-result":
            return None
        if payload.status == "error":
            cleanup_thread(tid)
            return None
        state.phase = "coding"
        error_ctx = ""
        if state.last_error:
            error_ctx = f"\n\nPrevious attempt failed:\n{state.last_error}"
            state.last_error = ""
        return HandlerResponse(
            payload=SwarmMessage(
                role="code-request",
                tool_name=state.tool_name,
                content=f"WIT:\n{state.wit_content}\n\nRequirements:\n{state.requirements}{error_ctx}",
                status="pending",
                error="",
                iteration=state.test_retries + state.review_retries,
                phase="coding",
            ),
            to="coder",
        )

    # ── Phase: coding ──────────────────────────────────────────────
    if state.phase == "coding":
        if payload.role != "code-result":
            return None
        if payload.status == "error":
            cleanup_thread(tid)
            return None
        state.code_content = payload.content
        state.phase = "src-saving"
        return HandlerResponse(
            payload=SwarmMessage(
                role="write-request",
                tool_name=state.tool_name,
                content=f"path:{state.tool_name}.ts\n{state.code_content}",
                status="pending",
                error="",
                iteration=0,
                phase="src-saving",
            ),
            to="workspace-write",
        )

    # ── Phase: src-saving ──────────────────────────────────────────
    if state.phase == "src-saving":
        if payload.role != "write-result":
            return None
        if payload.status == "error":
            cleanup_thread(tid)
            return None
        state.phase = "testing"
        return HandlerResponse(
            payload=SwarmMessage(
                role="test-request",
                tool_name=state.tool_name,
                content=f"WIT:\n{state.wit_content}\n\nSource:\n{state.code_content}",
                status="pending",
                error="",
                iteration=state.test_retries,
                phase="testing",
            ),
            to="tester",
        )

    # ── Phase: testing ─────────────────────────────────────────────
    if state.phase == "testing":
        if payload.role != "test-result":
            return None
        if payload.status == "error":
            state.test_retries += 1
            if state.test_retries >= MAX_TEST_RETRIES:
                cleanup_thread(tid)
                return None
            # Retry: go back to coding with error context
            state.last_error = payload.error or payload.content
            state.phase = "coding"
            return HandlerResponse(
                payload=SwarmMessage(
                    role="code-request",
                    tool_name=state.tool_name,
                    content=(
                        f"WIT:\n{state.wit_content}\n\n"
                        f"Requirements:\n{state.requirements}\n\n"
                        f"Previous code:\n{state.code_content}\n\n"
                        f"Test failure (attempt {state.test_retries}/{MAX_TEST_RETRIES}):\n"
                        f"{state.last_error}"
                    ),
                    status="pending",
                    error="",
                    iteration=state.test_retries,
                    phase="coding",
                ),
                to="coder",
            )
        state.test_content = payload.content
        state.phase = "tests-saving"
        return HandlerResponse(
            payload=SwarmMessage(
                role="write-request",
                tool_name=state.tool_name,
                content=f"path:test_{state.tool_name}.py\n{state.test_content}",
                status="pending",
                error="",
                iteration=0,
                phase="tests-saving",
            ),
            to="workspace-write",
        )

    # ── Phase: tests-saving ────────────────────────────────────────
    if state.phase == "tests-saving":
        if payload.role != "write-result":
            return None
        if payload.status == "error":
            cleanup_thread(tid)
            return None
        state.phase = "reviewing"
        return HandlerResponse(
            payload=SwarmMessage(
                role="review-request",
                tool_name=state.tool_name,
                content=(
                    f"WIT:\n{state.wit_content}\n\n"
                    f"Source:\n{state.code_content}\n\n"
                    f"Tests:\n{state.test_content}"
                ),
                status="pending",
                error="",
                iteration=state.review_retries,
                phase="reviewing",
            ),
            to="reviewer",
        )

    # ── Phase: reviewing ───────────────────────────────────────────
    if state.phase == "reviewing":
        if payload.role != "review-result":
            return None
        if payload.status == "error":
            # "error" means rejection
            state.review_retries += 1
            if state.review_retries >= MAX_REVIEW_RETRIES:
                cleanup_thread(tid)
                return None
            # Retry: go back to coding with reviewer feedback
            state.last_error = payload.error or payload.content
            state.phase = "coding"
            return HandlerResponse(
                payload=SwarmMessage(
                    role="code-request",
                    tool_name=state.tool_name,
                    content=(
                        f"WIT:\n{state.wit_content}\n\n"
                        f"Requirements:\n{state.requirements}\n\n"
                        f"Previous code:\n{state.code_content}\n\n"
                        f"Reviewer feedback (attempt {state.review_retries}/{MAX_REVIEW_RETRIES}):\n"
                        f"{state.last_error}"
                    ),
                    status="pending",
                    error="",
                    iteration=state.review_retries,
                    phase="coding",
                ),
                to="coder",
            )
        # Approved — workflow complete
        cleanup_thread(tid)
        return None

    # Unknown phase — terminate
    cleanup_thread(tid)
    return None
