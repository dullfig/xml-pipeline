"""
Native tools for agents.

Tools are sandboxed, permission-controlled functions that agents can invoke
to interact with the outside world.
"""

from .base import Tool, ToolResult, tool, get_tool_registry
from .calculate import calculate
from .fetch import fetch_url
from .files import read_file, write_file, list_dir, delete_file, configure_allowed_paths
from .shell import run_command, configure_allowed_commands, configure_blocked_commands
from .search import web_search, configure_search
from .keyvalue import key_value_get, key_value_set, key_value_delete
from .librarian import librarian_store, librarian_get, librarian_query, librarian_search, configure_librarian
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
    # Tools
    "calculate",
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
