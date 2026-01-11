"""
console.py — Human console listener for interactive input.

A message-driven console interface for the organism. The console is a regular
handler in the message flow:

  boot → console (await input) → console-router → ... → console (display + await)

The console handler:
1. Receives ConsolePrompt (may contain output to display)
2. Displays any output
3. Awaits keyboard input
4. Returns HandlerResponse with user's message → routes to console-router
5. Returns None on EOF/quit → disconnected

Commands:
  @listener message    Send message to specific listener
  /status              Show organism status
  /listeners           List registered listeners
  /quit                Shutdown organism

Example:
  > @greeter Hello World
  [shouter] HELLO WORLD!
"""

import asyncio
import sys
from dataclasses import dataclass
from typing import Optional

from third_party.xmlable import xmlify
from agentserver.message_bus.message_state import HandlerMetadata, HandlerResponse


# ============================================================================
# Payload Classes
# ============================================================================

@xmlify
@dataclass
class ConsolePrompt:
    """
    Prompt message to the console.

    Contains optional output to display before prompting for input.
    Sent by boot (initial prompt) and response-handler (after responses).
    """
    output: str = ""  # Text to display (may contain newlines)
    source: str = ""  # Who sent this (for coloring)
    show_banner: bool = False  # Show startup banner


@xmlify
@dataclass
class ConsoleInput:
    """User input from console, routed to console-router."""
    text: str = ""
    target: str = ""  # Listener to send to


# ============================================================================
# ANSI Colors
# ============================================================================

class Colors:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"


def print_colored(text: str, color: str = Colors.RESET):
    """Print with ANSI color."""
    try:
        print(f"{color}{text}{Colors.RESET}")
    except UnicodeEncodeError:
        # Fallback for Windows console
        print(text)


def print_banner():
    """Print startup banner."""
    print()
    print_colored("=" * 46, Colors.CYAN)
    print_colored("         xml-pipeline console v0.1          ", Colors.CYAN)
    print_colored("=" * 46, Colors.CYAN)
    print()
    print_colored("Commands:", Colors.DIM)
    print_colored("  @listener message  - Send to listener", Colors.DIM)
    print_colored("  /status            - Organism status", Colors.DIM)
    print_colored("  /listeners         - List listeners", Colors.DIM)
    print_colored("  /quit              - Shutdown", Colors.DIM)
    print()


# ============================================================================
# Console State (minimal, for commands like /listeners)
# ============================================================================

# Reference to pump for introspection commands
_pump_ref = None


def set_pump_ref(pump):
    """Set pump reference for introspection."""
    global _pump_ref
    _pump_ref = pump


# ============================================================================
# Input Helpers
# ============================================================================

async def read_input() -> Optional[str]:
    """Async readline from stdin. Returns None on EOF."""
    loop = asyncio.get_event_loop()
    try:
        line = await loop.run_in_executor(None, sys.stdin.readline)
        if not line:  # EOF
            return None
        return line.strip()
    except (EOFError, KeyboardInterrupt):
        return None


def parse_input(line: str) -> tuple[str, str, Optional[str]]:
    """
    Parse input line.

    Returns: (input_type, content, target)
      - ("message", "hello", "greeter") for @greeter hello
      - ("command", "status", None) for /status
      - ("quit", "", None) for /quit
      - ("empty", "", None) for blank line
    """
    if not line:
        return ("empty", "", None)

    if line.startswith("/"):
        parts = line[1:].split(None, 1)
        cmd = parts[0].lower() if parts else ""
        arg = parts[1] if len(parts) > 1 else ""
        if cmd in ("quit", "exit"):
            return ("quit", "", None)
        return ("command", cmd, arg if arg else None)

    if line.startswith("@"):
        parts = line[1:].split(None, 1)
        if len(parts) >= 1:
            target = parts[0]
            message = parts[1] if len(parts) > 1 else ""
            return ("message", message, target)

    return ("empty", "", None)


def handle_local_command(cmd: str, arg: Optional[str], metadata: HandlerMetadata) -> bool:
    """
    Handle local / commands that don't need to go through the pump.

    Returns True if command was handled, False otherwise.
    """
    if cmd == "status":
        print_colored("Status: running", Colors.GREEN)
        print_colored(f"Thread: {metadata.thread_id[:8]}...", Colors.DIM)
        return True

    elif cmd == "listeners":
        print_colored("Registered listeners:", Colors.CYAN)
        if _pump_ref and hasattr(_pump_ref, 'listeners'):
            for name, listener in _pump_ref.listeners.items():
                desc = getattr(listener, 'description', 'No description')
                print_colored(f"  - {name}: {desc}", Colors.DIM)
        else:
            print_colored("  (pump reference not available)", Colors.DIM)
        return True

    elif cmd == "help":
        print_colored("Commands:", Colors.CYAN)
        print_colored("  @listener message  - Send to listener", Colors.DIM)
        print_colored("  /status            - Organism status", Colors.DIM)
        print_colored("  /listeners         - List listeners", Colors.DIM)
        print_colored("  /quit              - Shutdown", Colors.DIM)
        return True

    else:
        print_colored(f"Unknown command: /{cmd}", Colors.RED)
        return True


# ============================================================================
# Console Handler
# ============================================================================

async def handle_console_prompt(
    payload: ConsolePrompt,
    metadata: HandlerMetadata
) -> HandlerResponse | None:
    """
    Main console handler — displays output, awaits input, returns message.

    This is called:
    1. On boot (show_banner=True, no output)
    2. After each response (output_lines contains response text)

    Returns:
    - HandlerResponse with ConsoleInput → routes to console-router
    - None → console disconnected (EOF or /quit)
    """
    # Show banner on first prompt
    if payload.show_banner:
        print_banner()

    # Display any output
    if payload.output:
        print()
        for line in payload.output.split("\n"):
            if payload.source:
                print_colored(f"[{payload.source}] {line}", Colors.CYAN)
            else:
                print_colored(line, Colors.CYAN)

    # Input loop - keep prompting until we get a valid message or quit
    while True:
        # Print prompt
        print(f"{Colors.GREEN}>{Colors.RESET} ", end="", flush=True)

        # Await input
        line = await read_input()

        # EOF - disconnect
        if line is None:
            print()
            print_colored("EOF - disconnecting", Colors.YELLOW)
            return None

        # Parse input
        input_type, content, target = parse_input(line)

        if input_type == "quit":
            print_colored("Shutting down...", Colors.YELLOW)
            return None

        elif input_type == "empty":
            continue  # Prompt again

        elif input_type == "command":
            # Handle local command and prompt again
            handle_local_command(content, target, metadata)
            continue

        elif input_type == "message":
            if not target:
                print_colored("No target. Use @listener message", Colors.RED)
                continue

            # Return message to console-router
            print_colored(f"[sending to {target}]", Colors.DIM)
            return HandlerResponse(
                payload=ConsoleInput(text=content, target=target),
                to="console-router",
            )


# ============================================================================
# Console Router Handler
# ============================================================================

@xmlify
@dataclass
class Greeting:
    """Greeting payload for greeter listener."""
    name: str = ""


async def handle_console_input(
    payload: ConsoleInput,
    metadata: HandlerMetadata
) -> HandlerResponse | None:
    """
    Route console input to the appropriate listener.

    Translates ConsoleInput into the target's expected payload format.
    """
    target = payload.target.lower()
    text = payload.text

    # Route to appropriate listener with correct payload
    if target == "greeter":
        return HandlerResponse(
            payload=Greeting(name=text),
            to="greeter",
        )

    # Generic routing - try to send raw text
    # This would need expansion for other listener types
    print_colored(f"Unknown target: {target}", Colors.RED)
    return HandlerResponse(
        payload=ConsolePrompt(
            output=f"Unknown target: {target}",
            source="console-router",
        ),
        to="console",
    )


# ============================================================================
# Response Handler
# ============================================================================

@xmlify
@dataclass
class ShoutedResponse:
    """Response from shouter."""
    message: str = ""


async def handle_shouted_response(
    payload: ShoutedResponse,
    metadata: HandlerMetadata
) -> HandlerResponse:
    """
    Handle responses and forward to console for display.

    Takes the final response and wraps it in ConsolePrompt.
    """
    return HandlerResponse(
        payload=ConsolePrompt(
            output=payload.message,
            source="shouter",
        ),
        to="console",
    )
