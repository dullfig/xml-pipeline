"""
Tests for key-value store tool (pluggable backend: SQLite + in-memory).
"""

from __future__ import annotations

import json
import time
from unittest.mock import patch

import pytest

from xml_pipeline.tools.keyvalue import (
    KVBackend,
    MemoryBackend,
    SqliteBackend,
    _get_backend,
    _reset,
    configure_keyvalue,
    key_value_delete,
    key_value_get,
    key_value_set,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_kv():
    """Reset KV state before each test."""
    _reset()
    yield
    _reset()


@pytest.fixture
def tmp_db(tmp_path):
    """Configure keyvalue with a temp SQLite DB."""
    db_path = str(tmp_path / "test_kv.db")
    configure_keyvalue(db_path=db_path)
    return db_path


@pytest.fixture
def memory_backend():
    """Return a standalone MemoryBackend."""
    return MemoryBackend()


# ============================================================================
# TestKVBackendProtocol — protocol compliance
# ============================================================================

class TestKVBackendProtocol:
    """Verify that concrete backends satisfy the Protocol."""

    def test_memory_is_kv_backend(self):
        assert isinstance(MemoryBackend(), KVBackend)

    def test_sqlite_is_kv_backend(self):
        assert isinstance(SqliteBackend("/tmp/fake.db"), KVBackend)

    def test_custom_backend_accepted(self):
        """A plain class matching the protocol is accepted."""
        class CustomBackend:
            async def get(self, key): return None
            async def set(self, key, value, ttl=None): pass
            async def delete(self, key): return False
            async def keys(self, pattern="*"): return []
            async def close(self): pass

        assert isinstance(CustomBackend(), KVBackend)


# ============================================================================
# TestPluggableBackend — configure_keyvalue(backend=...)
# ============================================================================

class TestPluggableBackend:
    """Test plugging in a custom backend."""

    async def test_custom_backend_used(self):
        """configure_keyvalue(backend=...) routes all ops through it."""
        calls = []

        class SpyBackend:
            async def get(self, key):
                calls.append(("get", key))
                return json.dumps("spied")
            async def set(self, key, value, ttl=None):
                calls.append(("set", key, value, ttl))
            async def delete(self, key):
                calls.append(("delete", key))
                return True
            async def keys(self, pattern="*"):
                return []
            async def close(self):
                pass

        configure_keyvalue(backend=SpyBackend())

        await key_value_set(key="k", value="v")
        result = await key_value_get(key="k")
        assert result.data == "spied"
        await key_value_delete(key="k")

        assert ("set", "default:k", json.dumps("v"), None) in calls
        assert ("get", "default:k") in calls
        assert ("delete", "default:k") in calls

    async def test_backend_overrides_db_path(self):
        """backend= takes precedence even if db_path was set before."""
        configure_keyvalue(db_path="/tmp/ignored.db")
        mb = MemoryBackend()
        configure_keyvalue(backend=mb)
        assert _get_backend() is mb


# ============================================================================
# TestMemoryBackend — direct backend API
# ============================================================================

class TestMemoryBackend:
    """Test MemoryBackend directly (not through @tool)."""

    async def test_get_set_delete(self, memory_backend):
        mb = memory_backend
        assert await mb.get("k") is None
        await mb.set("k", "v")
        assert await mb.get("k") == "v"
        assert await mb.delete("k") is True
        assert await mb.get("k") is None
        assert await mb.delete("k") is False

    async def test_ttl_expiry(self, memory_backend):
        mb = memory_backend
        await mb.set("k", "v", ttl=1)
        # Force expiry
        mb._store["k"] = ("v", time.time() - 1)
        assert await mb.get("k") is None

    async def test_keys_glob(self, memory_backend):
        mb = memory_backend
        await mb.set("ns1:a", "1")
        await mb.set("ns1:b", "2")
        await mb.set("ns2:a", "3")
        assert sorted(await mb.keys("ns1:*")) == ["ns1:a", "ns1:b"]
        assert await mb.keys("ns2:*") == ["ns2:a"]
        assert len(await mb.keys()) == 3

    async def test_keys_excludes_expired(self, memory_backend):
        mb = memory_backend
        await mb.set("live", "1")
        await mb.set("dead", "2", ttl=1)
        mb._store["dead"] = ("2", time.time() - 1)
        assert await mb.keys() == ["live"]

    async def test_close_is_noop(self, memory_backend):
        mb = memory_backend
        await mb.set("k", "v")
        await mb.close()
        # close doesn't destroy state (reset does that)
        assert await mb.get("k") == "v"


# ============================================================================
# TestKeyValueBasicInMemory — in-memory fallback (no configure)
# ============================================================================

class TestKeyValueBasicInMemory:
    """Test basic get/set/delete with in-memory backend (via @tool)."""

    async def test_set_and_get(self):
        result = await key_value_set(key="foo", value="bar")
        assert result.success is True

        result = await key_value_get(key="foo")
        assert result.success is True
        assert result.data == "bar"

    async def test_get_missing_key(self):
        result = await key_value_get(key="nonexistent")
        assert result.success is True
        assert result.data is None

    async def test_delete_existing_key(self):
        await key_value_set(key="x", value=1)
        result = await key_value_delete(key="x")
        assert result.success is True
        assert result.data is True

        result = await key_value_get(key="x")
        assert result.data is None

    async def test_delete_nonexistent_key(self):
        result = await key_value_delete(key="nope")
        assert result.success is True
        assert result.data is False

    async def test_namespaces_isolate(self):
        await key_value_set(key="k", value="a", namespace="ns1")
        await key_value_set(key="k", value="b", namespace="ns2")

        r1 = await key_value_get(key="k", namespace="ns1")
        r2 = await key_value_get(key="k", namespace="ns2")
        assert r1.data == "a"
        assert r2.data == "b"

    async def test_default_namespace(self):
        await key_value_set(key="k", value="v")
        result = await key_value_get(key="k")
        assert result.data == "v"

    async def test_overwrite_existing_key(self):
        await key_value_set(key="k", value="old")
        await key_value_set(key="k", value="new")
        result = await key_value_get(key="k")
        assert result.data == "new"


# ============================================================================
# TestKeyValueTTL — expiry behavior
# ============================================================================

class TestKeyValueTTL:
    """Test TTL (time-to-live) behavior."""

    async def test_ttl_not_expired(self):
        await key_value_set(key="k", value="v", ttl=3600)
        result = await key_value_get(key="k")
        assert result.data == "v"

    async def test_ttl_expired_in_memory(self):
        await key_value_set(key="k", value="v", ttl=1)
        # Force expiry via backend internals
        backend = _get_backend()
        backend._store["default:k"] = (json.dumps("v"), time.time() - 1)

        result = await key_value_get(key="k")
        assert result.data is None

    async def test_no_ttl_never_expires(self):
        await key_value_set(key="k", value="v")
        result = await key_value_get(key="k")
        assert result.data == "v"


# ============================================================================
# TestKeyValueTypes — JSON round-trip of various types
# ============================================================================

class TestKeyValueTypes:
    """Test storing and retrieving different JSON types."""

    @pytest.mark.parametrize("value", [
        "hello",
        42,
        3.14,
        True,
        False,
        None,
        {"nested": {"a": 1}},
        [1, 2, 3],
        [{"x": 1}, {"x": 2}],
    ])
    async def test_json_roundtrip_in_memory(self, value):
        await key_value_set(key="t", value=value)
        result = await key_value_get(key="t")
        assert result.data == value


# ============================================================================
# TestKeyValueSQLite — SQLite backend
# ============================================================================

class TestKeyValueSQLite:
    """Test SQLite-backed key-value store."""

    async def test_set_and_get(self, tmp_db):
        assert isinstance(_get_backend(), SqliteBackend)
        result = await key_value_set(key="foo", value="bar")
        assert result.success is True

        result = await key_value_get(key="foo")
        assert result.success is True
        assert result.data == "bar"

    async def test_db_file_created(self, tmp_db):
        import os
        await key_value_set(key="k", value="v")
        assert os.path.exists(tmp_db)

    async def test_get_missing_key(self, tmp_db):
        result = await key_value_get(key="nonexistent")
        assert result.success is True
        assert result.data is None

    async def test_delete(self, tmp_db):
        await key_value_set(key="k", value="v")
        result = await key_value_delete(key="k")
        assert result.data is True

        result = await key_value_get(key="k")
        assert result.data is None

    async def test_delete_nonexistent(self, tmp_db):
        result = await key_value_delete(key="nope")
        assert result.data is False

    async def test_namespaces(self, tmp_db):
        await key_value_set(key="k", value="a", namespace="ns1")
        await key_value_set(key="k", value="b", namespace="ns2")

        r1 = await key_value_get(key="k", namespace="ns1")
        r2 = await key_value_get(key="k", namespace="ns2")
        assert r1.data == "a"
        assert r2.data == "b"

    async def test_overwrite(self, tmp_db):
        await key_value_set(key="k", value="old")
        await key_value_set(key="k", value="new")
        result = await key_value_get(key="k")
        assert result.data == "new"

    async def test_ttl_not_expired(self, tmp_db):
        await key_value_set(key="k", value="v", ttl=3600)
        result = await key_value_get(key="k")
        assert result.data == "v"

    async def test_ttl_expired(self, tmp_db):
        """Expired keys should return None on read."""
        await key_value_set(key="k", value="v", ttl=1)
        # Manually expire it by manipulating the DB
        import aiosqlite
        async with aiosqlite.connect(tmp_db) as db:
            await db.execute(
                "UPDATE kv_store SET expiry_at = ? WHERE ns_key = ?",
                (time.time() - 10, "default:k"),
            )
            await db.commit()

        result = await key_value_get(key="k")
        assert result.data is None

    @pytest.mark.parametrize("value", [
        "string",
        42,
        3.14,
        True,
        False,
        None,
        {"nested": {"a": 1}},
        [1, "two", 3.0],
    ])
    async def test_json_roundtrip(self, tmp_db, value):
        await key_value_set(key="t", value=value)
        result = await key_value_get(key="t")
        assert result.data == value

    async def test_persistence_across_calls(self, tmp_db):
        """Data persists between separate configure calls (same DB)."""
        await key_value_set(key="persist", value="yes")

        # Re-configure with same path (simulates restart)
        _reset()
        configure_keyvalue(db_path=tmp_db)

        result = await key_value_get(key="persist")
        assert result.data == "yes"


# ============================================================================
# TestSQLiteBackendKeys — keys() method
# ============================================================================

class TestSQLiteBackendKeys:
    """Test SqliteBackend.keys() directly."""

    async def test_keys_all(self, tmp_db):
        backend = _get_backend()
        await backend.set("ns1:a", json.dumps(1))
        await backend.set("ns1:b", json.dumps(2))
        await backend.set("ns2:c", json.dumps(3))
        result = await backend.keys()
        assert sorted(result) == ["ns1:a", "ns1:b", "ns2:c"]

    async def test_keys_pattern(self, tmp_db):
        backend = _get_backend()
        await backend.set("ns1:a", json.dumps(1))
        await backend.set("ns1:b", json.dumps(2))
        await backend.set("ns2:c", json.dumps(3))
        result = await backend.keys("ns1:*")
        assert sorted(result) == ["ns1:a", "ns1:b"]

    async def test_keys_excludes_expired(self, tmp_db):
        backend = _get_backend()
        await backend.set("live", json.dumps(1))
        await backend.set("dead", json.dumps(2), ttl=1)
        # Manually expire
        import aiosqlite
        async with aiosqlite.connect(tmp_db) as db:
            await db.execute(
                "UPDATE kv_store SET expiry_at = ? WHERE ns_key = ?",
                (time.time() - 10, "dead"),
            )
            await db.commit()
        result = await backend.keys()
        assert result == ["live"]


# ============================================================================
# TestKeyValueFallback — behavior when aiosqlite unavailable
# ============================================================================

class TestKeyValueFallback:
    """Test in-memory fallback when aiosqlite is not available."""

    async def test_fallback_without_configure(self):
        """Without configure, uses in-memory store."""
        assert isinstance(_get_backend(), MemoryBackend)
        await key_value_set(key="k", value="v")
        result = await key_value_get(key="k")
        assert result.data == "v"

    async def test_fallback_when_aiosqlite_missing(self):
        """When aiosqlite is not available, falls back to in-memory."""
        with patch("xml_pipeline.tools.keyvalue.AIOSQLITE_AVAILABLE", False):
            configure_keyvalue(db_path="/tmp/fake.db")
            assert isinstance(_get_backend(), MemoryBackend)
            await key_value_set(key="k", value="v")
            result = await key_value_get(key="k")
            assert result.data == "v"


# ============================================================================
# #51 — Key name/length validation
# ============================================================================

class TestKeyValidation:
    """Test key name and length restrictions."""

    async def test_valid_key_accepted(self):
        result = await key_value_set(key="my_key-1.0:ns", value="v")
        assert result.success is True

    async def test_empty_key_rejected(self):
        """Empty key should be rejected (ValueError caught by @tool)."""
        from xml_pipeline.tools.keyvalue import _validate_key
        with pytest.raises(ValueError, match="must not be empty"):
            _validate_key("")

    async def test_key_too_long_rejected(self):
        """Key exceeding 256 chars should be rejected."""
        from xml_pipeline.tools.keyvalue import _validate_key
        with pytest.raises(ValueError, match="exceeds maximum length"):
            _validate_key("a" * 257)

    async def test_key_with_spaces_rejected(self):
        from xml_pipeline.tools.keyvalue import _validate_key
        with pytest.raises(ValueError, match="invalid characters"):
            _validate_key("has space")

    async def test_key_with_special_chars_rejected(self):
        from xml_pipeline.tools.keyvalue import _validate_key
        with pytest.raises(ValueError, match="invalid characters"):
            _validate_key("key;drop table")

    async def test_key_at_max_length_accepted(self):
        key = "a" * 256
        result = await key_value_set(key=key, value="v")
        assert result.success is True

    async def test_key_with_slash_rejected(self):
        from xml_pipeline.tools.keyvalue import _validate_key
        with pytest.raises(ValueError, match="invalid characters"):
            _validate_key("path/to/key")
