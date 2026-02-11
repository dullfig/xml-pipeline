"""
Tests for security hitlist round 2 items:
  #49 — Pipeline fan-out depth limit
  #50 — Error messages don't leak topology
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest

from xml_pipeline.message_bus.message_state import MessageState
from xml_pipeline.message_bus.stream_pump import MAX_MESSAGE_DEPTH


# ============================================================================
# #49 — Fan-out depth limit
# ============================================================================


class TestMessageDepthLimit:
    """Test that messages exceeding MAX_MESSAGE_DEPTH are dropped."""

    def test_max_depth_constant(self):
        assert MAX_MESSAGE_DEPTH == 50

    async def test_message_at_depth_exceeding_limit_is_dropped(self):
        """A MessageState with depth > MAX_MESSAGE_DEPTH should be dropped."""
        from xml_pipeline.message_bus.stream_pump import StreamPump
        from xml_pipeline.message_bus.pump_config import Listener

        pump = StreamPump(name="test-depth")

        # Create a mock listener
        mock_handler = MagicMock()
        listener = Listener(
            name="echo",
            payload_class=object,
            handler=mock_handler,
            description="test",
            is_agent=False,
            root_tag="echo.test",
        )

        state = MessageState(
            raw_bytes=b"<test/>",
            thread_id="thread-1",
            from_id="system",
            to_id="echo",
            target_listeners=[listener],
            metadata={"depth": MAX_MESSAGE_DEPTH + 1},
        )

        # Collect results from the async generator
        results = []
        async for result in pump._dispatch_to_handlers(state):
            results.append(result)

        # Should yield nothing (message dropped)
        assert len(results) == 0, (
            f"Expected message to be dropped at depth {MAX_MESSAGE_DEPTH + 1}, "
            f"but got {len(results)} results"
        )

    async def test_message_within_depth_limit_is_processed(self):
        """A MessageState within depth limit should not be dropped."""
        from xml_pipeline.message_bus.stream_pump import StreamPump
        from xml_pipeline.message_bus.pump_config import Listener

        pump = StreamPump(name="test-depth")

        # Mock a listener and handler
        call_count = 0

        async def counting_handler(payload, metadata):
            nonlocal call_count
            call_count += 1
            return None

        listener = Listener(
            name="echo",
            payload_class=object,
            handler=counting_handler,
            description="test",
            is_agent=False,
            root_tag="echo.test",
        )

        state = MessageState(
            payload="test-payload",
            thread_id="test-thread",
            from_id="external",
            to_id="echo",
            target_listeners=[listener],
            metadata={"depth": 5},
        )

        results = []
        async for result in pump._dispatch_to_handlers(state):
            results.append(result)

        # Handler was called (not dropped by depth check)
        assert call_count == 1

    def test_depth_zero_default(self):
        """Messages without explicit depth should default to 0."""
        state = MessageState(raw_bytes=b"<test/>")
        assert state.metadata.get("depth", 0) == 0


# ============================================================================
# #50 — Error messages don't leak topology
# ============================================================================


class TestErrorMessageTopology:
    """Test that error messages don't reveal internal topology."""

    async def test_unknown_target_hides_available_list(self):
        """System pipeline should not list available listeners on unknown target."""
        from xml_pipeline.message_bus.system_pipeline import SystemPipeline

        mock_pump = MagicMock()
        mock_pump.listeners = {"greeter": MagicMock(), "shouter": MagicMock()}

        pipeline = SystemPipeline(mock_pump)

        with pytest.raises(ValueError) as exc_info:
            await pipeline.inject_console("@nonexistent hello", user="admin")

        error_msg = str(exc_info.value)
        assert "Unknown target: nonexistent" in error_msg
        # Must NOT contain the available listener list
        assert "greeter" not in error_msg
        assert "shouter" not in error_msg
        assert "Available" not in error_msg

    async def test_rate_limit_hides_username(self):
        """Rate limit error should not reveal the username."""
        from xml_pipeline.message_bus.system_pipeline import SystemPipeline

        mock_pump = MagicMock()
        mock_pump.listeners = {"greeter": MagicMock()}

        pipeline = SystemPipeline(mock_pump)
        pipeline._max_rate = 0  # Immediately rate-limited

        with pytest.raises(ValueError) as exc_info:
            await pipeline.inject_console("@greeter hello", user="secret_admin")

        error_msg = str(exc_info.value)
        assert error_msg == "Rate limit exceeded"
        assert "secret_admin" not in error_msg
