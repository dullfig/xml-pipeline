"""
Key-value store tool - persistent agent state.

Backend: async SQLite via aiosqlite (zero external deps).
Falls back to in-memory dict if aiosqlite is unavailable.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

from .base import tool, ToolResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Backend detection
# ---------------------------------------------------------------------------
try:
    import aiosqlite
    AIOSQLITE_AVAILABLE = True
except ImportError:
    aiosqlite = None  # type: ignore[assignment]
    AIOSQLITE_AVAILABLE = False

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
_db_path: Optional[str] = None
_initialized: bool = False

# Fallback in-memory store (used when aiosqlite unavailable)
_mem_store: dict[str, tuple[Any, Optional[float]]] = {}  # ns_key → (value, expiry_at)


def configure_keyvalue(db_path: str = "~/.xml-pipeline/kv.db") -> None:
    """Set the SQLite database path. Call before first use."""
    global _db_path, _initialized
    import os
    _db_path = os.path.expanduser(db_path)
    _initialized = False


def _reset() -> None:
    """Reset global state (for tests)."""
    global _db_path, _initialized, _mem_store
    _db_path = None
    _initialized = False
    _mem_store.clear()


# ---------------------------------------------------------------------------
# SQLite helpers
# ---------------------------------------------------------------------------
_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS kv_store (
    ns_key TEXT PRIMARY KEY,
    value  TEXT NOT NULL,
    expiry_at REAL
)
"""


async def _ensure_db() -> None:
    """Create table on first use (idempotent)."""
    global _initialized
    if _initialized:
        return
    if not AIOSQLITE_AVAILABLE or not _db_path:
        return
    import os
    os.makedirs(os.path.dirname(os.path.abspath(_db_path)), exist_ok=True)
    async with aiosqlite.connect(_db_path) as db:
        await db.execute(_CREATE_TABLE)
        await db.commit()
    _initialized = True


def _ns_key(namespace: Optional[str], key: str) -> str:
    return f"{namespace or 'default'}:{key}"


async def _sqlite_get(ns_key: str) -> Any:
    await _ensure_db()
    async with aiosqlite.connect(_db_path) as db:
        cursor = await db.execute(
            "SELECT value, expiry_at FROM kv_store WHERE ns_key = ?", (ns_key,)
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        value_json, expiry_at = row
        if expiry_at is not None and time.time() > expiry_at:
            # Expired — lazily delete
            await db.execute("DELETE FROM kv_store WHERE ns_key = ?", (ns_key,))
            await db.commit()
            return None
        return json.loads(value_json)


async def _sqlite_set(ns_key: str, value: Any, ttl: Optional[int]) -> None:
    await _ensure_db()
    expiry_at = (time.time() + ttl) if ttl else None
    value_json = json.dumps(value)
    async with aiosqlite.connect(_db_path) as db:
        await db.execute(
            "INSERT OR REPLACE INTO kv_store (ns_key, value, expiry_at) VALUES (?, ?, ?)",
            (ns_key, value_json, expiry_at),
        )
        await db.commit()


async def _sqlite_delete(ns_key: str) -> bool:
    await _ensure_db()
    async with aiosqlite.connect(_db_path) as db:
        cursor = await db.execute(
            "DELETE FROM kv_store WHERE ns_key = ?", (ns_key,)
        )
        await db.commit()
        return cursor.rowcount > 0


# ---------------------------------------------------------------------------
# In-memory fallback
# ---------------------------------------------------------------------------

def _mem_get(ns_key: str) -> Any:
    entry = _mem_store.get(ns_key)
    if entry is None:
        return None
    value, expiry_at = entry
    if expiry_at is not None and time.time() > expiry_at:
        del _mem_store[ns_key]
        return None
    return value


def _mem_set(ns_key: str, value: Any, ttl: Optional[int]) -> None:
    expiry_at = (time.time() + ttl) if ttl else None
    _mem_store[ns_key] = (value, expiry_at)


def _mem_delete(ns_key: str) -> bool:
    return _mem_store.pop(ns_key, None) is not None


def _use_sqlite() -> bool:
    return AIOSQLITE_AVAILABLE and _db_path is not None


# ---------------------------------------------------------------------------
# @tool interface (unchanged signatures)
# ---------------------------------------------------------------------------

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
    nk = _ns_key(namespace, key)
    if _use_sqlite():
        value = await _sqlite_get(nk)
    else:
        value = _mem_get(nk)
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
    nk = _ns_key(namespace, key)
    try:
        if _use_sqlite():
            await _sqlite_set(nk, value, ttl)
        else:
            _mem_set(nk, value, ttl)
        return ToolResult(success=True, data=True)
    except (TypeError, ValueError) as e:
        return ToolResult(success=False, error=f"Value not JSON-serializable: {e}")


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
    nk = _ns_key(namespace, key)
    if _use_sqlite():
        deleted = await _sqlite_delete(nk)
    else:
        deleted = _mem_delete(nk)
    return ToolResult(success=True, data=deleted)
