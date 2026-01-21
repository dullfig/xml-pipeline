"""
test_todo_registry.py — Tests for the TodoUntil watcher system.

Tests:
1. TodoRegistry basic operations
2. Eyebrow raising on condition match
3. Nudge formatting
4. Integration with StreamPump
"""

import pytest
import asyncio
import uuid
from unittest.mock import AsyncMock, patch

from xml_pipeline.message_bus.todo_registry import TodoRegistry, TodoWatcher, get_todo_registry
from xml_pipeline.message_bus.stream_pump import StreamPump, ListenerConfig, OrganismConfig
from xml_pipeline.message_bus.message_state import HandlerMetadata, HandlerResponse
from xml_pipeline.primitives.todo import (
    TodoUntil, TodoComplete, TodoRegistered, TodoClosed,
    handle_todo_until, handle_todo_complete,
)


class TestTodoRegistry:
    """Test TodoRegistry basic operations."""

    def test_register_creates_watcher(self):
        """register() should create a watcher with correct fields."""
        registry = TodoRegistry()
        thread_id = str(uuid.uuid4())

        watcher_id = registry.register(
            thread_id=thread_id,
            issuer="greeter",
            wait_for="ShoutedResponse",
            from_listener="shouter",
            description="waiting for shout",
        )

        assert watcher_id is not None
        assert watcher_id in registry._by_id
        assert len(registry._watchers[thread_id]) == 1

        watcher = registry._by_id[watcher_id]
        assert watcher.thread_id == thread_id
        assert watcher.issuer == "greeter"
        assert watcher.wait_for == "shoutedresponse"  # Normalized to lowercase
        assert watcher.from_listener == "shouter"
        assert watcher.description == "waiting for shout"
        assert watcher.eyebrow_raised is False

    def test_check_raises_eyebrow_on_match(self):
        """check() should raise eyebrow when payload type matches."""
        registry = TodoRegistry()
        thread_id = str(uuid.uuid4())

        watcher_id = registry.register(
            thread_id=thread_id,
            issuer="greeter",
            wait_for="ShoutedResponse",
        )

        # Check with matching payload type
        newly_raised = registry.check(
            thread_id=thread_id,
            payload_type="ShoutedResponse",
            from_id="shouter",
            payload={"message": "HELLO!"},
        )

        assert len(newly_raised) == 1
        assert newly_raised[0].id == watcher_id
        assert newly_raised[0].eyebrow_raised is True
        assert newly_raised[0].triggered_from == "shouter"

    def test_check_respects_from_listener_filter(self):
        """check() should only match if from_listener matches."""
        registry = TodoRegistry()
        thread_id = str(uuid.uuid4())

        registry.register(
            thread_id=thread_id,
            issuer="greeter",
            wait_for="ShoutedResponse",
            from_listener="shouter",  # Must be from shouter
        )

        # Check from wrong sender - should NOT raise
        newly_raised = registry.check(
            thread_id=thread_id,
            payload_type="ShoutedResponse",
            from_id="other-agent",
        )
        assert len(newly_raised) == 0

        # Check from correct sender - should raise
        newly_raised = registry.check(
            thread_id=thread_id,
            payload_type="ShoutedResponse",
            from_id="shouter",
        )
        assert len(newly_raised) == 1

    def test_check_ignores_non_matching_payload(self):
        """check() should not raise eyebrow for non-matching payload."""
        registry = TodoRegistry()
        thread_id = str(uuid.uuid4())

        registry.register(
            thread_id=thread_id,
            issuer="greeter",
            wait_for="ShoutedResponse",
        )

        # Check with non-matching payload type
        newly_raised = registry.check(
            thread_id=thread_id,
            payload_type="Greeting",
            from_id="console",
        )

        assert len(newly_raised) == 0

    def test_check_ignores_different_thread(self):
        """check() should not match watchers on different threads."""
        registry = TodoRegistry()
        thread_id_1 = str(uuid.uuid4())
        thread_id_2 = str(uuid.uuid4())

        registry.register(
            thread_id=thread_id_1,
            issuer="greeter",
            wait_for="ShoutedResponse",
        )

        # Check on different thread
        newly_raised = registry.check(
            thread_id=thread_id_2,
            payload_type="ShoutedResponse",
            from_id="shouter",
        )

        assert len(newly_raised) == 0

    def test_get_raised_for_returns_raised_watchers(self):
        """get_raised_for() should return only raised watchers for agent."""
        registry = TodoRegistry()
        thread_id = str(uuid.uuid4())

        # Register two watchers for greeter
        registry.register(thread_id, "greeter", "ShoutedResponse")
        registry.register(thread_id, "greeter", "AnotherPayload")

        # Raise one
        registry.check(thread_id, "ShoutedResponse", "shouter")

        raised = registry.get_raised_for(thread_id, "greeter")
        assert len(raised) == 1
        assert raised[0].wait_for == "shoutedresponse"

    def test_close_removes_watcher(self):
        """close() should remove watcher by ID."""
        registry = TodoRegistry()
        thread_id = str(uuid.uuid4())

        watcher_id = registry.register(thread_id, "greeter", "ShoutedResponse")
        assert watcher_id in registry._by_id

        result = registry.close(watcher_id)
        assert result is True
        assert watcher_id not in registry._by_id
        assert thread_id not in registry._watchers  # Empty list cleaned up

    def test_close_returns_false_for_unknown(self):
        """close() should return False for unknown watcher ID."""
        registry = TodoRegistry()
        result = registry.close("nonexistent-id")
        assert result is False

    def test_format_nudge_empty_for_no_raised(self):
        """format_nudge() should return empty string for no raised watchers."""
        registry = TodoRegistry()
        nudge = registry.format_nudge([])
        assert nudge == ""

    def test_format_nudge_includes_watcher_info(self):
        """format_nudge() should format raised watchers."""
        registry = TodoRegistry()
        thread_id = str(uuid.uuid4())

        watcher_id = registry.register(
            thread_id=thread_id,
            issuer="greeter",
            wait_for="ShoutedResponse",
            description="waiting for shout",
        )
        registry.check(thread_id, "ShoutedResponse", "shouter")

        raised = registry.get_raised_for(thread_id, "greeter")
        nudge = registry.format_nudge(raised)

        assert "SYSTEM NOTE" in nudge
        assert "shoutedresponse" in nudge.lower()
        assert "waiting for shout" in nudge
        assert watcher_id in nudge
        assert "TodoComplete" in nudge


class TestTodoHandlers:
    """Test TodoUntil and TodoComplete handlers."""

    @pytest.mark.asyncio
    async def test_handle_todo_until_registers_watcher(self):
        """handle_todo_until should register a watcher."""
        # Clear any existing watchers
        registry = get_todo_registry()
        registry.clear()

        payload = TodoUntil(
            wait_for="ShoutedResponse",
            from_listener="shouter",
            description="test watcher",
        )

        metadata = HandlerMetadata(
            thread_id=str(uuid.uuid4()),
            from_id="greeter",
        )

        response = await handle_todo_until(payload, metadata)

        assert isinstance(response, HandlerResponse)
        assert isinstance(response.payload, TodoRegistered)
        assert response.payload.wait_for == "ShoutedResponse"
        assert response.to == "greeter"

        # Verify watcher was registered
        assert response.payload.id in registry._by_id

    @pytest.mark.asyncio
    async def test_handle_todo_complete_closes_watcher(self):
        """handle_todo_complete should close a watcher."""
        registry = get_todo_registry()
        registry.clear()

        thread_id = str(uuid.uuid4())

        # First register a watcher
        watcher_id = registry.register(
            thread_id=thread_id,
            issuer="greeter",
            wait_for="ShoutedResponse",
        )

        # Raise its eyebrow
        registry.check(thread_id, "ShoutedResponse", "shouter")

        # Now close it
        payload = TodoComplete(id=watcher_id)
        metadata = HandlerMetadata(thread_id=thread_id, from_id="greeter")

        response = await handle_todo_complete(payload, metadata)

        assert isinstance(response, HandlerResponse)
        assert isinstance(response.payload, TodoClosed)
        assert response.payload.id == watcher_id
        assert response.payload.was_raised is True

        # Verify watcher was removed
        assert watcher_id not in registry._by_id


class TestTodoIntegration:
    """Integration tests for TodoUntil with StreamPump."""

    @pytest.mark.asyncio
    async def test_todo_nudge_appears_in_metadata(self):
        """Raised eyebrows should appear in handler metadata."""
        from handlers.hello import Greeting, GreetingResponse, handle_greeting
        from xml_pipeline.llm.backend import LLMResponse

        # Clear registries
        todo_registry = get_todo_registry()
        todo_registry.clear()

        # Create pump with greeter
        config = OrganismConfig(name="todo-test")
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

        # Register a todo watcher for greeter
        thread_id = str(uuid.uuid4())
        watcher_id = todo_registry.register(
            thread_id=thread_id,
            issuer="greeter",
            wait_for="SomeResponse",
            description="waiting for something",
        )

        # Raise the eyebrow
        todo_registry.check(thread_id, "SomeResponse", "other")

        # Track metadata passed to handler
        captured_metadata = []
        original_handler = pump.listeners["greeter"].handler

        async def capturing_handler(payload, metadata):
            captured_metadata.append(metadata)
            return HandlerResponse(
                payload=GreetingResponse(message="hi", original_sender="test"),
                to="shouter",
            )

        pump.listeners["greeter"].handler = capturing_handler

        # Create and inject a message
        from xml_pipeline.message_bus.message_state import MessageState

        state = MessageState(
            payload=Greeting(name="Test"),
            thread_id=thread_id,
            from_id="console",
            target_listeners=[pump.listeners["greeter"]],
        )

        # Dispatch
        responses = []
        async for resp in pump._dispatch_to_handlers(state):
            responses.append(resp)

        # Verify nudge was in metadata
        assert len(captured_metadata) == 1
        assert "SYSTEM NOTE" in captured_metadata[0].todo_nudge
        assert watcher_id in captured_metadata[0].todo_nudge

    @pytest.mark.asyncio
    async def test_message_raises_eyebrow(self):
        """Incoming message should raise eyebrow on matching watcher."""
        from handlers.hello import Greeting, GreetingResponse
        from handlers.console import ShoutedResponse

        # Clear registry
        todo_registry = get_todo_registry()
        todo_registry.clear()

        config = OrganismConfig(name="eyebrow-test")
        pump = StreamPump(config)

        # Register a simple handler
        async def noop_handler(payload, metadata):
            return None

        pump.register_listener(ListenerConfig(
            name="response-handler",
            payload_class_path="handlers.console.ShoutedResponse",
            handler_path="handlers.console.handle_shouted_response",
            description="Test",
            payload_class=ShoutedResponse,
            handler=noop_handler,
        ))

        # Register a watcher waiting for ShoutedResponse
        thread_id = str(uuid.uuid4())
        watcher_id = todo_registry.register(
            thread_id=thread_id,
            issuer="greeter",
            wait_for="ShoutedResponse",
        )

        # Eyebrow should not be raised yet
        watcher = todo_registry._by_id[watcher_id]
        assert watcher.eyebrow_raised is False

        # Dispatch a ShoutedResponse message
        from xml_pipeline.message_bus.message_state import MessageState

        state = MessageState(
            payload=ShoutedResponse(message="HELLO!"),
            thread_id=thread_id,
            from_id="shouter",
            target_listeners=[pump.listeners["response-handler"]],
        )

        async for _ in pump._dispatch_to_handlers(state):
            pass

        # Now eyebrow should be raised
        assert watcher.eyebrow_raised is True
        assert watcher.triggered_from == "shouter"


class TestGreeterTodoFlow:
    """Test greeter's use of TodoUntil."""

    @pytest.mark.asyncio
    async def test_greeter_registers_todo_and_eyebrow_raised(self):
        """
        Greeter should register a TodoUntil, and eyebrow should raise
        when ShoutedResponse arrives.
        """
        from handlers.hello import Greeting, GreetingResponse, handle_greeting
        from handlers.console import ShoutedResponse

        # Clear registry
        todo_registry = get_todo_registry()
        todo_registry.clear()

        thread_id = str(uuid.uuid4())

        # Mock platform.complete (not llm.complete) since handle_greeting uses platform API
        mock_response = "Hello there!"

        with patch('xml_pipeline.platform.complete', new=AsyncMock(return_value=mock_response)):
            # Call greeter handler
            metadata = HandlerMetadata(
                thread_id=thread_id,
                from_id="console-router",
                own_name="greeter",
            )

            response = await handle_greeting(Greeting(name="Alice"), metadata)

        # Verify response goes to shouter
        assert response.to == "shouter"
        assert isinstance(response.payload, GreetingResponse)

        # Verify a watcher was registered
        watchers = todo_registry._watchers.get(thread_id, [])
        assert len(watchers) == 1
        assert watchers[0].issuer == "greeter"
        assert watchers[0].wait_for == "shoutedresponse"
        assert watchers[0].eyebrow_raised is False

        # Simulate ShoutedResponse arriving (from shouter)
        todo_registry.check(
            thread_id=thread_id,
            payload_type="ShoutedResponse",
            from_id="shouter",
            payload=ShoutedResponse(message="HELLO THERE!"),
        )

        # Eyebrow should now be raised
        assert watchers[0].eyebrow_raised is True
        assert watchers[0].triggered_from == "shouter"

    @pytest.mark.asyncio
    async def test_greeter_closes_raised_todos_on_next_call(self):
        """
        When greeter is called again with raised todos, it should close them.
        """
        from handlers.hello import Greeting, GreetingResponse, handle_greeting

        # Clear registry
        todo_registry = get_todo_registry()
        todo_registry.clear()

        thread_id = str(uuid.uuid4())

        # Pre-register a raised todo (simulating previous invocation)
        watcher_id = todo_registry.register(
            thread_id=thread_id,
            issuer="greeter",
            wait_for="ShoutedResponse",
        )
        todo_registry.check(thread_id, "ShoutedResponse", "shouter")

        # Verify eyebrow is raised
        assert todo_registry._by_id[watcher_id].eyebrow_raised is True

        # Mock platform.complete (not llm.complete) since handle_greeting uses platform API
        mock_response = "Hello again!"

        # Format the nudge as the pump would
        raised = todo_registry.get_raised_for(thread_id, "greeter")
        nudge = todo_registry.format_nudge(raised)

        with patch('xml_pipeline.platform.complete', new=AsyncMock(return_value=mock_response)):
            # Call greeter with the nudge
            metadata = HandlerMetadata(
                thread_id=thread_id,
                from_id="console-router",
                own_name="greeter",
                todo_nudge=nudge,
            )

            await handle_greeting(Greeting(name="Bob"), metadata)

        # Old watcher should be closed
        assert watcher_id not in todo_registry._by_id

        # But a new one should be registered for this greeting
        watchers = todo_registry._watchers.get(thread_id, [])
        assert len(watchers) == 1
        assert watchers[0].id != watcher_id  # New watcher


class TestTodoMultipleWatchers:
    """Test scenarios with multiple watchers."""

    def test_multiple_watchers_same_thread(self):
        """Multiple watchers on same thread should work independently."""
        registry = TodoRegistry()
        thread_id = str(uuid.uuid4())

        id1 = registry.register(thread_id, "agent1", "ResponseA")
        id2 = registry.register(thread_id, "agent2", "ResponseB")
        id3 = registry.register(thread_id, "agent1", "ResponseB")

        # Raise only ResponseB watchers
        raised = registry.check(thread_id, "ResponseB", "source")

        assert len(raised) == 2
        raised_ids = {w.id for w in raised}
        assert id2 in raised_ids
        assert id3 in raised_ids
        assert id1 not in raised_ids

    def test_watcher_only_raised_once(self):
        """Eyebrow should only raise once per watcher."""
        registry = TodoRegistry()
        thread_id = str(uuid.uuid4())

        registry.register(thread_id, "agent", "Response")

        # First check raises
        raised1 = registry.check(thread_id, "Response", "source")
        assert len(raised1) == 1

        # Second check does not re-raise
        raised2 = registry.check(thread_id, "Response", "source")
        assert len(raised2) == 0

    def test_close_all_for_thread(self):
        """close_all_for_thread should remove all watchers on thread."""
        registry = TodoRegistry()
        thread_id = str(uuid.uuid4())

        id1 = registry.register(thread_id, "agent1", "A")
        id2 = registry.register(thread_id, "agent2", "B")

        count = registry.close_all_for_thread(thread_id)

        assert count == 2
        assert id1 not in registry._by_id
        assert id2 not in registry._by_id
        assert thread_id not in registry._watchers
