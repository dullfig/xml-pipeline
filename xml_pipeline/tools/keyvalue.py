"""
Key-value store tool - persistent agent state.
"""

from typing import Any, Optional
from .base import tool, ToolResult


# TODO: Implement backend (Redis, SQLite, or in-memory)
_store: dict = {}  # Temporary in-memory store


@tool
async def key_value_get(
    key: str,
    namespace: Optional[str] = None,
) -> ToolResult:
    """
    Get a value from the key-value store.

    Args:
        key: Key to retrieve
        namespace: Namespace for isolation (default: agent name)

    Returns:
        Stored value, or null if not found
    """
    # TODO: Implement with Redis/SQLite
    ns_key = f"{namespace or 'default'}:{key}"
    value = _store.get(ns_key)
    return ToolResult(success=True, data=value)


@tool
async def key_value_set(
    key: str,
    value: Any,
    namespace: Optional[str] = None,
    ttl: Optional[int] = None,
) -> ToolResult:
    """
    Set a value in the key-value store.

    Args:
        key: Key to store
        value: Value to store (JSON-serializable)
        namespace: Namespace for isolation (default: agent name)
        ttl: Time-to-live in seconds (optional)

    Returns:
        success (bool)
    """
    # TODO: Implement with Redis/SQLite, handle TTL
    ns_key = f"{namespace or 'default'}:{key}"
    _store[ns_key] = value
    return ToolResult(success=True, data=True)


@tool
async def key_value_delete(
    key: str,
    namespace: Optional[str] = None,
) -> ToolResult:
    """
    Delete a key from the key-value store.

    Args:
        key: Key to delete
        namespace: Namespace for isolation

    Returns:
        deleted (bool)
    """
    # TODO: Implement with Redis/SQLite
    ns_key = f"{namespace or 'default'}:{key}"
    deleted = ns_key in _store
    _store.pop(ns_key, None)
    return ToolResult(success=True, data=deleted)
