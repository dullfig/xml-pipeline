"""
tui_console.py — Split-screen TUI console using prompt_toolkit.

Features:
- Fixed Command History (Up/Down arrows)
- Robust Scrolling with snap-to-bottom and blank line spacer
- Fully implemented /monitor, /status, /listeners commands
"""

from __future__ import annotations

import asyncio
import os
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
    )
    from prompt_toolkit.layout.dimension import Dimension
    from prompt_toolkit.layout.margins import ScrollbarMargin
    from prompt_toolkit.styles import Style
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.patch_stdout import patch_stdout
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
        self.buffer = Buffer(read_only=True, name="output")

    def append(self, text: str, style: str = "output"):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self._lines.append(f"[{timestamp}] {text}")
        self._update_buffer()

    def append_raw(self, text: str, style: str = "output"):
        self._lines.append(text)
        self._update_buffer()

    def _update_buffer(self):
        if len(self._lines) > self.max_lines:
            self._lines = self._lines[-self.max_lines:]

        was_at_end = self.is_at_bottom()
        text = "\n".join(self._lines)

        if was_at_end:
            self.buffer.set_document(
                Document(text=text, cursor_position=len(text)),
                bypass_readonly=True
            )
        else:
            old_pos = self.buffer.cursor_position
            self.buffer.set_document(
                Document(text=text, cursor_position=min(old_pos, len(text))),
                bypass_readonly=True
            )

    def is_at_bottom(self) -> bool:
        """Check if output is at the very bottom (with 1-line tolerance)."""
        text_len = len(self.buffer.text)
        if text_len == 0:
            return True
        # If cursor is after the start of the last line, we're "at bottom"
        last_line_start = self.buffer.text.rfind('\n') + 1
        return self.buffer.cursor_position >= last_line_start

    def scroll_to_bottom(self):
        """Force cursor to the end of buffer."""
        self.buffer.cursor_position = len(self.buffer.text)

    def clear(self):
        self._lines.clear()
        self.buffer.set_document(Document(text=""), bypass_readonly=True)


# ============================================================================
# TUI Console
# ============================================================================

class TUIConsole:
    def __init__(self, pump: StreamPump):
        self.pump = pump
        self.output = OutputBuffer()
        self.running = False
        self.attached = True
        self.use_simple_mode = False

        CONFIG_DIR.mkdir(parents=True, exist_ok=True)

        try:
            if not PROMPT_TOOLKIT_AVAILABLE:
                raise ImportError("prompt_toolkit not available")

            # Command history setup
            if HISTORY_FILE.exists() and not os.access(HISTORY_FILE, os.W_OK):
                os.chmod(HISTORY_FILE, 0o666)

            self.input_buffer = Buffer(
                history=FileHistory(str(HISTORY_FILE)),
                multiline=False,
                accept_handler=self._accept_handler
            )

            self._build_ui()
        except (NoConsoleScreenBufferError, ImportError, Exception) as e:
            self.use_simple_mode = True
            self.app = None
            print(f"\033[2mNote: Using simple mode ({type(e).__name__})\033[0m")

    def _accept_handler(self, buffer: Buffer) -> bool:
        text = buffer.text.strip()
        if text:
            asyncio.create_task(self._process_input(text))
        return False

    def _build_ui(self):
        kb = KeyBindings()

        @kb.add("c-c")
        @kb.add("c-d")
        def _(event):
            self.running = False
            event.app.exit()

        @kb.add("c-l")
        def _(event):
            self.output.clear()

        @kb.add("up")
        def _(event):
            self.input_buffer.history_backward()

        @kb.add("down")
        def _(event):
            self.input_buffer.history_forward()

        @kb.add("pageup")
        def _(event):
            buf = self.output.buffer
            doc = buf.document
            new_row = max(0, doc.cursor_position_row - 20)
            buf.cursor_position = doc.translate_row_col_to_index(new_row, 0)
            self._invalidate()

        @kb.add("pagedown")
        def _(event):
            buf = self.output.buffer
            doc = buf.document
            lines = doc.line_count
            new_row = doc.cursor_position_row + 20

            if new_row >= lines - 1:
                self.output.scroll_to_bottom()
            else:
                buf.cursor_position = doc.translate_row_col_to_index(new_row, 0)
            self._invalidate()

        @kb.add("c-home")
        def _(event):
            self.output.buffer.cursor_position = 0
            self._invalidate()

        @kb.add("c-end")
        def _(event):
            self.output.scroll_to_bottom()
            self._invalidate()

        output_control = BufferControl(
            buffer=self.output.buffer,
            focusable=False,
            include_default_input_processors=False,
        )

        self.output_window = Window(
            content=output_control,
            wrap_lines=True,
            right_margins=[ScrollbarMargin(display_arrows=True)],
        )

        def get_spacer_height():
            return 1 if self.output.is_at_bottom() else 0

        spacer = Window(height=lambda: Dimension.exact(get_spacer_height()))

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

        input_window = Window(
            content=BufferControl(buffer=self.input_buffer),
            height=1,
        )

        from prompt_toolkit.layout import VSplit
        input_row = VSplit([
            Window(
                content=FormattedTextControl(text=lambda: FormattedText([("class:prompt", "> ")])),
                width=2,
            ),
            input_window,
        ])

        root = HSplit([
            self.output_window,
            spacer,
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
        if self.use_simple_mode:
            self._print_simple(text, style)
        else:
            self.output.append(text, style)
            self._invalidate()

    def print_raw(self, text: str, style: str = "output"):
        if self.use_simple_mode:
            self._print_simple(text, style)
        else:
            self.output.append_raw(text, style)
            self._invalidate()

    def print_system(self, text: str):
        self.print(text, "output.system")

    def print_error(self, text: str):
        self.print(text, "output.error")

    def _invalidate(self):
        if self.app:
            try:
                self.app.invalidate()
            except Exception:
                pass

    def _print_simple(self, text: str, style: str = "output"):
        colors = {
            "output.system": "\033[2m",
            "output.error": "\033[31m",
            "output.dim": "\033[2m",
            "output.greeter": "\033[32m",
            "output.shouter": "\033[33m",
            "output.response": "\033[36m",
        }
        color = colors.get(style, "")
        print(f"{color}{text}\033[0m")

    async def run(self):
        self.running = True
        if self.use_simple_mode:
            await self._run_simple()
            return

        self.print_raw(f"xml-pipeline console v3.0", "output.system")
        self.print_raw(f"Organism: {self.pump.config.name}", "output.system")
        self.print_raw(f"Type /help for commands, @listener message to chat", "output.dim")
        self.print_raw("", "output")

        try:
            async def refresh_loop():
                while self.running:
                    await asyncio.sleep(0.1)
                    if self.app and self.app.is_running:
                        self.app.invalidate()

            refresh_task = asyncio.create_task(refresh_loop())
            try:
                await self.app.run_async()
            finally:
                refresh_task.cancel()
        except Exception as e:
            print(f"Console error: {e}")
        finally:
            self.running = False

    async def _run_simple(self):
        print(f"\033[36mxml-pipeline console v3.0 (simple mode)\033[0m")
        while self.running:
            try:
                line = await asyncio.get_event_loop().run_in_executor(None, lambda: input("> "))
                if line: await self._process_input(line.strip())
            except (EOFError, KeyboardInterrupt): break
        self.running = False

    async def _process_input(self, line: str):
        if not self.use_simple_mode:
            self.print_raw(f"> {line}", "output.dim")
        if line.startswith("/"):
            await self._handle_command(line)
        elif line.startswith("@"):
            await self._handle_message(line)
        else:
            self.print("Use @listener message or /command", "output.dim")

    async def _handle_command(self, line: str):
        parts = line[1:].split(None, 1)
        cmd = parts[0].lower() if parts else ""
        args = parts[1] if len(parts) > 1 else ""

        handler = getattr(self, f"_cmd_{cmd}", None)
        if handler:
            await handler(args)
        else:
            self.print_error(f"Unknown command: /{cmd}")

    async def _cmd_help(self, args: str):
        self.print_raw("Commands:", "output.system")
        self.print_raw("  /status, /listeners, /threads, /monitor, /clear, /quit", "output.dim")

    async def _cmd_status(self, args: str):
        from agentserver.memory import get_context_buffer
        buffer = get_context_buffer()
        stats = buffer.get_stats()
        self.print_raw(f"Organism: {self.pump.config.name}", "output.system")
        self.print_raw(f"Threads: {stats['thread_count']} active, {stats['total_slots']} slots total", "output.dim")

    async def _cmd_listeners(self, args: str):
        self.print_raw("Listeners:", "output.system")
        for name, l in self.pump.listeners.items():
            tag = "[agent]" if l.is_agent else "[handler]"
            self.print_raw(f"  {name:15} {tag} {l.description}", "output.dim")

    async def _cmd_threads(self, args: str):
        from agentserver.memory import get_context_buffer
        buffer = get_context_buffer()
        for tid, ctx in buffer._threads.items():
            self.print_raw(f"  {tid[:8]}... slots: {len(ctx)}", "output.dim")

    async def _cmd_monitor(self, args: str):
        from agentserver.memory import get_context_buffer
        buffer = get_context_buffer()
        if args == "*":
            for tid, ctx in buffer._threads.items():
                self.print_raw(f"--- Thread {tid[:8]} ---", "output.system")
                for slot in list(ctx)[-3:]:
                    self.print_raw(f"  {slot.from_id} -> {slot.to_id}: {type(slot.payload).__name__}", "output.dim")
        elif args:
            matches = [t for t in buffer._threads if t.startswith(args)]
            if not matches:
                self.print_error(f"No thread matching {args}")
                return
            ctx = buffer.get_thread(matches[0])
            for slot in ctx:
                self.print_raw(f"  [{slot.from_id} -> {slot.to_id}] {type(slot.payload).__name__}", "output.dim")
        else:
            self.print("Usage: /monitor <tid> or /monitor *", "output.dim")

    async def _cmd_clear(self, args: str):
        self.output.clear()

    async def _cmd_quit(self, args: str):
        self.running = False
        if self.app: self.app.exit()

    async def _handle_message(self, line: str):
        parts = line[1:].split(None, 1)
        if not parts: return
        target, message = parts[0].lower(), (parts[1] if len(parts) > 1 else "")
        if target not in self.pump.listeners:
            self.print_error(f"Unknown listener: {target}")
            return

        listener = self.pump.listeners[target]
        payload = self._create_payload(listener, message)
        if payload is None:
            self.print_error(f"Cannot create payload for {target}")
            return

        import uuid
        thread_id = str(uuid.uuid4())
        envelope = self.pump._wrap_in_envelope(payload, "console", target, thread_id)
        await self.pump.inject(envelope, thread_id, "console")

    def _create_payload(self, listener, message: str):
        payload_class = listener.payload_class
        if hasattr(payload_class, '__dataclass_fields__'):
            fields = list(payload_class.__dataclass_fields__.keys())
            if len(fields) == 1: return payload_class(**{fields[0]: message})
            if 'message' in fields: return payload_class(message=message)
            if 'text' in fields: return payload_class(text=message)
        return None

    def on_response(self, from_id: str, payload):
        style = "output.response" if from_id == "response-handler" else "output"
        text = f"[{from_id}] {getattr(payload, 'message', payload)}"
        self.print_raw(text, style)

def create_tui_console(pump: StreamPump) -> TUIConsole:
    return TUIConsole(pump)