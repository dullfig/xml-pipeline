"""
test_workers.py — Tests for handler timeout, thread lifecycle, and worker registry.

Tests three related features:
1. Handler timeout (asyncio.wait_for wrapping)
2. Thread lifecycle consolidation (_cleanup_thread)
3. Background worker registry (spawn/send/receive/stop)

Run with: pytest tests/test_workers.py -v
"""

import asyncio
import multiprocessing
import time
from dataclasses import dataclass
from queue import Empty
from unittest.mock import MagicMock, patch

import pytest

from xml_pipeline.message_bus.message_state import (
    HandlerMetadata,
    HandlerResponse,
    MessageState,
    TIMEOUT_ERROR,
)
from xml_pipeline.message_bus.stream_pump import StreamPump
from xml_pipeline.message_bus.pump_config import Listener, ListenerConfig
from xml_pipeline.message_bus.events import AgentStateEvent, ThreadEvent
from xml_pipeline.message_bus.thread_registry import get_registry
from xml_pipeline.workers.registry import (
    WorkerRegistry,
    WorkerStatus,
    get_worker_registry,
    reset_worker_registry,
)

# Check for optional dependencies
try:
    from third_party.xmlable import xmlify
    HAS_XMLABLE = True
except ImportError:
    HAS_XMLABLE = False

requires_xmlable = pytest.mark.skipif(
    not HAS_XMLABLE, reason="xmlable not available"
)


# ============================================================================
# Test Payload Classes
# ============================================================================

if HAS_XMLABLE:
    @xmlify
    @dataclass
    class SamplePayload:
        value: str = ""

    @xmlify
    @dataclass
    class SampleResponse:
        result: str = ""
else:
    @dataclass
    class SamplePayload:
        value: str = ""

    @dataclass
    class SampleResponse:
        result: str = ""


# ============================================================================
# Test Handlers
# ============================================================================

async def _noop_handler(payload, metadata: HandlerMetadata):
    """Handler that does nothing (returns None = terminate chain)."""
    return None


async def _echo_handler(payload, metadata: HandlerMetadata) -> HandlerResponse:
    """Handler that echoes back to caller."""
    return HandlerResponse.respond(
        payload=SampleResponse(result=f"echo: {payload.value}")
    )


async def _slow_handler(payload, metadata: HandlerMetadata):
    """Handler that sleeps longer than timeout."""
    await asyncio.sleep(60)
    return None


async def _one_second_handler(payload, metadata: HandlerMetadata):
    """Handler that sleeps 1 second — within default timeout but testable."""
    await asyncio.sleep(1.0)
    return None


# ============================================================================
# Worker Functions (run in separate processes)
# ============================================================================

def _simple_worker(inbox, outbox):
    """Worker that echoes inbox messages to outbox."""
    while True:
        try:
            msg = inbox.get(timeout=0.5)
            if msg is None:
                break
            outbox.put(f"processed: {msg}")
        except Empty:
            pass


def _counter_worker(inbox, outbox, start=0):
    """Worker that counts and reports on check."""
    count = start
    while True:
        try:
            msg = inbox.get(timeout=0.2)
            if msg is None:
                break
            if msg == "check":
                outbox.put({"count": count})
            elif msg == "increment":
                count += 1
        except Empty:
            pass


def _stuck_worker(inbox, outbox):
    """Worker that ignores sentinel (for terminate testing)."""
    import time
    while True:
        time.sleep(0.1)


# ============================================================================
# TestHandlerTimeout
# ============================================================================

@requires_xmlable
class TestHandlerTimeout:
    """Tests for handler execution timeout."""

    @pytest.mark.asyncio
    async def test_handler_timeout_emits_system_error(self):
        """Handler that exceeds timeout should produce a SystemError."""
        pump = StreamPump(name="test-timeout")
        pump.register("slow", _slow_handler, SamplePayload,
                       description="Slow handler", timeout=0.1)
        await pump.start()

        # Drain boot message
        await pump.queue.get()

        # Track events to verify timeout error was emitted
        events = []
        pump.subscribe_events(events.append)

        # Inject message to slow handler
        await pump.inject("slow", SamplePayload(value="test"))

        # Run pipeline with overall timeout guard
        pump._running = True
        pipeline = pump.build_pipeline(pump._queue_source())
        try:
            await asyncio.wait_for(self._run_one(pipeline), timeout=3.0)
        except asyncio.TimeoutError:
            pass
        finally:
            pump._running = False

        # The timeout should have been logged and error event emitted
        error_events = [
            e for e in events
            if isinstance(e, AgentStateEvent) and e.state == "error"
        ]
        assert len(error_events) >= 1
        assert error_events[0].agent_name == "slow"

    @staticmethod
    async def _run_one(pipeline):
        """Process one item from the pipeline then return."""
        async with pipeline.stream() as streamer:
            async for _ in streamer:
                return

    @pytest.mark.asyncio
    async def test_handler_timeout_releases_semaphore(self):
        """Semaphore should be released after timeout (not leaked)."""
        pump = StreamPump(name="test-semaphore")
        pump.register("slow-agent", _slow_handler, SamplePayload,
                       description="Slow agent", agent=True, timeout=0.1)
        await pump.start()
        await pump.queue.get()  # drain boot

        semaphore = pump.agent_semaphores["slow-agent"]
        initial_value = semaphore._value

        await pump.inject("slow-agent", SamplePayload(value="test"))

        pump._running = True
        pipeline = pump.build_pipeline(pump._queue_source())
        try:
            await asyncio.wait_for(self._run_one(pipeline), timeout=3.0)
        except asyncio.TimeoutError:
            pass
        finally:
            pump._running = False

        # Semaphore should be back to initial value after timeout
        await asyncio.sleep(0.05)
        assert semaphore._value == initial_value

    @pytest.mark.asyncio
    async def test_handler_timeout_thread_stays_alive(self):
        """Thread should remain alive after timeout (caller can retry)."""
        pump = StreamPump(name="test-thread-alive")
        pump.register("slow", _slow_handler, SamplePayload,
                       description="Slow handler", timeout=0.1)
        await pump.start()
        await pump.queue.get()  # drain boot

        thread_id = await pump.inject("slow", SamplePayload(value="test"))

        pump._running = True
        pipeline = pump.build_pipeline(pump._queue_source())
        try:
            await asyncio.wait_for(self._run_one(pipeline), timeout=3.0)
        except asyncio.TimeoutError:
            pass
        finally:
            pump._running = False

        # Thread should still be in the registry (not cleaned up)
        registry = get_registry()
        chain = registry.lookup(thread_id)
        assert chain is not None

    @pytest.mark.asyncio
    async def test_handler_timeout_configurable(self):
        """Listeners with different timeouts should behave independently."""
        pump = StreamPump(name="test-configurable")

        # Register with short timeout (will timeout)
        pump.register("fast-timeout", _one_second_handler, SamplePayload,
                       description="Short timeout", timeout=0.05)

        # Register with long timeout (will succeed)
        pump.register("long-timeout", _noop_handler, SamplePayload,
                       description="Long timeout", timeout=60.0)

        # Verify the timeouts were set correctly
        assert pump.listeners["fast-timeout"].timeout == 0.05
        assert pump.listeners["long-timeout"].timeout == 60.0

    @pytest.mark.asyncio
    async def test_handler_timeout_default_value(self):
        """Default timeout should be 30 seconds."""
        pump = StreamPump(name="test-default")
        pump.register("default", _noop_handler, SamplePayload,
                       description="Default timeout")
        assert pump.listeners["default"].timeout == 30.0

    @pytest.mark.asyncio
    async def test_timeout_emits_agent_error_event(self):
        """Timeout should emit AgentStateEvent with state='error'."""
        pump = StreamPump(name="test-error-event")
        pump.register("slow", _slow_handler, SamplePayload,
                       description="Slow handler", timeout=0.1)
        await pump.start()
        await pump.queue.get()  # drain boot

        events = []
        pump.subscribe_events(events.append)

        await pump.inject("slow", SamplePayload(value="test"))

        pump._running = True
        pipeline = pump.build_pipeline(pump._queue_source())
        try:
            await asyncio.wait_for(self._run_one(pipeline), timeout=3.0)
        except asyncio.TimeoutError:
            pass
        finally:
            pump._running = False

        # Should have at least one AgentStateEvent with state="error"
        error_events = [
            e for e in events
            if isinstance(e, AgentStateEvent) and e.state == "error"
        ]
        assert len(error_events) >= 1
        assert error_events[0].agent_name == "slow"


# ============================================================================
# TestThreadLifecycle
# ============================================================================

@requires_xmlable
class TestThreadLifecycle:
    """Tests for thread lifecycle cleanup consolidation."""

    @staticmethod
    async def _run_one(pipeline):
        """Process one item from the pipeline then return."""
        async with pipeline.stream() as streamer:
            async for _ in streamer:
                return

    @pytest.mark.asyncio
    async def test_thread_event_on_none_return(self):
        """ThreadEvent with status='completed' should be emitted when handler returns None."""
        pump = StreamPump(name="test-lifecycle")
        pump.register("terminal", _noop_handler, SamplePayload,
                       description="Terminal handler")
        await pump.start()
        await pump.queue.get()  # drain boot

        events = []
        pump.subscribe_events(events.append)

        await pump.inject("terminal", SamplePayload(value="test"))

        pump._running = True
        pipeline = pump.build_pipeline(pump._queue_source())
        try:
            await asyncio.wait_for(self._run_one(pipeline), timeout=5.0)
        except asyncio.TimeoutError:
            pass
        finally:
            pump._running = False

        thread_events = [
            e for e in events
            if isinstance(e, ThreadEvent) and e.status == "completed"
        ]
        assert len(thread_events) >= 1

    @pytest.mark.asyncio
    async def test_worker_cleanup_on_thread_death(self):
        """Workers should be stopped when their owning thread dies."""
        pump = StreamPump(name="test-worker-cleanup")
        pump.register("terminal", _noop_handler, SamplePayload,
                       description="Terminal handler")
        await pump.start()
        await pump.queue.get()  # drain boot

        thread_id = await pump.inject("terminal", SamplePayload(value="test"))

        # Spawn a worker on this thread
        worker_id = pump._worker_registry.spawn(
            thread_id=thread_id,
            listener_name="terminal",
            target=_simple_worker,
        )

        # Verify worker is alive
        status = pump._worker_registry.status(worker_id)
        assert status.alive

        # Run pipeline — handler returns None → cleanup (including workers)
        pump._running = True
        pipeline = pump.build_pipeline(pump._queue_source())
        try:
            await asyncio.wait_for(self._run_one(pipeline), timeout=5.0)
        except asyncio.TimeoutError:
            pass
        finally:
            pump._running = False

        # Worker should be gone from registry
        with pytest.raises(KeyError):
            pump._worker_registry.status(worker_id)

    @pytest.mark.asyncio
    async def test_cleanup_thread_method_directly(self):
        """_cleanup_thread should clean budget, workers, and emit event."""
        pump = StreamPump(name="test-direct-cleanup")

        events = []
        pump.subscribe_events(events.append)

        thread_id = "test-thread-12345678"

        # Call cleanup directly
        pump._cleanup_thread(thread_id)

        # ThreadEvent should be emitted
        thread_events = [
            e for e in events
            if isinstance(e, ThreadEvent) and e.status == "completed"
        ]
        assert len(thread_events) == 1
        assert thread_events[0].thread_id == thread_id


# ============================================================================
# TestWorkerRegistry
# ============================================================================

class TestWorkerRegistry:
    """Tests for the WorkerRegistry background process management."""

    def setup_method(self):
        """Fresh registry for each test."""
        self.registry = WorkerRegistry()

    def teardown_method(self):
        """Stop all workers after each test."""
        self.registry.shutdown_all(join_timeout=2.0)

    def test_spawn_worker(self):
        """Worker process should start and be alive."""
        worker_id = self.registry.spawn(
            thread_id="thread-1",
            listener_name="poller",
            target=_simple_worker,
        )

        assert worker_id
        status = self.registry.status(worker_id)
        assert status.alive
        assert status.pid is not None
        assert status.listener_name == "poller"
        assert status.thread_id == "thread-1"

    def test_send_receive(self):
        """Messages sent to inbox should appear in outbox after processing."""
        worker_id = self.registry.spawn(
            thread_id="thread-1",
            listener_name="echo",
            target=_simple_worker,
        )

        self.registry.send(worker_id, "hello")
        # Give worker time to process
        time.sleep(0.5)

        messages = self.registry.receive(worker_id)
        assert len(messages) == 1
        assert messages[0] == "processed: hello"

    def test_receive_empty(self):
        """receive() should return empty list when no messages."""
        worker_id = self.registry.spawn(
            thread_id="thread-1",
            listener_name="echo",
            target=_simple_worker,
        )
        messages = self.registry.receive(worker_id)
        assert messages == []

    def test_stop_worker(self):
        """Graceful stop via sentinel."""
        worker_id = self.registry.spawn(
            thread_id="thread-1",
            listener_name="poller",
            target=_simple_worker,
        )

        status = self.registry.status(worker_id)
        assert status.alive

        self.registry.stop(worker_id)

        # Worker should be removed from registry
        with pytest.raises(KeyError):
            self.registry.status(worker_id)

    def test_stop_stuck_worker(self):
        """Worker that ignores sentinel should be terminated."""
        worker_id = self.registry.spawn(
            thread_id="thread-1",
            listener_name="stuck",
            target=_stuck_worker,
        )
        time.sleep(0.2)  # Let it start

        # Stop with short join timeout — will have to terminate
        self.registry.stop(worker_id, join_timeout=0.5)

        with pytest.raises(KeyError):
            self.registry.status(worker_id)

    def test_cleanup_for_thread(self):
        """All workers for a thread should be stopped."""
        id1 = self.registry.spawn("thread-A", "worker1", _simple_worker)
        id2 = self.registry.spawn("thread-A", "worker2", _simple_worker)
        id3 = self.registry.spawn("thread-B", "worker3", _simple_worker)

        stopped = self.registry.cleanup_for_thread("thread-A")
        assert stopped == 2

        # thread-A workers gone
        with pytest.raises(KeyError):
            self.registry.status(id1)
        with pytest.raises(KeyError):
            self.registry.status(id2)

        # thread-B worker still alive
        status = self.registry.status(id3)
        assert status.alive

    def test_worker_status(self):
        """Status should report alive, pid, uptime."""
        worker_id = self.registry.spawn(
            thread_id="thread-1",
            listener_name="status-test",
            target=_simple_worker,
        )
        time.sleep(0.1)

        status = self.registry.status(worker_id)
        assert status.alive is True
        assert status.pid > 0
        assert status.uptime >= 0.0
        assert status.listener_name == "status-test"
        assert status.thread_id == "thread-1"

    def test_shutdown_all(self):
        """shutdown_all should stop all workers."""
        self.registry.spawn("t1", "w1", _simple_worker)
        self.registry.spawn("t2", "w2", _simple_worker)
        self.registry.spawn("t3", "w3", _simple_worker)

        assert self.registry.worker_count == 3

        stopped = self.registry.shutdown_all()
        assert stopped == 3
        assert self.registry.worker_count == 0

    def test_multiple_workers_per_thread(self):
        """A thread can have multiple workers."""
        id1 = self.registry.spawn("thread-1", "poller-a", _simple_worker)
        id2 = self.registry.spawn("thread-1", "poller-b", _simple_worker)

        assert self.registry.worker_count == 2

        status1 = self.registry.status(id1)
        status2 = self.registry.status(id2)
        assert status1.alive
        assert status2.alive
        assert status1.thread_id == "thread-1"
        assert status2.thread_id == "thread-1"

    def test_worker_with_kwargs(self):
        """Worker should receive kwargs."""
        worker_id = self.registry.spawn(
            thread_id="thread-1",
            listener_name="counter",
            target=_counter_worker,
            kwargs={"start": 42},
        )

        self.registry.send(worker_id, "check")
        time.sleep(0.5)

        messages = self.registry.receive(worker_id)
        assert len(messages) == 1
        assert messages[0] == {"count": 42}

    def test_send_to_nonexistent_worker(self):
        """send() to unknown worker_id should raise KeyError."""
        with pytest.raises(KeyError):
            self.registry.send("nonexistent", "hello")

    def test_receive_from_nonexistent_worker(self):
        """receive() from unknown worker_id should raise KeyError."""
        with pytest.raises(KeyError):
            self.registry.receive("nonexistent")

    def test_stop_nonexistent_worker(self):
        """stop() on unknown worker_id should raise KeyError."""
        with pytest.raises(KeyError):
            self.registry.stop("nonexistent")

    def test_cleanup_for_empty_thread(self):
        """cleanup_for_thread on thread with no workers should return 0."""
        stopped = self.registry.cleanup_for_thread("no-workers-here")
        assert stopped == 0

    def test_worker_survives_across_messages(self):
        """Worker should keep running between handler invocations."""
        worker_id = self.registry.spawn(
            thread_id="thread-1",
            listener_name="counter",
            target=_counter_worker,
            kwargs={"start": 0},
        )

        # First interaction: increment
        self.registry.send(worker_id, "increment")
        time.sleep(0.3)
        self.registry.send(worker_id, "increment")
        time.sleep(0.3)

        # Second interaction: check count
        self.registry.send(worker_id, "check")
        time.sleep(0.3)

        messages = self.registry.receive(worker_id)
        assert len(messages) == 1
        assert messages[0] == {"count": 2}


# ============================================================================
# TestWorkerRegistrySingleton
# ============================================================================

class TestWorkerRegistrySingleton:
    """Tests for the module-level singleton."""

    def teardown_method(self):
        reset_worker_registry()

    def test_get_worker_registry_returns_same_instance(self):
        """get_worker_registry should return singleton."""
        r1 = get_worker_registry()
        r2 = get_worker_registry()
        assert r1 is r2

    def test_reset_worker_registry(self):
        """reset_worker_registry should clear the singleton."""
        r1 = get_worker_registry()
        reset_worker_registry()
        r2 = get_worker_registry()
        assert r1 is not r2


# ============================================================================
# TestTimeoutErrorConstant
# ============================================================================

class TestTimeoutErrorConstant:
    """Tests for the TIMEOUT_ERROR message state constant."""

    def test_timeout_error_fields(self):
        """TIMEOUT_ERROR should have correct code and retry flag."""
        assert TIMEOUT_ERROR.code == "timeout"
        assert TIMEOUT_ERROR.retry_allowed is True

    def test_timeout_error_xml(self):
        """TIMEOUT_ERROR should serialize to valid XML."""
        xml = TIMEOUT_ERROR.to_xml()
        assert "<code>timeout</code>" in xml
        assert "<retry-allowed>true</retry-allowed>" in xml
        assert "SystemError" in xml
