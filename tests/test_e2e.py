"""
test_e2e.py — End-to-end integration tests for the full organism lifecycle.

These tests exercise the complete system as a user would experience it:
- StreamPump(name=...) → pump.register() → pump.start() → pump.inject() → pump.run()
- Real pipeline processing (repair → C14N → validation → routing → handler dispatch)
- Event observation via pump.subscribe_events()
- No LLM dependency — deterministic handlers only

Run with: pytest tests/test_e2e.py -v
"""

import asyncio
from dataclasses import dataclass
from typing import List

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


# ============================================================================
# Inline payloads — no external handler imports
# ============================================================================

@xmlify
@dataclass
class Question:
    text: str


@xmlify
@dataclass
class Answer:
    text: str
    source: str


# ============================================================================
# Inline handlers
# ============================================================================

async def handle_ask(payload: Question, metadata: HandlerMetadata) -> HandlerResponse:
    """Agent handler — receives Question, forwards Answer to logger."""
    return HandlerResponse(
        payload=Answer(text=payload.text.upper(), source=metadata.own_name or "oracle"),
        to="logger",
    )


async def handle_log(payload: Answer, metadata: HandlerMetadata) -> None:
    """Sink handler — receives Answer, terminates chain."""
    return None


async def handle_echo(payload: Question, metadata: HandlerMetadata) -> HandlerResponse:
    """Tool that always responds to caller with the same payload type."""
    return HandlerResponse.respond(
        payload=Question(text=f"echo:{payload.text}"),
    )


# Stateful handler for respond/prune tests: agent that calls echo, then terminates
_agent_received: List = []


async def handle_agent(payload: Question, metadata: HandlerMetadata):
    """Agent that calls echo on first message, terminates on second Question (echo response)."""
    _agent_received.append(payload)
    if len(_agent_received) == 1:
        # First call: forward to echo
        return HandlerResponse(
            payload=Question(text=payload.text),
            to="echo",
        )
    else:
        # Got response from echo — terminate
        return None


async def handle_slow(payload: Question, metadata: HandlerMetadata):
    """Handler that takes too long."""
    await asyncio.sleep(10)
    return HandlerResponse(payload=Answer(text="late", source="slow"), to="logger")


async def handle_secret(payload: Question, metadata: HandlerMetadata) -> None:
    """A handler that should not be reachable by constrained agents."""
    return None


# ============================================================================
# Helpers
# ============================================================================


async def _drain_queue(pump):
    """Drain unprocessed items from queue so shutdown() won't block."""
    while not pump.queue.empty():
        try:
            pump.queue.get_nowait()
            pump.queue.task_done()
        except asyncio.QueueEmpty:
            break


async def run_until_events(pump, event_type, count, *, timeout=5.0):
    """Run pump until `count` events of `event_type` are collected."""
    collected = []
    done = asyncio.Event()

    def on_event(e):
        if isinstance(e, event_type):
            collected.append(e)
            if len(collected) >= count:
                done.set()

    pump.subscribe_events(on_event)
    task = asyncio.create_task(pump.run())
    try:
        await asyncio.wait_for(done.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        pass
    finally:
        pump._running = False
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await _drain_queue(pump)
    return collected


async def run_for_duration(pump, *, duration=2.0):
    """Run pump for a fixed duration, collecting all events."""
    all_events = []

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


# ============================================================================
# Fixture: fresh pump state
# ============================================================================

@pytest.fixture(autouse=True)
def _reset_globals():
    """Reset all global singletons before each test."""
    reset_registry()
    reset_stream_pump()
    reset_budget_registry()
    reset_context_buffer()
    get_prompt_registry().clear()
    _agent_received.clear()
    yield
    reset_registry()
    reset_stream_pump()
    reset_budget_registry()
    reset_context_buffer()
    get_prompt_registry().clear()


# ============================================================================
# 1. Bootstrap Lifecycle
# ============================================================================


class TestBootstrapLifecycle:

    @pytest.mark.asyncio
    async def test_start_injects_boot(self):
        """After start(), queue has 1 message (boot). Run pump, observe Boot event."""
        pump = StreamPump(name="test-boot")
        pump.register("oracle", handle_ask, Question,
                       description="Oracle", agent=True, peers=["logger"])
        pump.register("logger", handle_log, Answer, description="Logger")
        await pump.start()

        assert pump.queue.qsize() >= 1

        events = await run_until_events(pump, MessageReceivedEvent, 1, timeout=5.0)
        boot_events = [e for e in events if e.payload_type == "Boot"]
        assert len(boot_events) >= 1
        assert boot_events[0].to_id == "system.boot"

    @pytest.mark.asyncio
    async def test_system_listeners_registered(self):
        """After start(), system listeners are registered."""
        pump = StreamPump(name="test-sys")
        pump.register("oracle", handle_ask, Question,
                       description="Oracle", agent=True, peers=["logger"])
        pump.register("logger", handle_log, Answer, description="Logger")
        await pump.start()

        expected = ["system.boot", "system.todo", "system.todo-complete",
                    "system.sequence", "system.buffer"]
        for name in expected:
            assert name in pump.listeners, f"Missing system listener: {name}"

        await _drain_queue(pump)

    @pytest.mark.asyncio
    async def test_shutdown_idempotent(self):
        """Can call shutdown() twice without error."""
        pump = StreamPump(name="test-shutdown")
        pump.register("oracle", handle_ask, Question,
                       description="Oracle", agent=True, peers=["logger"])
        pump.register("logger", handle_log, Answer, description="Logger")
        await pump.start()

        # Drain queue so shutdown won't block waiting for task_done
        await _drain_queue(pump)
        await pump.shutdown()
        await pump.shutdown()  # Should not raise


# ============================================================================
# 2. Single-Hop Flow
# ============================================================================


class TestSingleHopFlow:

    @pytest.mark.asyncio
    async def test_single_handler_called(self):
        """Register oracle, inject Question. Observe MessageReceived for oracle."""
        pump = StreamPump(name="test-single")
        pump.register("oracle", handle_ask, Question,
                       description="Oracle", agent=True, peers=["logger"])
        pump.register("logger", handle_log, Answer, description="Logger")
        await pump.start()

        await pump.inject("oracle", Question(text="hello"))

        # boot→system.boot, Question→oracle, Answer→logger = 3 MessageReceivedEvents
        events = await run_until_events(pump, MessageReceivedEvent, 3, timeout=5.0)

        oracle_recv = [e for e in events if e.to_id == "oracle"]
        assert len(oracle_recv) >= 1
        assert oracle_recv[0].payload_type == "Question"

    @pytest.mark.asyncio
    async def test_handler_none_terminates_thread(self):
        """Sink handler returns None → ThreadEvent(completed)."""
        pump = StreamPump(name="test-sink")
        pump.register("logger", handle_log, Answer, description="Logger")
        await pump.start()

        await pump.inject("logger", Answer(text="done", source="test"))

        # 2 ThreadEvents: boot thread + logger thread
        events = await run_until_events(pump, ThreadEvent, 2, timeout=5.0)
        completed = [e for e in events if e.status == "completed"]
        assert len(completed) >= 2


# ============================================================================
# 3. Multi-Hop Chain
# ============================================================================


class TestMultiHopChain:

    @pytest.mark.asyncio
    async def test_two_hop_chain(self):
        """oracle→logger chain. Oracle gets Question, sends Answer to logger, logger terminates."""
        pump = StreamPump(name="test-chain")
        pump.register("oracle", handle_ask, Question,
                       description="Oracle", agent=True, peers=["logger"])
        pump.register("logger", handle_log, Answer, description="Logger")
        await pump.start()

        await pump.inject("oracle", Question(text="world"))

        # 2 ThreadEvents: boot thread + chain thread
        events = await run_until_events(pump, ThreadEvent, 2, timeout=5.0)
        completed = [e for e in events if e.status == "completed"]
        assert len(completed) >= 2

    @pytest.mark.asyncio
    async def test_respond_prunes_chain(self):
        """Agent calls echo tool. Echo uses .respond(). Agent receives Question back."""
        pump = StreamPump(name="test-respond")
        pump.register("agent", handle_agent, Question,
                       description="Agent", agent=True, peers=["echo"])
        pump.register("echo", handle_echo, Question, description="Echo tool")
        await pump.start()

        await pump.inject("agent", Question(text="ping"))

        # Boot handler returns None → ThreadEvent(completed) for boot thread.
        # Agent chain also terminates → another ThreadEvent(completed).
        # Wait for 2 ThreadEvents to cover boot + agent chain.
        events = await run_until_events(pump, ThreadEvent, 2, timeout=5.0)
        completed = [e for e in events if e.status == "completed"]
        assert len(completed) >= 2

        # Agent should have received two Questions: the original and the echo response
        assert len(_agent_received) == 2
        assert isinstance(_agent_received[0], Question)
        assert _agent_received[0].text == "ping"
        assert isinstance(_agent_received[1], Question)
        assert _agent_received[1].text == "echo:ping"


# ============================================================================
# 4. Calculator Tool Integration
# ============================================================================


class TestCalculatorIntegration:

    @pytest.mark.asyncio
    async def test_calculator_as_listener(self):
        """Calculator handler processes 2+3 and sends CalculateResult via .respond()."""
        from xml_pipeline.tools.calculate import Calculate, CalculateResult, handle_calculate

        pump = StreamPump(name="test-calc")
        pump.register("calculator", handle_calculate, Calculate,
                       description="Math tool")
        await pump.start()

        sent_events = []

        def on_event(e):
            if isinstance(e, MessageSentEvent):
                sent_events.append(e)

        pump.subscribe_events(on_event)
        task = asyncio.create_task(pump.run())
        await pump.inject("calculator", Calculate(expression="2+3"))

        await asyncio.sleep(1.5)
        pump._running = False
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await _drain_queue(pump)

        calc_sent = [e for e in sent_events if e.payload_type == "CalculateResult"]
        assert len(calc_sent) >= 1
        result = calc_sent[0].payload
        assert isinstance(result, CalculateResult)
        assert result.result == "5"
        assert result.error == ""

    @pytest.mark.asyncio
    async def test_calculator_error_roundtrip(self):
        """Calculator handles division by zero — returns CalculateResult with error."""
        from xml_pipeline.tools.calculate import Calculate, CalculateResult, handle_calculate

        pump = StreamPump(name="test-calc-err")
        pump.register("calculator", handle_calculate, Calculate,
                       description="Math tool")
        await pump.start()

        sent_events = []

        def on_event(e):
            if isinstance(e, MessageSentEvent):
                sent_events.append(e)

        pump.subscribe_events(on_event)
        task = asyncio.create_task(pump.run())
        await pump.inject("calculator", Calculate(expression="1/0"))

        await asyncio.sleep(1.5)
        pump._running = False
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await _drain_queue(pump)

        calc_sent = [e for e in sent_events if e.payload_type == "CalculateResult"]
        assert len(calc_sent) >= 1
        result = calc_sent[0].payload
        assert isinstance(result, CalculateResult)
        assert result.error != ""


# ============================================================================
# 5. Peer Constraint Enforcement
# ============================================================================


class TestPeerConstraints:

    @pytest.mark.asyncio
    async def test_peer_violation_blocked(self):
        """Agent with peers=[logger] tries to send to 'secret' → blocked, secret never receives."""
        async def handle_rogue_agent(payload: Question, metadata: HandlerMetadata):
            # Always tries to send to forbidden target
            return HandlerResponse(
                payload=Question(text="sneaky"),
                to="secret",
            )

        pump = StreamPump(name="test-peer-block")
        pump.register("rogue", handle_rogue_agent, Question,
                       description="Rogue agent", agent=True, peers=["logger"])
        pump.register("logger", handle_log, Answer, description="Logger")
        pump.register("secret", handle_secret, Question, description="Secret")
        await pump.start()

        await pump.inject("rogue", Question(text="attack"))

        all_events = await run_for_duration(pump, duration=2.0)

        # Secret handler should NOT have received any message
        secret_recv = [e for e in all_events
                       if isinstance(e, MessageReceivedEvent) and e.to_id == "secret"]
        assert len(secret_recv) == 0, "Secret handler should not receive messages from constrained agent"

        # Rogue agent should have received the initial Question
        rogue_recv = [e for e in all_events
                      if isinstance(e, MessageReceivedEvent) and e.to_id == "rogue"]
        assert len(rogue_recv) >= 1

    @pytest.mark.asyncio
    async def test_peer_table_restricts(self):
        """Peer table restricts agent's peers below the YAML ceiling."""
        search_reached = []

        async def handle_table_agent(payload: Question, metadata: HandlerMetadata):
            return HandlerResponse(
                payload=Question(text="search me"),
                to="search",
            )

        async def handle_search(payload: Question, metadata: HandlerMetadata) -> None:
            search_reached.append(True)
            return None

        pump = StreamPump(name="test-table")
        pump.register("agent", handle_table_agent, Question,
                       description="Agent", agent=True,
                       peers=["search", "logger"])
        pump.register("search", handle_search, Question, description="Search")
        pump.register("logger", handle_log, Answer, description="Logger")
        await pump.start()

        # Register a restricted table that only allows "logger"
        pump.register_peer_table("restricted", {
            "agent": ["logger"],
        })

        await pump.inject("agent", Question(text="find"), routing_table="restricted")

        all_events = await run_for_duration(pump, duration=2.0)

        # Search should NOT be reached because the peer table restricts to logger only
        search_recv = [e for e in all_events
                       if isinstance(e, MessageReceivedEvent) and e.to_id == "search"]
        assert len(search_recv) == 0, "Search should not be reachable under restricted table"
        assert len(search_reached) == 0


# ============================================================================
# 6. Handler Timeout
# ============================================================================


class TestHandlerTimeout:

    @pytest.mark.asyncio
    async def test_handler_timeout(self):
        """Slow handler triggers timeout → AgentStateEvent(error)."""
        pump = StreamPump(name="test-timeout")
        pump.register("slow", handle_slow, Question,
                       description="Slow handler", timeout=0.5)
        pump.register("logger", handle_log, Answer, description="Logger")
        await pump.start()

        await pump.inject("slow", Question(text="wait"))

        all_events = await run_for_duration(pump, duration=3.0)

        error_events = [e for e in all_events
                        if isinstance(e, AgentStateEvent) and e.state == "error"]
        assert len(error_events) >= 1
        assert error_events[0].agent_name == "slow"


# ============================================================================
# 7. Full Lifecycle Events
# ============================================================================


class TestEventSequence:

    @pytest.mark.asyncio
    async def test_event_sequence(self):
        """Run single-hop chain. Verify event ordering: processing→received→sent→idle."""
        pump = StreamPump(name="test-events")
        pump.register("oracle", handle_ask, Question,
                       description="Oracle", agent=True, peers=["logger"])
        pump.register("logger", handle_log, Answer, description="Logger")
        await pump.start()

        all_events = []

        def on_event(e):
            all_events.append(e)

        pump.subscribe_events(on_event)
        task = asyncio.create_task(pump.run())

        # Drain boot first
        await asyncio.sleep(0.5)
        boot_count = len(all_events)

        await pump.inject("oracle", Question(text="test"))
        await asyncio.sleep(2.0)

        pump._running = False
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await _drain_queue(pump)

        # Events after boot
        post_boot = all_events[boot_count:]

        agent_states = [e for e in post_boot if isinstance(e, AgentStateEvent)]
        recv_events = [e for e in post_boot if isinstance(e, MessageReceivedEvent)]
        sent_events = [e for e in post_boot if isinstance(e, MessageSentEvent)]
        thread_events = [e for e in post_boot if isinstance(e, ThreadEvent)]

        # Oracle should process and send
        oracle_processing = [e for e in agent_states
                             if e.agent_name == "oracle" and e.state == "processing"]
        assert len(oracle_processing) >= 1

        oracle_recv = [e for e in recv_events if e.to_id == "oracle"]
        assert len(oracle_recv) >= 1

        oracle_sent = [e for e in sent_events if e.from_id == "oracle"]
        assert len(oracle_sent) >= 1
        assert oracle_sent[0].payload_type == "Answer"

        # Logger should receive and terminate thread
        logger_recv = [e for e in recv_events if e.to_id == "logger"]
        assert len(logger_recv) >= 1

        completed = [e for e in thread_events if e.status == "completed"]
        assert len(completed) >= 1

        # Verify ordering: oracle processing comes before oracle idle
        oracle_agent_events = [e for e in post_boot
                               if isinstance(e, AgentStateEvent) and e.agent_name == "oracle"]
        if len(oracle_agent_events) >= 2:
            assert oracle_agent_events[0].state == "processing"
            assert oracle_agent_events[1].state == "idle"


# ============================================================================
# 8. Concurrent Threads
# ============================================================================


class TestConcurrentThreads:

    @pytest.mark.asyncio
    async def test_three_concurrent_messages(self):
        """Inject 3 messages. All 3 are received by the handler."""
        received_texts = []

        async def handle_counting_log(payload: Answer, metadata: HandlerMetadata) -> None:
            received_texts.append(payload.text)
            return None

        pump = StreamPump(name="test-concurrent")
        pump.register("logger", handle_counting_log, Answer, description="Logger")
        await pump.start()

        for i in range(3):
            await pump.inject("logger", Answer(text=f"msg-{i}", source="test"))

        all_events = await run_for_duration(pump, duration=2.0)

        # All 3 messages should have been processed by the handler
        assert len(received_texts) == 3
        assert set(received_texts) == {"msg-0", "msg-1", "msg-2"}

        # Should have MessageReceivedEvents for logger (at least 3)
        logger_recv = [e for e in all_events
                       if isinstance(e, MessageReceivedEvent) and e.to_id == "logger"]
        assert len(logger_recv) >= 3


# ============================================================================
# 9. YAML Config
# ============================================================================


class TestYAMLConfig:

    @pytest.mark.asyncio
    async def test_from_yaml_boots(self):
        """Load config/organism.yaml via from_yaml(). Verify listeners registered."""
        pump = await StreamPump.from_yaml("config/organism.yaml")

        assert pump.config.name == "hello-world"
        assert "greeter" in pump.listeners
        assert "shouter" in pump.listeners
        assert "response-handler" in pump.listeners

        # System listeners present
        assert "system.boot" in pump.listeners
        assert "system.todo" in pump.listeners

        # Don't run pipeline (avoids LLM dependency)
        await _drain_queue(pump)
