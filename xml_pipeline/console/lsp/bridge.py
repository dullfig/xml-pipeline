"""
Bridge between LSP client and prompt_toolkit.

Provides:
- LSPCompleter: Async completer for prompt_toolkit using LSP
- DiagnosticsProcessor: Processes diagnostics for inline display
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Optional, Iterable, TYPE_CHECKING

if TYPE_CHECKING:
    from .client import YAMLLSPClient, LSPDiagnostic, LSPCompletion

try:
    from prompt_toolkit.completion import Completer, Completion
    from prompt_toolkit.document import Document
    PROMPT_TOOLKIT_AVAILABLE = True
except ImportError:
    PROMPT_TOOLKIT_AVAILABLE = False
    # Stub classes for type checking
    class Completer:  # type: ignore
        pass
    class Completion:  # type: ignore
        pass
    class Document:  # type: ignore
        pass


@dataclass
class DiagnosticMark:
    """A diagnostic marker for display in the editor."""

    line: int
    column: int
    end_column: int
    message: str
    severity: str  # error, warning, info, hint

    @property
    def is_error(self) -> bool:
        return self.severity == "error"

    @property
    def is_warning(self) -> bool:
        return self.severity == "warning"

    @property
    def style(self) -> str:
        """Get prompt_toolkit style for this diagnostic."""
        if self.severity == "error":
            return "class:diagnostic.error"
        elif self.severity == "warning":
            return "class:diagnostic.warning"
        elif self.severity == "info":
            return "class:diagnostic.info"
        else:
            return "class:diagnostic.hint"


class LSPCompleter(Completer):
    """
    prompt_toolkit completer that uses LSP for suggestions.

    Usage:
        completer = LSPCompleter(lsp_client, document_uri)
        buffer = Buffer(completer=completer)
    """

    def __init__(
        self,
        client: Optional["YAMLLSPClient"],
        uri: str,
        fallback_completer: Optional[Completer] = None,
    ):
        """
        Initialize the LSP completer.

        Args:
            client: LSP client (can be None for fallback-only mode)
            uri: Document URI for LSP requests
            fallback_completer: Fallback when LSP unavailable
        """
        self.client = client
        self.uri = uri
        self.fallback_completer = fallback_completer
        self._cache: dict[tuple[int, int], list["LSPCompletion"]] = {}
        self._cache_version = 0

    def invalidate_cache(self) -> None:
        """Invalidate the completion cache."""
        self._cache.clear()
        self._cache_version += 1

    def get_completions(
        self,
        document: Document,
        complete_event,
    ) -> Iterable[Completion]:
        """
        Get completions for the current document position.

        This is called synchronously by prompt_toolkit.
        We use a cached result if available, otherwise
        return nothing (async completions handled separately).
        """
        if not PROMPT_TOOLKIT_AVAILABLE:
            return

        # Get current position
        line = document.cursor_position_row
        col = document.cursor_position_col

        # Check cache
        cache_key = (line, col)
        if cache_key in self._cache:
            completions = self._cache[cache_key]
            for item in completions:
                yield Completion(
                    text=item.insert_text or item.label,
                    start_position=-len(self._get_word_before_cursor(document)),
                    display=item.label,
                    display_meta=item.detail or item.kind,
                )
            return

        # Fallback to basic completer
        if self.fallback_completer:
            yield from self.fallback_completer.get_completions(
                document, complete_event
            )

    async def get_completions_async(
        self,
        document: Document,
    ) -> list["LSPCompletion"]:
        """
        Get completions asynchronously from LSP.

        Call this when Ctrl+Space is pressed.
        """
        if self.client is None:
            return []

        line = document.cursor_position_row
        col = document.cursor_position_col

        # Request from LSP
        completions = await self.client.completion(self.uri, line, col)

        # Cache result
        self._cache[(line, col)] = completions

        return completions

    def _get_word_before_cursor(self, document: Document) -> str:
        """Get the word being typed before cursor."""
        text = document.text_before_cursor
        if not text:
            return ""

        # Find word boundary
        i = len(text) - 1
        while i >= 0 and (text[i].isalnum() or text[i] in "_-"):
            i -= 1

        return text[i + 1:]


class DiagnosticsProcessor:
    """
    Processes LSP diagnostics for display in the editor.

    Converts LSP diagnostics into markers that can be
    displayed inline in the prompt_toolkit editor.
    """

    def __init__(self, client: Optional["YAMLLSPClient"], uri: str):
        self.client = client
        self.uri = uri
        self._marks: list[DiagnosticMark] = []

    def get_marks(self) -> list[DiagnosticMark]:
        """Get current diagnostic marks."""
        return self._marks

    def get_marks_for_line(self, line: int) -> list[DiagnosticMark]:
        """Get diagnostic marks for a specific line."""
        return [m for m in self._marks if m.line == line]

    def has_errors(self) -> bool:
        """Check if there are any error-level diagnostics."""
        return any(m.is_error for m in self._marks)

    def has_warnings(self) -> bool:
        """Check if there are any warning-level diagnostics."""
        return any(m.is_warning for m in self._marks)

    def get_error_count(self) -> int:
        """Get number of errors."""
        return sum(1 for m in self._marks if m.is_error)

    def get_warning_count(self) -> int:
        """Get number of warnings."""
        return sum(1 for m in self._marks if m.is_warning)

    async def update(self, content: str, version: int = 1) -> list[DiagnosticMark]:
        """
        Update diagnostics by sending content to LSP.

        Returns the new list of diagnostic marks.
        """
        if self.client is None:
            self._marks = []
            return []

        diagnostics = await self.client.did_change(self.uri, content, version)

        self._marks = [
            DiagnosticMark(
                line=d.line,
                column=d.column,
                end_column=d.end_column,
                message=d.message,
                severity=d.severity,
            )
            for d in diagnostics
        ]

        return self._marks

    def format_status(self) -> str:
        """Format diagnostics as status bar text."""
        errors = self.get_error_count()
        warnings = self.get_warning_count()

        if errors == 0 and warnings == 0:
            return ""

        parts = []
        if errors > 0:
            parts.append(f"{errors} error{'s' if errors > 1 else ''}")
        if warnings > 0:
            parts.append(f"{warnings} warning{'s' if warnings > 1 else ''}")

        return " | ".join(parts)

    def format_messages(self, max_lines: int = 3) -> list[str]:
        """Format diagnostic messages for display."""
        messages = []

        for mark in self._marks[:max_lines]:
            prefix = "E" if mark.is_error else "W"
            messages.append(f"[{prefix}] Line {mark.line + 1}: {mark.message}")

        remaining = len(self._marks) - max_lines
        if remaining > 0:
            messages.append(f"... and {remaining} more")

        return messages


class HoverPopup:
    """
    Manages hover information display.

    Shows documentation when hovering over a field
    or pressing F1 on a position.
    """

    def __init__(self, client: Optional["YAMLLSPClient"], uri: str):
        self.client = client
        self.uri = uri
        self._current_hover: Optional[str] = None
        self._hover_position: Optional[tuple[int, int]] = None

    async def get_hover(self, line: int, col: int) -> Optional[str]:
        """
        Get hover information for a position.

        Returns formatted hover text or None.
        """
        if self.client is None:
            return None

        hover = await self.client.hover(self.uri, line, col)

        if hover is None:
            self._current_hover = None
            self._hover_position = None
            return None

        self._current_hover = hover.contents
        self._hover_position = (line, col)

        return hover.contents

    def clear(self) -> None:
        """Clear current hover."""
        self._current_hover = None
        self._hover_position = None

    @property
    def has_hover(self) -> bool:
        """Check if there's an active hover."""
        return self._current_hover is not None

    @property
    def text(self) -> str:
        """Get current hover text."""
        return self._current_hover or ""
