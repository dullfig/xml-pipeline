"""
test_context_buffer.py — Tests for the AI agent virtual memory manager.

Tests:
1. Append-only semantics
2. Immutability guarantees
3. Thread isolation
4. Slot references
5. GC and limits
"""

import pytest
import uuid
from dataclasses import dataclass, FrozenInstanceError

from agentserver.memory.context_buffer import (
    ContextBuffer,
    ThreadContext,
    BufferSlot,
    SlotMetadata,
    get_context_buffer,
    slot_to_handler_metadata,
)


# Test payload classes
@dataclass
class TestPayload:
    message: str
    value: int = 0


@dataclass(frozen=True)
class FrozenPayload:
    message: str


class TestBufferSlotImmutability:
    """Test that buffer slots cannot be modified."""

    def test_slot_is_frozen(self):
        """BufferSlot should be frozen (immutable)."""
        metadata = SlotMetadata(
            thread_id="t1",
            from_id="sender",
            to_id="receiver",
            slot_index=0,
            timestamp="2024-01-01T00:00:00Z",
            payload_type="TestPayload",
        )
        slot = BufferSlot(payload=TestPayload(message="hello"), metadata=metadata)

        # Cannot modify slot attributes
        with pytest.raises(FrozenInstanceError):
            slot.metadata = None

    def test_slot_metadata_is_frozen(self):
        """SlotMetadata should be frozen (immutable)."""
        metadata = SlotMetadata(
            thread_id="t1",
            from_id="sender",
            to_id="receiver",
            slot_index=0,
            timestamp="2024-01-01T00:00:00Z",
            payload_type="TestPayload",
        )

        with pytest.raises(FrozenInstanceError):
            metadata.thread_id = "modified"

    def test_payload_reference_preserved(self):
        """Slot should preserve reference to original payload."""
        payload = TestPayload(message="original")
        metadata = SlotMetadata(
            thread_id="t1",
            from_id="sender",
            to_id="receiver",
            slot_index=0,
            timestamp="2024-01-01T00:00:00Z",
            payload_type="TestPayload",
        )
        slot = BufferSlot(payload=payload, metadata=metadata)

        # Same reference
        assert slot.payload is payload


class TestThreadContext:
    """Test single-thread context buffer."""

    def test_append_creates_slot(self):
        """Appending returns a BufferSlot."""
        ctx = ThreadContext("thread-1")

        slot = ctx.append(
            payload=TestPayload(message="test"),
            from_id="sender",
            to_id="receiver",
        )

        assert isinstance(slot, BufferSlot)
        assert slot.payload.message == "test"
        assert slot.from_id == "sender"
        assert slot.to_id == "receiver"
        assert slot.index == 0

    def test_append_increments_index(self):
        """Each append gets a new index."""
        ctx = ThreadContext("thread-1")

        slot1 = ctx.append(TestPayload("a"), "s", "r")
        slot2 = ctx.append(TestPayload("b"), "s", "r")
        slot3 = ctx.append(TestPayload("c"), "s", "r")

        assert slot1.index == 0
        assert slot2.index == 1
        assert slot3.index == 2
        assert len(ctx) == 3

    def test_getitem_returns_slot(self):
        """Can access slots by index."""
        ctx = ThreadContext("thread-1")

        ctx.append(TestPayload("first"), "s", "r")
        ctx.append(TestPayload("second"), "s", "r")

        assert ctx[0].payload.message == "first"
        assert ctx[1].payload.message == "second"

    def test_iteration(self):
        """Can iterate over all slots."""
        ctx = ThreadContext("thread-1")

        ctx.append(TestPayload("a"), "s", "r")
        ctx.append(TestPayload("b"), "s", "r")
        ctx.append(TestPayload("c"), "s", "r")

        messages = [slot.payload.message for slot in ctx]
        assert messages == ["a", "b", "c"]

    def test_get_by_type(self):
        """Can filter slots by payload type."""
        ctx = ThreadContext("thread-1")

        ctx.append(TestPayload("test"), "s", "r")
        ctx.append(FrozenPayload("frozen"), "s", "r")
        ctx.append(TestPayload("test2"), "s", "r")

        test_slots = ctx.get_by_type("TestPayload")
        assert len(test_slots) == 2

        frozen_slots = ctx.get_by_type("FrozenPayload")
        assert len(frozen_slots) == 1

    def test_get_from(self):
        """Can filter slots by sender."""
        ctx = ThreadContext("thread-1")

        ctx.append(TestPayload("a"), "alice", "r")
        ctx.append(TestPayload("b"), "bob", "r")
        ctx.append(TestPayload("c"), "alice", "r")

        alice_slots = ctx.get_from("alice")
        assert len(alice_slots) == 2

        bob_slots = ctx.get_from("bob")
        assert len(bob_slots) == 1


class TestContextBuffer:
    """Test global context buffer."""

    def test_get_or_create_thread(self):
        """get_or_create_thread creates new thread if needed."""
        buffer = ContextBuffer()

        ctx1 = buffer.get_or_create_thread("thread-1")
        ctx2 = buffer.get_or_create_thread("thread-1")
        ctx3 = buffer.get_or_create_thread("thread-2")

        assert ctx1 is ctx2  # Same thread
        assert ctx1 is not ctx3  # Different thread

    def test_append_to_thread(self):
        """append() adds to correct thread."""
        buffer = ContextBuffer()

        slot1 = buffer.append("t1", TestPayload("a"), "s", "r")
        slot2 = buffer.append("t2", TestPayload("b"), "s", "r")
        slot3 = buffer.append("t1", TestPayload("c"), "s", "r")

        t1 = buffer.get_thread("t1")
        t2 = buffer.get_thread("t2")

        assert len(t1) == 2
        assert len(t2) == 1
        assert t1[0].payload.message == "a"
        assert t1[1].payload.message == "c"
        assert t2[0].payload.message == "b"

    def test_thread_isolation(self):
        """Threads cannot see each other's slots."""
        buffer = ContextBuffer()

        buffer.append("thread-a", TestPayload("secret-a"), "s", "r")
        buffer.append("thread-b", TestPayload("secret-b"), "s", "r")

        thread_a = buffer.get_thread("thread-a")
        thread_b = buffer.get_thread("thread-b")

        # Each thread only sees its own messages
        a_messages = [s.payload.message for s in thread_a]
        b_messages = [s.payload.message for s in thread_b]

        assert a_messages == ["secret-a"]
        assert b_messages == ["secret-b"]

    def test_delete_thread(self):
        """delete_thread removes thread context."""
        buffer = ContextBuffer()

        buffer.append("t1", TestPayload("test"), "s", "r")
        assert buffer.thread_exists("t1")

        result = buffer.delete_thread("t1")
        assert result is True
        assert not buffer.thread_exists("t1")

    def test_max_slots_limit(self):
        """Exceeding max_slots_per_thread raises MemoryError."""
        buffer = ContextBuffer()
        buffer.max_slots_per_thread = 3

        buffer.append("t1", TestPayload("1"), "s", "r")
        buffer.append("t1", TestPayload("2"), "s", "r")
        buffer.append("t1", TestPayload("3"), "s", "r")

        with pytest.raises(MemoryError) as exc_info:
            buffer.append("t1", TestPayload("4"), "s", "r")

        assert "exceeded max slots" in str(exc_info.value)

    def test_max_threads_gc(self):
        """Exceeding max_threads triggers GC of oldest thread."""
        buffer = ContextBuffer()
        buffer.max_threads = 2

        buffer.append("t1", TestPayload("first"), "s", "r")
        buffer.append("t2", TestPayload("second"), "s", "r")

        # Adding third thread should GC the oldest
        buffer.append("t3", TestPayload("third"), "s", "r")

        stats = buffer.get_stats()
        assert stats["thread_count"] == 2

        # t1 should be gone (oldest)
        assert not buffer.thread_exists("t1")
        assert buffer.thread_exists("t2")
        assert buffer.thread_exists("t3")

    def test_get_stats(self):
        """get_stats returns buffer statistics."""
        buffer = ContextBuffer()

        buffer.append("t1", TestPayload("a"), "s", "r")
        buffer.append("t1", TestPayload("b"), "s", "r")
        buffer.append("t2", TestPayload("c"), "s", "r")

        stats = buffer.get_stats()

        assert stats["thread_count"] == 2
        assert stats["total_slots"] == 3


class TestHandlerMetadataAdapter:
    """Test conversion from SlotMetadata to HandlerMetadata."""

    def test_slot_to_handler_metadata(self):
        """slot_to_handler_metadata converts correctly."""
        buffer = ContextBuffer()

        slot = buffer.append(
            thread_id="t1",
            payload=TestPayload("test"),
            from_id="sender",
            to_id="receiver",
            own_name="test-agent",
            is_self_call=True,
            usage_instructions="instructions here",
            todo_nudge="nudge here",
        )

        metadata = slot_to_handler_metadata(slot)

        assert metadata.thread_id == "t1"
        assert metadata.from_id == "sender"
        assert metadata.own_name == "test-agent"
        assert metadata.is_self_call is True
        assert metadata.usage_instructions == "instructions here"
        assert metadata.todo_nudge == "nudge here"


class TestSingleton:
    """Test singleton behavior."""

    def test_get_context_buffer_singleton(self):
        """get_context_buffer returns same instance."""
        buf1 = get_context_buffer()
        buf2 = get_context_buffer()

        assert buf1 is buf2

    def test_clear_resets_state(self):
        """clear() removes all threads."""
        buffer = get_context_buffer()
        buffer.append("test-thread", TestPayload("test"), "s", "r")

        buffer.clear()

        assert not buffer.thread_exists("test-thread")
        assert buffer.get_stats()["thread_count"] == 0


class TestPumpIntegration:
    """Test context buffer integration with StreamPump."""

    @pytest.mark.asyncio
    async def test_buffer_records_messages_during_flow(self):
        """Context buffer should record messages as they flow through pump."""
        from unittest.mock import AsyncMock, patch
        from agentserver.message_bus.stream_pump import StreamPump, ListenerConfig, OrganismConfig
        from agentserver.message_bus.message_state import HandlerResponse
        from agentserver.llm.backend import LLMResponse

        # Import handlers
        from handlers.hello import Greeting, GreetingResponse, handle_greeting, handle_shout
        from handlers.console import ShoutedResponse

        # Clear buffer
        buffer = get_context_buffer()
        buffer.clear()

        # Create pump with greeter and shouter
        config = OrganismConfig(name="buffer-test")
        pump = StreamPump(config)

        pump.register_listener(ListenerConfig(
            name="greeter",
            payload_class_path="handlers.hello.Greeting",
            handler_path="handlers.hello.handle_greeting",
            description="Greeting agent",
            is_agent=True,
            peers=["shouter"],
            payload_class=Greeting,
            handler=handle_greeting,
        ))

        pump.register_listener(ListenerConfig(
            name="shouter",
            payload_class_path="handlers.hello.GreetingResponse",
            handler_path="handlers.hello.handle_shout",
            description="Shouts",
            payload_class=GreetingResponse,
            handler=handle_shout,
        ))

        # Mock LLM
        mock_llm = LLMResponse(
            content="Hello there!",
            model="mock",
            usage={"total_tokens": 5},
            finish_reason="stop",
        )

        # Prevent re-injection loops
        async def noop_reinject(state):
            pass
        pump._reinject_responses = noop_reinject

        with patch('agentserver.llm.complete', new=AsyncMock(return_value=mock_llm)):
            # Create envelope for Greeting
            thread_id = str(uuid.uuid4())
            envelope = f"""<message xmlns="https://xml-pipeline.org/ns/envelope/v1">
                <meta><from>user</from><to>greeter</to><thread>{thread_id}</thread></meta>
                <Greeting xmlns=""><Name>Alice</Name></Greeting>
            </message>""".encode()

            await pump.inject(envelope, thread_id, from_id="user")

            # Run pump to process
            pump._running = True
            pipeline = pump.build_pipeline(pump._queue_source())

            async def run_chain():
                async with pipeline.stream() as streamer:
                    count = 0
                    async for _ in streamer:
                        count += 1
                        if count >= 3:
                            break

            import asyncio
            try:
                await asyncio.wait_for(run_chain(), timeout=3.0)
            except asyncio.TimeoutError:
                pass
            finally:
                pump._running = False

        # Verify buffer recorded the messages
        thread_ctx = buffer.get_thread(thread_id)
        assert thread_ctx is not None, "Thread should exist in buffer"
        assert len(thread_ctx) >= 1, "Buffer should have at least one message"

        # Check that we recorded a Greeting
        greeting_slots = thread_ctx.get_by_type("Greeting")
        assert len(greeting_slots) >= 1, "Should have recorded Greeting"
        assert greeting_slots[0].payload.name == "Alice"
