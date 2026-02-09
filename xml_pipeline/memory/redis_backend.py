"""
redis_backend.py — Redis implementation of SharedBackend.

Uses Redis for distributed shared state, enabling:
- Multi-tenant deployments (key prefix isolation)
- Automatic TTL-based garbage collection
- Cross-machine state sharing

Key schema:
- Buffer slots: `{prefix}buffer:{thread_id}` → Redis LIST of pickled slots
- Registry chain→uuid: `{prefix}chain:{chain}` → uuid string
- Registry uuid→chain: `{prefix}uuid:{uuid}` → chain string

Dependencies:
    pip install redis
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

try:
    import redis
except ImportError:
    redis = None  # type: ignore

logger = logging.getLogger(__name__)


class RedisBackend:
    """
    Redis-backed shared state for multi-process deployments.

    All operations are synchronous (blocking). For async contexts,
    wrap calls in asyncio.to_thread() or use a connection pool.
    """

    def __init__(
        self,
        url: str = "redis://localhost:6379",
        prefix: str = "xp:",
        ttl: int = 86400,
    ) -> None:
        """
        Initialize Redis backend.

        Args:
            url: Redis connection URL
            prefix: Key prefix for multi-tenancy isolation
            ttl: TTL in seconds for automatic cleanup (0 = no TTL)
        """
        if redis is None:
            raise ImportError(
                "redis package not installed. Install with: pip install redis"
            )

        self.url = url
        self.prefix = prefix
        self.ttl = ttl

        # Connect to Redis
        self._client = redis.from_url(url, decode_responses=False)

        # Verify connection
        try:
            self._client.ping()
            logger.info(f"Redis backend connected: {url}")
        except redis.ConnectionError as e:
            logger.error(f"Redis connection failed: {e}")
            raise

    def _buffer_key(self, thread_id: str) -> str:
        """Get Redis key for a thread's buffer."""
        return f"{self.prefix}buffer:{thread_id}"

    def _chain_key(self, chain: str) -> str:
        """Get Redis key for chain→uuid mapping."""
        return f"{self.prefix}chain:{chain}"

    def _uuid_key(self, uuid: str) -> str:
        """Get Redis key for uuid→chain mapping."""
        return f"{self.prefix}uuid:{uuid}"

    # =========================================================================
    # Context Buffer Operations
    # =========================================================================

    def buffer_append(self, thread_id: str, slot_data: bytes) -> int:
        """Append a slot to a thread's buffer."""
        key = self._buffer_key(thread_id)

        # RPUSH returns new length, so index = length - 1
        new_len = self._client.rpush(key, slot_data)

        # Set/refresh TTL
        if self.ttl > 0:
            self._client.expire(key, self.ttl)

        return new_len - 1

    def buffer_get_thread(self, thread_id: str) -> List[bytes]:
        """Get all slots for a thread."""
        key = self._buffer_key(thread_id)
        # LRANGE returns all elements (0, -1 = full list)
        result = self._client.lrange(key, 0, -1)
        return result if result else []

    def buffer_get_slot(self, thread_id: str, index: int) -> Optional[bytes]:
        """Get a specific slot by index."""
        key = self._buffer_key(thread_id)
        # LINDEX returns element at index, or None
        return self._client.lindex(key, index)

    def buffer_thread_len(self, thread_id: str) -> int:
        """Get number of slots in a thread."""
        key = self._buffer_key(thread_id)
        return self._client.llen(key)

    def buffer_thread_exists(self, thread_id: str) -> bool:
        """Check if a thread has any slots."""
        key = self._buffer_key(thread_id)
        return self._client.exists(key) > 0

    def buffer_delete_thread(self, thread_id: str) -> bool:
        """Delete all slots for a thread."""
        key = self._buffer_key(thread_id)
        deleted = self._client.delete(key)
        return deleted > 0

    def buffer_list_threads(self) -> List[str]:
        """List all thread IDs with slots."""
        pattern = f"{self.prefix}buffer:*"
        keys = self._client.keys(pattern)
        prefix_len = len(f"{self.prefix}buffer:")
        return [k.decode() if isinstance(k, bytes) else k for k in keys]

    def buffer_clear(self) -> None:
        """Clear all buffer data."""
        pattern = f"{self.prefix}buffer:*"
        keys = self._client.keys(pattern)
        if keys:
            self._client.delete(*keys)

    # =========================================================================
    # Thread Registry Operations
    # =========================================================================

    def registry_set(self, chain: str, uuid: str) -> None:
        """Set bidirectional mapping: chain ↔ uuid."""
        chain_key = self._chain_key(chain)
        uuid_key = self._uuid_key(uuid)

        # Use pipeline for atomicity
        pipe = self._client.pipeline()
        pipe.set(chain_key, uuid.encode())
        pipe.set(uuid_key, chain.encode())

        if self.ttl > 0:
            pipe.expire(chain_key, self.ttl)
            pipe.expire(uuid_key, self.ttl)

        pipe.execute()

    def registry_get_uuid(self, chain: str) -> Optional[str]:
        """Get UUID for a chain."""
        key = self._chain_key(chain)
        value = self._client.get(key)
        if value:
            return value.decode() if isinstance(value, bytes) else value
        return None

    def registry_get_chain(self, uuid: str) -> Optional[str]:
        """Get chain for a UUID."""
        key = self._uuid_key(uuid)
        value = self._client.get(key)
        if value:
            return value.decode() if isinstance(value, bytes) else value
        return None

    def registry_delete(self, uuid: str) -> bool:
        """Delete mapping by UUID."""
        uuid_key = self._uuid_key(uuid)

        # First get chain to delete both directions
        chain = self._client.get(uuid_key)
        if not chain:
            return False

        if isinstance(chain, bytes):
            chain = chain.decode()

        chain_key = self._chain_key(chain)

        # Delete both keys
        deleted = self._client.delete(uuid_key, chain_key)
        return deleted > 0

    def registry_list_all(self) -> Dict[str, str]:
        """Get all UUID → chain mappings."""
        pattern = f"{self.prefix}uuid:*"
        keys = self._client.keys(pattern)

        result = {}
        prefix_len = len(f"{self.prefix}uuid:")

        for key in keys:
            if isinstance(key, bytes):
                key = key.decode()
            uuid = key[prefix_len:]
            chain = self._client.get(key)
            if chain:
                if isinstance(chain, bytes):
                    chain = chain.decode()
                result[uuid] = chain

        return result

    def registry_clear(self) -> None:
        """Clear all registry data."""
        chain_pattern = f"{self.prefix}chain:*"
        uuid_pattern = f"{self.prefix}uuid:*"

        chain_keys = self._client.keys(chain_pattern)
        uuid_keys = self._client.keys(uuid_pattern)

        all_keys = list(chain_keys) + list(uuid_keys)
        if all_keys:
            self._client.delete(*all_keys)

    # =========================================================================
    # Lifecycle
    # =========================================================================

    def close(self) -> None:
        """Close Redis connection."""
        if self._client:
            self._client.close()
            logger.info("Redis backend closed")

    # =========================================================================
    # Health Check
    # =========================================================================

    def ping(self) -> bool:
        """Check if Redis is reachable."""
        try:
            return self._client.ping()
        except Exception:
            return False

    def info(self) -> Dict[str, int]:
        """Get backend statistics."""
        buffer_keys = len(self._client.keys(f"{self.prefix}buffer:*"))
        uuid_keys = len(self._client.keys(f"{self.prefix}uuid:*"))

        return {
            "buffer_threads": buffer_keys,
            "registry_entries": uuid_keys,
            "ttl_seconds": self.ttl,
        }
