"""
coordinator.py — Deterministic FSM coordinator for the coding swarm.

Routes messages between architect, coder, tester, reviewer, and workspace
tools. No LLM calls — pure state-machine logic. Marked ``agent: true`` only
for peer-enforcement.

State Machine Phases
--------------------
init         → receive task            → send design-request to architect
designing    → receive design-result   → validate WIT → send write-request to workspace-write
wit-saving   → receive write-result    → send code-request to coder
coding       → receive code-result     → validate code → send write-request (AS) to workspace-write
src-saving   → receive write-result    → send compile-request to compile
building     → receive compile-result  → if fail & retries<3: back to coder
                                         else: send test-request to tester
testing      → receive test-result     → validate JSON → send test-run-request to test-runner
test-running → receive test-run-result → if fail: back to coder
                                         else: send write-request (tests)
tests-saving → receive write-result    → send review-request to reviewer
reviewing    → receive review-result   → if reject & retries<2: back to coding
                                         else: return None (complete)
"""

from __future__ import annotations

import ast
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Dict, Optional

from xml_pipeline.message_bus.message_state import HandlerMetadata, HandlerResponse

from handlers.coding_swarm.payloads import SwarmMessage

logger = logging.getLogger(__name__)

MAX_TEST_RETRIES = 3
MAX_REVIEW_RETRIES = 2
MAX_DESIGN_RETRIES = 3
MAX_COMPILE_RETRIES = 3
MAX_CODE_SIZE = 1 * 1024 * 1024  # 1 MB
MAX_STATES = 100
STATE_TTL = 3600  # 1 hour

TOOL_NAME_RE = re.compile(r'^[a-z][a-z0-9_-]*$')


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
    design_retries: int = 0
    compile_retries: int = 0
    last_error: str = ""
    created_at: float = field(default_factory=time.time)


# Thread-scoped state: thread_id -> CoordinatorState
_states: Dict[str, CoordinatorState] = {}


def get_states() -> Dict[str, CoordinatorState]:
    """Expose state dict for testing."""
    return _states


def cleanup_thread(thread_id: str) -> None:
    """Remove state for a finished thread."""
    _states.pop(thread_id, None)


# ── Validation helpers ────────────────────────────────────────────────────

def _validate_tool_name(name: str) -> Optional[str]:
    """Validate a tool name. Returns error string or None."""
    if not name:
        return "Empty tool name"
    if len(name) > 64:
        return f"Tool name too long ({len(name)} > 64)"
    if not TOOL_NAME_RE.match(name):
        return "Tool name must start with lowercase letter, contain only [a-z0-9_-]"
    return None


def _validate_wit_content(content: str) -> Optional[str]:
    """Validate WIT content using the WASM WIT parser. Returns error string or None."""
    if not content or not content.strip():
        return "Empty WIT content"
    try:
        from xml_pipeline.wasm.wit_parser import parse_wit
        parse_wit(content)
        return None
    except Exception as exc:
        return f"Invalid WIT: {exc}"


def _validate_python_content(content: str) -> Optional[str]:
    """Validate Python source via ast.parse(). Returns error string or None."""
    if not content or not content.strip():
        return "Empty Python content"
    try:
        ast.parse(content)
        return None
    except SyntaxError as exc:
        return f"Python syntax error: {exc}"


def _validate_code_content(content: str) -> Optional[str]:
    """Validate code content is non-empty and within size limit. Returns error string or None."""
    if not content or not content.strip():
        return "Empty code content"
    if len(content.encode("utf-8")) > MAX_CODE_SIZE:
        return f"Code exceeds {MAX_CODE_SIZE} byte limit"
    return None


def _validate_json_test_cases(content: str) -> Optional[str]:
    """Validate JSON test case array. Returns error string or None."""
    if not content or not content.strip():
        return "Empty test case content"
    try:
        cases = json.loads(content)
    except json.JSONDecodeError as exc:
        return f"Invalid JSON: {exc}"
    if not isinstance(cases, list):
        return "Test cases must be a JSON array"
    for i, case in enumerate(cases):
        if not isinstance(case, dict):
            return f"Test case {i} must be an object"
        if "function" not in case:
            return f"Test case {i} missing 'function' field"
        if "expected" not in case:
            return f"Test case {i} missing 'expected' field"
    return None


def _evict_expired_states() -> None:
    """Remove states older than STATE_TTL."""
    now = time.time()
    expired = [tid for tid, s in _states.items() if now - s.created_at > STATE_TTL]
    for tid in expired:
        logger.info("Evicting expired coordinator state for thread %s", tid)
        del _states[tid]


async def handle_coordinator(
    payload: SwarmMessage,
    metadata: HandlerMetadata,
) -> Optional[HandlerResponse]:
    """Deterministic FSM — dispatches based on phase + incoming role."""
    tid = metadata.thread_id
    try:
        return await _handle_coordinator_inner(payload, metadata)
    except Exception:
        logger.exception("Coordinator crashed for thread %s, cleaning up state", tid)
        _states.pop(tid, None)
        return None


async def _handle_coordinator_inner(
    payload: SwarmMessage,
    metadata: HandlerMetadata,
) -> Optional[HandlerResponse]:
    """Inner coordinator logic (wrapped by handle_coordinator for cleanup)."""
    tid = metadata.thread_id
    state = _states.get(tid)

    # ── Phase: init ────────────────────────────────────────────────
    if state is None:
        if payload.role != "task":
            return None  # Unexpected message without an active task

        # Validate tool name
        name_error = _validate_tool_name(payload.tool_name)
        if name_error:
            return None

        # Evict expired states and enforce max
        _evict_expired_states()
        if len(_states) >= MAX_STATES:
            logger.warning("Max coordinator states (%d) reached, rejecting task", MAX_STATES)
            return None

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

        # Validate WIT output
        wit_error = _validate_wit_content(payload.content)
        if wit_error:
            state.design_retries += 1
            if state.design_retries >= MAX_DESIGN_RETRIES:
                logger.warning("Max design retries for thread %s", tid)
                cleanup_thread(tid)
                return None
            # Retry: send back to architect with error feedback
            return HandlerResponse(
                payload=SwarmMessage(
                    role="design-request",
                    tool_name=state.tool_name,
                    content=(
                        f"{state.requirements}\n\n"
                        f"Previous attempt failed validation (attempt "
                        f"{state.design_retries}/{MAX_DESIGN_RETRIES}):\n{wit_error}"
                    ),
                    status="pending",
                    error="",
                    iteration=state.design_retries,
                    phase="designing",
                ),
                to="architect",
            )

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

        # Validate code output
        code_error = _validate_code_content(payload.content)
        if code_error:
            state.test_retries += 1
            if state.test_retries >= MAX_TEST_RETRIES:
                cleanup_thread(tid)
                return None
            state.last_error = code_error
            return HandlerResponse(
                payload=SwarmMessage(
                    role="code-request",
                    tool_name=state.tool_name,
                    content=(
                        f"WIT:\n{state.wit_content}\n\n"
                        f"Requirements:\n{state.requirements}\n\n"
                        f"Code validation failed (attempt {state.test_retries}/{MAX_TEST_RETRIES}):\n"
                        f"{code_error}"
                    ),
                    status="pending",
                    error="",
                    iteration=state.test_retries,
                    phase="coding",
                ),
                to="coder",
            )

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
        state.phase = "building"
        return HandlerResponse(
            payload=SwarmMessage(
                role="compile-request",
                tool_name=state.tool_name,
                content=state.code_content,
                status="pending",
                error="",
                iteration=state.compile_retries,
                phase="building",
            ),
            to="compile",
        )

    # ── Phase: building ───────────────────────────────────────────
    if state.phase == "building":
        if payload.role != "compile-result":
            return None
        if payload.status == "error":
            state.compile_retries += 1
            if state.compile_retries >= MAX_COMPILE_RETRIES:
                logger.warning("Max compile retries for thread %s", tid)
                cleanup_thread(tid)
                return None
            # Retry: go back to coder with compile error
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
                        f"Compile error (attempt {state.compile_retries}/{MAX_COMPILE_RETRIES}):\n"
                        f"{state.last_error}"
                    ),
                    status="pending",
                    error="",
                    iteration=state.compile_retries,
                    phase="coding",
                ),
                to="coder",
            )
        # Compilation succeeded — move to testing
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

        # Validate JSON test cases
        json_error = _validate_json_test_cases(payload.content)
        if json_error:
            state.test_retries += 1
            if state.test_retries >= MAX_TEST_RETRIES:
                cleanup_thread(tid)
                return None
            state.last_error = json_error
            return HandlerResponse(
                payload=SwarmMessage(
                    role="test-request",
                    tool_name=state.tool_name,
                    content=(
                        f"WIT:\n{state.wit_content}\n\nSource:\n{state.code_content}\n\n"
                        f"Previous test cases had format errors (attempt "
                        f"{state.test_retries}/{MAX_TEST_RETRIES}):\n{json_error}"
                    ),
                    status="pending",
                    error="",
                    iteration=state.test_retries,
                    phase="testing",
                ),
                to="tester",
            )

        state.test_content = payload.content
        state.phase = "test-running"
        return HandlerResponse(
            payload=SwarmMessage(
                role="test-run-request",
                tool_name=state.tool_name,
                content=state.test_content,
                status="pending",
                error="",
                iteration=state.test_retries,
                phase="test-running",
            ),
            to="test-runner",
        )

    # ── Phase: test-running ──────────────────────────────────────
    if state.phase == "test-running":
        if payload.role != "test-run-result":
            return None
        if payload.status == "error":
            state.test_retries += 1
            if state.test_retries >= MAX_TEST_RETRIES:
                cleanup_thread(tid)
                return None
            # Retry: go back to coder with test run failure details
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
                        f"Test run failure (attempt {state.test_retries}/{MAX_TEST_RETRIES}):\n"
                        f"{state.last_error}"
                    ),
                    status="pending",
                    error="",
                    iteration=state.test_retries,
                    phase="coding",
                ),
                to="coder",
            )
        # Tests passed — save test cases
        state.phase = "tests-saving"
        return HandlerResponse(
            payload=SwarmMessage(
                role="write-request",
                tool_name=state.tool_name,
                content=f"path:test_{state.tool_name}.json\n{state.test_content}",
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
