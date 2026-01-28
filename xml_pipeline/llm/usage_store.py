"""
Usage Store — Persistent storage for billing and usage analytics.

Stores UsageEvents to SQLite (default) or PostgreSQL for:
- Historical billing queries
- Usage analytics and reporting
- Audit trails

The store auto-subscribes to UsageTracker on initialization,
persisting all events transparently.

Example:
    from xml_pipeline.llm.usage_store import get_usage_store

    store = await get_usage_store()

    # Query historical usage
    events = await store.query(
        start_time="2025-01-01T00:00:00Z",
        end_time="2025-01-31T23:59:59Z",
        org_id="org-123",
    )

    # Get billing summary
    summary = await store.get_billing_summary(
        org_id="org-123",
        start_time="2025-01-01T00:00:00Z",
    )
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import aiosqlite
    HAS_AIOSQLITE = True
except ImportError:
    HAS_AIOSQLITE = False

from xml_pipeline.llm.usage_tracker import UsageEvent, get_usage_tracker

logger = logging.getLogger(__name__)


# Default database path
DEFAULT_DB_PATH = Path.home() / ".xml-pipeline" / "usage.db"


@dataclass
class BillingSummary:
    """Aggregated billing data for a time period."""
    org_id: Optional[str]
    start_time: str
    end_time: str
    total_tokens: int
    prompt_tokens: int
    completion_tokens: int
    request_count: int
    total_cost: float
    by_model: Dict[str, Dict[str, Any]]
    by_agent: Dict[str, Dict[str, Any]]


class UsageStore:
    """
    Persistent storage for usage events.

    Uses SQLite by default, with async I/O via aiosqlite.
    Automatically subscribes to UsageTracker to capture all events.
    """

    def __init__(self, db_path: Optional[str] = None):
        """
        Initialize the usage store.

        Args:
            db_path: Path to SQLite database. Defaults to ~/.xml-pipeline/usage.db
        """
        if not HAS_AIOSQLITE:
            raise ImportError(
                "aiosqlite is required for usage persistence. "
                "Install with: pip install aiosqlite"
            )

        self._db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

        self._initialized = False
        self._init_lock = asyncio.Lock()

        # Queue for async persistence (events come from sync callback)
        self._queue: asyncio.Queue[UsageEvent] = asyncio.Queue()
        self._writer_task: Optional[asyncio.Task] = None
        self._running = False

    async def initialize(self) -> None:
        """Initialize database schema and start background writer."""
        async with self._init_lock:
            if self._initialized:
                return

            # Create tables
            async with aiosqlite.connect(str(self._db_path)) as db:
                await db.execute("""
                    CREATE TABLE IF NOT EXISTS usage_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        thread_id TEXT NOT NULL,
                        agent_id TEXT,
                        model TEXT NOT NULL,
                        provider TEXT NOT NULL,
                        prompt_tokens INTEGER NOT NULL,
                        completion_tokens INTEGER NOT NULL,
                        total_tokens INTEGER NOT NULL,
                        latency_ms REAL NOT NULL,
                        estimated_cost REAL,
                        metadata TEXT,

                        -- Denormalized for billing queries
                        org_id TEXT,
                        user_id TEXT
                    )
                """)

                # Indexes for common queries
                await db.execute("""
                    CREATE INDEX IF NOT EXISTS idx_usage_timestamp
                    ON usage_events(timestamp)
                """)
                await db.execute("""
                    CREATE INDEX IF NOT EXISTS idx_usage_org_id
                    ON usage_events(org_id)
                """)
                await db.execute("""
                    CREATE INDEX IF NOT EXISTS idx_usage_agent_id
                    ON usage_events(agent_id)
                """)
                await db.execute("""
                    CREATE INDEX IF NOT EXISTS idx_usage_model
                    ON usage_events(model)
                """)

                await db.commit()

            # Start background writer
            self._running = True
            self._writer_task = asyncio.create_task(self._writer_loop())

            # Subscribe to usage tracker
            tracker = get_usage_tracker()
            tracker.subscribe(self._on_usage_event)

            self._initialized = True
            logger.info(f"UsageStore initialized: {self._db_path}")

    def _on_usage_event(self, event: UsageEvent) -> None:
        """
        Callback for UsageTracker events.

        This runs synchronously from the tracker, so we queue
        the event for async persistence.
        """
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning("Usage event queue full, dropping event")

    async def _writer_loop(self) -> None:
        """Background task that writes queued events to database."""
        batch: List[UsageEvent] = []
        batch_timeout = 1.0  # Flush every second or 100 events

        while self._running or not self._queue.empty():
            try:
                # Collect batch
                try:
                    event = await asyncio.wait_for(
                        self._queue.get(),
                        timeout=batch_timeout
                    )
                    batch.append(event)

                    # Drain queue up to batch size
                    while len(batch) < 100:
                        try:
                            event = self._queue.get_nowait()
                            batch.append(event)
                        except asyncio.QueueEmpty:
                            break

                except asyncio.TimeoutError:
                    pass

                # Write batch
                if batch:
                    await self._write_batch(batch)
                    batch = []

            except Exception as e:
                logger.error(f"Usage writer error: {e}")
                await asyncio.sleep(1.0)

    async def _write_batch(self, events: List[UsageEvent]) -> None:
        """Write a batch of events to database."""
        async with aiosqlite.connect(str(self._db_path)) as db:
            await db.executemany(
                """
                INSERT INTO usage_events (
                    timestamp, thread_id, agent_id, model, provider,
                    prompt_tokens, completion_tokens, total_tokens,
                    latency_ms, estimated_cost, metadata, org_id, user_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        e.timestamp,
                        e.thread_id,
                        e.agent_id,
                        e.model,
                        e.provider,
                        e.prompt_tokens,
                        e.completion_tokens,
                        e.total_tokens,
                        e.latency_ms,
                        e.estimated_cost,
                        json.dumps(e.metadata) if e.metadata else None,
                        e.metadata.get("org_id") if e.metadata else None,
                        e.metadata.get("user_id") if e.metadata else None,
                    )
                    for e in events
                ]
            )
            await db.commit()

        logger.debug(f"Persisted {len(events)} usage events")

    async def query(
        self,
        *,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        org_id: Optional[str] = None,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        model: Optional[str] = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """
        Query historical usage events.

        Args:
            start_time: ISO 8601 timestamp (inclusive)
            end_time: ISO 8601 timestamp (inclusive)
            org_id: Filter by organization
            user_id: Filter by user
            agent_id: Filter by agent
            model: Filter by model
            limit: Max results (default 1000)
            offset: Pagination offset

        Returns:
            List of usage event dicts
        """
        conditions = []
        params = []

        if start_time:
            conditions.append("timestamp >= ?")
            params.append(start_time)
        if end_time:
            conditions.append("timestamp <= ?")
            params.append(end_time)
        if org_id:
            conditions.append("org_id = ?")
            params.append(org_id)
        if user_id:
            conditions.append("user_id = ?")
            params.append(user_id)
        if agent_id:
            conditions.append("agent_id = ?")
            params.append(agent_id)
        if model:
            conditions.append("model = ?")
            params.append(model)

        where_clause = " AND ".join(conditions) if conditions else "1=1"

        async with aiosqlite.connect(str(self._db_path)) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                f"""
                SELECT * FROM usage_events
                WHERE {where_clause}
                ORDER BY timestamp DESC
                LIMIT ? OFFSET ?
                """,
                params + [limit, offset]
            )
            rows = await cursor.fetchall()

        return [
            {
                "id": row["id"],
                "timestamp": row["timestamp"],
                "thread_id": row["thread_id"],
                "agent_id": row["agent_id"],
                "model": row["model"],
                "provider": row["provider"],
                "prompt_tokens": row["prompt_tokens"],
                "completion_tokens": row["completion_tokens"],
                "total_tokens": row["total_tokens"],
                "latency_ms": row["latency_ms"],
                "estimated_cost": row["estimated_cost"],
                "metadata": json.loads(row["metadata"]) if row["metadata"] else {},
            }
            for row in rows
        ]

    async def get_billing_summary(
        self,
        *,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        org_id: Optional[str] = None,
    ) -> BillingSummary:
        """
        Get aggregated billing summary for a time period.

        Args:
            start_time: ISO 8601 timestamp (inclusive)
            end_time: ISO 8601 timestamp (inclusive)
            org_id: Filter by organization

        Returns:
            BillingSummary with totals and breakdowns
        """
        conditions = []
        params = []

        if start_time:
            conditions.append("timestamp >= ?")
            params.append(start_time)
        if end_time:
            conditions.append("timestamp <= ?")
            params.append(end_time)
        if org_id:
            conditions.append("org_id = ?")
            params.append(org_id)

        where_clause = " AND ".join(conditions) if conditions else "1=1"

        async with aiosqlite.connect(str(self._db_path)) as db:
            # Overall totals
            cursor = await db.execute(
                f"""
                SELECT
                    COALESCE(SUM(total_tokens), 0) as total_tokens,
                    COALESCE(SUM(prompt_tokens), 0) as prompt_tokens,
                    COALESCE(SUM(completion_tokens), 0) as completion_tokens,
                    COUNT(*) as request_count,
                    COALESCE(SUM(estimated_cost), 0) as total_cost
                FROM usage_events
                WHERE {where_clause}
                """,
                params
            )
            row = await cursor.fetchone()

            totals = {
                "total_tokens": row[0],
                "prompt_tokens": row[1],
                "completion_tokens": row[2],
                "request_count": row[3],
                "total_cost": round(row[4], 4),
            }

            # By model
            cursor = await db.execute(
                f"""
                SELECT
                    model,
                    SUM(total_tokens) as total_tokens,
                    SUM(prompt_tokens) as prompt_tokens,
                    SUM(completion_tokens) as completion_tokens,
                    COUNT(*) as request_count,
                    SUM(estimated_cost) as total_cost
                FROM usage_events
                WHERE {where_clause}
                GROUP BY model
                """,
                params
            )
            by_model = {
                row[0]: {
                    "total_tokens": row[1],
                    "prompt_tokens": row[2],
                    "completion_tokens": row[3],
                    "request_count": row[4],
                    "total_cost": round(row[5] or 0, 4),
                }
                for row in await cursor.fetchall()
            }

            # By agent
            cursor = await db.execute(
                f"""
                SELECT
                    agent_id,
                    SUM(total_tokens) as total_tokens,
                    SUM(prompt_tokens) as prompt_tokens,
                    SUM(completion_tokens) as completion_tokens,
                    COUNT(*) as request_count,
                    SUM(estimated_cost) as total_cost
                FROM usage_events
                WHERE {where_clause} AND agent_id IS NOT NULL
                GROUP BY agent_id
                """,
                params
            )
            by_agent = {
                row[0]: {
                    "total_tokens": row[1],
                    "prompt_tokens": row[2],
                    "completion_tokens": row[3],
                    "request_count": row[4],
                    "total_cost": round(row[5] or 0, 4),
                }
                for row in await cursor.fetchall()
            }

        return BillingSummary(
            org_id=org_id,
            start_time=start_time or "",
            end_time=end_time or datetime.now(timezone.utc).isoformat(),
            total_tokens=totals["total_tokens"],
            prompt_tokens=totals["prompt_tokens"],
            completion_tokens=totals["completion_tokens"],
            request_count=totals["request_count"],
            total_cost=totals["total_cost"],
            by_model=by_model,
            by_agent=by_agent,
        )

    async def get_daily_usage(
        self,
        *,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        org_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get usage aggregated by day for charting.

        Returns:
            List of {date, total_tokens, request_count, total_cost}
        """
        conditions = []
        params = []

        if start_time:
            conditions.append("timestamp >= ?")
            params.append(start_time)
        if end_time:
            conditions.append("timestamp <= ?")
            params.append(end_time)
        if org_id:
            conditions.append("org_id = ?")
            params.append(org_id)

        where_clause = " AND ".join(conditions) if conditions else "1=1"

        async with aiosqlite.connect(str(self._db_path)) as db:
            cursor = await db.execute(
                f"""
                SELECT
                    DATE(timestamp) as date,
                    SUM(total_tokens) as total_tokens,
                    COUNT(*) as request_count,
                    SUM(estimated_cost) as total_cost
                FROM usage_events
                WHERE {where_clause}
                GROUP BY DATE(timestamp)
                ORDER BY date
                """,
                params
            )
            rows = await cursor.fetchall()

        return [
            {
                "date": row[0],
                "total_tokens": row[1],
                "request_count": row[2],
                "total_cost": round(row[3] or 0, 4),
            }
            for row in rows
        ]

    async def count(
        self,
        *,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        org_id: Optional[str] = None,
    ) -> int:
        """Get total count of events matching criteria."""
        conditions = []
        params = []

        if start_time:
            conditions.append("timestamp >= ?")
            params.append(start_time)
        if end_time:
            conditions.append("timestamp <= ?")
            params.append(end_time)
        if org_id:
            conditions.append("org_id = ?")
            params.append(org_id)

        where_clause = " AND ".join(conditions) if conditions else "1=1"

        async with aiosqlite.connect(str(self._db_path)) as db:
            cursor = await db.execute(
                f"SELECT COUNT(*) FROM usage_events WHERE {where_clause}",
                params
            )
            row = await cursor.fetchone()
            return row[0]

    async def close(self) -> None:
        """Shutdown the store, flushing pending writes."""
        self._running = False

        # Unsubscribe from tracker
        tracker = get_usage_tracker()
        tracker.unsubscribe(self._on_usage_event)

        # Wait for writer to finish
        if self._writer_task:
            await self._writer_task
            self._writer_task = None

        logger.info("UsageStore closed")


# =============================================================================
# Global Instance
# =============================================================================

_store: Optional[UsageStore] = None
_store_lock = threading.Lock()


async def get_usage_store(db_path: Optional[str] = None) -> UsageStore:
    """
    Get the global usage store, initializing if needed.

    Args:
        db_path: Optional path to SQLite database

    Returns:
        Initialized UsageStore
    """
    global _store

    if _store is None:
        with _store_lock:
            if _store is None:
                _store = UsageStore(db_path)

    if not _store._initialized:
        await _store.initialize()

    return _store


async def close_usage_store() -> None:
    """Close the global usage store."""
    global _store
    if _store is not None:
        await _store.close()
        _store = None


def reset_usage_store() -> None:
    """Reset global store (for testing)."""
    global _store
    with _store_lock:
        _store = None
