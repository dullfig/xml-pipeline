"""
YAML Language Server Protocol client.

Wraps communication with yaml-language-server for:
- Autocompletion
- Diagnostics (validation errors)
- Hover information
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Any
import logging

logger = logging.getLogger(__name__)


# Check for lsp-client availability
def _check_lsp_client() -> bool:
    """Check if lsp-client package is available."""
    try:
        import lsp_client  # noqa: F401
        return True
    except ImportError:
        return False


def _check_yaml_language_server() -> bool:
    """Check if yaml-language-server is installed."""
    return shutil.which("yaml-language-server") is not None


def is_lsp_available() -> tuple[bool, str]:
    """
    Check if LSP support is available.

    Returns (available, reason) tuple.
    """
    if not _check_lsp_client():
        return False, "lsp-client package not installed (pip install lsp-client)"

    if not _check_yaml_language_server():
        return False, "yaml-language-server not found (npm install -g yaml-language-server)"

    return True, "LSP available"


@dataclass
class LSPCompletion:
    """Normalized completion item from LSP."""

    label: str
    kind: str = "text"  # text, keyword, property, value, snippet
    detail: str = ""
    documentation: str = ""
    insert_text: str = ""
    sort_text: str = ""

    @classmethod
    def from_lsp(cls, item: dict[str, Any]) -> "LSPCompletion":
        """Create from LSP CompletionItem."""
        kind_map = {
            1: "text",
            2: "method",
            3: "function",
            5: "field",
            6: "variable",
            9: "module",
            10: "property",
            12: "value",
            14: "keyword",
            15: "snippet",
        }

        return cls(
            label=item.get("label", ""),
            kind=kind_map.get(item.get("kind", 1), "text"),
            detail=item.get("detail", ""),
            documentation=_extract_documentation(item.get("documentation")),
            insert_text=item.get("insertText", item.get("label", "")),
            sort_text=item.get("sortText", item.get("label", "")),
        )


@dataclass
class LSPDiagnostic:
    """Normalized diagnostic from LSP."""

    line: int
    column: int
    end_line: int
    end_column: int
    message: str
    severity: str = "error"  # error, warning, info, hint
    source: str = "yaml-language-server"

    @classmethod
    def from_lsp(cls, diag: dict[str, Any]) -> "LSPDiagnostic":
        """Create from LSP Diagnostic."""
        severity_map = {1: "error", 2: "warning", 3: "info", 4: "hint"}

        range_data = diag.get("range", {})
        start = range_data.get("start", {})
        end = range_data.get("end", {})

        return cls(
            line=start.get("line", 0),
            column=start.get("character", 0),
            end_line=end.get("line", 0),
            end_column=end.get("character", 0),
            message=diag.get("message", ""),
            severity=severity_map.get(diag.get("severity", 1), "error"),
            source=diag.get("source", "yaml-language-server"),
        )


@dataclass
class LSPHover:
    """Normalized hover information from LSP."""

    contents: str
    range_start_line: Optional[int] = None
    range_start_col: Optional[int] = None

    @classmethod
    def from_lsp(cls, hover: dict[str, Any]) -> Optional["LSPHover"]:
        """Create from LSP Hover response."""
        if not hover:
            return None

        contents = hover.get("contents")
        if isinstance(contents, str):
            text = contents
        elif isinstance(contents, dict):
            text = contents.get("value", str(contents))
        elif isinstance(contents, list):
            text = "\n".join(
                c.get("value", str(c)) if isinstance(c, dict) else str(c)
                for c in contents
            )
        else:
            return None

        range_data = hover.get("range", {})
        start = range_data.get("start", {})

        return cls(
            contents=text,
            range_start_line=start.get("line"),
            range_start_col=start.get("character"),
        )


def _extract_documentation(doc: Any) -> str:
    """Extract documentation string from LSP documentation field."""
    if doc is None:
        return ""
    if isinstance(doc, str):
        return doc
    if isinstance(doc, dict):
        return doc.get("value", "")
    return str(doc)


class YAMLLSPClient:
    """
    Client for communicating with yaml-language-server.

    Uses stdio for communication with the language server process.
    """

    def __init__(self, schema_uri: Optional[str] = None):
        """
        Initialize the LSP client.

        Args:
            schema_uri: Default schema URI for YAML files
        """
        self.schema_uri = schema_uri
        self._process: Optional[subprocess.Popen] = None
        self._reader_task: Optional[asyncio.Task] = None
        self._request_id = 0
        self._pending_requests: dict[int, asyncio.Future] = {}
        self._diagnostics: dict[str, list[LSPDiagnostic]] = {}
        self._initialized = False
        self._lock = asyncio.Lock()

    async def start(self) -> bool:
        """
        Start the language server.

        Returns True if started successfully.
        """
        available, reason = is_lsp_available()
        if not available:
            logger.warning(f"LSP not available: {reason}")
            return False

        try:
            self._process = subprocess.Popen(
                ["yaml-language-server", "--stdio"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            # Start reader task
            self._reader_task = asyncio.create_task(self._read_messages())

            # Initialize LSP
            await self._initialize()
            self._initialized = True
            return True

        except Exception as e:
            logger.error(f"Failed to start yaml-language-server: {e}")
            await self.stop()
            return False

    async def stop(self) -> None:
        """Stop the language server."""
        self._initialized = False

        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
            self._reader_task = None

        if self._process:
            self._process.terminate()
            try:
                self._process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._process.kill()
            self._process = None

        # Cancel pending requests
        for future in self._pending_requests.values():
            if not future.done():
                future.cancel()
        self._pending_requests.clear()

    async def _initialize(self) -> None:
        """Send LSP initialize request."""
        result = await self._request(
            "initialize",
            {
                "processId": None,
                "rootUri": None,
                "capabilities": {
                    "textDocument": {
                        "completion": {
                            "completionItem": {
                                "snippetSupport": True,
                                "documentationFormat": ["markdown", "plaintext"],
                            }
                        },
                        "hover": {
                            "contentFormat": ["markdown", "plaintext"],
                        },
                        "publishDiagnostics": {},
                    }
                },
                "initializationOptions": {
                    "yaml": {
                        "validate": True,
                        "hover": True,
                        "completion": True,
                        "schemas": {},
                    }
                },
            },
        )
        logger.debug(f"LSP initialized: {result}")

        # Send initialized notification
        await self._notify("initialized", {})

    async def did_open(self, uri: str, content: str) -> None:
        """Notify server that a document was opened."""
        if not self._initialized:
            return

        await self._notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": uri,
                    "languageId": "yaml",
                    "version": 1,
                    "text": content,
                }
            },
        )

    async def did_change(self, uri: str, content: str, version: int = 1) -> list[LSPDiagnostic]:
        """
        Notify server of document change.

        Returns current diagnostics for the document.
        """
        if not self._initialized:
            return []

        await self._notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": version},
                "contentChanges": [{"text": content}],
            },
        )

        # Wait briefly for diagnostics
        await asyncio.sleep(0.1)

        return self._diagnostics.get(uri, [])

    async def did_close(self, uri: str) -> None:
        """Notify server that a document was closed."""
        if not self._initialized:
            return

        await self._notify(
            "textDocument/didClose",
            {"textDocument": {"uri": uri}},
        )

        # Clear diagnostics
        self._diagnostics.pop(uri, None)

    async def completion(
        self, uri: str, line: int, column: int
    ) -> list[LSPCompletion]:
        """
        Request completions at a position.

        Args:
            uri: Document URI
            line: 0-indexed line number
            column: 0-indexed column number

        Returns list of completion items.
        """
        if not self._initialized:
            return []

        try:
            result = await self._request(
                "textDocument/completion",
                {
                    "textDocument": {"uri": uri},
                    "position": {"line": line, "character": column},
                },
            )

            if result is None:
                return []

            items = result.get("items", []) if isinstance(result, dict) else result
            return [LSPCompletion.from_lsp(item) for item in items]

        except Exception as e:
            logger.debug(f"Completion request failed: {e}")
            return []

    async def hover(self, uri: str, line: int, column: int) -> Optional[LSPHover]:
        """
        Request hover information at a position.

        Args:
            uri: Document URI
            line: 0-indexed line number
            column: 0-indexed column number
        """
        if not self._initialized:
            return None

        try:
            result = await self._request(
                "textDocument/hover",
                {
                    "textDocument": {"uri": uri},
                    "position": {"line": line, "character": column},
                },
            )

            return LSPHover.from_lsp(result) if result else None

        except Exception as e:
            logger.debug(f"Hover request failed: {e}")
            return None

    def get_diagnostics(self, uri: str) -> list[LSPDiagnostic]:
        """Get current diagnostics for a document."""
        return self._diagnostics.get(uri, [])

    async def _request(self, method: str, params: dict[str, Any]) -> Any:
        """Send a request and wait for response."""
        async with self._lock:
            self._request_id += 1
            req_id = self._request_id

        message = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params,
        }

        future: asyncio.Future = asyncio.Future()
        self._pending_requests[req_id] = future

        try:
            await self._send_message(message)
            return await asyncio.wait_for(future, timeout=5.0)
        except asyncio.TimeoutError:
            logger.warning(f"LSP request timed out: {method}")
            return None
        finally:
            self._pending_requests.pop(req_id, None)

    async def _notify(self, method: str, params: dict[str, Any]) -> None:
        """Send a notification (no response expected)."""
        message = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        await self._send_message(message)

    async def _send_message(self, message: dict[str, Any]) -> None:
        """Send a JSON-RPC message to the server."""
        if not self._process or not self._process.stdin:
            return

        content = json.dumps(message)
        header = f"Content-Length: {len(content)}\r\n\r\n"

        try:
            self._process.stdin.write(header.encode())
            self._process.stdin.write(content.encode())
            self._process.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            logger.error(f"Failed to send LSP message: {e}")

    async def _read_messages(self) -> None:
        """Read messages from the server."""
        if not self._process or not self._process.stdout:
            return

        loop = asyncio.get_event_loop()

        try:
            while True:
                # Read header
                header = b""
                while b"\r\n\r\n" not in header:
                    chunk = await loop.run_in_executor(
                        None, self._process.stdout.read, 1
                    )
                    if not chunk:
                        return  # EOF
                    header += chunk

                # Parse content length
                content_length = 0
                for line in header.decode().split("\r\n"):
                    if line.startswith("Content-Length:"):
                        content_length = int(line.split(":")[1].strip())
                        break

                if content_length == 0:
                    continue

                # Read content
                content = await loop.run_in_executor(
                    None, self._process.stdout.read, content_length
                )

                if not content:
                    return

                # Parse and handle message
                try:
                    message = json.loads(content.decode())
                    await self._handle_message(message)
                except json.JSONDecodeError as e:
                    logger.error(f"Failed to parse LSP message: {e}")

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"LSP reader error: {e}")

    async def _handle_message(self, message: dict[str, Any]) -> None:
        """Handle an incoming LSP message."""
        if "id" in message and "result" in message:
            # Response to a request
            req_id = message["id"]
            if req_id in self._pending_requests:
                future = self._pending_requests[req_id]
                if not future.done():
                    future.set_result(message.get("result"))

        elif "id" in message and "error" in message:
            # Error response
            req_id = message["id"]
            if req_id in self._pending_requests:
                future = self._pending_requests[req_id]
                if not future.done():
                    error = message["error"]
                    future.set_exception(
                        Exception(f"LSP error: {error.get('message', error)}")
                    )

        elif message.get("method") == "textDocument/publishDiagnostics":
            # Diagnostics notification
            params = message.get("params", {})
            uri = params.get("uri", "")
            diagnostics = [
                LSPDiagnostic.from_lsp(d)
                for d in params.get("diagnostics", [])
            ]
            self._diagnostics[uri] = diagnostics
            logger.debug(f"Received {len(diagnostics)} diagnostics for {uri}")

        elif "method" in message:
            # Other notification
            logger.debug(f"LSP notification: {message.get('method')}")
