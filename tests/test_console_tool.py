"""Tests for the console tool, handlers, and payload classes."""

import pytest
from lxml import etree

from third_party.xmlable import parse_string

from xml_pipeline.message_bus.message_state import HandlerMetadata, HandlerResponse
from xml_pipeline.tools.base import ToolResult, get_tool_registry
from xml_pipeline.tools.console import (
    ConsoleInput,
    ConsoleNotify,
    ConsolePrompt,
    StdioBackend,
    console_notify,
    console_prompt,
    get_console_backend,
    handle_console_notify,
    handle_console_prompt,
    reset_console_backend,
    set_console_backend,
)


# ── Helpers ──────────────────────────────────────────────────────────────


def _metadata() -> HandlerMetadata:
    """Minimal metadata fixture for handler tests."""
    return HandlerMetadata(
        thread_id="test-thread-000",
        from_id="caller",
    )


class MockBackend:
    """Deterministic console backend for testing (no actual I/O)."""

    def __init__(self, input_response: str = "mock input"):
        self.notifications: list[tuple[str, str, str]] = []
        self.prompts: list[tuple[str, str]] = []
        self.input_response = input_response

    async def notify(self, source: str, text: str, *, level: str = "info") -> None:
        self.notifications.append((source, text, level))

    async def prompt(self, source: str, text: str) -> str:
        self.prompts.append((source, text))
        return self.input_response

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass


@pytest.fixture(autouse=True)
def _clean_backend():
    """Reset the console backend before and after each test."""
    reset_console_backend()
    yield
    reset_console_backend()


# ═══════════════════════════════════════════════════════════════════════════
# TestBackendRegistry — get/set/reset
# ═══════════════════════════════════════════════════════════════════════════


class TestBackendRegistry:
    """Console backend singleton management."""

    def test_default_backend_is_stdio(self) -> None:
        backend = get_console_backend()
        assert isinstance(backend, StdioBackend)

    def test_custom_backend_via_set(self) -> None:
        mock = MockBackend()
        set_console_backend(mock)
        assert get_console_backend() is mock

    def test_reset_clears_backend(self) -> None:
        mock = MockBackend()
        set_console_backend(mock)
        reset_console_backend()
        # After reset, lazy-init gives a fresh StdioBackend
        backend = get_console_backend()
        assert isinstance(backend, StdioBackend)
        assert backend is not mock


# ═══════════════════════════════════════════════════════════════════════════
# TestHandleConsoleNotify — message-bus handler
# ═══════════════════════════════════════════════════════════════════════════


class TestHandleConsoleNotify:
    """The handle_console_notify() async handler for the message bus."""

    async def test_fires_and_forgets(self) -> None:
        """Handler returns None (terminates chain)."""
        mock = MockBackend()
        set_console_backend(mock)
        payload = ConsoleNotify(source="agent", text="hello", level="info")
        result = await handle_console_notify(payload, _metadata())
        assert result is None
        assert mock.notifications == [("agent", "hello", "info")]

    async def test_level_passed_through(self) -> None:
        """All four levels are accepted and forwarded to backend."""
        mock = MockBackend()
        set_console_backend(mock)
        for level in ("info", "warn", "error", "success"):
            payload = ConsoleNotify(source="src", text="msg", level=level)
            await handle_console_notify(payload, _metadata())
        levels = [n[2] for n in mock.notifications]
        assert levels == ["info", "warn", "error", "success"]


# ═══════════════════════════════════════════════════════════════════════════
# TestHandleConsolePrompt — message-bus handler
# ═══════════════════════════════════════════════════════════════════════════


class TestHandleConsolePrompt:
    """The handle_console_prompt() async handler for the message bus."""

    async def test_returns_console_input(self) -> None:
        """Handler returns HandlerResponse with ConsoleInput payload."""
        mock = MockBackend(input_response="Alice")
        set_console_backend(mock)
        payload = ConsolePrompt(source="greeter", text="What is your name?")
        resp = await handle_console_prompt(payload, _metadata())
        assert isinstance(resp, HandlerResponse)
        assert resp.is_response is True
        assert isinstance(resp.payload, ConsoleInput)
        assert resp.payload.source == "greeter"
        assert resp.payload.text == "Alice"
        # Display source uses metadata.from_id (trusted), not payload.source
        assert mock.prompts == [("caller", "What is your name?")]

    async def test_echoes_source(self) -> None:
        """Source from the prompt is echoed back in ConsoleInput (payload field preserved)."""
        mock = MockBackend(input_response="yes")
        set_console_backend(mock)
        payload = ConsolePrompt(source="monitor", text="Continue?")
        resp = await handle_console_prompt(payload, _metadata())
        # ConsoleInput.source preserves the payload.source for round-trip routing
        assert resp.payload.source == "monitor"
        # But display uses metadata.from_id (trusted identity)
        assert mock.prompts == [("caller", "Continue?")]


# ═══════════════════════════════════════════════════════════════════════════
# TestConsoleTools — @tool decorator
# ═══════════════════════════════════════════════════════════════════════════


class TestConsoleTools:
    """The @tool-decorated console_notify() and console_prompt() functions."""

    async def test_notify_tool_success(self) -> None:
        mock = MockBackend()
        set_console_backend(mock)
        result = await console_notify(source="agent", text="hi", level="info")
        assert isinstance(result, ToolResult)
        assert result.success is True
        assert result.data is None
        assert mock.notifications == [("agent", "hi", "info")]

    async def test_prompt_tool_returns_input(self) -> None:
        mock = MockBackend(input_response="42")
        set_console_backend(mock)
        result = await console_prompt(source="calc", text="Enter number:")
        assert isinstance(result, ToolResult)
        assert result.success is True
        assert result.data == "42"

    async def test_notify_registered_in_tool_registry(self) -> None:
        registry = get_tool_registry()
        tool_obj = registry.get("console_notify")
        assert tool_obj is not None
        assert tool_obj.name == "console_notify"

    async def test_prompt_registered_in_tool_registry(self) -> None:
        registry = get_tool_registry()
        tool_obj = registry.get("console_prompt")
        assert tool_obj is not None
        assert tool_obj.name == "console_prompt"


# ═══════════════════════════════════════════════════════════════════════════
# TestPayloadRoundtrip — xmlify serialization
# ═══════════════════════════════════════════════════════════════════════════


class TestPayloadRoundtrip:
    """@xmlify dataclass → XML → back."""

    def test_console_notify_round_trip(self) -> None:
        original = ConsoleNotify(source="agent", text="hello world", level="warn")
        xml_tree = original.xml_value("ConsoleNotify")
        xml_str = etree.tostring(xml_tree, encoding="unicode")
        restored = parse_string(ConsoleNotify, xml_str)
        assert restored.source == original.source
        assert restored.text == original.text
        assert restored.level == original.level

    def test_console_prompt_round_trip(self) -> None:
        original = ConsolePrompt(source="greeter", text="What is your name?")
        xml_tree = original.xml_value("ConsolePrompt")
        xml_str = etree.tostring(xml_tree, encoding="unicode")
        restored = parse_string(ConsolePrompt, xml_str)
        assert restored.source == original.source
        assert restored.text == original.text

    def test_console_input_round_trip(self) -> None:
        original = ConsoleInput(source="greeter", text="Alice")
        xml_tree = original.xml_value("ConsoleInput")
        xml_str = etree.tostring(xml_tree, encoding="unicode")
        restored = parse_string(ConsoleInput, xml_str)
        assert restored.source == original.source
        assert restored.text == original.text
