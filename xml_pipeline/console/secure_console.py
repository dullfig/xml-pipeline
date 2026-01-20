"""
secure_console.py — Password-protected console for privileged operations.

The console is the sole privileged interface to the organism. Privileged
operations are only accessible via local keyboard input, never over the network.

Features:
- Password protection with Argon2id hashing
- Protected commands require password re-entry
- Attach/detach model with idle timeout
- Integration with context buffer for /monitor

Security model:
- Keyboard = Local = Trusted
- No network port for privileged operations
- Password hash stored in ~/.xml-pipeline/console.key (mode 600)
"""

from __future__ import annotations

import asyncio
import getpass
import os
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Callable, Awaitable

import yaml
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

# prompt_toolkit may not work in all terminals (e.g., Git Bash on Windows)
# We provide a fallback to simple input()
try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.patch_stdout import patch_stdout
    PROMPT_TOOLKIT_AVAILABLE = True
except ImportError:
    PROMPT_TOOLKIT_AVAILABLE = False

if TYPE_CHECKING:
    from xml_pipeline.message_bus.stream_pump import StreamPump


# ============================================================================
# Constants
# ============================================================================

CONFIG_DIR = Path.home() / ".xml-pipeline"
KEY_FILE = CONFIG_DIR / "console.key"
HISTORY_FILE = CONFIG_DIR / "history"

# Commands that require password re-entry
PROTECTED_COMMANDS = {"restart", "kill", "pause", "resume"}

# Idle timeout before auto-detach (seconds, 0 = disabled)
DEFAULT_IDLE_TIMEOUT = 30 * 60  # 30 minutes


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


def cprint(text: str, color: str = Colors.RESET):
    """Print with ANSI color."""
    try:
        print(f"{color}{text}{Colors.RESET}")
    except UnicodeEncodeError:
        print(text)


# ============================================================================
# Password Management
# ============================================================================

class PasswordManager:
    """Manages password hashing and verification."""

    def __init__(self, key_path: Path = KEY_FILE):
        self.key_path = key_path
        self.hasher = PasswordHasher()
        self._hash: Optional[str] = None

    def ensure_config_dir(self):
        """Create config directory if needed."""
        self.key_path.parent.mkdir(parents=True, exist_ok=True)

    def has_password(self) -> bool:
        """Check if password has been set."""
        return self.key_path.exists()

    def load_hash(self) -> Optional[str]:
        """Load password hash from file."""
        if not self.key_path.exists():
            return None
        try:
            with open(self.key_path) as f:
                data = yaml.safe_load(f)
                self._hash = data.get("hash")
                return self._hash
        except Exception:
            return None

    def save_hash(self, password: str) -> None:
        """Hash password and save to file."""
        self.ensure_config_dir()

        hash_value = self.hasher.hash(password)
        data = {
            "algorithm": "argon2id",
            "hash": hash_value,
            "created": datetime.now(timezone.utc).isoformat(),
        }

        with open(self.key_path, "w") as f:
            yaml.dump(data, f)

        # Set file permissions to 600 (owner read/write only)
        if sys.platform != "win32":
            os.chmod(self.key_path, stat.S_IRUSR | stat.S_IWUSR)

        self._hash = hash_value

    def verify(self, password: str) -> bool:
        """Verify password against stored hash."""
        if self._hash is None:
            self.load_hash()
        if self._hash is None:
            return False
        try:
            self.hasher.verify(self._hash, password)
            return True
        except VerifyMismatchError:
            return False


# ============================================================================
# Secure Console
# ============================================================================

class SecureConsole:
    """
    Password-protected console with privileged command support.

    The console can be in one of two states:
    - Attached: Full access, can send messages and run commands
    - Detached: Limited access, only /commands work, @messages rejected
    """

    def __init__(
        self,
        pump: StreamPump,
        idle_timeout: int = DEFAULT_IDLE_TIMEOUT,
    ):
        self.pump = pump
        self.idle_timeout = idle_timeout
        self.password_mgr = PasswordManager()

        # State
        self.authenticated = False
        self.attached = True  # Start attached
        self.running = False

        # prompt_toolkit session (may be None if fallback mode)
        self.session: Optional[PromptSession] = None
        self.use_simple_input = False  # Fallback mode flag

    # ------------------------------------------------------------------
    # Startup
    # ------------------------------------------------------------------

    def _init_prompt_session(self) -> None:
        """Initialize prompt session (with fallback)."""
        if self.session is not None:
            return  # Already initialized

        self.password_mgr.ensure_config_dir()
        if PROMPT_TOOLKIT_AVAILABLE:
            try:
                self.session = PromptSession(
                    history=FileHistory(str(HISTORY_FILE))
                )
            except Exception as e:
                cprint(f"Note: Using simple input mode ({type(e).__name__})", Colors.DIM)
                self.use_simple_input = True
        else:
            self.use_simple_input = True

    async def authenticate(self) -> bool:
        """
        Authenticate user (call before starting pump).

        Returns True if authenticated, False otherwise.
        """
        self._init_prompt_session()

        # Ensure password is set up
        if not await self._ensure_password():
            return False

        # Authenticate
        if not await self._authenticate():
            cprint("Authentication failed.", Colors.RED)
            return False

        self.authenticated = True
        return True

    async def run_command_loop(self) -> None:
        """
        Run the command loop (call after authentication).

        This shows the banner and enters the main input loop.
        """
        if not self.authenticated:
            cprint("Not authenticated. Call authenticate() first.", Colors.RED)
            return

        self.running = True
        self._print_banner()
        await self._main_loop()

    async def run(self) -> None:
        """Main console loop (combines authenticate + run_command_loop)."""
        if await self.authenticate():
            await self.run_command_loop()

    async def _ensure_password(self) -> bool:
        """Ensure password is set up (first run setup)."""
        if self.password_mgr.has_password():
            return True

        cprint("\n" + "=" * 50, Colors.CYAN)
        cprint("  First-time setup: Create console password", Colors.CYAN)
        cprint("=" * 50 + "\n", Colors.CYAN)

        cprint("This password protects privileged operations.", Colors.DIM)
        cprint("It will be required at startup and for protected commands.\n", Colors.DIM)

        # Get password with confirmation
        while True:
            password = await self._prompt_password("New password: ")
            if not password:
                cprint("Password cannot be empty.", Colors.RED)
                continue

            if len(password) < 4:
                cprint("Password must be at least 4 characters.", Colors.RED)
                continue

            confirm = await self._prompt_password("Confirm password: ")
            if password != confirm:
                cprint("Passwords do not match.", Colors.RED)
                continue

            break

        self.password_mgr.save_hash(password)
        cprint("\nPassword set successfully.\n", Colors.GREEN)
        return True

    async def _authenticate(self) -> bool:
        """Authenticate user at startup."""
        self.password_mgr.load_hash()

        for attempt in range(3):
            password = await self._prompt_password("Password: ")
            if self.password_mgr.verify(password):
                self.authenticated = True
                return True
            cprint("Incorrect password.", Colors.RED)

        return False

    async def _prompt_password(self, prompt: str) -> str:
        """Prompt for password (hidden input when possible)."""
        if self.use_simple_input:
            # Simple input mode: use visible input (getpass unreliable in some terminals)
            cprint("(password will be visible)", Colors.DIM)
            print(prompt, end="", flush=True)
            loop = asyncio.get_event_loop()
            try:
                line = await loop.run_in_executor(None, sys.stdin.readline)
                return line.strip() if line else ""
            except (EOFError, KeyboardInterrupt):
                return ""
        else:
            # Use prompt_toolkit for password input (hidden)
            try:
                session = PromptSession()
                return await session.prompt_async(prompt, is_password=True)
            except (EOFError, KeyboardInterrupt):
                return ""
            except Exception:
                # Fallback if prompt_toolkit fails mid-session
                self.use_simple_input = True
                return await self._prompt_password(prompt)

    # ------------------------------------------------------------------
    # Main Loop
    # ------------------------------------------------------------------

    async def _main_loop(self) -> None:
        """Main input loop."""
        while self.running:
            try:
                # Determine prompt based on attach state
                prompt_str = "> " if self.attached else "# "

                # Read input
                line = await self._read_input(prompt_str)

                await self._handle_input(line.strip())

            except EOFError:
                cprint("\nEOF received. Shutting down.", Colors.YELLOW)
                break
            except KeyboardInterrupt:
                continue

    async def _read_input(self, prompt: str) -> str:
        """Read a line of input (with fallback for non-TTY terminals)."""
        if self.use_simple_input:
            # Fallback: simple blocking input
            loop = asyncio.get_event_loop()
            print(prompt, end="", flush=True)
            try:
                line = await loop.run_in_executor(None, sys.stdin.readline)
                if not line:
                    raise EOFError()
                return line.strip()
            except (EOFError, KeyboardInterrupt):
                raise
        else:
            # Use prompt_toolkit with optional timeout
            try:
                with patch_stdout():
                    if self.idle_timeout > 0:
                        try:
                            return await asyncio.wait_for(
                                self.session.prompt_async(prompt),
                                timeout=self.idle_timeout
                            )
                        except asyncio.TimeoutError:
                            cprint("\nIdle timeout. Detaching console.", Colors.YELLOW)
                            self.attached = False
                            return ""
                    else:
                        return await self.session.prompt_async(prompt)
            except Exception:
                # Fall back to simple input if prompt_toolkit fails
                self.use_simple_input = True
                return await self._read_input(prompt)

    async def _handle_input(self, line: str) -> None:
        """Route input to appropriate handler."""
        if not line:
            return

        if line.startswith("/"):
            await self._handle_command(line)
        elif line.startswith("@"):
            await self._handle_message(line)
        else:
            cprint("Use @listener message or /command", Colors.DIM)

    # ------------------------------------------------------------------
    # Command Handling
    # ------------------------------------------------------------------

    async def _handle_command(self, line: str) -> None:
        """Handle /command."""
        parts = line[1:].split(None, 1)
        cmd = parts[0].lower() if parts else ""
        args = parts[1] if len(parts) > 1 else ""

        # Check if protected command
        if cmd in PROTECTED_COMMANDS:
            if not await self._verify_password():
                cprint("Password required for this command.", Colors.RED)
                return

        # Dispatch to handler
        handler = getattr(self, f"_cmd_{cmd}", None)
        if handler:
            await handler(args)
        else:
            cprint(f"Unknown command: /{cmd}", Colors.RED)
            cprint("Type /help for available commands.", Colors.DIM)

    async def _verify_password(self) -> bool:
        """Verify password for protected commands."""
        password = await self._prompt_password("Password: ")
        return self.password_mgr.verify(password)

    # ------------------------------------------------------------------
    # Message Handling
    # ------------------------------------------------------------------

    async def _handle_message(self, line: str) -> None:
        """Handle @listener message."""
        if not self.attached:
            cprint("Console detached. Use /attach first.", Colors.RED)
            return

        parts = line[1:].split(None, 1)
        if not parts:
            cprint("Usage: @listener message", Colors.DIM)
            return

        target = parts[0].lower()
        message = parts[1] if len(parts) > 1 else ""

        # Check if listener exists
        if target not in self.pump.listeners:
            cprint(f"Unknown listener: {target}", Colors.RED)
            cprint("Use /listeners to see available listeners.", Colors.DIM)
            return

        cprint(f"[sending to {target}]", Colors.DIM)

        # Create payload based on target listener
        listener = self.pump.listeners[target]
        payload = self._create_payload(listener, message)
        if payload is None:
            cprint(f"Cannot create payload for {target}", Colors.RED)
            return

        # Create thread and inject message
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
        """Create payload instance for a listener from message text."""
        payload_class = listener.payload_class

        # Try to create payload with common field patterns
        # Most payloads have a single text field like 'name', 'message', 'text', etc.
        if hasattr(payload_class, '__dataclass_fields__'):
            fields = payload_class.__dataclass_fields__
            field_names = list(fields.keys())

            if len(field_names) == 1:
                # Single field - use the message as its value
                return payload_class(**{field_names[0]: message})
            elif 'name' in field_names:
                return payload_class(name=message)
            elif 'message' in field_names:
                return payload_class(message=message)
            elif 'text' in field_names:
                return payload_class(text=message)

        # Fallback: try with no args
        try:
            return payload_class()
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Commands: Informational
    # ------------------------------------------------------------------

    async def _cmd_help(self, args: str) -> None:
        """Show available commands."""
        cprint("\nCommands:", Colors.CYAN)
        cprint("  /help              Show this help", Colors.DIM)
        cprint("  /status            Show organism status", Colors.DIM)
        cprint("  /listeners         List registered listeners", Colors.DIM)
        cprint("  /threads           List active threads", Colors.DIM)
        cprint("  /buffer <thread>   Inspect thread's context buffer", Colors.DIM)
        cprint("  /monitor <thread>  Show recent messages from thread", Colors.DIM)
        cprint("  /monitor *         Show recent messages from all threads", Colors.DIM)
        cprint("")
        cprint("Configuration:", Colors.CYAN)
        cprint("  /config            Show current config", Colors.DIM)
        cprint("  /config -e         Edit organism.yaml", Colors.DIM)
        cprint("  /config @name      Edit listener config", Colors.DIM)
        cprint("  /config --list     List listener configs", Colors.DIM)
        cprint("")
        cprint("Protected (require password):", Colors.YELLOW)
        cprint("  /restart           Restart the pipeline", Colors.DIM)
        cprint("  /kill <thread>     Terminate a thread", Colors.DIM)
        cprint("  /pause             Pause message processing", Colors.DIM)
        cprint("  /resume            Resume message processing", Colors.DIM)
        cprint("")
        cprint("Session:", Colors.CYAN)
        cprint("  /attach            Attach console (enable @messages)", Colors.DIM)
        cprint("  /detach            Detach console (organism keeps running)", Colors.DIM)
        cprint("  /passwd            Change console password", Colors.DIM)
        cprint("  /quit              Graceful shutdown", Colors.DIM)
        cprint("")

    async def _cmd_status(self, args: str) -> None:
        """Show organism status."""
        from xml_pipeline.memory import get_context_buffer
        from xml_pipeline.message_bus.thread_registry import get_registry

        buffer = get_context_buffer()
        registry = get_registry()
        stats = buffer.get_stats()

        cprint(f"\nOrganism: {self.pump.config.name}", Colors.CYAN)
        cprint(f"Status: {'attached' if self.attached else 'detached'}",
               Colors.GREEN if self.attached else Colors.YELLOW)
        cprint(f"Listeners: {len(self.pump.listeners)}", Colors.DIM)
        cprint(f"Threads: {stats['thread_count']} active", Colors.DIM)
        cprint(f"Buffer: {stats['total_slots']} slots across threads", Colors.DIM)
        cprint("")

    async def _cmd_listeners(self, args: str) -> None:
        """List registered listeners."""
        cprint("\nRegistered listeners:", Colors.CYAN)
        for name, listener in self.pump.listeners.items():
            agent_tag = "[agent] " if listener.is_agent else ""
            cprint(f"  {name:20} {agent_tag}{listener.description}", Colors.DIM)
        cprint("")

    async def _cmd_threads(self, args: str) -> None:
        """List active threads."""
        from xml_pipeline.memory import get_context_buffer

        buffer = get_context_buffer()
        stats = buffer.get_stats()

        if stats["thread_count"] == 0:
            cprint("\nNo active threads.", Colors.DIM)
            return

        cprint(f"\nActive threads ({stats['thread_count']}):", Colors.CYAN)

        # Access internal threads dict (not ideal but works for now)
        for thread_id, ctx in buffer._threads.items():
            slot_count = len(ctx)
            age = datetime.now(timezone.utc) - ctx._created_at
            age_str = str(age).split(".")[0]  # Remove microseconds

            # Get last sender/receiver
            if slot_count > 0:
                last = ctx[-1]
                flow = f"{last.from_id} -> {last.to_id}"
            else:
                flow = "(empty)"

            cprint(f"  {thread_id[:12]}...  slots={slot_count:3}  age={age_str}  {flow}", Colors.DIM)
        cprint("")

    async def _cmd_buffer(self, args: str) -> None:
        """Inspect a thread's context buffer."""
        if not args:
            cprint("Usage: /buffer <thread-id>", Colors.DIM)
            return

        from xml_pipeline.memory import get_context_buffer
        buffer = get_context_buffer()

        # Find thread by prefix
        thread_id = None
        for tid in buffer._threads.keys():
            if tid.startswith(args):
                thread_id = tid
                break

        if not thread_id:
            cprint(f"Thread not found: {args}", Colors.RED)
            return

        ctx = buffer.get_thread(thread_id)
        cprint(f"\nThread: {thread_id}", Colors.CYAN)
        cprint(f"Slots: {len(ctx)}", Colors.DIM)
        cprint("-" * 60, Colors.DIM)

        for slot in ctx:
            payload_type = type(slot.payload).__name__
            cprint(f"[{slot.index}] {slot.from_id} -> {slot.to_id}: {payload_type}", Colors.DIM)
            # Show first 100 chars of payload repr
            payload_repr = repr(slot.payload)[:100]
            cprint(f"    {payload_repr}", Colors.DIM)
        cprint("")

    async def _cmd_monitor(self, args: str) -> None:
        """Show recent messages from a thread's context buffer."""
        if not args:
            cprint("Usage: /monitor <thread-id>", Colors.DIM)
            cprint("       /monitor *  (show all threads)", Colors.DIM)
            return

        from xml_pipeline.memory import get_context_buffer
        buffer = get_context_buffer()

        # Find thread by prefix (or * for all)
        monitor_all = args.strip() == "*"
        thread_id = None

        if not monitor_all:
            for tid in buffer._threads.keys():
                if tid.startswith(args):
                    thread_id = tid
                    break

            if not thread_id:
                cprint(f"Thread not found: {args}", Colors.RED)
                return

        # Show header
        if monitor_all:
            cprint("\nAll threads:", Colors.CYAN)
        else:
            cprint(f"\nThread {thread_id[:12]}...:", Colors.CYAN)
        cprint("-" * 60, Colors.DIM)

        # Show messages
        if monitor_all:
            for tid, ctx in buffer._threads.items():
                if len(ctx) > 0:
                    cprint(f"\n[{tid[:12]}...] ({len(ctx)} messages)", Colors.YELLOW)
                    # Show last 5 messages per thread
                    for slot in ctx.get_slice(-5):
                        self._print_monitor_slot(tid, slot)
        else:
            ctx = buffer.get_thread(thread_id)
            # Show all messages (up to 20)
            slots = ctx.get_slice(-20)
            for slot in slots:
                self._print_monitor_slot(thread_id, slot)
            if len(ctx) > 20:
                cprint(f"  ... ({len(ctx) - 20} earlier messages)", Colors.DIM)

        cprint("")

    def _print_monitor_slot(self, thread_id: str, slot) -> None:
        """Print a single slot in monitor format."""
        payload_type = type(slot.payload).__name__
        tid_short = thread_id[:8]
        timestamp = slot.metadata.timestamp.split("T")[1][:8] if "T" in slot.metadata.timestamp else ""

        # Color based on direction
        if slot.from_id == "console":
            color = Colors.GREEN
        elif "response" in slot.to_id.lower() or "console" in slot.to_id.lower():
            color = Colors.CYAN
        else:
            color = Colors.DIM

        # Format: [time] thread: from -> to: Type
        cprint(f"[{timestamp}] {tid_short}: {slot.from_id} -> {slot.to_id}: {payload_type}", color)

        # Show payload content (abbreviated)
        payload_str = repr(slot.payload)
        if len(payload_str) > 80:
            payload_str = payload_str[:77] + "..."
        cprint(f"         {payload_str}", Colors.DIM)

    async def _cmd_config(self, args: str) -> None:
        """
        Edit configuration files.

        /config           - Edit organism.yaml
        /config @name     - Edit listener config (e.g., /config @greeter)
        /config --list    - List available listener configs
        /config --show    - Show current config (read-only)
        """
        args = args.strip() if args else ""

        if args == "--list":
            await self._config_list()
        elif args == "--show" or args == "":
            await self._config_show()
        elif args.startswith("@"):
            listener_name = args[1:].strip()
            if listener_name:
                await self._config_edit_listener(listener_name)
            else:
                cprint("Usage: /config @listener_name", Colors.RED)
        elif args == "--edit" or args == "-e":
            await self._config_edit_organism()
        else:
            cprint(f"Unknown option: {args}", Colors.RED)
            cprint("Usage:", Colors.DIM)
            cprint("  /config           Show current config", Colors.DIM)
            cprint("  /config -e        Edit organism.yaml", Colors.DIM)
            cprint("  /config @name     Edit listener config", Colors.DIM)
            cprint("  /config --list    List listener configs", Colors.DIM)

    async def _config_show(self) -> None:
        """Show current configuration (read-only)."""
        cprint(f"\nOrganism: {self.pump.config.name}", Colors.CYAN)
        cprint(f"Port: {self.pump.config.port}", Colors.DIM)
        cprint(f"Thread scheduling: {self.pump.config.thread_scheduling}", Colors.DIM)
        cprint(f"Max concurrent pipelines: {self.pump.config.max_concurrent_pipelines}", Colors.DIM)
        cprint(f"Max concurrent handlers: {self.pump.config.max_concurrent_handlers}", Colors.DIM)
        cprint(f"Max concurrent per agent: {self.pump.config.max_concurrent_per_agent}", Colors.DIM)
        cprint("\nUse /config -e to edit organism.yaml", Colors.DIM)
        cprint("Use /config @listener to edit a listener config", Colors.DIM)
        cprint("")

    async def _config_list(self) -> None:
        """List available listener configs."""
        from xml_pipeline.config import get_listener_config_store

        store = get_listener_config_store()
        listeners = store.list_listeners()

        cprint("\nListener configurations:", Colors.CYAN)
        cprint(f"Directory: {store.listeners_dir}", Colors.DIM)
        cprint("")

        if not listeners:
            cprint("  No listener configs found.", Colors.DIM)
            cprint("  Use /config @name to create one.", Colors.DIM)
        else:
            for name in sorted(listeners):
                config = store.get(name)
                agent_tag = "[agent]" if config.agent else "[tool]" if config.tool else ""
                cprint(f"  @{name:20} {agent_tag} {config.description or ''}", Colors.DIM)

        # Also show registered listeners without config files
        unconfigured = [
            name for name in self.pump.listeners.keys()
            if name not in listeners
        ]
        if unconfigured:
            cprint("\nRegistered listeners without config files:", Colors.YELLOW)
            for name in sorted(unconfigured):
                listener = self.pump.listeners[name]
                agent_tag = "[agent]" if listener.is_agent else ""
                cprint(f"  @{name:20} {agent_tag} {listener.description}", Colors.DIM)

        cprint("")

    async def _config_edit_organism(self) -> None:
        """Edit organism.yaml in the full-screen editor."""
        from xml_pipeline.console.editor import edit_text_async
        from xml_pipeline.config.schema import ensure_schemas
        from xml_pipeline.config.split_loader import (
            get_organism_yaml_path,
            load_organism_yaml_content,
            save_organism_yaml_content,
        )

        # Ensure schemas are written for LSP
        try:
            ensure_schemas()
        except Exception as e:
            cprint(f"Warning: Could not write schemas: {e}", Colors.YELLOW)

        # Find organism.yaml
        config_path = get_organism_yaml_path()
        if config_path is None:
            cprint("No organism.yaml found.", Colors.RED)
            cprint("Searched in:", Colors.DIM)
            cprint("  ~/.xml-pipeline/organism.yaml", Colors.DIM)
            cprint("  ./organism.yaml", Colors.DIM)
            cprint("  ./config/organism.yaml", Colors.DIM)
            return

        # Load content
        try:
            content = load_organism_yaml_content(config_path)
        except Exception as e:
            cprint(f"Failed to load config: {e}", Colors.RED)
            return

        # Edit
        cprint(f"Editing: {config_path}", Colors.CYAN)
        cprint("Press Ctrl+S to save, Ctrl+Q to cancel", Colors.DIM)
        cprint("")

        edited_text, saved = await edit_text_async(
            content,
            title=f"organism.yaml ({config_path.name})",
            schema_type="organism",
        )

        if saved and edited_text is not None:
            try:
                save_organism_yaml_content(config_path, edited_text)
                cprint("Configuration saved.", Colors.GREEN)
                cprint("Note: Restart required for changes to take effect.", Colors.YELLOW)
            except yaml.YAMLError as e:
                cprint(f"Invalid YAML: {e}", Colors.RED)
            except Exception as e:
                cprint(f"Failed to save: {e}", Colors.RED)
        else:
            cprint("Edit cancelled.", Colors.DIM)

    async def _config_edit_listener(self, name: str) -> None:
        """Edit a listener config in the full-screen editor."""
        from xml_pipeline.config import get_listener_config_store
        from xml_pipeline.console.editor import edit_text_async
        from xml_pipeline.config.schema import ensure_schemas

        # Ensure schemas are written for LSP
        try:
            ensure_schemas()
        except Exception as e:
            cprint(f"Warning: Could not write schemas: {e}", Colors.YELLOW)

        store = get_listener_config_store()

        # Load or create content
        if store.exists(name):
            content = store.load_yaml(name)
            cprint(f"Editing: {store.path_for(name)}", Colors.CYAN)
        else:
            # Check if it's a registered listener
            if name in self.pump.listeners:
                cprint(f"Creating new config for registered listener: {name}", Colors.CYAN)
            else:
                cprint(f"Creating new config for: {name}", Colors.CYAN)
            content = store._default_template(name)

        cprint("Press Ctrl+S to save, Ctrl+Q to cancel", Colors.DIM)
        cprint("")

        # Edit
        edited_text, saved = await edit_text_async(
            content,
            title=f"{name}.yaml",
            schema_type="listener",
        )

        if saved and edited_text is not None:
            try:
                path = store.save_yaml(name, edited_text)
                cprint(f"Saved: {path}", Colors.GREEN)
                cprint("Note: Restart required for changes to take effect.", Colors.YELLOW)
            except yaml.YAMLError as e:
                cprint(f"Invalid YAML: {e}", Colors.RED)
            except Exception as e:
                cprint(f"Failed to save: {e}", Colors.RED)
        else:
            cprint("Edit cancelled.", Colors.DIM)

    # ------------------------------------------------------------------
    # Commands: Protected
    # ------------------------------------------------------------------

    async def _cmd_restart(self, args: str) -> None:
        """Restart the pipeline."""
        cprint("Restarting pipeline...", Colors.YELLOW)
        await self.pump.shutdown()

        # Re-bootstrap
        from xml_pipeline.message_bus.stream_pump import bootstrap
        self.pump = await bootstrap()

        # Start pump in background
        asyncio.create_task(self.pump.run())
        cprint("Pipeline restarted.", Colors.GREEN)

    async def _cmd_kill(self, args: str) -> None:
        """Terminate a thread."""
        if not args:
            cprint("Usage: /kill <thread-id>", Colors.DIM)
            return

        from xml_pipeline.memory import get_context_buffer
        buffer = get_context_buffer()

        # Find thread by prefix
        thread_id = None
        for tid in buffer._threads.keys():
            if tid.startswith(args):
                thread_id = tid
                break

        if not thread_id:
            cprint(f"Thread not found: {args}", Colors.RED)
            return

        buffer.delete_thread(thread_id)
        cprint(f"Thread {thread_id[:12]}... terminated.", Colors.YELLOW)

    async def _cmd_pause(self, args: str) -> None:
        """Pause message processing."""
        cprint("Pause not yet implemented.", Colors.YELLOW)
        # TODO: Implement pump pause

    async def _cmd_resume(self, args: str) -> None:
        """Resume message processing."""
        cprint("Resume not yet implemented.", Colors.YELLOW)
        # TODO: Implement pump resume

    # ------------------------------------------------------------------
    # Commands: Session
    # ------------------------------------------------------------------

    async def _cmd_attach(self, args: str) -> None:
        """Attach console."""
        if self.attached:
            cprint("Already attached.", Colors.DIM)
            return

        if not await self._verify_password():
            cprint("Password required to attach.", Colors.RED)
            return

        self.attached = True
        cprint("Console attached.", Colors.GREEN)

    async def _cmd_detach(self, args: str) -> None:
        """Detach console."""
        if not self.attached:
            cprint("Already detached.", Colors.DIM)
            return

        self.attached = False
        cprint("Console detached. Organism continues running.", Colors.YELLOW)
        cprint("Use /attach to re-attach.", Colors.DIM)

    async def _cmd_passwd(self, args: str) -> None:
        """Change console password."""
        # Verify current password
        current = await self._prompt_password("Current password: ")
        if not self.password_mgr.verify(current):
            cprint("Incorrect password.", Colors.RED)
            return

        # Get new password
        while True:
            new_pass = await self._prompt_password("New password: ")
            if not new_pass or len(new_pass) < 4:
                cprint("Password must be at least 4 characters.", Colors.RED)
                continue

            confirm = await self._prompt_password("Confirm new password: ")
            if new_pass != confirm:
                cprint("Passwords do not match.", Colors.RED)
                continue

            break

        self.password_mgr.save_hash(new_pass)
        cprint("Password changed successfully.", Colors.GREEN)

    async def _cmd_quit(self, args: str) -> None:
        """Graceful shutdown."""
        cprint("Shutting down...", Colors.YELLOW)
        self.running = False
        await self.pump.shutdown()

    # ------------------------------------------------------------------
    # UI Helpers
    # ------------------------------------------------------------------

    def _print_banner(self) -> None:
        """Print startup banner."""
        print()
        cprint("+" + "=" * 44 + "+", Colors.CYAN)
        cprint("|" + " " * 8 + "xml-pipeline console v3.0" + " " * 9 + "|", Colors.CYAN)
        cprint("+" + "=" * 44 + "+", Colors.CYAN)
        print()
        cprint(f"Organism '{self.pump.config.name}' ready.", Colors.GREEN)
        cprint(f"{len(self.pump.listeners)} listeners registered.", Colors.DIM)
        cprint("Type /help for commands.", Colors.DIM)
        print()
