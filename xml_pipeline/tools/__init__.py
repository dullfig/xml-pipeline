"""
Native tools for agents.

Tools are sandboxed, permission-controlled functions that agents can invoke
to interact with the outside world.
"""

from .base import Tool, ToolResult, tool, get_tool_registry
from .calculate import calculate, Calculate, CalculateResult, handle_calculate
from .fetch import fetch_url, FetchRequest, FetchResponse, handle_fetch
from .files import read_file, write_file, list_dir, delete_file, configure_allowed_paths
from .shell import run_command, configure_allowed_commands, configure_blocked_commands, ShellCommand, ShellResult, handle_shell
from .search import web_search, configure_search
from .keyvalue import key_value_get, key_value_set, key_value_delete, configure_keyvalue, KVBackend, SqliteBackend, MemoryBackend
from .librarian import librarian_store, librarian_get, librarian_query, librarian_search, configure_librarian
from .console import (
    console_notify, console_prompt,
    ConsoleNotify, ConsolePrompt, ConsoleInput,
    handle_console_notify, handle_console_prompt,
    get_console_backend, set_console_backend,
)
from .convert import xml_to_json, json_to_xml, xml_extract

__all__ = [
    # Base
    "Tool",
    "ToolResult",
    "tool",
    "get_tool_registry",
    # Configuration
    "configure_allowed_paths",
    "configure_allowed_commands",
    "configure_blocked_commands",
    "configure_search",
    "configure_librarian",
    # Calculator listener
    "Calculate",
    "CalculateResult",
    "handle_calculate",
    # Fetch listener
    "FetchRequest",
    "FetchResponse",
    "handle_fetch",
    # Shell listener
    "ShellCommand",
    "ShellResult",
    "handle_shell",
    # Key-value backend
    "KVBackend",
    "SqliteBackend",
    "MemoryBackend",
    "configure_keyvalue",
    # Console listener
    "ConsoleNotify",
    "ConsolePrompt",
    "ConsoleInput",
    "handle_console_notify",
    "handle_console_prompt",
    "get_console_backend",
    "set_console_backend",
    # Tools
    "calculate",
    "console_notify",
    "console_prompt",
    "fetch_url",
    "read_file",
    "write_file",
    "list_dir",
    "delete_file",
    "run_command",
    "web_search",
    "key_value_get",
    "key_value_set",
    "key_value_delete",
    "librarian_store",
    "librarian_get",
    "librarian_query",
    "librarian_search",
    # Conversion
    "xml_to_json",
    "json_to_xml",
    "xml_extract",
]
