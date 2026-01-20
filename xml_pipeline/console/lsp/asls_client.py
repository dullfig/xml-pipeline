"""
AssemblyScript Language Server Protocol client.

Wraps communication with asls (AssemblyScript Language Server) for:
- Autocompletion for AgentServer SDK types
- Type checking and diagnostics
- Hover documentation

Install: npm install -g assemblyscript-lsp

Used for editing WASM listener source files written in AssemblyScript.
"""

from __future__ import annotations

import asyncio
import shutil
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Any

from .client import (
    LSPCompletion,
    LSPDiagnostic,
    LSPHover,
)

logger = logging.getLogger(__name__)


def _check_asls() -> bool:
    """Check if asls (AssemblyScript Language Server) is installed."""
    return shutil.which("asls") is not None


def is_asls_available() -> tuple[bool, str]:
    """
    Check if AssemblyScript LSP support is available.

    Returns (available, reason) tuple.
    """
    if not _check_asls():
        return False, "asls not found (npm install -g assemblyscript-lsp)"

    return True, "AssemblyScript LSP available"


# File extensions handled by ASLS
ASSEMBLYSCRIPT_EXTENSIONS = {".ts", ".as"}


def is_assemblyscript_file(path: str | Path) -> bool:
    """Check if a file should use the AssemblyScript LSP."""
    return Path(path).suffix.lower() in ASSEMBLYSCRIPT_EXTENSIONS


@dataclass
class ASLSConfig:
    """
    Configuration for the AssemblyScript Language Server.

    These settings are passed during initialization.
    """

    # Path to asconfig.json (AssemblyScript project config)
    asconfig_path: Optional[str] = None

    # Path to AgentServer SDK type definitions
    sdk_types_path: Optional[str] = None

    # Enable strict null checks
    strict_null_checks: bool = True

    # Enable additional diagnostics
    verbose_diagnostics: bool = False


class ASLSClient:
    """
    Client for communicating with the AssemblyScript Language Server.

    Uses stdio for communication with the language server process.

    Usage:
        client = ASLSClient()
        if await client.start():
            await client.did_open(uri, content)
            completions = await client.completion(uri, line, col)
            await client.stop()
    """

    def __init__(self, config: Optional[ASLSConfig] = None):
        """
        Initialize the ASLS client.

        Args:
            config: Optional ASLS configuration
        """
        self.config = config or ASLSConfig()
        self._process: Optional[asyncio.subprocess.Process] = None
        self._reader_task: Optional[asyncio.Task] = None
        self._request_id = 0
        self._pending_requests: dict[int, asyncio.Future] = {}
        self._diagnostics: dict[str, list[LSPDiagnostic]] = {}
        self._initialized = False
        self._lock = asyncio.Lock()

    async def start(self) -> bool:
        """
        Start the AssemblyScript language server.

        Returns True if started successfully.
        """
        available, reason = is_asls_available()
        if not available:
            logger.warning(f"ASLS not available: {reason}")
            return False

        try:
            self._process = await asyncio.create_subprocess_exec(
                "asls", "--stdio",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            # Start reader task
            self._reader_task = asyncio.create_task(self._read_messages())

            # Initialize LSP
            await self._initialize()
            self._initialized = True
            logger.info("AssemblyScript language server started")
            return True

        except Exception as e:
            logger.error(f"Failed to start asls: {e}")
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
                await asyncio.wait_for(self._process.wait(), timeout=2)
            except asyncio.TimeoutError:
                self._process.kill()
            self._process = None

        # Cancel pending requests
        for future in self._pending_requests.values():
            if not future.done():
                future.cancel()
        self._pending_requests.clear()

    async def _initialize(self) -> None:
        """Send LSP initialize request."""
        init_options: dict[str, Any] = {}

        if self.config.asconfig_path:
            init_options["asconfigPath"] = self.config.asconfig_path

        if self.config.sdk_types_path:
            init_options["sdkTypesPath"] = self.config.sdk_types_path

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
                        "publishDiagnostics": {
                            "relatedInformation": True,
                        },
                        "signatureHelp": {
                            "signatureInformation": {
                                "documentationFormat": ["markdown", "plaintext"],
                            }
                        },
                    }
                },
                "initializationOptions": init_options,
            },
        )
        logger.debug(f"ASLS initialized: {result}")

        # Send initialized notification
        await self._notify("initialized", {})

    async def did_open(self, uri: str, content: str) -> None:
        """Notify server that a document was opened."""
        if not self._initialized:
            return

        # Determine language ID based on extension
        language_id = "assemblyscript"
        if uri.endswith(".ts"):
            language_id = "typescript"  # ASLS may prefer this

        await self._notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": uri,
                    "languageId": language_id,
                    "version": 1,
                    "text": content,
                }
            },
        )

    async def did_change(
        self, uri: str, content: str, version: int = 1
    ) -> list[LSPDiagnostic]:
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
        await asyncio.sleep(0.2)  # ASLS may need more time than YAML

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
            logger.debug(f"ASLS completion request failed: {e}")
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
            logger.debug(f"ASLS hover request failed: {e}")
            return None

    async def signature_help(
        self, uri: str, line: int, column: int
    ) -> Optional[dict[str, Any]]:
        """
        Request signature help at a position.

        Useful when typing function arguments.
        """
        if not self._initialized:
            return None

        try:
            result = await self._request(
                "textDocument/signatureHelp",
                {
                    "textDocument": {"uri": uri},
                    "position": {"line": line, "character": column},
                },
            )
            return result

        except Exception as e:
            logger.debug(f"ASLS signature help request failed: {e}")
            return None

    async def go_to_definition(
        self, uri: str, line: int, column: int
    ) -> Optional[list[dict[str, Any]]]:
        """
        Request go-to-definition at a position.

        Returns list of location objects.
        """
        if not self._initialized:
            return None

        try:
            result = await self._request(
                "textDocument/definition",
                {
                    "textDocument": {"uri": uri},
                    "position": {"line": line, "character": column},
                },
            )

            if result is None:
                return None

            # Normalize to list
            if isinstance(result, dict):
                return [result]
            return result

        except Exception as e:
            logger.debug(f"ASLS go-to-definition failed: {e}")
            return None

    def get_diagnostics(self, uri: str) -> list[LSPDiagnostic]:
        """Get current diagnostics for a document."""
        return self._diagnostics.get(uri, [])

    # -------------------------------------------------------------------------
    # LSP Protocol Implementation (shared pattern with YAMLLSPClient)
    # -------------------------------------------------------------------------

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
            return await asyncio.wait_for(future, timeout=10.0)  # Longer timeout for ASLS
        except asyncio.TimeoutError:
            logger.warning(f"ASLS request timed out: {method}")
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

        import json
        content = json.dumps(message)
        header = f"Content-Length: {len(content)}\r\n\r\n"

        try:
            self._process.stdin.write(header.encode())
            self._process.stdin.write(content.encode())
            await self._process.stdin.drain()
        except (BrokenPipeError, OSError, ConnectionResetError) as e:
            logger.error(f"Failed to send ASLS message: {e}")

    async def _read_messages(self) -> None:
        """Read messages from the server."""
        if not self._process or not self._process.stdout:
            return

        import json

        try:
            while True:
                # Read header
                header = b""
                while b"\r\n\r\n" not in header:
                    chunk = await self._process.stdout.read(1)
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
                content = await self._process.stdout.read(content_length)

                if not content:
                    return

                # Parse and handle message
                try:
                    message = json.loads(content.decode())
                    await self._handle_message(message)
                except json.JSONDecodeError as e:
                    logger.error(f"Failed to parse ASLS message: {e}")

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"ASLS reader error: {e}")

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
                        Exception(f"ASLS error: {error.get('message', error)}")
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
            logger.debug(f"ASLS: {len(diagnostics)} diagnostics for {uri}")

        elif "method" in message:
            # Other notification
            logger.debug(f"ASLS notification: {message.get('method')}")
