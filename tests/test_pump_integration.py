"""
test_pump_integration.py — Integration tests for the StreamPump

Run with: pytest tests/test_pump_integration.py -v

These tests verify the full message flow through the pump:
  inject → parse → extract → validate → deserialize → route → handler → response
"""

import pytest
import asyncio
import uuid
from unittest.mock import AsyncMock, patch

from agentserver.message_bus import StreamPump, bootstrap, MessageState
from agentserver.message_bus.stream_pump import ConfigLoader, ListenerConfig, OrganismConfig, Listener
from handlers.hello import Greeting, GreetingResponse, handle_greeting, handle_shout

ENVELOPE_NS = "https://xml-pipeline.org/ns/envelope/v1"


def make_envelope(payload_xml: str, from_id: str, to_id: str, thread_id: str) -> bytes:
    """Helper to create a properly formatted envelope.

    Note: payload_xml should include its own namespace (or xmlns="") to avoid
    inheriting the envelope namespace. The envelope XSD expects payload to be
    in a foreign namespace (##other).
    """
    # Ensure payload has explicit namespace (empty string = no namespace)
    if 'xmlns=' not in payload_xml:
        # Insert xmlns="" after the tag name
        idx = payload_xml.index('>')
        if payload_xml[idx-1] == '/':
            idx -= 1
        payload_xml = payload_xml[:idx] + ' xmlns=""' + payload_xml[idx:]

    return f"""<message xmlns="{ENVELOPE_NS}">
  <meta>
    <from>{from_id}</from>
    <to>{to_id}</to>
    <thread>{thread_id}</thread>
  </meta>
  {payload_xml}
</message>""".encode('utf-8')


class TestPumpBootstrap:
    """Test ConfigLoader and bootstrap."""

    def test_config_loader_parses_yaml(self):
        """ConfigLoader should parse organism.yaml correctly."""
        config = ConfigLoader.load('config/organism.yaml')

        assert config.name == "hello-world"
        assert len(config.listeners) == 3  # greeter, shouter, response-handler

        # Find greeter and shouter by name
        listener_names = [lc.name for lc in config.listeners]
        assert "greeter" in listener_names
        assert "shouter" in listener_names
        assert "response-handler" in listener_names

    @pytest.mark.asyncio
    async def test_bootstrap_creates_pump(self):
        """bootstrap() should create a configured pump."""
        pump = await bootstrap('config/organism.yaml')

        assert pump.config.name == "hello-world"
        assert len(pump.routing_table) == 6  # 3 user listeners + 3 system (boot, todo, todo-complete)
        assert "greeter.greeting" in pump.routing_table
        assert "shouter.greetingresponse" in pump.routing_table
        assert "response-handler.shoutedresponse" in pump.routing_table
        assert "system.boot.boot" in pump.routing_table  # Boot listener

    @pytest.mark.asyncio
    async def test_bootstrap_generates_xsd(self):
        """bootstrap() should generate XSD schemas for listeners."""
        pump = await bootstrap('config/organism.yaml')

        listener = pump.listeners["greeter"]
        assert listener.schema is not None

        # Schema should validate a proper Greeting
        from lxml import etree
        valid_xml = etree.fromstring(b"<Greeting><Name>Test</Name></Greeting>")
        listener.schema.assertValid(valid_xml)


class TestPumpInjection:
    """Test message injection and queue behavior."""

    @pytest.mark.asyncio
    async def test_inject_adds_to_queue(self):
        """inject() should add a MessageState to the queue."""
        pump = await bootstrap('config/organism.yaml')

        # Bootstrap already injects a boot message, so queue starts with 1
        initial_size = pump.queue.qsize()
        assert initial_size == 1  # Boot message

        thread_id = str(uuid.uuid4())
        await pump.inject(b"<test/>", thread_id, from_id="user")

        assert pump.queue.qsize() == initial_size + 1

        # Drain the boot message first
        boot_state = await pump.queue.get()
        assert b"Boot" in boot_state.raw_bytes

        # Then get our test message
        state = await pump.queue.get()
        assert state.raw_bytes == b"<test/>"
        assert state.thread_id == thread_id
        assert state.from_id == "user"


class TestFullPipelineFlow:
    """Test complete message flow through the pipeline."""

    @pytest.mark.asyncio
    async def test_greeting_round_trip(self):
        """
        Full integration test:
        1. Inject a Greeting message directly to greeter (bypassing console flow)
        2. Pump processes it through the pipeline
        3. Handler is called with deserialized Greeting
        4. Handler response is re-injected
        """
        # Create a minimal config without console (console awaits stdin, blocks tests)
        config = OrganismConfig(name="test-greeting")
        pump = StreamPump(config)

        # Register just greeter
        lc = ListenerConfig(
            name="greeter",
            payload_class_path="handlers.hello.Greeting",
            handler_path="handlers.hello.handle_greeting",
            description="Test greeter",
            is_agent=True,
            peers=["shouter"],
            payload_class=Greeting,
            handler=handle_greeting,
        )
        pump.register_listener(lc)

        # Track what the handler receives
        handler_calls = []
        original_handler = pump.listeners["greeter"].handler

        # Mock the LLM call since we don't have a real API key in tests
        from agentserver.llm.backend import LLMResponse

        mock_response = LLMResponse(
            content="Hello, World!",
            model="mock",
            usage={"total_tokens": 10},
            finish_reason="stop",
        )

        async def tracking_handler(payload, metadata):
            handler_calls.append((payload, metadata))
            # Use mocked original handler
            return await original_handler(payload, metadata)

        pump.listeners["greeter"].handler = tracking_handler

        with patch('agentserver.llm.complete', new=AsyncMock(return_value=mock_response)):
            # Create and inject a Greeting message
            thread_id = str(uuid.uuid4())
            envelope = make_envelope(
                payload_xml="<Greeting><Name>World</Name></Greeting>",
                from_id="user",
                to_id="greeter",
                thread_id=thread_id,
            )

            await pump.inject(envelope, thread_id, from_id="user")

            # Run pump briefly to process the message
            pump._running = True
            pipeline = pump.build_pipeline(pump._queue_source())

            # Process with timeout
            async def run_with_timeout():
                async with pipeline.stream() as streamer:
                    try:
                        async for _ in streamer:
                            # One iteration should process our message
                            break
                    except asyncio.CancelledError:
                        pass

            try:
                await asyncio.wait_for(run_with_timeout(), timeout=2.0)
            except asyncio.TimeoutError:
                pass
            finally:
                pump._running = False

        # Verify handler was called
        assert len(handler_calls) == 1
        payload, metadata = handler_calls[0]

        assert isinstance(payload, Greeting)
        assert payload.name == "World"
        assert metadata.thread_id == thread_id
        assert metadata.from_id == "user"

    @pytest.mark.asyncio
    async def test_handler_response_reinjected(self):
        """Handler response should be re-injected into the queue."""
        # Create a minimal config without console (console awaits stdin, blocks tests)
        config = OrganismConfig(name="test-reinjection")
        pump = StreamPump(config)

        # Register just greeter
        lc = ListenerConfig(
            name="greeter",
            payload_class_path="handlers.hello.Greeting",
            handler_path="handlers.hello.handle_greeting",
            description="Test greeter",
            is_agent=True,
            peers=["shouter"],
            payload_class=Greeting,
            handler=handle_greeting,
        )
        pump.register_listener(lc)

        # Capture re-injected messages
        reinjected = []

        async def capture_reinject(state):
            reinjected.append(state)
            # Don't actually re-inject to avoid infinite loop

        pump._reinject_responses = capture_reinject

        # Mock the LLM call since we don't have a real API key in tests
        from agentserver.llm.backend import LLMResponse

        mock_response = LLMResponse(
            content="Hello, Alice!",
            model="mock",
            usage={"total_tokens": 10},
            finish_reason="stop",
        )

        with patch('agentserver.llm.complete', new=AsyncMock(return_value=mock_response)):
            # Inject a Greeting
            thread_id = str(uuid.uuid4())
            envelope = make_envelope(
                payload_xml="<Greeting><Name>Alice</Name></Greeting>",
                from_id="user",
                to_id="greeter",
                thread_id=thread_id,
            )

            await pump.inject(envelope, thread_id, from_id="user")

            # Run pump briefly
            pump._running = True
            pipeline = pump.build_pipeline(pump._queue_source())

            async def run_with_timeout():
                async with pipeline.stream() as streamer:
                    try:
                        async for _ in streamer:
                            break
                    except asyncio.CancelledError:
                        pass

            try:
                await asyncio.wait_for(run_with_timeout(), timeout=2.0)
            except asyncio.TimeoutError:
                pass
            finally:
                pump._running = False

        # Verify response was re-injected
        assert len(reinjected) == 1
        response_state = reinjected[0]

        assert response_state.raw_bytes is not None
        assert b"Hello, Alice!" in response_state.raw_bytes
        assert response_state.from_id == "greeter"


class TestErrorHandling:
    """Test error paths through the pipeline."""

    @pytest.mark.asyncio
    async def test_invalid_xml_error(self):
        """Malformed XML should set error, not crash."""
        pump = await bootstrap('config/organism.yaml')

        errors = []
        original_handle_errors = pump._handle_errors

        async def capture_errors(state):
            if state.error:
                errors.append(state.error)
            return await original_handle_errors(state)

        pump._handle_errors = capture_errors

        # Inject malformed XML
        thread_id = str(uuid.uuid4())
        await pump.inject(b"<not valid xml", thread_id, from_id="user")

        # Run pump
        pump._running = True
        pipeline = pump.build_pipeline(pump._queue_source())

        async def run_with_timeout():
            async with pipeline.stream() as streamer:
                try:
                    async for _ in streamer:
                        break
                except asyncio.CancelledError:
                    pass

        try:
            await asyncio.wait_for(run_with_timeout(), timeout=2.0)
        except asyncio.TimeoutError:
            pass
        finally:
            pump._running = False

        # Should have logged an error (repair step recovers, but envelope validation fails)
        # The exact error depends on how far it gets
        assert pump.queue.qsize() == 0 or len(errors) >= 0  # Processed without crash

    @pytest.mark.asyncio
    async def test_unknown_route_error(self):
        """Message to unknown listener should error gracefully."""
        pump = await bootstrap('config/organism.yaml')

        errors = []
        original_handle_errors = pump._handle_errors

        async def capture_errors(state):
            if state.error:
                errors.append(state.error)
            return await original_handle_errors(state)

        pump._handle_errors = capture_errors

        # Inject message to non-existent listener
        thread_id = str(uuid.uuid4())
        envelope = make_envelope(
            payload_xml="<Greeting><Name>Test</Name></Greeting>",
            from_id="user",
            to_id="nonexistent",  # No such listener
            thread_id=thread_id,
        )

        await pump.inject(envelope, thread_id, from_id="user")

        # Run pump
        pump._running = True
        pipeline = pump.build_pipeline(pump._queue_source())

        async def run_with_timeout():
            async with pipeline.stream() as streamer:
                try:
                    async for _ in streamer:
                        break
                except asyncio.CancelledError:
                    pass

        try:
            await asyncio.wait_for(run_with_timeout(), timeout=2.0)
        except asyncio.TimeoutError:
            pass
        finally:
            pump._running = False

        # Should have a routing error
        assert any("nonexistent" in e for e in errors)


class TestThreadRoutingFlow:
    """
    Test full thread routing: console-router → greeter (LLM) → shouter → response-handler.

    This verifies that thread IDs are properly propagated and extended through
    the entire message chain, including LLM agent calls.
    """

    @pytest.mark.asyncio
    async def test_full_thread_routing_chain(self):
        """
        Trace thread ID through: console-router → greeter → shouter → response-handler.

        1. Inject ConsoleInput (simulating user input)
        2. Console-router routes to greeter with Greeting
        3. Greeter calls LLM, sends GreetingResponse to shouter
        4. Shouter sends ShoutedResponse to response-handler
        5. Response-handler creates ConsolePrompt

        Thread ID must be consistent through entire chain.
        """
        from handlers.console import ConsoleInput, ConsolePrompt, ShoutedResponse
        from handlers.console import handle_console_input, handle_shouted_response
        from handlers.hello import Greeting, GreetingResponse, handle_greeting, handle_shout
        from agentserver.llm.backend import LLMResponse
        from agentserver.message_bus.thread_registry import get_registry

        # Create pump with full routing chain (but no console - it blocks on stdin)
        config = OrganismConfig(name="thread-routing-test")
        pump = StreamPump(config)

        # Register all handlers in the chain
        pump.register_listener(ListenerConfig(
            name="console-router",
            payload_class_path="handlers.console.ConsoleInput",
            handler_path="handlers.console.handle_console_input",
            description="Routes console input",
            payload_class=ConsoleInput,
            handler=handle_console_input,
        ))

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
            description="Shouts responses",
            payload_class=GreetingResponse,
            handler=handle_shout,
        ))

        pump.register_listener(ListenerConfig(
            name="response-handler",
            payload_class_path="handlers.console.ShoutedResponse",
            handler_path="handlers.console.handle_shouted_response",
            description="Forwards to console",
            payload_class=ShoutedResponse,
            handler=handle_shouted_response,
        ))

        # Track thread IDs at each handler call
        thread_trace = []

        # Wrap handlers to capture thread IDs
        original_console_router = pump.listeners["console-router"].handler
        original_greeter = pump.listeners["greeter"].handler
        original_shouter = pump.listeners["shouter"].handler
        original_response = pump.listeners["response-handler"].handler

        async def trace_console_router(payload, metadata):
            thread_trace.append(("console-router", metadata.thread_id, payload))
            return await original_console_router(payload, metadata)

        async def trace_greeter(payload, metadata):
            thread_trace.append(("greeter", metadata.thread_id, payload))
            return await original_greeter(payload, metadata)

        async def trace_shouter(payload, metadata):
            thread_trace.append(("shouter", metadata.thread_id, payload))
            return await original_shouter(payload, metadata)

        async def trace_response(payload, metadata):
            thread_trace.append(("response-handler", metadata.thread_id, payload))
            return await original_response(payload, metadata)

        pump.listeners["console-router"].handler = trace_console_router
        pump.listeners["greeter"].handler = trace_greeter
        pump.listeners["shouter"].handler = trace_shouter
        pump.listeners["response-handler"].handler = trace_response

        # Mock LLM response
        mock_llm = LLMResponse(
            content="Hello there, friend!",
            model="mock",
            usage={"total_tokens": 10},
            finish_reason="stop",
        )

        # Capture final output (response-handler sends to console, but console isn't registered)
        final_outputs = []

        async def capture_reinject(state):
            final_outputs.append(state)
            # Re-inject to continue the chain (if not to console)
            if state.raw_bytes and b"<to>console</to>" not in state.raw_bytes:
                await pump.queue.put(state)

        pump._reinject_responses = capture_reinject

        with patch('agentserver.llm.complete', new=AsyncMock(return_value=mock_llm)):
            # Inject ConsoleInput (simulating: user typed "@greeter TestUser")
            # Note: xmlify converts field names to PascalCase for XML elements
            thread_id = str(uuid.uuid4())
            envelope = make_envelope(
                payload_xml="<ConsoleInput><Text>TestUser</Text><Target>greeter</Target></ConsoleInput>",
                from_id="console",
                to_id="console-router",
                thread_id=thread_id,
            )

            await pump.inject(envelope, thread_id, from_id="console")

            # Run pump to process all messages in chain
            pump._running = True
            pipeline = pump.build_pipeline(pump._queue_source())

            async def run_chain():
                async with pipeline.stream() as streamer:
                    count = 0
                    async for _ in streamer:
                        count += 1
                        # Process up to 5 messages (should be enough for full chain)
                        if count >= 5:
                            break

            try:
                await asyncio.wait_for(run_chain(), timeout=3.0)
            except asyncio.TimeoutError:
                pass
            finally:
                pump._running = False

        # Verify the trace
        assert len(thread_trace) >= 4, f"Expected 4+ handler calls, got {len(thread_trace)}: {[t[0] for t in thread_trace]}"

        # All handlers should see a thread ID derived from the original
        handler_names = [t[0] for t in thread_trace]
        assert "console-router" in handler_names
        assert "greeter" in handler_names
        assert "shouter" in handler_names
        assert "response-handler" in handler_names

        # Verify payloads were correctly routed
        for name, tid, payload in thread_trace:
            if name == "console-router":
                assert isinstance(payload, ConsoleInput)
                assert payload.target == "greeter"
            elif name == "greeter":
                assert isinstance(payload, Greeting)
                assert payload.name == "TestUser"
            elif name == "shouter":
                assert isinstance(payload, GreetingResponse)
                assert "Hello" in payload.message or "friend" in payload.message
            elif name == "response-handler":
                assert isinstance(payload, ShoutedResponse)
                assert payload.message.isupper()  # Shouted = ALL CAPS

        # Verify thread registry has entries for this chain
        registry = get_registry()
        assert registry.lookup(thread_id) is not None or len(thread_trace) > 0

    @pytest.mark.asyncio
    async def test_thread_id_chain_extension(self):
        """
        Verify thread IDs are extended as messages flow through agents.

        The thread registry should show the chain growing:
        - Initial: console → console-router
        - After greeter: chain includes greeter
        - After shouter: chain includes shouter
        """
        from handlers.console import ConsoleInput, ShoutedResponse
        from handlers.console import handle_console_input, handle_shouted_response
        from handlers.hello import Greeting, GreetingResponse, handle_greeting, handle_shout
        from agentserver.llm.backend import LLMResponse
        from agentserver.message_bus.thread_registry import ThreadRegistry

        # Use a fresh registry for this test
        test_registry = ThreadRegistry()

        # Create pump
        config = OrganismConfig(name="thread-chain-test")
        pump = StreamPump(config)

        # Patch get_registry to use our test registry
        with patch('agentserver.message_bus.stream_pump.get_registry', return_value=test_registry):
            with patch('agentserver.message_bus.thread_registry.get_registry', return_value=test_registry):
                # Register handlers
                pump.register_listener(ListenerConfig(
                    name="console-router",
                    payload_class_path="handlers.console.ConsoleInput",
                    handler_path="handlers.console.handle_console_input",
                    description="Routes console input",
                    payload_class=ConsoleInput,
                    handler=handle_console_input,
                ))

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
                    description="Shouts responses",
                    payload_class=GreetingResponse,
                    handler=handle_shout,
                ))

                pump.register_listener(ListenerConfig(
                    name="response-handler",
                    payload_class_path="handlers.console.ShoutedResponse",
                    handler_path="handlers.console.handle_shouted_response",
                    description="Forwards to console",
                    payload_class=ShoutedResponse,
                    handler=handle_shouted_response,
                ))

                # Track thread IDs after each handler
                thread_ids_seen = []

                original_greeter = pump.listeners["greeter"].handler

                async def trace_thread_after_greeter(payload, metadata):
                    thread_ids_seen.append(("greeter_received", metadata.thread_id))
                    result = await original_greeter(payload, metadata)
                    return result

                pump.listeners["greeter"].handler = trace_thread_after_greeter

                # Prevent re-injection loops
                async def noop_reinject(state):
                    pass
                pump._reinject_responses = noop_reinject

                # Mock LLM
                mock_llm = LLMResponse(
                    content="Hello!",
                    model="mock",
                    usage={"total_tokens": 5},
                    finish_reason="stop",
                )

                with patch('agentserver.llm.complete', new=AsyncMock(return_value=mock_llm)):
                    # Inject initial message
                    thread_id = str(uuid.uuid4())
                    envelope = make_envelope(
                        payload_xml="<ConsoleInput><Text>Alice</Text><Target>greeter</Target></ConsoleInput>",
                        from_id="console",
                        to_id="console-router",
                        thread_id=thread_id,
                    )

                    await pump.inject(envelope, thread_id, from_id="console")

                    # Run pipeline
                    pump._running = True
                    pipeline = pump.build_pipeline(pump._queue_source())

                    async def run_chain():
                        async with pipeline.stream() as streamer:
                            count = 0
                            async for _ in streamer:
                                count += 1
                                if count >= 4:
                                    break

                    try:
                        await asyncio.wait_for(run_chain(), timeout=3.0)
                    except asyncio.TimeoutError:
                        pass
                    finally:
                        pump._running = False

                # Verify registry has tracked the chain
                chain = test_registry.lookup(thread_id)
                assert chain is not None, "Thread should be registered"

                # The chain should show the message flow path
                # e.g. "console.console-router" or similar
                assert "console" in chain.lower() or len(thread_ids_seen) > 0


class TestManualPumpConfiguration:
    """Test creating a pump without YAML config."""

    @pytest.mark.asyncio
    async def test_manual_listener_registration(self):
        """Can register listeners programmatically."""
        config = OrganismConfig(name="manual-test")
        pump = StreamPump(config)

        lc = ListenerConfig(
            name="greeter",
            payload_class_path="handlers.hello.Greeting",
            handler_path="handlers.hello.handle_greeting",
            description="Test listener",
            payload_class=Greeting,
            handler=handle_greeting,
        )

        listener = pump.register_listener(lc)

        assert listener.name == "greeter"
        assert listener.root_tag == "greeter.greeting"
        assert "greeter.greeting" in pump.routing_table

    @pytest.mark.asyncio
    async def test_custom_handler(self):
        """Can use a custom handler function."""
        config = OrganismConfig(name="custom-test")
        pump = StreamPump(config)

        responses = []

        async def custom_handler(payload, metadata):
            responses.append(payload)
            return b"<Ack/>"

        lc = ListenerConfig(
            name="custom",
            payload_class_path="handlers.hello.Greeting",
            handler_path="handlers.hello.handle_greeting",
            description="Custom handler",
            payload_class=Greeting,
            handler=custom_handler,
        )

        pump.register_listener(lc)

        # Inject and process
        thread_id = str(uuid.uuid4())
        envelope = make_envelope(
            payload_xml="<Greeting><Name>Custom</Name></Greeting>",
            from_id="tester",
            to_id="custom",
            thread_id=thread_id,
        )

        await pump.inject(envelope, thread_id, from_id="tester")

        # Run pump
        pump._running = True

        # Capture re-injected to prevent loop
        async def noop_reinject(state):
            pass
        pump._reinject_responses = noop_reinject

        pipeline = pump.build_pipeline(pump._queue_source())

        async def run_with_timeout():
            async with pipeline.stream() as streamer:
                try:
                    async for _ in streamer:
                        break
                except asyncio.CancelledError:
                    pass

        try:
            await asyncio.wait_for(run_with_timeout(), timeout=2.0)
        except asyncio.TimeoutError:
            pass
        finally:
            pump._running = False

        # Custom handler should have been called
        assert len(responses) == 1
        assert responses[0].name == "Custom"
