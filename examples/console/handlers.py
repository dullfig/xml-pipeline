"""
handlers.py — Example handlers for the console demo.

These handlers demonstrate the basic patterns without LLM dependencies:
- Greeting: Simple greeting flow
- Echo: Echo back input
- Response printing to console

No LLM calls, no complex logic — just show how messages flow.
"""

from dataclasses import dataclass

from third_party.xmlable import xmlify
from xml_pipeline.message_bus.message_state import HandlerMetadata, HandlerResponse


# ============================================================================
# Payloads
# ============================================================================

@xmlify
@dataclass
class Greeting:
    """A greeting request."""
    name: str


@xmlify
@dataclass
class GreetingReply:
    """Response from the greeter."""
    message: str


@xmlify
@dataclass
class Echo:
    """Echo request — repeats back whatever you send."""
    text: str


@xmlify
@dataclass
class EchoReply:
    """Echoed response."""
    text: str


@xmlify
@dataclass
class ConsoleOutput:
    """Output to display on console."""
    source: str
    text: str


# ============================================================================
# Handlers
# ============================================================================

async def handle_greeting(payload: Greeting, metadata: HandlerMetadata) -> HandlerResponse:
    """
    Handle a Greeting and return a friendly response.

    This is a pure tool (no LLM) — just demonstrates message routing.
    """
    message = f"Hello, {payload.name}! Welcome to xml-pipeline."

    return HandlerResponse(
        payload=ConsoleOutput(source="greeter", text=message),
        to="console-output",
    )


async def handle_echo(payload: Echo, metadata: HandlerMetadata) -> HandlerResponse:
    """
    Echo back whatever text was sent.

    Demonstrates simple request/response pattern.
    """
    return HandlerResponse(
        payload=ConsoleOutput(source="echo", text=payload.text),
        to="console-output",
    )


async def handle_print(payload: ConsoleOutput, metadata: HandlerMetadata) -> None:
    """
    Print output to the console.

    This is a terminal handler — returns None to end the chain.
    Uses console_registry to find the active console (if any).
    """
    # Try to use registered console, fall back to print
    try:
        from .console import get_active_console
        console = get_active_console()
        if console is not None:
            console.display_response(payload.source, payload.text)
            return
    except (ImportError, RuntimeError):
        pass

    # Fallback: just print with color
    print(f"\033[36m[{payload.source}]\033[0m {payload.text}")
