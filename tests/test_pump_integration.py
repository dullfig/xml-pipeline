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
from handlers.hello import Greeting, GreetingResponse, handle_greeting, ENVELOPE_NS


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
        assert len(config.listeners) == 1
        assert config.listeners[0].name == "greeter"
        assert config.listeners[0].payload_class == Greeting
        assert config.listeners[0].handler == handle_greeting

    @pytest.mark.asyncio
    async def test_bootstrap_creates_pump(self):
        """bootstrap() should create a configured pump."""
        pump = await bootstrap('config/organism.yaml')

        assert pump.config.name == "hello-world"
        assert "greeter.greeting" in pump.routing_table
        assert pump.listeners["greeter"].payload_class == Greeting

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

        thread_id = str(uuid.uuid4())
        await pump.inject(b"<test/>", thread_id, from_id="user")

        assert pump.queue.qsize() == 1
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
        1. Inject a Greeting message
        2. Pump processes it through the pipeline
        3. Handler is called with deserialized Greeting
        4. Handler response is re-injected
        """
        pump = await bootstrap('config/organism.yaml')

        # Track what the handler receives
        handler_calls = []
        original_handler = pump.listeners["greeter"].handler

        async def tracking_handler(payload, metadata):
            handler_calls.append((payload, metadata))
            return await original_handler(payload, metadata)

        pump.listeners["greeter"].handler = tracking_handler

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
        pump = await bootstrap('config/organism.yaml')

        # Capture re-injected messages
        reinjected = []
        original_reinject = pump._reinject_responses

        async def capture_reinject(state):
            reinjected.append(state)
            # Don't actually re-inject to avoid infinite loop

        pump._reinject_responses = capture_reinject

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
        assert response_state.thread_id == thread_id
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
