"""
Key-value store tool - persistent agent state.

Pluggable backend via KVBackend protocol.
Default: SQLite (via aiosqlite) with in-memory fallback.
"""

from __future__ import annotations

import fnmatch
import json
import logging
import os
import re
import time
from typing import Any, Optional, Protocol, runtime_checkable

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
# TTL validation
# ---------------------------------------------------------------------------

MAX_TTL_SECONDS = 315_360_000  # ~10 years
MAX_KEY_LENGTH = 256
_KEY_PATTERN = re.compile(r'^[a-zA-Z0-9_:.\-]+$')


def _validate_key(key: str) -> None:
    """Validate key name. Raises ValueError if invalid."""
    if not key:
        raise ValueError("Key must not be empty")
    if len(key) > MAX_KEY_LENGTH:
        raise ValueError(f"Key exceeds maximum length ({MAX_KEY_LENGTH}), got {len(key)}")
    if not _KEY_PATTERN.match(key):
        raise ValueError(
            f"Key contains invalid characters: {key!r} "
            f"(allowed: a-z, A-Z, 0-9, _, :, ., -)"
        )


def _validate_ttl(ttl: Optional[float]) -> None:
    """Validate TTL value. Raises ValueError if invalid."""
    if ttl is not None:
        if ttl < 0:
            raise ValueError(f"TTL must be non-negative, got {ttl}")
        if ttl > MAX_TTL_SECONDS:
            raise ValueError(f"TTL exceeds maximum ({MAX_TTL_SECONDS}s), got {ttl}")


# ---------------------------------------------------------------------------
# Backend protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class KVBackend(Protocol):
    """Pluggable backend for key-value storage.

    All values are strings (JSON-serialized by the @tool layer).
    Keys are pre-namespaced by the @tool layer (e.g. "default:mykey").
    """

    async def get(self, key: str) -> Optional[str]:
        """Return the stored value, or None if missing/expired."""
        ...

    async def set(self, key: str, value: str, ttl: Optional[float] = None) -> None:
        """Store a value. ttl is seconds until expiry (None = no expiry)."""
        ...

    async def delete(self, key: str) -> bool:
        """Delete a key. Return True if it existed."""
        ...

    async def keys(self, pattern: str = "*") -> list[str]:
        """Return keys matching a glob pattern. Expired keys excluded."""
        ...

    async def close(self) -> None:
        """Release resources."""
        ...


# ---------------------------------------------------------------------------
# SQLite backend
# ---------------------------------------------------------------------------

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS kv_store (
    ns_key TEXT PRIMARY KEY,
    value  TEXT NOT NULL,
    expiry_at REAL
)
"""


class SqliteBackend:
    """KVBackend backed by aiosqlite."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._initialized = False

    async def _ensure_db(self) -> None:
        if self._initialized:
            return
        os.makedirs(os.path.dirname(os.path.abspath(self._db_path)), exist_ok=True)
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(_CREATE_TABLE)
            await db.commit()
        self._initialized = True

    async def get(self, key: str) -> Optional[str]:
        await self._ensure_db()
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "SELECT value, expiry_at FROM kv_store WHERE ns_key = ?", (key,)
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            value, expiry_at = row
            if expiry_at is not None and time.time() > expiry_at:
                await db.execute("DELETE FROM kv_store WHERE ns_key = ?", (key,))
                await db.commit()
                return None
            return value

    async def set(self, key: str, value: str, ttl: Optional[float] = None) -> None:
        _validate_ttl(ttl)
        await self._ensure_db()
        expiry_at = (time.time() + ttl) if ttl else None
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO kv_store (ns_key, value, expiry_at) VALUES (?, ?, ?)",
                (key, value, expiry_at),
            )
            await db.commit()

    async def delete(self, key: str) -> bool:
        await self._ensure_db()
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "DELETE FROM kv_store WHERE ns_key = ?", (key,)
            )
            await db.commit()
            return cursor.rowcount > 0

    async def keys(self, pattern: str = "*") -> list[str]:
        await self._ensure_db()
        if pattern == "*":
            sql_pattern = "%"
        else:
            sql_pattern = pattern.replace("*", "%").replace("?", "_")
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "SELECT ns_key FROM kv_store WHERE ns_key LIKE ? "
                "AND (expiry_at IS NULL OR expiry_at > ?)",
                (sql_pattern, time.time()),
            )
            rows = await cursor.fetchall()
            return [row[0] for row in rows]

    async def close(self) -> None:
        pass  # connections are per-operation


# ---------------------------------------------------------------------------
# In-memory backend
# ---------------------------------------------------------------------------

class MemoryBackend:
    """KVBackend backed by an in-memory dict. No persistence."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[str, Optional[float]]] = {}

    async def get(self, key: str) -> Optional[str]:
        entry = self._store.get(key)
        if entry is None:
            return None
        value, expiry_at = entry
        if expiry_at is not None and time.time() > expiry_at:
            del self._store[key]
            return None
        return value

    async def set(self, key: str, value: str, ttl: Optional[float] = None) -> None:
        _validate_ttl(ttl)
        expiry_at = (time.time() + ttl) if ttl else None
        self._store[key] = (value, expiry_at)

    async def delete(self, key: str) -> bool:
        return self._store.pop(key, None) is not None

    async def keys(self, pattern: str = "*") -> list[str]:
        now = time.time()
        result = []
        for key, (_, expiry_at) in list(self._store.items()):
            if expiry_at is not None and now > expiry_at:
                del self._store[key]
                continue
            if pattern == "*" or fnmatch.fnmatch(key, pattern):
                result.append(key)
        return result

    async def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Global backend management
# ---------------------------------------------------------------------------

_backend: Optional[KVBackend] = None


def configure_keyvalue(
    db_path: Optional[str] = None,
    *,
    backend: Optional[KVBackend] = None,
) -> None:
    """Configure the KV backend.

    Usage:
        configure_keyvalue(db_path="data.db")          # SQLite (default)
        configure_keyvalue(backend=MyRedisBackend())    # Custom backend
    """
    global _backend
    if backend is not None:
        _backend = backend
    elif db_path is not None:
        expanded = os.path.expanduser(db_path)
        if AIOSQLITE_AVAILABLE:
            _backend = SqliteBackend(expanded)
        else:
            logger.warning("aiosqlite not available, falling back to in-memory backend")
            _backend = MemoryBackend()
    else:
        # No args = reset to unconfigured (next access gets MemoryBackend)
        _backend = None


def _get_backend() -> KVBackend:
    """Return the active backend, creating MemoryBackend if unconfigured."""
    global _backend
    if _backend is None:
        _backend = MemoryBackend()
    return _backend


def _reset() -> None:
    """Reset global state (for tests)."""
    global _backend
    _backend = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ns_key(namespace: Optional[str], key: str) -> str:
    return f"{namespace or 'default'}:{key}"


# ---------------------------------------------------------------------------
# @tool interface
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
    _validate_key(key)
    nk = _ns_key(namespace, key)
    raw = await _get_backend().get(nk)
    value = json.loads(raw) if raw is not None else None
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
    _validate_key(key)
    nk = _ns_key(namespace, key)
    try:
        raw = json.dumps(value)
        await _get_backend().set(nk, raw, ttl=ttl)
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
    _validate_key(key)
    nk = _ns_key(namespace, key)
    deleted = await _get_backend().delete(nk)
    return ToolResult(success=True, data=deleted)
