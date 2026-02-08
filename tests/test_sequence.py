"""
test_sequence.py — Tests for the Sequence orchestration primitives.

Tests:
1. SequenceRegistry basic operations
2. SequenceStart handler
3. Step result handling
4. Error propagation
"""

import pytest
import uuid

from xml_pipeline.message_bus.sequence_registry import (
    SequenceRegistry,
    SequenceState,
    get_sequence_registry,
    reset_sequence_registry,
)
from xml_pipeline.message_bus.message_state import HandlerMetadata, HandlerResponse
from xml_pipeline.primitives.sequence import (
    SequenceStart,
    SequenceComplete,
    SequenceError,
    handle_sequence_start,
)


class TestSequenceRegistry:
    """Test SequenceRegistry basic operations."""

    def test_create_sequence_state(self):
        """create() should create a sequence state with correct fields."""
        registry = SequenceRegistry()

        state = registry.create(
            sequence_id="seq001",
            steps=["step1", "step2", "step3"],
            return_to="caller",
            thread_id="thread-123",
            from_id="console",
            initial_payload="<TestPayload/>",
        )

        assert state.sequence_id == "seq001"
        assert state.steps == ["step1", "step2", "step3"]
        assert state.return_to == "caller"
        assert state.thread_id == "thread-123"
        assert state.from_id == "console"
        assert state.current_index == 0
        assert state.is_complete is False
        assert state.current_step == "step1"
        assert state.remaining_steps == ["step1", "step2", "step3"]
        assert state.last_result == "<TestPayload/>"

    def test_get_returns_sequence(self):
        """get() should return sequence by ID."""
        registry = SequenceRegistry()

        registry.create(
            sequence_id="seq002",
            steps=["a", "b"],
            return_to="x",
            thread_id="t",
            from_id="c",
        )

        state = registry.get("seq002")
        assert state is not None
        assert state.sequence_id == "seq002"

        # Non-existent returns None
        assert registry.get("nonexistent") is None

    def test_advance_increments_index(self):
        """advance() should increment index and store result."""
        registry = SequenceRegistry()

        registry.create(
            sequence_id="seq003",
            steps=["a", "b", "c"],
            return_to="x",
            thread_id="t",
            from_id="c",
        )

        # Advance first step
        state = registry.advance("seq003", "<ResultA/>")
        assert state.current_index == 1
        assert state.results == ["<ResultA/>"]
        assert state.last_result == "<ResultA/>"
        assert state.current_step == "b"
        assert state.is_complete is False

        # Advance second step
        state = registry.advance("seq003", "<ResultB/>")
        assert state.current_index == 2
        assert state.results == ["<ResultA/>", "<ResultB/>"]
        assert state.current_step == "c"

        # Advance third step - now complete
        state = registry.advance("seq003", "<ResultC/>")
        assert state.current_index == 3
        assert state.is_complete is True
        assert state.current_step is None
        assert state.remaining_steps == []

    def test_mark_failed(self):
        """mark_failed() should set failed state."""
        registry = SequenceRegistry()

        registry.create(
            sequence_id="seq004",
            steps=["a", "b"],
            return_to="x",
            thread_id="t",
            from_id="c",
        )

        state = registry.mark_failed("seq004", "a", "XSD validation failed")

        assert state.failed is True
        assert state.failed_step == "a"
        assert state.error == "XSD validation failed"
        assert state.is_complete is False  # Not complete, but failed
        assert state.current_step is None  # No current step when failed
        assert state.remaining_steps == []  # No remaining steps when failed

    def test_remove_deletes_sequence(self):
        """remove() should delete sequence from registry."""
        registry = SequenceRegistry()

        registry.create(
            sequence_id="seq005",
            steps=["a"],
            return_to="x",
            thread_id="t",
            from_id="c",
        )

        assert registry.get("seq005") is not None
        result = registry.remove("seq005")
        assert result is True
        assert registry.get("seq005") is None

        # Remove non-existent returns False
        assert registry.remove("nonexistent") is False

    def test_list_active(self):
        """list_active() should return all active sequence IDs."""
        registry = SequenceRegistry()

        registry.create("seq-a", ["1"], "x", "t", "c")
        registry.create("seq-b", ["2"], "x", "t", "c")
        registry.create("seq-c", ["3"], "x", "t", "c")

        active = registry.list_active()
        assert set(active) == {"seq-a", "seq-b", "seq-c"}

    def test_clear(self):
        """clear() should remove all sequences."""
        registry = SequenceRegistry()

        registry.create("seq-1", ["a"], "x", "t", "c")
        registry.create("seq-2", ["b"], "x", "t", "c")

        registry.clear()

        assert registry.list_active() == []


class TestSequenceStateProperties:
    """Test SequenceState computed properties."""

    def test_is_complete_after_all_steps(self):
        """is_complete should be True when all steps are done."""
        state = SequenceState(
            sequence_id="test",
            steps=["a", "b"],
            return_to="x",
            thread_id="t",
            from_id="c",
            current_index=2,  # Past all steps
        )
        assert state.is_complete is True

    def test_not_complete_when_failed(self):
        """is_complete should be False when failed."""
        state = SequenceState(
            sequence_id="test",
            steps=["a", "b"],
            return_to="x",
            thread_id="t",
            from_id="c",
            current_index=2,
            failed=True,
        )
        assert state.is_complete is False

    def test_current_step_none_when_complete(self):
        """current_step should be None when sequence is complete."""
        state = SequenceState(
            sequence_id="test",
            steps=["a"],
            return_to="x",
            thread_id="t",
            from_id="c",
            current_index=1,
        )
        assert state.current_step is None

    def test_remaining_steps_empty_when_complete(self):
        """remaining_steps should be empty when complete."""
        state = SequenceState(
            sequence_id="test",
            steps=["a", "b"],
            return_to="x",
            thread_id="t",
            from_id="c",
            current_index=2,
        )
        assert state.remaining_steps == []


class TestSequenceStartPayload:
    """Test SequenceStart payload serialization."""

    def test_sequence_start_fields(self):
        """SequenceStart should have expected fields."""
        payload = SequenceStart(
            steps="step1,step2",
            payload="<Test/>",
            return_to="caller",
            sequence_id="custom-id",
        )

        assert payload.steps == "step1,step2"
        assert payload.payload == "<Test/>"
        assert payload.return_to == "caller"
        assert payload.sequence_id == "custom-id"

    def test_sequence_start_default_values(self):
        """SequenceStart should have sensible defaults."""
        payload = SequenceStart()

        assert payload.steps == ""
        assert payload.payload == ""
        assert payload.return_to == ""
        assert payload.sequence_id == ""


class TestSequenceCompletePayload:
    """Test SequenceComplete payload."""

    def test_sequence_complete_fields(self):
        """SequenceComplete should have expected fields."""
        payload = SequenceComplete(
            sequence_id="seq123",
            final_result="<Result>42</Result>",
            step_count=3,
        )

        assert payload.sequence_id == "seq123"
        assert payload.final_result == "<Result>42</Result>"
        assert payload.step_count == 3


class TestSequenceErrorPayload:
    """Test SequenceError payload."""

    def test_sequence_error_fields(self):
        """SequenceError should have expected fields."""
        payload = SequenceError(
            sequence_id="seq456",
            failed_step="bad-step",
            step_index=1,
            error="Validation failed",
        )

        assert payload.sequence_id == "seq456"
        assert payload.failed_step == "bad-step"
        assert payload.step_index == 1
        assert payload.error == "Validation failed"


class TestHandleSequenceStartValidation:
    """Test validation in handle_sequence_start."""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Reset registries before each test."""
        reset_sequence_registry()

    @pytest.mark.asyncio
    async def test_empty_steps_returns_error(self):
        """handle_sequence_start should return error for empty steps."""
        from unittest.mock import patch, MagicMock

        # Mock get_stream_pump to avoid dependency
        mock_pump = MagicMock()
        mock_pump.listeners = {}

        with patch('xml_pipeline.message_bus.singleton.get_stream_pump', return_value=mock_pump):
            payload = SequenceStart(
                steps="",  # Empty
                payload="<Test/>",
                return_to="caller",
            )

            metadata = HandlerMetadata(
                thread_id=str(uuid.uuid4()),
                from_id="console",
            )

            response = await handle_sequence_start(payload, metadata)

            assert isinstance(response, HandlerResponse)
            assert isinstance(response.payload, SequenceError)
            assert "No steps" in response.payload.error

    @pytest.mark.asyncio
    async def test_unknown_step_returns_error(self):
        """handle_sequence_start should return error for unknown step."""
        from unittest.mock import patch, MagicMock

        # Mock get_stream_pump with no listeners
        mock_pump = MagicMock()
        mock_pump.listeners = {}  # No listeners registered

        with patch('xml_pipeline.message_bus.singleton.get_stream_pump', return_value=mock_pump):
            payload = SequenceStart(
                steps="unknown_step",
                payload="<Test/>",
                return_to="caller",
            )

            metadata = HandlerMetadata(
                thread_id=str(uuid.uuid4()),
                from_id="console",
            )

            response = await handle_sequence_start(payload, metadata)

            assert isinstance(response, HandlerResponse)
            assert isinstance(response.payload, SequenceError)
            assert "Unknown listener" in response.payload.error
            assert "unknown_step" in response.payload.failed_step


class TestSequenceRegistrySingleton:
    """Test singleton pattern for SequenceRegistry."""

    def test_get_sequence_registry_returns_singleton(self):
        """get_sequence_registry should return same instance."""
        reset_sequence_registry()

        reg1 = get_sequence_registry()
        reg2 = get_sequence_registry()

        assert reg1 is reg2

    def test_reset_creates_new_instance(self):
        """reset_sequence_registry should clear singleton."""
        reg1 = get_sequence_registry()
        reg1.create("test", ["a"], "x", "t", "c")

        reset_sequence_registry()
        reg2 = get_sequence_registry()

        assert reg2.get("test") is None


class TestSequenceMultipleSteps:
    """Test sequences with multiple steps."""

    def test_three_step_sequence(self):
        """A three-step sequence should advance through all steps."""
        registry = SequenceRegistry()

        registry.create(
            sequence_id="multi",
            steps=["add", "multiply", "format"],
            return_to="caller",
            thread_id="t",
            from_id="c",
            initial_payload="<Input>5</Input>",
        )

        # Step 1: add
        state = registry.get("multi")
        assert state.current_step == "add"
        state = registry.advance("multi", "<Sum>8</Sum>")
        assert state.last_result == "<Sum>8</Sum>"

        # Step 2: multiply
        assert state.current_step == "multiply"
        state = registry.advance("multi", "<Product>40</Product>")

        # Step 3: format
        assert state.current_step == "format"
        state = registry.advance("multi", "<Formatted>Result: 40</Formatted>")

        # Complete
        assert state.is_complete is True
        assert len(state.results) == 3
        assert state.results[0] == "<Sum>8</Sum>"
        assert state.results[1] == "<Product>40</Product>"
        assert state.results[2] == "<Formatted>Result: 40</Formatted>"

    def test_failure_at_middle_step(self):
        """Failure at middle step should stop sequence."""
        registry = SequenceRegistry()

        registry.create(
            sequence_id="fail-mid",
            steps=["step1", "step2", "step3"],
            return_to="caller",
            thread_id="t",
            from_id="c",
        )

        # Step 1 succeeds
        registry.advance("fail-mid", "<R1/>")

        # Step 2 fails
        state = registry.mark_failed("fail-mid", "step2", "Connection timeout")

        assert state.failed is True
        assert state.failed_step == "step2"
        assert state.current_index == 1  # Was at step 2 (index 1)
        assert len(state.results) == 1  # Only step 1 result


class TestSequenceWithRealSteps:
    """Integration-style tests with mock handlers."""

    @pytest.mark.asyncio
    async def test_sequence_creates_ephemeral_listener(self):
        """Starting a sequence should create an ephemeral listener."""
        from unittest.mock import patch, MagicMock, AsyncMock

        mock_pump = MagicMock()
        mock_pump.listeners = {"step1": MagicMock(), "step2": MagicMock()}
        mock_pump.register_generic_listener = MagicMock()

        with patch('xml_pipeline.message_bus.singleton.get_stream_pump', return_value=mock_pump):
            payload = SequenceStart(
                steps="step1,step2",
                payload="<Input/>",
                return_to="caller",
            )

            metadata = HandlerMetadata(
                thread_id=str(uuid.uuid4()),
                from_id="console",
            )

            response = await handle_sequence_start(payload, metadata)

            # Should have registered an ephemeral listener
            mock_pump.register_generic_listener.assert_called_once()
            call_args = mock_pump.register_generic_listener.call_args
            name_arg = call_args.kwargs.get('name') or call_args.args[0]
            assert name_arg.startswith("sequence_")
