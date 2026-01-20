"""
console.py — Simple interactive console for xml-pipeline.

This is a minimal, copy-friendly implementation that shows how to:
- Send messages to listeners via the message pump
- Display responses
- Handle basic commands

No password auth, no TUI split-screen, no LSP — just the essentials.
Uses prompt_toolkit if available, falls back to basic input().

Copy this file and modify for your own use case.
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from typing import TYPE_CHECKING, Optional

# Optional: prompt_toolkit for better terminal experience
try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import InMemoryHistory
    from prompt_toolkit.patch_stdout import patch_stdout
    PROMPT_TOOLKIT = True
except ImportError:
    PROMPT_TOOLKIT = False

if TYPE_CHECKING:
    from xml_pipeline.message_bus.stream_pump import StreamPump


# ============================================================================
# Global console registry (for handlers to find us)
# ============================================================================

_active_console: Optional["Console"] = None


def get_active_console() -> Optional["Console"]:
    """Get the currently active console instance."""
    return _active_console


def set_active_console(console: Optional["Console"]) -> None:
    """Set the active console instance."""
    global _active_console
    _active_console = console


# ============================================================================
# ANSI Colors (simple, no dependencies)
# ============================================================================

class Colors:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    CYAN = "\033[36m"


def cprint(text: str, color: str = "") -> None:
    """Print with optional ANSI color."""
    if color:
        print(f"{color}{text}{Colors.RESET}")
    else:
        print(text)


# ============================================================================
# Console
# ============================================================================

class Console:
    """
    Simple interactive console for xml-pipeline.

    Usage:
        pump = await bootstrap("organism.yaml")
        console = Console(pump)
        await console.run()
    """

    def __init__(self, pump: StreamPump):
        self.pump = pump
        self.running = False
        self._session: Optional[PromptSession] = None

    async def run(self) -> None:
        """Main console loop."""
        set_active_console(self)
        self.running = True

        self._print_banner()

        # Initialize prompt session if available
        if PROMPT_TOOLKIT:
            self._session = PromptSession(history=InMemoryHistory())

        try:
            while self.running:
                try:
                    line = await self._read_input("> ")
                    if line:
                        await self._handle_input(line.strip())
                except EOFError:
                    cprint("\nGoodbye!", Colors.YELLOW)
                    break
                except KeyboardInterrupt:
                    continue
        finally:
            set_active_console(None)

    async def _read_input(self, prompt: str) -> str:
        """Read a line of input."""
        if PROMPT_TOOLKIT and self._session:
            with patch_stdout():
                return await self._session.prompt_async(prompt)
        else:
            # Fallback: blocking input in executor
            loop = asyncio.get_event_loop()
            print(prompt, end="", flush=True)
            line = await loop.run_in_executor(None, sys.stdin.readline)
            return line.strip() if line else ""

    async def _handle_input(self, line: str) -> None:
        """Route input to appropriate handler."""
        if line.startswith("/"):
            await self._handle_command(line)
        elif line.startswith("@"):
            await self._handle_message(line)
        else:
            cprint("Use @listener message or /command", Colors.DIM)
            cprint("Type /help for available commands", Colors.DIM)

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    async def _handle_command(self, line: str) -> None:
        """Handle /command."""
        parts = line[1:].split(None, 1)
        cmd = parts[0].lower() if parts else ""
        args = parts[1] if len(parts) > 1 else ""

        commands = {
            "help": self._cmd_help,
            "h": self._cmd_help,
            "listeners": self._cmd_listeners,
            "ls": self._cmd_listeners,
            "status": self._cmd_status,
            "quit": self._cmd_quit,
            "q": self._cmd_quit,
            "exit": self._cmd_quit,
        }

        handler = commands.get(cmd)
        if handler:
            await handler(args)
        else:
            cprint(f"Unknown command: /{cmd}", Colors.RED)
            cprint("Type /help for available commands", Colors.DIM)

    async def _cmd_help(self, args: str) -> None:
        """Show help."""
        cprint("\nCommands:", Colors.CYAN)
        cprint("  /help, /h          Show this help", Colors.DIM)
        cprint("  /listeners, /ls    List registered listeners", Colors.DIM)
        cprint("  /status            Show organism status", Colors.DIM)
        cprint("  /quit, /q          Exit", Colors.DIM)
        cprint("")
        cprint("Messages:", Colors.CYAN)
        cprint("  @listener text     Send message to listener", Colors.DIM)
        cprint("")
        cprint("Examples:", Colors.CYAN)
        cprint("  @greeter Alice     Greet Alice", Colors.DIM)
        cprint("  @echo Hello!       Echo back 'Hello!'", Colors.DIM)
        cprint("")

    async def _cmd_listeners(self, args: str) -> None:
        """List registered listeners."""
        cprint("\nListeners:", Colors.CYAN)
        for name, listener in sorted(self.pump.listeners.items()):
            desc = listener.description or "(no description)"
            cprint(f"  {name:20} {desc}", Colors.DIM)
        cprint("")

    async def _cmd_status(self, args: str) -> None:
        """Show organism status."""
        cprint(f"\nOrganism: {self.pump.config.name}", Colors.CYAN)
        cprint(f"Listeners: {len(self.pump.listeners)}", Colors.DIM)
        cprint(f"Running: {self.pump._running}", Colors.DIM)
        cprint("")

    async def _cmd_quit(self, args: str) -> None:
        """Exit the console."""
        cprint("Shutting down...", Colors.YELLOW)
        self.running = False

    # ------------------------------------------------------------------
    # Message Sending
    # ------------------------------------------------------------------

    async def _handle_message(self, line: str) -> None:
        """Handle @listener message."""
        parts = line[1:].split(None, 1)
        if not parts:
            cprint("Usage: @listener message", Colors.DIM)
            return

        target = parts[0].lower()
        message = parts[1] if len(parts) > 1 else ""

        # Check if listener exists
        if target not in self.pump.listeners:
            cprint(f"Unknown listener: {target}", Colors.RED)
            cprint("Use /listeners to see available listeners", Colors.DIM)
            return

        # Create payload
        listener = self.pump.listeners[target]
        payload = self._create_payload(listener, message)
        if payload is None:
            cprint(f"Cannot create payload for {target}", Colors.RED)
            return

        cprint(f"[sending to {target}]", Colors.DIM)

        # Create thread and inject
        thread_id = str(uuid.uuid4())

        envelope = self.pump._wrap_in_envelope(
            payload=payload,
            from_id="console",
            to_id=target,
            thread_id=thread_id,
        )

        await self.pump.inject(envelope, thread_id=thread_id, from_id="console")

    def _create_payload(self, listener, message: str):
        """Create payload instance from message text."""
        payload_class = listener.payload_class

        # Try common field patterns
        if hasattr(payload_class, "__dataclass_fields__"):
            fields = list(payload_class.__dataclass_fields__.keys())

            if len(fields) == 1:
                return payload_class(**{fields[0]: message})
            elif "name" in fields:
                return payload_class(name=message)
            elif "text" in fields:
                return payload_class(text=message)
            elif "message" in fields:
                return payload_class(message=message)

        # Fallback
        try:
            return payload_class()
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Output (called by handlers)
    # ------------------------------------------------------------------

    def display_response(self, source: str, text: str) -> None:
        """Display a response from a handler."""
        cprint(f"[{source}] {text}", Colors.CYAN)

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _print_banner(self) -> None:
        """Print startup banner."""
        print()
        cprint("=" * 50, Colors.CYAN)
        cprint("  xml-pipeline console", Colors.CYAN)
        cprint("=" * 50, Colors.CYAN)
        print()
        cprint(f"Organism: {self.pump.config.name}", Colors.GREEN)
        cprint(f"Listeners: {len(self.pump.listeners)}", Colors.DIM)
        cprint("Type /help for commands", Colors.DIM)
        print()
