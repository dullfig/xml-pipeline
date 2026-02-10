"""
Console tool — display messages and prompt for user input.

Provides two capabilities:
- Notify: print a message to the console (fire-and-forget)
- Prompt: print a message, wait for user input, return the input

Backend-agnostic via ConsoleBackend protocol. Ships with StdioBackend;
swap in a web or TUI backend via set_console_backend().
"""

import asyncio
import sys
from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable

from third_party.xmlable import xmlify

from xml_pipeline.message_bus.message_state import HandlerMetadata, HandlerResponse

from .base import ToolResult, tool


# ---------------------------------------------------------------------------
# ANSI helpers
# ---------------------------------------------------------------------------

_RESET = "\033[0m"
_BOLD = "\033[1m"
_CYAN = "\033[36m"
_YELLOW = "\033[33m"
_RED = "\033[31m"
_GREEN = "\033[32m"

_LEVEL_COLORS: dict[str, str] = {
    "info": _CYAN,
    "warn": _YELLOW,
    "error": _RED,
    "success": _GREEN,
}


# ---------------------------------------------------------------------------
# Backend protocol + registry
# ---------------------------------------------------------------------------


@runtime_checkable
class ConsoleBackend(Protocol):
    """Interface for console I/O backends."""

    async def notify(self, source: str, text: str, *, level: str = "info") -> None: ...
    async def prompt(self, source: str, text: str) -> str: ...
    async def start(self) -> None: ...
    async def stop(self) -> None: ...


class StdioBackend:
    """Default backend — ANSI-colored stdout + stdin via executor."""

    async def notify(self, source: str, text: str, *, level: str = "info") -> None:
        color = _LEVEL_COLORS.get(level, "")
        reset = _RESET if color else ""
        print(f"{color}[{source}]{reset} {text}")

    async def prompt(self, source: str, text: str) -> str:
        print(f"{_BOLD}[{source}]{_RESET} {text}")
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, lambda: input("> ")
        )

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass


_backend: Optional[ConsoleBackend] = None


def get_console_backend() -> ConsoleBackend:
    """Return the active backend; lazy-init StdioBackend if none set."""
    global _backend
    if _backend is None:
        _backend = StdioBackend()
    return _backend


def set_console_backend(backend: Optional[ConsoleBackend]) -> None:
    """Set the active console backend (or None to clear)."""
    global _backend
    _backend = backend


def reset_console_backend() -> None:
    """Reset to default (lazy StdioBackend). Useful in tests."""
    global _backend
    _backend = None


# ---------------------------------------------------------------------------
# Message-bus payload classes
# ---------------------------------------------------------------------------


@xmlify
@dataclass
class ConsoleNotify:
    """Print a message to the console (fire-and-forget)."""
    source: str   # Who is reporting (agent name or label)
    text: str     # The message to display
    level: str    # "info", "warn", "error", "success"


@xmlify
@dataclass
class ConsolePrompt:
    """Print a message and wait for user input."""
    source: str   # Who is asking
    text: str     # The prompt question


@xmlify
@dataclass
class ConsoleInput:
    """User input returned from a console prompt."""
    source: str   # Echo back who asked
    text: str     # The user's input


# ---------------------------------------------------------------------------
# Message-bus handlers
# ---------------------------------------------------------------------------


async def handle_console_notify(
    payload: ConsoleNotify, metadata: HandlerMetadata
) -> None:
    """Handler for console.notify — fire-and-forget, terminates chain."""
    backend = get_console_backend()
    await backend.notify(payload.source, payload.text, level=payload.level)
    return None


async def handle_console_prompt(
    payload: ConsolePrompt, metadata: HandlerMetadata
) -> HandlerResponse:
    """Handler for console.prompt — waits for input, responds to caller."""
    import logging as _log
    _log.getLogger(__name__).info(
        "Console prompt from=%s source=%s text=%r",
        metadata.from_id, payload.source, payload.text[:200],
    )
    # Use the authenticated caller identity as the display source,
    # not the untrusted payload.source field (prevents spoofing)
    display_source = metadata.from_id or payload.source
    backend = get_console_backend()
    user_input = await backend.prompt(display_source, payload.text)
    return HandlerResponse.respond(
        payload=ConsoleInput(source=payload.source, text=user_input),
    )


# ---------------------------------------------------------------------------
# @tool wrappers (existing tool API)
# ---------------------------------------------------------------------------


@tool
async def console_notify(source: str, text: str, level: str = "info") -> ToolResult:
    """
    Print a message to the console.

    Levels: info, warn, error, success.
    Fire-and-forget — no return data.

    Examples:
    - console_notify("greeter", "Hello!", "info")
    - console_notify("monitor", "Disk full", "error")
    """
    backend = get_console_backend()
    await backend.notify(source, text, level=level)
    return ToolResult(success=True, data=None)


@tool
async def console_prompt(source: str, text: str) -> ToolResult:
    """
    Print a message and wait for user input.

    Returns the user's input as a string.

    Examples:
    - console_prompt("greeter", "What is your name?")
    """
    backend = get_console_backend()
    user_input = await backend.prompt(source, text)
    return ToolResult(success=True, data=user_input)
