"""
Full-screen text editor using prompt_toolkit.

Provides a vim-like editing experience for configuration files.
"""

from typing import Optional, Tuple

try:
    from prompt_toolkit import Application
    from prompt_toolkit.buffer import Buffer
    from prompt_toolkit.layout import Layout, HSplit, VSplit
    from prompt_toolkit.layout.containers import Window, ConditionalContainer
    from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.filters import Condition
    from prompt_toolkit.styles import Style
    from prompt_toolkit.lexers import PygmentsLexer
    PROMPT_TOOLKIT_AVAILABLE = True
except ImportError:
    PROMPT_TOOLKIT_AVAILABLE = False

try:
    from pygments.lexers.data import YamlLexer
    PYGMENTS_AVAILABLE = True
except ImportError:
    PYGMENTS_AVAILABLE = False


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
        syntax: Syntax highlighting ("yaml", "text")

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
    lexer = None
    if PYGMENTS_AVAILABLE and syntax == "yaml":
        lexer = PygmentsLexer(YamlLexer)

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
