"""
test_buffer.py — Tests for the Buffer (fan-out) orchestration primitives.

Tests:
1. BufferRegistry basic operations
2. BufferStart handler
3. Result collection
4. Fire-and-forget mode
"""

import pytest
import uuid

from xml_pipeline.message_bus.buffer_registry import (
    BufferRegistry,
    BufferState,
    BufferItemResult,
    get_buffer_registry,
    reset_buffer_registry,
)
from xml_pipeline.message_bus.message_state import HandlerMetadata, HandlerResponse
from xml_pipeline.primitives.buffer import (
    BufferStart,
    BufferComplete,
    BufferDispatched,
    BufferError,
    handle_buffer_start,
    _extract_worker_index,
    _format_buffer_results,
)


class TestBufferRegistry:
    """Test BufferRegistry basic operations."""

    def test_create_buffer_state(self):
        """create() should create a buffer state with correct fields."""
        registry = BufferRegistry()

        state = registry.create(
            buffer_id="buf001",
            total_items=5,
            return_to="caller",
            thread_id="thread-123",
            from_id="console",
            target="worker",
            collect=True,
        )

        assert state.buffer_id == "buf001"
        assert state.total_items == 5
        assert state.return_to == "caller"
        assert state.thread_id == "thread-123"
        assert state.from_id == "console"
        assert state.target == "worker"
        assert state.collect is True
        assert state.completed_count == 0
        assert state.successful_count == 0
        assert state.is_complete is False
        assert state.pending_count == 5

    def test_get_returns_buffer(self):
        """get() should return buffer by ID."""
        registry = BufferRegistry()

        registry.create(
            buffer_id="buf002",
            total_items=3,
            return_to="x",
            thread_id="t",
            from_id="c",
            target="w",
        )

        state = registry.get("buf002")
        assert state is not None
        assert state.buffer_id == "buf002"

        # Non-existent returns None
        assert registry.get("nonexistent") is None

    def test_record_result_stores_result(self):
        """record_result() should store result and update counts."""
        registry = BufferRegistry()

        registry.create(
            buffer_id="buf003",
            total_items=3,
            return_to="x",
            thread_id="t",
            from_id="c",
            target="w",
        )

        # Record first result (success)
        state = registry.record_result(
            buffer_id="buf003",
            index=0,
            result="<Result0/>",
            success=True,
        )

        assert state.completed_count == 1
        assert state.successful_count == 1
        assert state.is_complete is False
        assert 0 in state.results
        assert state.results[0].result == "<Result0/>"
        assert state.results[0].success is True

        # Record second result (failure)
        state = registry.record_result(
            buffer_id="buf003",
            index=1,
            result="<Error/>",
            success=False,
            error="Timeout",
        )

        assert state.completed_count == 2
        assert state.successful_count == 1  # Still just 1
        assert state.results[1].success is False
        assert state.results[1].error == "Timeout"

        # Record third result - now complete
        state = registry.record_result(
            buffer_id="buf003",
            index=2,
            result="<Result2/>",
            success=True,
        )

        assert state.completed_count == 3
        assert state.successful_count == 2
        assert state.is_complete is True
        assert state.pending_count == 0

    def test_record_result_ignores_duplicates(self):
        """record_result() should not count same index twice."""
        registry = BufferRegistry()

        registry.create(
            buffer_id="buf004",
            total_items=2,
            return_to="x",
            thread_id="t",
            from_id="c",
            target="w",
        )

        # Record index 0
        state = registry.record_result("buf004", 0, "<R/>", True)
        assert state.completed_count == 1

        # Try to record index 0 again
        state = registry.record_result("buf004", 0, "<Duplicate/>", True)
        assert state.completed_count == 1  # Should not increment
        assert state.results[0].result == "<R/>"  # Original preserved

    def test_remove_deletes_buffer(self):
        """remove() should delete buffer from registry."""
        registry = BufferRegistry()

        registry.create(
            buffer_id="buf005",
            total_items=1,
            return_to="x",
            thread_id="t",
            from_id="c",
            target="w",
        )

        assert registry.get("buf005") is not None
        result = registry.remove("buf005")
        assert result is True
        assert registry.get("buf005") is None

        # Remove non-existent returns False
        assert registry.remove("nonexistent") is False

    def test_list_active(self):
        """list_active() should return all active buffer IDs."""
        registry = BufferRegistry()

        registry.create("buf-a", 1, "x", "t", "c", "w")
        registry.create("buf-b", 2, "x", "t", "c", "w")
        registry.create("buf-c", 3, "x", "t", "c", "w")

        active = registry.list_active()
        assert set(active) == {"buf-a", "buf-b", "buf-c"}

    def test_clear(self):
        """clear() should remove all buffers."""
        registry = BufferRegistry()

        registry.create("buf-1", 1, "x", "t", "c", "w")
        registry.create("buf-2", 2, "x", "t", "c", "w")

        registry.clear()

        assert registry.list_active() == []


class TestBufferStateProperties:
    """Test BufferState computed properties."""

    def test_is_complete_when_all_received(self):
        """is_complete should be True when all items are received."""
        state = BufferState(
            buffer_id="test",
            total_items=3,
            return_to="x",
            thread_id="t",
            from_id="c",
            target="w",
            completed_count=3,
        )
        assert state.is_complete is True

    def test_pending_count(self):
        """pending_count should reflect remaining items."""
        state = BufferState(
            buffer_id="test",
            total_items=5,
            return_to="x",
            thread_id="t",
            from_id="c",
            target="w",
            completed_count=2,
        )
        assert state.pending_count == 3

    def test_get_ordered_results(self):
        """get_ordered_results should return results in order."""
        state = BufferState(
            buffer_id="test",
            total_items=3,
            return_to="x",
            thread_id="t",
            from_id="c",
            target="w",
            results={
                0: BufferItemResult(0, "<R0/>", True),
                2: BufferItemResult(2, "<R2/>", True),
                # Index 1 missing
            },
        )

        ordered = state.get_ordered_results()
        assert len(ordered) == 3
        assert ordered[0] is not None
        assert ordered[0].result == "<R0/>"
        assert ordered[1] is None  # Missing
        assert ordered[2] is not None
        assert ordered[2].result == "<R2/>"


class TestBufferStartPayload:
    """Test BufferStart payload."""

    def test_buffer_start_fields(self):
        """BufferStart should have expected fields."""
        payload = BufferStart(
            target="worker",
            items="item1\nitem2\nitem3",
            collect=True,
            return_to="caller",
            buffer_id="custom-id",
        )

        assert payload.target == "worker"
        assert payload.items == "item1\nitem2\nitem3"
        assert payload.collect is True
        assert payload.return_to == "caller"
        assert payload.buffer_id == "custom-id"

    def test_buffer_start_default_values(self):
        """BufferStart should have sensible defaults."""
        payload = BufferStart()

        assert payload.target == ""
        assert payload.items == ""
        assert payload.collect is True
        assert payload.return_to == ""
        assert payload.buffer_id == ""


class TestBufferCompletePayload:
    """Test BufferComplete payload."""

    def test_buffer_complete_fields(self):
        """BufferComplete should have expected fields."""
        payload = BufferComplete(
            buffer_id="buf123",
            total=5,
            successful=4,
            results="<results>...</results>",
        )

        assert payload.buffer_id == "buf123"
        assert payload.total == 5
        assert payload.successful == 4
        assert payload.results == "<results>...</results>"


class TestBufferDispatchedPayload:
    """Test BufferDispatched payload (fire-and-forget mode)."""

    def test_buffer_dispatched_fields(self):
        """BufferDispatched should have expected fields."""
        payload = BufferDispatched(
            buffer_id="buf456",
            total=10,
        )

        assert payload.buffer_id == "buf456"
        assert payload.total == 10


class TestBufferErrorPayload:
    """Test BufferError payload."""

    def test_buffer_error_fields(self):
        """BufferError should have expected fields."""
        payload = BufferError(
            buffer_id="buf789",
            error="Unknown target listener",
        )

        assert payload.buffer_id == "buf789"
        assert payload.error == "Unknown target listener"


class TestExtractWorkerIndex:
    """Test _extract_worker_index helper."""

    def test_extracts_index_from_chain(self):
        """Should extract worker index from thread chain."""
        chain = "root.parent.buffer_abc123_w5"
        index = _extract_worker_index(chain, "abc123")
        assert index == 5

    def test_extracts_double_digit_index(self):
        """Should handle double-digit indices."""
        chain = "x.buffer_xyz_w42"
        index = _extract_worker_index(chain, "xyz")
        assert index == 42

    def test_returns_none_for_no_match(self):
        """Should return None when pattern doesn't match."""
        chain = "something.else.entirely"
        index = _extract_worker_index(chain, "abc")
        assert index is None

    def test_returns_none_for_wrong_buffer_id(self):
        """Should return None when buffer ID doesn't match."""
        chain = "root.buffer_other_w3"
        index = _extract_worker_index(chain, "abc")
        assert index is None


class TestFormatBufferResults:
    """Test _format_buffer_results helper."""

    def test_formats_complete_results(self):
        """Should format all results as XML."""
        state = BufferState(
            buffer_id="test",
            total_items=2,
            return_to="x",
            thread_id="t",
            from_id="c",
            target="w",
            results={
                0: BufferItemResult(0, "<Result>A</Result>", True),
                1: BufferItemResult(1, "<Result>B</Result>", True),
            },
        )

        xml = _format_buffer_results(state)

        assert "<results>" in xml
        assert "</results>" in xml
        assert 'index="0"' in xml
        assert 'index="1"' in xml
        assert 'success="true"' in xml
        assert "<Result>A</Result>" in xml
        assert "<Result>B</Result>" in xml

    def test_formats_partial_failure(self):
        """Should format mixed success/failure results."""
        state = BufferState(
            buffer_id="test",
            total_items=2,
            return_to="x",
            thread_id="t",
            from_id="c",
            target="w",
            results={
                0: BufferItemResult(0, "<Good/>", True),
                1: BufferItemResult(1, "<Error/>", False, "timeout"),
            },
        )

        xml = _format_buffer_results(state)

        assert 'success="true"' in xml
        assert 'success="false"' in xml

    def test_formats_missing_results(self):
        """Should handle missing results."""
        state = BufferState(
            buffer_id="test",
            total_items=3,
            return_to="x",
            thread_id="t",
            from_id="c",
            target="w",
            results={
                0: BufferItemResult(0, "<R/>", True),
                # Index 1 missing
                2: BufferItemResult(2, "<R/>", True),
            },
        )

        xml = _format_buffer_results(state)

        assert 'index="1"' in xml
        assert "missing" in xml


class TestHandleBufferStartValidation:
    """Test validation in handle_buffer_start."""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Reset registries before each test."""
        reset_buffer_registry()

    @pytest.mark.asyncio
    async def test_empty_items_returns_error(self):
        """handle_buffer_start should return error for empty items."""
        from unittest.mock import patch, MagicMock

        mock_pump = MagicMock()
        mock_pump.listeners = {}

        with patch('xml_pipeline.message_bus.stream_pump.get_stream_pump', return_value=mock_pump):
            payload = BufferStart(
                target="worker",
                items="",  # Empty
                return_to="caller",
            )

            metadata = HandlerMetadata(
                thread_id=str(uuid.uuid4()),
                from_id="console",
            )

            response = await handle_buffer_start(payload, metadata)

            assert isinstance(response, HandlerResponse)
            assert isinstance(response.payload, BufferError)
            assert "No items" in response.payload.error

    @pytest.mark.asyncio
    async def test_unknown_target_returns_error(self):
        """handle_buffer_start should return error for unknown target."""
        from unittest.mock import patch, MagicMock

        mock_pump = MagicMock()
        mock_pump.listeners = {}  # No listeners

        with patch('xml_pipeline.message_bus.stream_pump.get_stream_pump', return_value=mock_pump):
            payload = BufferStart(
                target="unknown_worker",
                items="item1\nitem2",
                return_to="caller",
            )

            metadata = HandlerMetadata(
                thread_id=str(uuid.uuid4()),
                from_id="console",
            )

            response = await handle_buffer_start(payload, metadata)

            assert isinstance(response, HandlerResponse)
            assert isinstance(response.payload, BufferError)
            assert "Unknown target" in response.payload.error


class TestBufferRegistrySingleton:
    """Test singleton pattern for BufferRegistry."""

    def test_get_buffer_registry_returns_singleton(self):
        """get_buffer_registry should return same instance."""
        reset_buffer_registry()

        reg1 = get_buffer_registry()
        reg2 = get_buffer_registry()

        assert reg1 is reg2

    def test_reset_creates_new_instance(self):
        """reset_buffer_registry should clear singleton."""
        reg1 = get_buffer_registry()
        reg1.create("test", 1, "x", "t", "c", "w")

        reset_buffer_registry()
        reg2 = get_buffer_registry()

        assert reg2.get("test") is None


class TestBufferCollectVsFireAndForget:
    """Test collect=True vs collect=False behavior."""

    @pytest.mark.asyncio
    async def test_collect_mode_returns_none(self):
        """With collect=True, handler should return None (wait for results)."""
        from unittest.mock import patch, MagicMock, AsyncMock

        mock_pump = MagicMock()
        mock_pump.listeners = {"worker": MagicMock()}
        mock_pump.register_generic_listener = MagicMock()
        mock_pump._wrap_in_envelope = MagicMock(return_value=b"<envelope/>")
        mock_pump.inject = AsyncMock()

        # Mock thread registry
        mock_thread_registry = MagicMock()
        mock_thread_registry.lookup = MagicMock(return_value="root.parent")
        mock_thread_registry.get_or_create = MagicMock(return_value="worker-uuid")

        with patch('xml_pipeline.message_bus.stream_pump.get_stream_pump', return_value=mock_pump):
            with patch('xml_pipeline.message_bus.thread_registry.get_registry', return_value=mock_thread_registry):
                payload = BufferStart(
                    target="worker",
                    items="item1\nitem2",
                    return_to="caller",
                    collect=True,
                )

                metadata = HandlerMetadata(
                    thread_id=str(uuid.uuid4()),
                    from_id="console",
                )

                response = await handle_buffer_start(payload, metadata)

                # With collect=True, returns None (ephemeral handler will send BufferComplete)
                assert response is None

                # Ephemeral listener should be registered
                mock_pump.register_generic_listener.assert_called_once()

    @pytest.mark.asyncio
    async def test_fire_and_forget_returns_dispatched(self):
        """With collect=False, handler should return BufferDispatched immediately."""
        from unittest.mock import patch, MagicMock, AsyncMock

        mock_pump = MagicMock()
        mock_pump.listeners = {"worker": MagicMock()}
        mock_pump.register_generic_listener = MagicMock()
        mock_pump._wrap_in_envelope = MagicMock(return_value=b"<envelope/>")
        mock_pump.inject = AsyncMock()

        mock_thread_registry = MagicMock()
        mock_thread_registry.lookup = MagicMock(return_value="root.parent")
        mock_thread_registry.get_or_create = MagicMock(return_value="worker-uuid")

        with patch('xml_pipeline.message_bus.stream_pump.get_stream_pump', return_value=mock_pump):
            with patch('xml_pipeline.message_bus.thread_registry.get_registry', return_value=mock_thread_registry):
                payload = BufferStart(
                    target="worker",
                    items="item1\nitem2\nitem3",
                    return_to="caller",
                    collect=False,  # Fire-and-forget
                )

                metadata = HandlerMetadata(
                    thread_id=str(uuid.uuid4()),
                    from_id="console",
                )

                response = await handle_buffer_start(payload, metadata)

                # With collect=False, returns BufferDispatched
                assert isinstance(response, HandlerResponse)
                assert isinstance(response.payload, BufferDispatched)
                assert response.payload.total == 3

                # No ephemeral listener registered for fire-and-forget
                mock_pump.register_generic_listener.assert_not_called()


class TestBufferResultCollection:
    """Test result collection behavior."""

    def test_result_collection_partial_success(self):
        """Buffer should track partial success correctly."""
        registry = BufferRegistry()

        registry.create("partial", 5, "x", "t", "c", "w")

        # 3 successes, 2 failures
        registry.record_result("partial", 0, "<R/>", True)
        registry.record_result("partial", 1, "<E/>", False, "error")
        registry.record_result("partial", 2, "<R/>", True)
        registry.record_result("partial", 3, "<E/>", False, "error")
        registry.record_result("partial", 4, "<R/>", True)

        state = registry.get("partial")

        assert state.is_complete is True
        assert state.completed_count == 5
        assert state.successful_count == 3

    def test_results_out_of_order(self):
        """Buffer should handle results arriving out of order."""
        registry = BufferRegistry()

        registry.create("ooo", 3, "x", "t", "c", "w")

        # Results arrive out of order
        registry.record_result("ooo", 2, "<R2/>", True)
        registry.record_result("ooo", 0, "<R0/>", True)
        registry.record_result("ooo", 1, "<R1/>", True)

        state = registry.get("ooo")

        assert state.is_complete is True
        ordered = state.get_ordered_results()
        assert ordered[0].result == "<R0/>"
        assert ordered[1].result == "<R1/>"
        assert ordered[2].result == "<R2/>"
