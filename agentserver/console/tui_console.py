"""
tui_console.py — Split-screen TUI console using prompt_toolkit.

Layout:
    ┌────────────────────────────────────────────┐
    │ [greeter] Hello! Welcome!                  │  ← Scrolling output
    │ [shouter] HELLO! WELCOME!                  │
    │ [system] Thread completed                  │
    │                                            │
    ├──────────── hello-world ───────────────────┤  ← Status bar
    │ > @greeter hi                              │  ← Input area
    └────────────────────────────────────────────┘

Features:
- Output scrolls up, input stays at bottom
- Status bar shows organism name
- Color-coded messages by source
- Command history with up/down arrows
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional

try:
    from prompt_toolkit import Application
    from prompt_toolkit.buffer import Buffer
    from prompt_toolkit.document import Document
    from prompt_toolkit.formatted_text import FormattedText, HTML
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import (
        Layout,
        HSplit,
        Window,
        FormattedTextControl,
        BufferControl,
        ScrollablePane,
    )
    from prompt_toolkit.layout.dimension import Dimension
    from prompt_toolkit.layout.margins import ScrollbarMargin
    from prompt_toolkit.styles import Style
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.output.win32 import NoConsoleScreenBufferError
    PROMPT_TOOLKIT_AVAILABLE = True
except ImportError:
    PROMPT_TOOLKIT_AVAILABLE = False
    NoConsoleScreenBufferError = Exception

if TYPE_CHECKING:
    from agentserver.message_bus.stream_pump import StreamPump


# ============================================================================
# Constants
# ============================================================================

CONFIG_DIR = Path.home() / ".xml-pipeline"
HISTORY_FILE = CONFIG_DIR / "history"


# ============================================================================
# Style
# ============================================================================

STYLE = Style.from_dict({
    "output": "#ffffff",
    "output.system": "#888888 italic",
    "output.greeter": "#00ff00",
    "output.shouter": "#ffff00",
    "output.response": "#00ffff",
    "output.error": "#ff0000",
    "output.dim": "#666666",
    "separator": "#444444",
    "separator.text": "#888888",
    "input": "#ffffff",
    "prompt": "#00ff00 bold",
})


# ============================================================================
# Output Buffer
# ============================================================================

class OutputBuffer:
    """Manages scrolling output history using a text Buffer."""

    def __init__(self, max_lines: int = 1000):
        self.max_lines = max_lines
        self._lines: List[str] = []
        # Create a read-only buffer for display
        self.buffer = Buffer(read_only=True, name="output")

    def append(self, text: str, style: str = "output"):
        """Add a line to output."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        self._lines.append(f"[{timestamp}] {text}")
        self._update_buffer()

    def append_raw(self, text: str, style: str = "output"):
        """Add without timestamp."""
        self._lines.append(text)
        self._update_buffer()

    def _update_buffer(self):
        """Update the buffer content and scroll to bottom."""
        # Trim if needed
        if len(self._lines) > self.max_lines:
            self._lines = self._lines[-self.max_lines:]

        # Update buffer text
        text = "\n".join(self._lines)
        self.buffer.set_document(
            Document(text=text, cursor_position=len(text)),
            bypass_readonly=True
        )

    def clear(self):
        """Clear output."""
        self._lines.clear()
        self.buffer.set_document(Document(text=""), bypass_readonly=True)


# ============================================================================
# TUI Console
# ============================================================================

class TUIConsole:
    """Split-screen terminal UI console."""

    def __init__(self, pump: StreamPump):
        self.pump = pump
        self.output = OutputBuffer()
        self.running = False
        self.attached = True
        self.use_simple_mode = False

        # Ensure config dir exists
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)

        # Try to build the TUI, fallback to simple mode if needed
        try:
            if not PROMPT_TOOLKIT_AVAILABLE:
                raise ImportError("prompt_toolkit not available")

            # Input buffer with history
            self.input_buffer = Buffer(
                history=FileHistory(str(HISTORY_FILE)),
                multiline=False,
            )

            # Build the UI
            self._build_ui()
        except (NoConsoleScreenBufferError, ImportError, Exception) as e:
            # Fallback to simple mode
            self.use_simple_mode = True
            self.app = None
            print(f"\033[2mNote: Using simple mode ({type(e).__name__})\033[0m")

    def _build_ui(self):
        """Build the prompt_toolkit layout."""

        # Key bindings
        kb = KeyBindings()

        @kb.add("enter")
        def handle_enter(event):
            """Handle enter key - process input."""
            text = self.input_buffer.text.strip()
            if text:
                # Schedule processing (can't await in key handler)
                asyncio.create_task(self._process_input(text))
            self.input_buffer.reset()

        @kb.add("c-c")
        def handle_ctrl_c(event):
            """Handle Ctrl+C - quit."""
            self.running = False
            event.app.exit()

        @kb.add("c-d")
        def handle_ctrl_d(event):
            """Handle Ctrl+D - quit."""
            self.running = False
            event.app.exit()

        @kb.add("c-l")
        def handle_ctrl_l(event):
            """Handle Ctrl+L - clear output."""
            self.output.clear()

        # Up/Down for command history
        @kb.add("up")
        def handle_up(event):
            """Previous command in history."""
            self.input_buffer.auto_up()

        @kb.add("down")
        def handle_down(event):
            """Next command in history."""
            self.input_buffer.auto_down()

        # Page Up/Down scroll output (no focus change needed)
        @kb.add("pageup")
        def handle_pageup(event):
            """Scroll output up a page."""
            buf = self.output.buffer
            doc = buf.document
            new_row = max(0, doc.cursor_position_row - 20)
            new_pos = doc.translate_row_col_to_index(new_row, 0)
            buf.cursor_position = new_pos

        @kb.add("pagedown")
        def handle_pagedown(event):
            """Scroll output down a page."""
            buf = self.output.buffer
            doc = buf.document
            new_row = min(doc.line_count - 1, doc.cursor_position_row + 20)
            new_pos = doc.translate_row_col_to_index(new_row, 0)
            buf.cursor_position = new_pos

        @kb.add("c-home")
        def handle_ctrl_home(event):
            """Scroll to top of output."""
            self.output.buffer.cursor_position = 0

        @kb.add("c-end")
        def handle_ctrl_end(event):
            """Scroll to bottom of output."""
            self.output.buffer.cursor_position = len(self.output.buffer.text)

        # Output uses BufferControl for scrolling (not focusable - input keeps focus)
        output_control = BufferControl(
            buffer=self.output.buffer,
            focusable=False,  # Keep focus on input, use Page Up/Down to scroll
            include_default_input_processors=False,
        )

        # Output window - takes all available space, scrolls with cursor
        self.output_window = Window(
            content=output_control,
            wrap_lines=True,
            right_margins=[ScrollbarMargin(display_arrows=True)],
        )

        # Blank line spacer above separator
        spacer = Window(height=1)

        # Separator line with status
        def get_separator():
            name = self.pump.config.name
            width = 60
            padding = "─" * ((width - len(name) - 4) // 2)
            return FormattedText([
                ("class:separator", padding),
                ("class:separator.text", f" {name} "),
                ("class:separator", padding),
            ])

        separator = Window(
            content=FormattedTextControl(text=get_separator),
            height=1,
        )

        # Input area - single window with buffer control
        input_control = BufferControl(buffer=self.input_buffer)
        input_window = Window(
            content=input_control,
            height=1,
        )

        # Prompt + input row
        from prompt_toolkit.layout import VSplit

        input_row = VSplit([
            Window(
                content=FormattedTextControl(text=lambda: FormattedText([("class:prompt", "> ")])),
                width=2,
            ),
            input_window,
        ])

        # Main layout
        root = HSplit([
            self.output_window,  # Scrollable output history
            spacer,              # Blank line above separator
            separator,
            input_row,
        ])

        self.layout = Layout(root, focused_element=input_window)

        self.app = Application(
            layout=self.layout,
            key_bindings=kb,
            style=STYLE,
            full_screen=True,
            mouse_support=True,
        )

    def print(self, text: str, style: str = "output"):
        """Print to output area."""
        if self.use_simple_mode:
            self._print_simple(text, style)
        else:
            self.output.append(text, style)
            self._invalidate()

    def print_raw(self, text: str, style: str = "output"):
        """Print without timestamp."""
        if self.use_simple_mode:
            self._print_simple(text, style)
        else:
            self.output.append_raw(text, style)
            self._invalidate()

    def _invalidate(self):
        """Invalidate the app to trigger redraw."""
        if self.app:
            try:
                self.app.invalidate()
            except Exception:
                pass

    def _print_simple(self, text: str, style: str = "output"):
        """Print in simple mode with ANSI colors."""
        colors = {
            "output.system": "\033[2m",      # Dim
            "output.error": "\033[31m",      # Red
            "output.dim": "\033[2m",         # Dim
            "output.greeter": "\033[32m",    # Green
            "output.shouter": "\033[33m",    # Yellow
            "output.response": "\033[36m",   # Cyan
        }
        color = colors.get(style, "")
        reset = "\033[0m" if color else ""
        print(f"{color}{text}{reset}")

    def print_system(self, text: str):
        """Print system message."""
        self.print(text, "output.system")

    def print_error(self, text: str):
        """Print error message."""
        self.print(text, "output.error")

    async def run(self):
        """Run the console."""
        self.running = True

        if self.use_simple_mode:
            await self._run_simple()
            return

        # Welcome message
        self.print_raw(f"xml-pipeline console v3.0", "output.system")
        self.print_raw(f"Organism: {self.pump.config.name}", "output.system")
        self.print_raw(f"Listeners: {len(self.pump.listeners)}", "output.system")
        self.print_raw(f"Type /help for commands, @listener message to chat", "output.dim")
        self.print_raw("", "output")

        try:
            # Create a background task to poll for updates
            async def refresh_loop():
                while self.running:
                    await asyncio.sleep(0.1)  # 100ms refresh rate
                    if self.app and self.app.is_running:
                        self.app.invalidate()

            # Start refresh loop as background task
            refresh_task = asyncio.create_task(refresh_loop())

            try:
                await self.app.run_async()
            finally:
                refresh_task.cancel()
                try:
                    await refresh_task
                except asyncio.CancelledError:
                    pass
        except Exception as e:
            print(f"Console error: {e}")
        finally:
            self.running = False

    async def _run_simple(self):
        """Run in simple mode (fallback for non-TUI terminals)."""
        print(f"\033[36mxml-pipeline console v3.0 (simple mode)\033[0m")
        print(f"Organism: {self.pump.config.name}")
        print(f"Listeners: {len(self.pump.listeners)}")
        print(f"\033[2mType /help for commands, @listener message to chat\033[0m")
        print()

        while self.running:
            try:
                line = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: input("> ")
                )
                line = line.strip()
                if line:
                    await self._process_input(line)
            except EOFError:
                break
            except KeyboardInterrupt:
                break

        self.running = False

    async def _process_input(self, line: str):
        """Process user input."""
        # Echo input to output (only in TUI mode, simple mode already shows it)
        if not self.use_simple_mode:
            self.print_raw(f"> {line}", "output.dim")

        if line.startswith("/"):
            await self._handle_command(line)
        elif line.startswith("@"):
            await self._handle_message(line)
        else:
            self.print("Use @listener message or /command", "output.dim")

    # ------------------------------------------------------------------
    # Command Handling
    # ------------------------------------------------------------------

    async def _handle_command(self, line: str):
        """Handle /command."""
        parts = line[1:].split(None, 1)
        cmd = parts[0].lower() if parts else ""
        args = parts[1] if len(parts) > 1 else ""

        handler = getattr(self, f"_cmd_{cmd}", None)
        if handler:
            await handler(args)
        else:
            self.print_error(f"Unknown command: /{cmd}")
            self.print("Type /help for available commands.", "output.dim")

    async def _cmd_help(self, args: str):
        """Show help."""
        self.print_raw("Commands:", "output.system")
        self.print_raw("  /help              Show this help", "output.dim")
        self.print_raw("  /status            Show organism status", "output.dim")
        self.print_raw("  /listeners         List registered listeners", "output.dim")
        self.print_raw("  /threads           List active threads", "output.dim")
        self.print_raw("  /monitor <thread>  Show messages from thread", "output.dim")
        self.print_raw("  /monitor *         Show messages from all threads", "output.dim")
        self.print_raw("  /clear             Clear output", "output.dim")
        self.print_raw("  /quit              Exit console", "output.dim")
        self.print_raw("", "output")
        self.print_raw("Messages:", "output.system")
        self.print_raw("  @listener message  Send message to listener", "output.dim")
        self.print_raw("", "output")
        self.print_raw("Shortcuts:", "output.system")
        self.print_raw("  Ctrl+C / Ctrl+D    Quit", "output.dim")
        self.print_raw("  Ctrl+L             Clear output", "output.dim")
        self.print_raw("  Up/Down            Command history", "output.dim")
        self.print_raw("  Page Up/Down       Scroll output", "output.dim")
        self.print_raw("  Ctrl+Home/End      Jump to top/bottom of output", "output.dim")

    async def _cmd_status(self, args: str):
        """Show status."""
        from agentserver.memory import get_context_buffer
        from agentserver.platform import get_prompt_registry

        buffer = get_context_buffer()
        registry = get_prompt_registry()
        stats = buffer.get_stats()
        reg_stats = registry.get_stats()

        self.print_raw(f"Organism: {self.pump.config.name}", "output.system")
        self.print_raw(f"Listeners: {len(self.pump.listeners)}", "output.dim")
        self.print_raw(f"Agents: {reg_stats['agent_count']} (prompts registered)", "output.dim")
        self.print_raw(f"Threads: {stats['thread_count']} active", "output.dim")
        self.print_raw(f"Buffer: {stats['total_slots']} slots", "output.dim")

    async def _cmd_listeners(self, args: str):
        """List listeners."""
        self.print_raw("Listeners:", "output.system")
        for name, listener in self.pump.listeners.items():
            tag = "[agent]" if listener.is_agent else "[handler]"
            self.print_raw(f"  {name:20} {tag} {listener.description}", "output.dim")

    async def _cmd_threads(self, args: str):
        """List threads."""
        from agentserver.memory import get_context_buffer

        buffer = get_context_buffer()
        stats = buffer.get_stats()

        if stats["thread_count"] == 0:
            self.print_raw("No active threads.", "output.dim")
            return

        self.print_raw(f"Active threads ({stats['thread_count']}):", "output.system")
        for thread_id in stats.get("threads", [])[:10]:
            slots = buffer.get_thread(thread_id)
            self.print_raw(f"  {thread_id[:8]}... ({len(slots)} slots)", "output.dim")

    async def _cmd_monitor(self, args: str):
        """Show messages from thread."""
        from agentserver.memory import get_context_buffer

        buffer = get_context_buffer()

        if args == "*":
            # Show all threads
            stats = buffer.get_stats()
            for thread_id in stats.get("threads", [])[:5]:
                self.print_raw(f"Thread {thread_id[:8]}...:", "output.system")
                slots = buffer.get_thread(thread_id)
                for slot in slots[-5:]:
                    payload_type = type(slot.payload).__name__
                    self.print_raw(f"  [{slot.from_id}→{slot.to_id}] {payload_type}", "output.dim")
        elif args:
            # Find thread by prefix
            stats = buffer.get_stats()
            matches = [t for t in stats.get("threads", []) if t.startswith(args)]
            if not matches:
                self.print_error(f"No thread matching: {args}")
                return

            thread_id = matches[0]
            slots = buffer.get_thread(thread_id)
            self.print_raw(f"Thread {thread_id[:8]}... ({len(slots)} slots):", "output.system")
            for slot in slots:
                payload_type = type(slot.payload).__name__
                preview = str(slot.payload)[:50]
                self.print_raw(f"  [{slot.from_id}→{slot.to_id}] {payload_type}: {preview}", "output.dim")
        else:
            self.print("Usage: /monitor <thread-prefix> or /monitor *", "output.dim")

    async def _cmd_clear(self, args: str):
        """Clear output."""
        self.output.clear()

    async def _cmd_quit(self, args: str):
        """Quit console."""
        self.print_system("Shutting down...")
        self.running = False
        if self.app:
            self.app.exit()

    # ------------------------------------------------------------------
    # Message Handling
    # ------------------------------------------------------------------

    async def _handle_message(self, line: str):
        """Handle @listener message."""
        parts = line[1:].split(None, 1)
        if not parts:
            self.print("Usage: @listener message", "output.dim")
            return

        target = parts[0].lower()
        message = parts[1] if len(parts) > 1 else ""

        if target not in self.pump.listeners:
            self.print_error(f"Unknown listener: {target}")
            self.print("Use /listeners to see available listeners.", "output.dim")
            return

        self.print(f"Sending to {target}...", "output.dim")

        # Create payload
        listener = self.pump.listeners[target]
        payload = self._create_payload(listener, message)
        if payload is None:
            self.print_error(f"Cannot create payload for {target}")
            return

        # Create thread and inject
        import uuid
        thread_id = str(uuid.uuid4())

        envelope = self.pump._wrap_in_envelope(
            payload=payload,
            from_id="console",
            to_id=target,
            thread_id=thread_id,
        )

        await self.pump.inject(envelope, thread_id=thread_id, from_id="console")

    def _create_payload(self, listener, message: str):
        """Create payload instance for listener."""
        payload_class = listener.payload_class

        if hasattr(payload_class, '__dataclass_fields__'):
            fields = payload_class.__dataclass_fields__
            field_names = list(fields.keys())

            if len(field_names) == 1:
                return payload_class(**{field_names[0]: message})
            elif 'name' in field_names:
                return payload_class(name=message)
            elif 'message' in field_names:
                return payload_class(message=message)
            elif 'text' in field_names:
                return payload_class(text=message)

        try:
            return payload_class()
        except Exception:
            return None

    # ------------------------------------------------------------------
    # External Output Hook
    # ------------------------------------------------------------------

    def on_response(self, from_id: str, payload):
        """Called when a response arrives (hook for response-handler)."""
        payload_type = type(payload).__name__

        # Determine style based on source
        if from_id == "shouter":
            style = "output.shouter"
        elif from_id == "greeter":
            style = "output.greeter"
        elif from_id == "response-handler":
            style = "output.response"
        else:
            style = "output"

        # Format the response
        if hasattr(payload, 'message'):
            text = f"[{from_id}] {payload.message}"
        else:
            text = f"[{from_id}] {payload}"

        self.print_raw(text, style)


# ============================================================================
# Factory
# ============================================================================

def create_tui_console(pump: StreamPump) -> TUIConsole:
    """Create a TUI console for the pump."""
    return TUIConsole(pump)
