"""
Console Example — Interactive terminal for xml-pipeline.

This example demonstrates how to build an interactive console
that sends messages to listeners and displays responses.

Usage:
    python -m examples.console [config.yaml]

Or in your own code:
    from examples.console import Console
    console = Console(pump)
    await console.run()

Dependencies:
    pip install prompt_toolkit  # For rich terminal input (optional)

The console provides:
    - @listener message — Send message to a listener
    - /help — Show available commands
    - /listeners — List registered listeners
    - /quit — Graceful shutdown

This is a reference implementation. Feel free to copy and modify
for your own use case.
"""

from .console import Console
from .handlers import Greeting, Echo, handle_greeting, handle_echo, handle_print

__all__ = [
    "Console",
    "Greeting",
    "Echo",
    "handle_greeting",
    "handle_echo",
    "handle_print",
]
