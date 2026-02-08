"""
Tests for key-value store tool (SQLite backend + in-memory fallback).
"""

from __future__ import annotations

import asyncio
import os
import time
from unittest.mock import patch

import pytest

from xml_pipeline.tools.keyvalue import (
    _mem_delete,
    _mem_get,
    _mem_set,
    _reset,
    _use_sqlite,
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


# ============================================================================
# TestKeyValueBasicInMemory — in-memory fallback (no configure)
# ============================================================================

class TestKeyValueBasicInMemory:
    """Test basic get/set/delete with in-memory backend."""

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
        # Without namespace defaults to "default"
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
        # Directly set with a past expiry
        _mem_set("default:k", "v", ttl=None)
        from xml_pipeline.tools.keyvalue import _mem_store
        _mem_store["default:k"] = ("v", time.time() - 1)

        result = await key_value_get(key="k")
        assert result.data is None

    async def test_no_ttl_never_expires(self):
        await key_value_set(key="k", value="v")
        # Should be accessible indefinitely
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
        assert _use_sqlite() is True
        result = await key_value_set(key="foo", value="bar")
        assert result.success is True

        result = await key_value_get(key="foo")
        assert result.success is True
        assert result.data == "bar"

    async def test_db_file_created(self, tmp_db):
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
        # Insert with a very short TTL, then fake time
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
# TestKeyValueFallback — behavior when aiosqlite unavailable
# ============================================================================

class TestKeyValueFallback:
    """Test in-memory fallback when aiosqlite is not available."""

    async def test_fallback_without_configure(self):
        """Without configure, uses in-memory store."""
        assert _use_sqlite() is False
        await key_value_set(key="k", value="v")
        result = await key_value_get(key="k")
        assert result.data == "v"

    async def test_fallback_when_aiosqlite_missing(self):
        """When aiosqlite is not available, falls back to in-memory."""
        with patch("xml_pipeline.tools.keyvalue.AIOSQLITE_AVAILABLE", False):
            configure_keyvalue(db_path="/tmp/fake.db")
            assert _use_sqlite() is False
            await key_value_set(key="k", value="v")
            result = await key_value_get(key="k")
            assert result.data == "v"
