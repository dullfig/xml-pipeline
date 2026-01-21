"""
Tests for shared backend implementations.

Tests InMemoryBackend and ManagerBackend.
Redis tests require a running Redis server (skipped if not available).
"""

import pytest
from dataclasses import dataclass

from xml_pipeline.memory.memory_backend import InMemoryBackend
from xml_pipeline.memory.shared_backend import (
    BackendConfig,
    serialize_slot,
    deserialize_slot,
)
from xml_pipeline.memory.context_buffer import BufferSlot, SlotMetadata


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def memory_backend():
    """Create fresh in-memory backend."""
    backend = InMemoryBackend()
    yield backend
    backend.close()


@pytest.fixture
def manager_backend():
    """Create fresh manager backend."""
    from xml_pipeline.memory.manager_backend import ManagerBackend

    backend = ManagerBackend()
    yield backend
    backend.close()


def redis_available() -> bool:
    """Check if Redis is available."""
    try:
        import redis

        client = redis.from_url("redis://localhost:6379")
        client.ping()
        client.close()
        return True
    except Exception:
        return False


@pytest.fixture
def redis_backend():
    """Create fresh Redis backend (skipped if Redis not available)."""
    if not redis_available():
        pytest.skip("Redis not available")

    from xml_pipeline.memory.redis_backend import RedisBackend

    backend = RedisBackend(
        url="redis://localhost:6379",
        prefix="xp_test:",
        ttl=300,
    )
    # Clear test keys
    backend.buffer_clear()
    backend.registry_clear()
    yield backend
    # Cleanup
    backend.buffer_clear()
    backend.registry_clear()
    backend.close()


# ============================================================================
# Sample data
# ============================================================================


@dataclass
class SamplePayload:
    """Sample payload for testing."""

    message: str
    value: int


def make_slot(thread_id: str, index: int = 0) -> BufferSlot:
    """Create a sample buffer slot."""
    payload = SamplePayload(message="hello", value=42)
    metadata = SlotMetadata(
        thread_id=thread_id,
        from_id="sender",
        to_id="receiver",
        slot_index=index,
        timestamp="2024-01-15T00:00:00Z",
        payload_type="SamplePayload",
    )
    return BufferSlot(payload=payload, metadata=metadata)


# ============================================================================
# InMemoryBackend Tests
# ============================================================================


class TestInMemoryBackend:
    """Tests for InMemoryBackend."""

    def test_buffer_append_and_get(self, memory_backend):
        """Test appending and retrieving buffer slots."""
        slot = make_slot("thread-1")
        slot_bytes = serialize_slot(slot)

        # Append
        index = memory_backend.buffer_append("thread-1", slot_bytes)
        assert index == 0

        # Append another
        index2 = memory_backend.buffer_append("thread-1", slot_bytes)
        assert index2 == 1

        # Get all
        slots = memory_backend.buffer_get_thread("thread-1")
        assert len(slots) == 2

        # Deserialize and verify
        retrieved = deserialize_slot(slots[0])
        assert retrieved.metadata.thread_id == "thread-1"
        assert retrieved.payload.message == "hello"

    def test_buffer_get_slot(self, memory_backend):
        """Test getting specific slot by index."""
        slot = make_slot("thread-1")
        slot_bytes = serialize_slot(slot)

        memory_backend.buffer_append("thread-1", slot_bytes)
        memory_backend.buffer_append("thread-1", slot_bytes)

        # Get specific slot
        data = memory_backend.buffer_get_slot("thread-1", 1)
        assert data is not None

        # Non-existent index
        data = memory_backend.buffer_get_slot("thread-1", 999)
        assert data is None

    def test_buffer_thread_exists(self, memory_backend):
        """Test thread existence check."""
        assert not memory_backend.buffer_thread_exists("thread-1")

        slot_bytes = serialize_slot(make_slot("thread-1"))
        memory_backend.buffer_append("thread-1", slot_bytes)

        assert memory_backend.buffer_thread_exists("thread-1")

    def test_buffer_delete_thread(self, memory_backend):
        """Test deleting thread buffer."""
        slot_bytes = serialize_slot(make_slot("thread-1"))
        memory_backend.buffer_append("thread-1", slot_bytes)

        assert memory_backend.buffer_delete_thread("thread-1")
        assert not memory_backend.buffer_thread_exists("thread-1")
        assert not memory_backend.buffer_delete_thread("thread-1")  # Already deleted

    def test_buffer_list_threads(self, memory_backend):
        """Test listing all threads."""
        slot_bytes = serialize_slot(make_slot("thread-1"))
        memory_backend.buffer_append("thread-1", slot_bytes)
        memory_backend.buffer_append("thread-2", slot_bytes)

        threads = memory_backend.buffer_list_threads()
        assert set(threads) == {"thread-1", "thread-2"}

    def test_registry_set_and_get(self, memory_backend):
        """Test registry set and get operations."""
        memory_backend.registry_set("a.b.c", "uuid-123")

        # Get UUID from chain
        uuid = memory_backend.registry_get_uuid("a.b.c")
        assert uuid == "uuid-123"

        # Get chain from UUID
        chain = memory_backend.registry_get_chain("uuid-123")
        assert chain == "a.b.c"

    def test_registry_delete(self, memory_backend):
        """Test registry delete."""
        memory_backend.registry_set("a.b.c", "uuid-123")

        assert memory_backend.registry_delete("uuid-123")
        assert memory_backend.registry_get_uuid("a.b.c") is None
        assert memory_backend.registry_get_chain("uuid-123") is None
        assert not memory_backend.registry_delete("uuid-123")  # Already deleted

    def test_registry_list_all(self, memory_backend):
        """Test listing all registry entries."""
        memory_backend.registry_set("a.b", "uuid-1")
        memory_backend.registry_set("x.y.z", "uuid-2")

        all_entries = memory_backend.registry_list_all()
        assert all_entries == {"uuid-1": "a.b", "uuid-2": "x.y.z"}

    def test_registry_clear(self, memory_backend):
        """Test clearing registry."""
        memory_backend.registry_set("a.b", "uuid-1")
        memory_backend.registry_clear()

        assert memory_backend.registry_list_all() == {}


# ============================================================================
# ManagerBackend Tests
# ============================================================================


class TestManagerBackend:
    """Tests for ManagerBackend (multiprocessing.Manager)."""

    def test_buffer_append_and_get(self, manager_backend):
        """Test appending and retrieving buffer slots."""
        slot = make_slot("thread-1")
        slot_bytes = serialize_slot(slot)

        index = manager_backend.buffer_append("thread-1", slot_bytes)
        assert index == 0

        slots = manager_backend.buffer_get_thread("thread-1")
        assert len(slots) == 1

    def test_registry_operations(self, manager_backend):
        """Test registry operations via Manager."""
        manager_backend.registry_set("a.b.c", "uuid-123")

        uuid = manager_backend.registry_get_uuid("a.b.c")
        assert uuid == "uuid-123"

        chain = manager_backend.registry_get_chain("uuid-123")
        assert chain == "a.b.c"


# ============================================================================
# RedisBackend Tests
# ============================================================================


@pytest.mark.skipif(not redis_available(), reason="Redis not available")
class TestRedisBackend:
    """Tests for RedisBackend (requires running Redis)."""

    def test_buffer_append_and_get(self, redis_backend):
        """Test appending and retrieving buffer slots."""
        slot = make_slot("thread-1")
        slot_bytes = serialize_slot(slot)

        index = redis_backend.buffer_append("thread-1", slot_bytes)
        assert index == 0

        slots = redis_backend.buffer_get_thread("thread-1")
        assert len(slots) == 1

        retrieved = deserialize_slot(slots[0])
        assert retrieved.metadata.thread_id == "thread-1"

    def test_buffer_thread_len(self, redis_backend):
        """Test getting thread length."""
        slot_bytes = serialize_slot(make_slot("thread-1"))

        redis_backend.buffer_append("thread-1", slot_bytes)
        redis_backend.buffer_append("thread-1", slot_bytes)
        redis_backend.buffer_append("thread-1", slot_bytes)

        assert redis_backend.buffer_thread_len("thread-1") == 3

    def test_registry_operations(self, redis_backend):
        """Test registry operations via Redis."""
        redis_backend.registry_set("console.router.greeter", "uuid-abc")

        uuid = redis_backend.registry_get_uuid("console.router.greeter")
        assert uuid == "uuid-abc"

        chain = redis_backend.registry_get_chain("uuid-abc")
        assert chain == "console.router.greeter"

    def test_registry_delete(self, redis_backend):
        """Test registry delete removes both directions."""
        redis_backend.registry_set("a.b", "uuid-123")

        assert redis_backend.registry_delete("uuid-123")
        assert redis_backend.registry_get_uuid("a.b") is None
        assert redis_backend.registry_get_chain("uuid-123") is None

    def test_ping(self, redis_backend):
        """Test Redis ping."""
        assert redis_backend.ping()

    def test_info(self, redis_backend):
        """Test backend info."""
        slot_bytes = serialize_slot(make_slot("thread-1"))
        redis_backend.buffer_append("thread-1", slot_bytes)
        redis_backend.registry_set("a.b", "uuid-1")

        info = redis_backend.info()
        assert "buffer_threads" in info
        assert "registry_entries" in info


# ============================================================================
# Integration: ContextBuffer with SharedBackend
# ============================================================================


class TestContextBufferWithBackend:
    """Test ContextBuffer using shared backends."""

    def test_context_buffer_with_memory_backend(self):
        """Test ContextBuffer works with in-memory backend."""
        from xml_pipeline.memory.context_buffer import ContextBuffer

        backend = InMemoryBackend()
        buffer = ContextBuffer(backend=backend)

        assert buffer.is_shared

        # Append slot
        slot = buffer.append(
            thread_id="test-thread",
            payload=SamplePayload(message="hello", value=42),
            from_id="sender",
            to_id="receiver",
        )

        assert slot.thread_id == "test-thread"
        assert slot.payload.message == "hello"

        # Get thread
        thread = buffer.get_thread("test-thread")
        assert thread is not None
        assert len(thread) == 1

        # Get stats
        stats = buffer.get_stats()
        assert stats["thread_count"] == 1
        assert stats["backend"] == "shared"

        backend.close()


# ============================================================================
# Integration: ThreadRegistry with SharedBackend
# ============================================================================


class TestThreadRegistryWithBackend:
    """Test ThreadRegistry using shared backends."""

    def test_thread_registry_with_memory_backend(self):
        """Test ThreadRegistry works with in-memory backend."""
        from xml_pipeline.message_bus.thread_registry import ThreadRegistry

        backend = InMemoryBackend()
        registry = ThreadRegistry(backend=backend)

        assert registry.is_shared

        # Initialize root
        root_uuid = registry.initialize_root("test-organism")
        assert root_uuid is not None
        assert registry.root_chain == "system.test-organism"

        # Get or create chain
        uuid = registry.get_or_create("a.b.c")
        assert uuid is not None

        # Lookup
        chain = registry.lookup(uuid)
        assert chain == "a.b.c"

        # Extend chain
        new_uuid = registry.extend_chain(uuid, "d")
        new_chain = registry.lookup(new_uuid)
        assert new_chain == "a.b.c.d"

        # Prune for response
        target, pruned_uuid = registry.prune_for_response(new_uuid)
        assert target == "c"
        assert registry.lookup(pruned_uuid) == "a.b.c"

        backend.close()
