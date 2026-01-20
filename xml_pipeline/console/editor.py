"""
Full-screen text editor using prompt_toolkit.

Provides a vim-like editing experience for configuration files.
Supports optional LSP integration for YAML files.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from xml_pipeline.console.lsp import YAMLLSPClient, ASLSClient
    from typing import Union
    LSPClientType = Union[YAMLLSPClient, ASLSClient]

logger = logging.getLogger(__name__)

try:
    from prompt_toolkit import Application
    from prompt_toolkit.buffer import Buffer
    from prompt_toolkit.layout import Layout, HSplit, VSplit
    from prompt_toolkit.layout.containers import Window, ConditionalContainer, Float, FloatContainer
    from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
    from prompt_toolkit.layout.menus import CompletionsMenu
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.filters import Condition
    from prompt_toolkit.styles import Style
    from prompt_toolkit.lexers import PygmentsLexer
    from prompt_toolkit.completion import Completer, Completion
    from prompt_toolkit.document import Document
    PROMPT_TOOLKIT_AVAILABLE = True
except ImportError:
    PROMPT_TOOLKIT_AVAILABLE = False

try:
    from pygments.lexers.data import YamlLexer
    from pygments.lexers.javascript import TypeScriptLexer
    PYGMENTS_AVAILABLE = True
except ImportError:
    PYGMENTS_AVAILABLE = False


# Supported syntax types and their lexers
SYNTAX_LEXERS = {
    "yaml": "YamlLexer",
    "typescript": "TypeScriptLexer",
    "assemblyscript": "TypeScriptLexer",  # AS uses TS syntax
    "ts": "TypeScriptLexer",
    "as": "TypeScriptLexer",
}


def get_lexer_for_syntax(syntax: str) -> Optional[object]:
    """
    Get a Pygments lexer for the given syntax type.

    Args:
        syntax: Syntax name ("yaml", "typescript", "ts", "as", "assemblyscript")

    Returns:
        PygmentsLexer instance or None
    """
    if not PYGMENTS_AVAILABLE:
        return None

    syntax_lower = syntax.lower()

    if syntax_lower in ("yaml", "yml"):
        return PygmentsLexer(YamlLexer)
    elif syntax_lower in ("typescript", "ts", "assemblyscript", "as"):
        return PygmentsLexer(TypeScriptLexer)
    else:
        return None


def detect_syntax_from_path(path: str | Path) -> str:
    """
    Detect syntax type from file extension.

    Returns:
        Syntax name for use with get_lexer_for_syntax()
    """
    ext = Path(path).suffix.lower()

    extension_map = {
        ".yaml": "yaml",
        ".yml": "yaml",
        ".ts": "typescript",
        ".as": "assemblyscript",
    }

    return extension_map.get(ext, "text")


def edit_text(
    initial_text: str,
    title: str = "Editor",
    syntax: str = "yaml",
) -> Tuple[Optional[str], bool]:
    """
    Open full-screen editor for text.

    Args:
        initial_text: Text to edit
        title: Title shown in header
        syntax: Syntax highlighting ("yaml", "typescript", "ts", "as", "text")

    Returns:
        (edited_text, saved) - edited_text is None if cancelled
    """
    if not PROMPT_TOOLKIT_AVAILABLE:
        print("Error: prompt_toolkit not installed")
        return None, False

    # State
    result = {"text": None, "saved": False}

    # Create buffer with initial text
    buffer = Buffer(
        multiline=True,
        name="editor",
    )
    buffer.text = initial_text

    # Key bindings
    kb = KeyBindings()

    @kb.add("c-s")  # Ctrl+S to save
    def save(event):
        result["text"] = buffer.text
        result["saved"] = True
        event.app.exit()

    @kb.add("c-q")  # Ctrl+Q to quit without saving
    def quit_nosave(event):
        result["text"] = None
        result["saved"] = False
        event.app.exit()

    @kb.add("escape")  # Escape to quit
    def escape(event):
        result["text"] = None
        result["saved"] = False
        event.app.exit()

    # Syntax highlighting
    lexer = get_lexer_for_syntax(syntax)

    # Layout
    header = Window(
        height=1,
        content=FormattedTextControl(
            lambda: [
                ("class:header", f" {title} "),
                ("class:header.key", " Ctrl+S"),
                ("class:header", "=Save "),
                ("class:header.key", " Ctrl+Q"),
                ("class:header", "=Quit "),
            ]
        ),
        style="class:header",
    )

    editor_window = Window(
        content=BufferControl(
            buffer=buffer,
            lexer=lexer,
        ),
    )

    # Status bar showing cursor position
    def get_status():
        row = buffer.document.cursor_position_row + 1
        col = buffer.document.cursor_position_col + 1
        lines = len(buffer.text.split("\n"))
        return [
            ("class:status", f" Line {row}/{lines}, Col {col} "),
        ]

    status_bar = Window(
        height=1,
        content=FormattedTextControl(get_status),
        style="class:status",
    )

    layout = Layout(
        HSplit([
            header,
            editor_window,
            status_bar,
        ])
    )

    # Styles
    style = Style.from_dict({
        "header": "bg:#005f87 #ffffff",
        "header.key": "bg:#005f87 #ffff00 bold",
        "status": "bg:#444444 #ffffff",
    })

    # Create and run application
    app = Application(
        layout=layout,
        key_bindings=kb,
        style=style,
        full_screen=True,
        mouse_support=True,
    )

    app.run()

    return result["text"], result["saved"]


def edit_file(filepath: str, title: Optional[str] = None) -> bool:
    """
    Edit a file in the full-screen editor.

    Args:
        filepath: Path to file
        title: Optional title (defaults to filename)

    Returns:
        True if saved, False if cancelled
    """
    from pathlib import Path

    path = Path(filepath)
    title = title or path.name

    # Load existing content or empty
    if path.exists():
        initial_text = path.read_text()
    else:
        initial_text = ""

    # Edit
    edited_text, saved = edit_text(initial_text, title=title, syntax="yaml")

    # Save if requested
    if saved and edited_text is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(edited_text)
        return True

    return False


# Fallback: use system editor via subprocess
def edit_with_system_editor(filepath: str) -> bool:
    """
    Edit file using system's default editor ($EDITOR or fallback).

    Returns True if file was modified.
    """
    import os
    import subprocess
    from pathlib import Path

    path = Path(filepath)

    # Get editor from environment
    editor = os.environ.get("EDITOR", os.environ.get("VISUAL", ""))

    if not editor:
        # Fallback based on platform
        import platform
        if platform.system() == "Windows":
            editor = "notepad"
        else:
            editor = "nano"  # Most likely available

    # Get modification time before edit
    mtime_before = path.stat().st_mtime if path.exists() else None

    # Open editor
    try:
        subprocess.run([editor, str(path)], check=True)
    except subprocess.CalledProcessError:
        return False
    except FileNotFoundError:
        print(f"Editor not found: {editor}")
        return False

    # Check if modified
    if path.exists():
        mtime_after = path.stat().st_mtime
        return mtime_before is None or mtime_after > mtime_before

    return False


# =============================================================================
# LSP-Enhanced Editor
# =============================================================================


class LSPEditor:
    """
    Full-screen editor with optional LSP integration.

    Provides:
    - Syntax highlighting (YAML, TypeScript/AssemblyScript via Pygments)
    - Autocompletion (LSP when available)
    - Inline diagnostics (LSP when available)
    - Hover documentation on F1 (LSP when available)

    Usage:
        # YAML config editing
        editor = LSPEditor(schema_type="listener")
        edited_text, saved = await editor.edit(initial_text, title="greeter.yaml")

        # AssemblyScript listener editing
        editor = LSPEditor(syntax="assemblyscript")
        edited_text, saved = await editor.edit(source_code, title="handler.ts")
    """

    def __init__(
        self,
        schema_type: Optional[str] = None,
        schema_uri: Optional[str] = None,
        syntax: str = "yaml",
    ):
        """
        Initialize the LSP editor.

        Args:
            schema_type: Schema type ("organism" or "listener") for YAML modeline
            schema_uri: Explicit schema URI to use
            syntax: Syntax highlighting ("yaml", "typescript", "assemblyscript", "ts", "as")
        """
        self.schema_type = schema_type
        self.schema_uri = schema_uri
        self.syntax = syntax
        self._lsp_client: Optional["LSPClientType"] = None
        self._lsp_type: Optional[str] = None  # "yaml" or "assemblyscript"
        self._diagnostics_text = ""
        self._hover_text = ""
        self._show_hover = False
        self._document_version = 0

    async def edit(
        self,
        initial_text: str,
        title: str = "Editor",
        document_uri: Optional[str] = None,
    ) -> Tuple[Optional[str], bool]:
        """
        Open the editor with LSP support.

        Args:
            initial_text: Initial content to edit
            title: Title shown in header
            document_uri: URI for LSP (auto-generated if not provided)

        Returns:
            (edited_text, saved) - edited_text is None if cancelled
        """
        if not PROMPT_TOOLKIT_AVAILABLE:
            print("Error: prompt_toolkit not installed")
            return None, False

        # Determine LSP type based on syntax
        syntax_lower = self.syntax.lower()
        if syntax_lower in ("yaml", "yml"):
            self._lsp_type = "yaml"
        elif syntax_lower in ("typescript", "ts", "assemblyscript", "as"):
            self._lsp_type = "assemblyscript"
        else:
            self._lsp_type = None

        # Try to get appropriate LSP client
        try:
            from xml_pipeline.console.lsp import get_lsp_manager, LSPServerType
            manager = get_lsp_manager()

            if self._lsp_type == "yaml":
                self._lsp_client = await manager.get_yaml_client()
            elif self._lsp_type == "assemblyscript":
                self._lsp_client = await manager.get_asls_client()
            else:
                self._lsp_client = None

        except Exception as e:
            logger.debug(f"LSP not available: {e}")
            self._lsp_client = None

        # Generate document URI with appropriate extension
        if document_uri is None:
            ext = ".yaml" if self._lsp_type == "yaml" else ".ts"
            document_uri = f"file:///temp/{title.replace(' ', '_')}{ext}"

        # Ensure schema modeline is present (YAML only)
        if self._lsp_type == "yaml":
            initial_text = self._ensure_modeline(initial_text)

        # Open document in LSP
        if self._lsp_client:
            await self._lsp_client.did_open(document_uri, initial_text)

        try:
            result = await self._run_editor(initial_text, title, document_uri)
        finally:
            # Close document in LSP
            if self._lsp_client:
                await self._lsp_client.did_close(document_uri)
                try:
                    from xml_pipeline.console.lsp import get_lsp_manager, LSPServerType
                    manager = get_lsp_manager()
                    if self._lsp_type == "yaml":
                        await manager.release_client(LSPServerType.YAML)
                    elif self._lsp_type == "assemblyscript":
                        await manager.release_client(LSPServerType.ASSEMBLYSCRIPT)
                except Exception:
                    pass

        return result

    def _ensure_modeline(self, text: str) -> str:
        """Ensure YAML has schema modeline if schema_type is set."""
        if self.schema_type is None:
            return text

        modeline = f"# yaml-language-server: $schema=~/.xml-pipeline/schemas/{self.schema_type}.schema.json"

        # Check if modeline already exists
        lines = text.split("\n")
        for line in lines[:3]:  # Check first 3 lines
            if "yaml-language-server" in line and "$schema" in line:
                return text

        # Add modeline at the top
        return modeline + "\n" + text

    async def _run_editor(
        self,
        initial_text: str,
        title: str,
        uri: str,
    ) -> Tuple[Optional[str], bool]:
        """Run the editor application."""
        result = {"text": None, "saved": False}

        # Create buffer
        buffer = Buffer(multiline=True, name="editor")
        buffer.text = initial_text

        # Key bindings
        kb = KeyBindings()

        @kb.add("c-s")  # Ctrl+S to save
        def save(event):
            result["text"] = buffer.text
            result["saved"] = True
            event.app.exit()

        @kb.add("c-q")  # Ctrl+Q to quit without saving
        def quit_nosave(event):
            result["text"] = None
            result["saved"] = False
            event.app.exit()

        @kb.add("escape")  # Escape to quit
        def escape(event):
            result["text"] = None
            result["saved"] = False
            event.app.exit()

        @kb.add("c-space")  # Ctrl+Space for completion
        async def trigger_completion(event):
            if self._lsp_client:
                doc = buffer.document
                line = doc.cursor_position_row
                col = doc.cursor_position_col
                completions = await self._lsp_client.completion(uri, line, col)
                if completions:
                    # Show first completion as hint
                    self._diagnostics_text = f"Completions: {', '.join(c.label for c in completions[:5])}"
                    event.app.invalidate()

        @kb.add("f1")  # F1 for hover
        async def show_hover(event):
            if self._lsp_client:
                doc = buffer.document
                line = doc.cursor_position_row
                col = doc.cursor_position_col
                hover = await self._lsp_client.hover(uri, line, col)
                if hover:
                    self._hover_text = hover.contents[:200]  # Truncate
                    self._show_hover = True
                else:
                    self._hover_text = ""
                    self._show_hover = False
                event.app.invalidate()

        @kb.add("escape", filter=Condition(lambda: self._show_hover))
        def hide_hover(event):
            self._show_hover = False
            event.app.invalidate()

        @kb.add("c-p")  # Ctrl+P for signature help (ASLS only)
        async def show_signature_help(event):
            # Only available for ASLS
            if self._lsp_client and self._lsp_type == "assemblyscript":
                doc = buffer.document
                line = doc.cursor_position_row
                col = doc.cursor_position_col
                try:
                    sig_help = await self._lsp_client.signature_help(uri, line, col)
                    if sig_help and sig_help.get("signatures"):
                        sig = sig_help["signatures"][0]
                        label = sig.get("label", "")
                        self._hover_text = f"Signature: {label}"
                        self._show_hover = True
                    else:
                        self._hover_text = ""
                        self._show_hover = False
                except Exception:
                    pass
                event.app.invalidate()

        # Syntax highlighting
        lexer = get_lexer_for_syntax(self.syntax)

        # Header
        def get_header():
            if self._lsp_client:
                if self._lsp_type == "yaml":
                    lsp_status = " [YAML LSP]"
                elif self._lsp_type == "assemblyscript":
                    lsp_status = " [ASLS]"
                else:
                    lsp_status = " [LSP]"
            else:
                lsp_status = ""

            parts = [
                ("class:header", f" {title}{lsp_status} "),
                ("class:header.key", " Ctrl+S"),
                ("class:header", "=Save "),
                ("class:header.key", " Ctrl+Q"),
                ("class:header", "=Quit "),
                ("class:header.key", " F1"),
                ("class:header", "=Hover "),
            ]

            # Add Ctrl+P hint for AssemblyScript
            if self._lsp_type == "assemblyscript" and self._lsp_client:
                parts.extend([
                    ("class:header.key", " Ctrl+P"),
                    ("class:header", "=Sig "),
                ])

            return parts

        header = Window(
            height=1,
            content=FormattedTextControl(get_header),
            style="class:header",
        )

        # Editor window
        editor_window = Window(
            content=BufferControl(
                buffer=buffer,
                lexer=lexer,
            ),
        )

        # Status bar
        def get_status():
            row = buffer.document.cursor_position_row + 1
            col = buffer.document.cursor_position_col + 1
            lines = len(buffer.text.split("\n"))

            parts = [("class:status", f" Line {row}/{lines}, Col {col} ")]

            if self._diagnostics_text:
                parts.append(("class:status.diag", f" | {self._diagnostics_text}"))

            return parts

        status_bar = Window(
            height=1,
            content=FormattedTextControl(get_status),
            style="class:status",
        )

        # Hover popup (shown conditionally)
        def get_hover_text():
            if self._show_hover and self._hover_text:
                return [("class:hover", self._hover_text)]
            return []

        hover_window = ConditionalContainer(
            Window(
                height=3,
                content=FormattedTextControl(get_hover_text),
                style="class:hover",
            ),
            filter=Condition(lambda: self._show_hover and bool(self._hover_text)),
        )

        # Layout
        layout = Layout(
            HSplit([
                header,
                editor_window,
                hover_window,
                status_bar,
            ])
        )

        # Styles
        style = Style.from_dict({
            "header": "bg:#005f87 #ffffff",
            "header.key": "bg:#005f87 #ffff00 bold",
            "status": "bg:#444444 #ffffff",
            "status.diag": "bg:#444444 #ff8800",
            "hover": "bg:#333333 #ffffff italic",
            "diagnostic.error": "bg:#5f0000 #ffffff",
            "diagnostic.warning": "bg:#5f5f00 #ffffff",
        })

        # Set up diagnostics callback
        async def on_text_changed(buff):
            if self._lsp_client:
                self._document_version += 1
                diagnostics = await self._lsp_client.did_change(
                    uri, buff.text, self._document_version
                )
                if diagnostics:
                    errors = sum(1 for d in diagnostics if d.severity == "error")
                    warnings = sum(1 for d in diagnostics if d.severity == "warning")
                    parts = []
                    if errors:
                        parts.append(f"{errors} error{'s' if errors > 1 else ''}")
                    if warnings:
                        parts.append(f"{warnings} warning{'s' if warnings > 1 else ''}")
                    self._diagnostics_text = " | ".join(parts) if parts else ""
                else:
                    self._diagnostics_text = ""

        buffer.on_text_changed += lambda buff: asyncio.create_task(on_text_changed(buff))

        # Create and run application
        app: Application = Application(
            layout=layout,
            key_bindings=kb,
            style=style,
            full_screen=True,
            mouse_support=True,
        )

        await app.run_async()

        return result["text"], result["saved"]


async def edit_text_async(
    initial_text: str,
    title: str = "Editor",
    schema_type: Optional[str] = None,
    syntax: str = "yaml",
) -> Tuple[Optional[str], bool]:
    """
    Async wrapper for LSP-enabled text editing.

    Args:
        initial_text: Text to edit
        title: Title shown in header
        schema_type: "organism" or "listener" for YAML schema modeline
        syntax: Syntax highlighting ("yaml", "typescript", "assemblyscript", "ts", "as")

    Returns:
        (edited_text, saved) - edited_text is None if cancelled
    """
    editor = LSPEditor(schema_type=schema_type, syntax=syntax)
    return await editor.edit(initial_text, title=title)


async def edit_file_async(
    filepath: str,
    title: Optional[str] = None,
    schema_type: Optional[str] = None,
    syntax: Optional[str] = None,
) -> bool:
    """
    Edit a file with LSP support.

    Args:
        filepath: Path to file
        title: Optional title (defaults to filename)
        schema_type: "organism" or "listener" for YAML schema modeline
        syntax: Syntax highlighting (auto-detected from extension if not specified)

    Returns:
        True if saved, False if cancelled
    """
    path = Path(filepath)
    title = title or path.name

    # Auto-detect syntax from extension if not specified
    if syntax is None:
        syntax = detect_syntax_from_path(path)

    # Load existing content or empty
    if path.exists():
        initial_text = path.read_text()
    else:
        initial_text = ""

    # Edit
    edited_text, saved = await edit_text_async(
        initial_text,
        title=title,
        schema_type=schema_type,
        syntax=syntax,
    )

    # Save if requested
    if saved and edited_text is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(edited_text)
        return True

    return False


async def edit_assemblyscript_source(
    filepath: str,
    title: Optional[str] = None,
) -> bool:
    """
    Edit an AssemblyScript listener source file with ASLS support.

    Args:
        filepath: Path to .ts or .as file
        title: Optional title (defaults to filename)

    Returns:
        True if saved, False if cancelled
    """
    return await edit_file_async(
        filepath,
        title=title,
        syntax="assemblyscript",
    )
