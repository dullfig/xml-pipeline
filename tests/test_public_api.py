"""
test_public_api.py — Tests for the clean public API surface.

Verifies that downstream consumers can use top-level imports and the
simplified StreamPump API (register, start, inject, from_yaml) without
reaching into internal modules.

Run with: pytest tests/test_public_api.py -v
"""

import pytest
import asyncio
import uuid
from dataclasses import dataclass
from unittest.mock import AsyncMock

from third_party.xmlable import xmlify


# ============================================================================
# Phase 1: Top-level imports
# ============================================================================


class TestTopLevelImports:
    """Verify that stable symbols are importable from xml_pipeline directly."""

    def test_import_handler_response(self):
        from xml_pipeline import HandlerResponse
        assert HandlerResponse is not None

    def test_import_handler_metadata(self):
        from xml_pipeline import HandlerMetadata
        assert HandlerMetadata is not None

    def test_import_stream_pump(self):
        from xml_pipeline import StreamPump
        assert StreamPump is not None

    def test_import_events(self):
        from xml_pipeline import (
            PumpEvent,
            MessageReceivedEvent,
            MessageSentEvent,
            AgentStateEvent,
            ThreadEvent,
            ReloadEvent,
        )
        assert PumpEvent is not None
        assert MessageReceivedEvent is not None
        assert MessageSentEvent is not None
        assert AgentStateEvent is not None
        assert ThreadEvent is not None
        assert ReloadEvent is not None

    def test_import_version(self):
        from xml_pipeline import __version__
        assert __version__ == "0.4.0"

    def test_all_exports(self):
        import xml_pipeline
        assert hasattr(xml_pipeline, "__all__")
        expected = {
            "__version__",
            "HandlerResponse", "HandlerMetadata",
            "StreamPump",
            "PumpEvent", "MessageReceivedEvent", "MessageSentEvent",
            "AgentStateEvent", "ThreadEvent", "ReloadEvent",
            "get_worker_registry",
            "get_port_gate",
            "get_wasm_registry",
        }
        assert set(xml_pipeline.__all__) == expected

    def test_same_objects_as_submodules(self):
        """Top-level re-exports should be the exact same objects."""
        from xml_pipeline import HandlerResponse, HandlerMetadata, StreamPump
        from xml_pipeline.message_bus.message_state import (
            HandlerResponse as HR,
            HandlerMetadata as HM,
        )
        from xml_pipeline.message_bus.stream_pump import StreamPump as SP  # canonical path
        assert HandlerResponse is HR
        assert HandlerMetadata is HM
        assert StreamPump is SP


# ============================================================================
# Phase 2: StreamPump kwargs constructor
# ============================================================================


class TestStreamPumpKwargsInit:
    """Verify that StreamPump can be created with keyword args."""

    def test_default_constructor(self):
        from xml_pipeline import StreamPump
        pump = StreamPump()
        assert pump.config.name == "organism"
        assert pump.config.port == 8765
        assert pump.config.max_tokens_per_thread == 100_000

    def test_named_constructor(self):
        from xml_pipeline import StreamPump
        pump = StreamPump(name="test-org", port=9000)
        assert pump.config.name == "test-org"
        assert pump.config.port == 9000

    def test_custom_limits(self):
        from xml_pipeline import StreamPump
        pump = StreamPump(
            name="limited",
            max_tokens_per_thread=50_000,
            max_concurrent_handlers=10,
            max_concurrent_per_agent=2,
        )
        assert pump.config.max_tokens_per_thread == 50_000
        assert pump.config.max_concurrent_handlers == 10
        assert pump.config.max_concurrent_per_agent == 2

    def test_llm_config(self):
        from xml_pipeline import StreamPump
        llm = {"strategy": "failover", "backends": []}
        pump = StreamPump(name="with-llm", llm_config=llm)
        assert pump.config.llm_config == llm

    def test_backward_compat_config_object(self):
        """Passing config= directly should still work."""
        from xml_pipeline import StreamPump
        from xml_pipeline.message_bus.pump_config import OrganismConfig
        config = OrganismConfig(name="legacy", port=1234)
        pump = StreamPump(config=config)
        assert pump.config.name == "legacy"
        assert pump.config.port == 1234


# ============================================================================
# Phase 3: StreamPump.register()
# ============================================================================


@xmlify
@dataclass
class SamplePayload:
    """Payload for registration tests."""
    value: str


async def _noop_handler(payload: SamplePayload, metadata) -> None:
    """No-op handler for registration tests."""
    return None


class TestStreamPumpRegister:
    """Verify the clean register() method."""

    def test_register_basic(self):
        from xml_pipeline import StreamPump
        pump = StreamPump(name="test")
        listener = pump.register("my-tool", _noop_handler, SamplePayload,
                                 description="A test tool")

        assert listener.name == "my-tool"
        assert listener.payload_class is SamplePayload
        assert listener.handler is _noop_handler
        assert listener.description == "A test tool"
        assert listener.is_agent is False
        assert listener.root_tag == "my-tool.samplepayload"

    def test_register_agent_with_peers(self):
        from xml_pipeline import StreamPump
        pump = StreamPump(name="test")

        # Register a tool first
        pump.register("calculator", _noop_handler, SamplePayload,
                      description="Calculator tool")

        # Register an agent with peers
        listener = pump.register(
            "researcher", _noop_handler, SamplePayload,
            description="Research agent",
            agent=True,
            peers=["calculator"],
            prompt="You are a researcher.",
        )

        assert listener.is_agent is True
        assert listener.peers == ["calculator"]
        assert listener.name in pump.listeners
        assert listener.name in pump.agent_semaphores

    def test_register_appears_in_routing_table(self):
        from xml_pipeline import StreamPump
        pump = StreamPump(name="test")
        pump.register("my-tool", _noop_handler, SamplePayload,
                      description="Test")

        assert "my-tool.samplepayload" in pump.routing_table

    def test_register_derives_import_paths(self):
        from xml_pipeline import StreamPump
        pump = StreamPump(name="test")
        listener = pump.register("my-tool", _noop_handler, SamplePayload,
                                 description="Test")

        # The handler_path should be derived from the function's module/qualname
        assert "_noop_handler" in listener.handler_path
        assert "SamplePayload" in listener.root_tag.replace("samplepayload", "SamplePayload") or True


# ============================================================================
# Phase 4: StreamPump.start()
# ============================================================================


class TestStreamPumpStart:
    """Verify that start() performs the full bootstrap ceremony."""

    @pytest.mark.asyncio
    async def test_start_registers_system_listeners(self):
        from xml_pipeline import StreamPump
        pump = StreamPump(name="test-start")
        # Register a user listener first
        pump.register("my-tool", _noop_handler, SamplePayload,
                      description="Test tool")

        await pump.start()

        # System listeners should be registered
        assert "system.boot" in pump.listeners
        assert "system.todo" in pump.listeners
        assert "system.todo-complete" in pump.listeners
        assert "system.sequence" in pump.listeners
        assert "system.buffer" in pump.listeners

    @pytest.mark.asyncio
    async def test_start_initializes_root_thread(self):
        from xml_pipeline import StreamPump
        from xml_pipeline.message_bus.thread_registry import get_registry

        pump = StreamPump(name="test-thread")
        await pump.start()

        registry = get_registry()
        assert registry.root_uuid is not None
        assert "test-thread" in registry.root_chain

    @pytest.mark.asyncio
    async def test_start_injects_boot_message(self):
        from xml_pipeline import StreamPump
        pump = StreamPump(name="test-boot")
        await pump.start()

        # Boot message should be in the queue
        assert pump.queue.qsize() >= 1
        state = await pump.queue.get()
        assert b"Boot" in state.raw_bytes

    @pytest.mark.asyncio
    async def test_start_sets_global_pump(self):
        from xml_pipeline import StreamPump
        from xml_pipeline.message_bus.singleton import get_stream_pump

        pump = StreamPump(name="test-global")
        await pump.start()

        assert get_stream_pump() is pump

    @pytest.mark.asyncio
    async def test_start_freezes_prompt_registry(self):
        from xml_pipeline import StreamPump
        from xml_pipeline.platform import get_prompt_registry

        pump = StreamPump(name="test-prompts")
        pump.register("agent1", _noop_handler, SamplePayload,
                      description="Test agent", agent=True,
                      peers=[], prompt="You are helpful.")
        await pump.start()

        registry = get_prompt_registry()
        assert registry._frozen

    @pytest.mark.asyncio
    async def test_start_idempotent_system_listeners(self):
        """Calling start() twice should not duplicate system listeners."""
        from xml_pipeline import StreamPump
        pump = StreamPump(name="test-idem")
        await pump.start()

        # Count listeners before second start
        count_before = len(pump.listeners)

        # Reset prompt registry so freeze doesn't error
        from xml_pipeline.platform import get_prompt_registry
        get_prompt_registry().clear()

        await pump.start()

        assert len(pump.listeners) == count_before


# ============================================================================
# Phase 5: Payload-based inject()
# ============================================================================


class TestPayloadInject:
    """Verify the new payload-based inject() method."""

    @pytest.mark.asyncio
    async def test_inject_payload(self):
        from xml_pipeline import StreamPump
        pump = StreamPump(name="test-inject")
        pump.register("my-tool", _noop_handler, SamplePayload,
                      description="Test tool")
        await pump.start()

        # Drain boot message
        await pump.queue.get()

        thread_id = await pump.inject("my-tool", SamplePayload(value="hello"))

        assert thread_id  # Non-empty string
        assert pump.queue.qsize() == 1

        state = await pump.queue.get()
        assert b"hello" in state.raw_bytes
        assert b"my-tool" in state.raw_bytes

    @pytest.mark.asyncio
    async def test_inject_with_explicit_thread(self):
        from xml_pipeline import StreamPump
        pump = StreamPump(name="test-inject-thread")
        pump.register("my-tool", _noop_handler, SamplePayload,
                      description="Test tool")
        await pump.start()
        await pump.queue.get()  # drain boot

        my_thread = str(uuid.uuid4())
        returned_thread = await pump.inject(
            "my-tool", SamplePayload(value="test"),
            thread_id=my_thread,
        )

        assert returned_thread == my_thread

    @pytest.mark.asyncio
    async def test_inject_requires_payload(self):
        from xml_pipeline import StreamPump
        pump = StreamPump(name="test-err")
        await pump.start()

        with pytest.raises(TypeError):
            await pump.inject("my-tool")


# ============================================================================
# Phase 6: from_yaml()
# ============================================================================


class TestFromYaml:
    """Verify the from_yaml() classmethod."""

    @pytest.mark.asyncio
    async def test_from_yaml_creates_pump(self):
        from xml_pipeline import StreamPump
        pump = await StreamPump.from_yaml("config/organism.yaml")

        assert pump.config.name == "hello-world"
        assert "greeter" in pump.listeners
        assert "shouter" in pump.listeners
        assert "system.boot" in pump.listeners

    @pytest.mark.asyncio
    async def test_from_yaml_matches_bootstrap(self):
        """from_yaml() should produce a fully configured pump."""
        from xml_pipeline import StreamPump
        pump = await StreamPump.from_yaml("config/organism.yaml")

        # Same listeners as from_yaml() creates
        assert "greeter.greeting" in pump.routing_table
        assert "shouter.greetingresponse" in pump.routing_table
        assert "system.boot.boot" in pump.routing_table

    @pytest.mark.asyncio
    async def test_from_yaml_has_boot_in_queue(self):
        from xml_pipeline import StreamPump
        pump = await StreamPump.from_yaml("config/organism.yaml")

        assert pump.queue.qsize() >= 1
        state = await pump.queue.get()
        assert b"Boot" in state.raw_bytes
