"""
Tests for usage persistence layer.

Tests UsageStore's SQLite persistence, querying, and billing summaries.
"""

from __future__ import annotations

import asyncio
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from xml_pipeline.llm.usage_tracker import UsageEvent, get_usage_tracker, reset_usage_tracker
from xml_pipeline.llm.usage_store import UsageStore, reset_usage_store


@pytest.fixture
def temp_db():
    """Create a temporary database file."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        yield f.name
    # Cleanup happens automatically


@pytest.fixture
async def store(temp_db):
    """Create and initialize a UsageStore with temp database."""
    reset_usage_tracker()
    reset_usage_store()

    s = UsageStore(db_path=temp_db)
    await s.initialize()
    yield s
    await s.close()


class TestUsageStoreBasics:
    """Test basic store operations."""

    async def test_initialize_creates_table(self, temp_db):
        """Store initialization creates the usage_events table."""
        reset_usage_store()
        store = UsageStore(db_path=temp_db)
        await store.initialize()

        # Query should work (table exists)
        events = await store.query(limit=10)
        assert events == []

        await store.close()

    async def test_store_initializes_once(self, store):
        """Multiple initialize calls are idempotent."""
        # Should not raise
        await store.initialize()
        await store.initialize()

    async def test_empty_query(self, store):
        """Query on empty store returns empty list."""
        events = await store.query()
        assert events == []

    async def test_empty_count(self, store):
        """Count on empty store returns 0."""
        count = await store.count()
        assert count == 0


class TestEventPersistence:
    """Test event persistence via subscriber pattern."""

    async def test_event_persisted_via_tracker(self, store):
        """Events recorded in tracker are persisted to store."""
        tracker = get_usage_tracker()

        # Record an event
        tracker.record(
            thread_id="test-thread-1",
            agent_id="greeter",
            model="grok-4.1",
            provider="xai",
            prompt_tokens=100,
            completion_tokens=50,
            latency_ms=250.5,
            metadata={"org_id": "org-123"},
        )

        # Give background writer time to flush
        await asyncio.sleep(1.5)

        # Query should find the event
        events = await store.query()
        assert len(events) == 1

        event = events[0]
        assert event["thread_id"] == "test-thread-1"
        assert event["agent_id"] == "greeter"
        assert event["model"] == "grok-4.1"
        assert event["provider"] == "xai"
        assert event["prompt_tokens"] == 100
        assert event["completion_tokens"] == 50
        assert event["total_tokens"] == 150
        assert event["latency_ms"] == 250.5

    async def test_multiple_events_persisted(self, store):
        """Multiple events are all persisted."""
        tracker = get_usage_tracker()

        for i in range(5):
            tracker.record(
                thread_id=f"thread-{i}",
                agent_id="agent",
                model="grok-4.1",
                provider="xai",
                prompt_tokens=100 * (i + 1),
                completion_tokens=50 * (i + 1),
                latency_ms=100.0,
            )

        await asyncio.sleep(1.5)

        events = await store.query()
        assert len(events) == 5

    async def test_event_with_cost_estimate(self, store):
        """Estimated cost is persisted."""
        tracker = get_usage_tracker()

        # grok-4.1 has pricing in MODEL_COSTS
        tracker.record(
            thread_id="cost-thread",
            agent_id="agent",
            model="grok-4.1",
            provider="xai",
            prompt_tokens=1000,
            completion_tokens=500,
            latency_ms=100.0,
        )

        await asyncio.sleep(1.5)

        events = await store.query()
        assert len(events) == 1
        assert events[0]["estimated_cost"] is not None
        assert events[0]["estimated_cost"] > 0


class TestQueryFiltering:
    """Test query filtering options."""

    async def test_filter_by_agent(self, store):
        """Filter events by agent_id."""
        tracker = get_usage_tracker()

        tracker.record(
            thread_id="t1", agent_id="greeter", model="grok-4.1",
            provider="xai", prompt_tokens=100, completion_tokens=50, latency_ms=100.0,
        )
        tracker.record(
            thread_id="t2", agent_id="shouter", model="grok-4.1",
            provider="xai", prompt_tokens=100, completion_tokens=50, latency_ms=100.0,
        )

        await asyncio.sleep(1.5)

        events = await store.query(agent_id="greeter")
        assert len(events) == 1
        assert events[0]["agent_id"] == "greeter"

    async def test_filter_by_model(self, store):
        """Filter events by model."""
        tracker = get_usage_tracker()

        tracker.record(
            thread_id="t1", agent_id="agent", model="grok-4.1",
            provider="xai", prompt_tokens=100, completion_tokens=50, latency_ms=100.0,
        )
        tracker.record(
            thread_id="t2", agent_id="agent", model="claude-sonnet-4",
            provider="anthropic", prompt_tokens=100, completion_tokens=50, latency_ms=100.0,
        )

        await asyncio.sleep(1.5)

        events = await store.query(model="grok-4.1")
        assert len(events) == 1
        assert events[0]["model"] == "grok-4.1"

    async def test_filter_by_org_id(self, store):
        """Filter events by org_id in metadata."""
        tracker = get_usage_tracker()

        tracker.record(
            thread_id="t1", agent_id="agent", model="grok-4.1",
            provider="xai", prompt_tokens=100, completion_tokens=50, latency_ms=100.0,
            metadata={"org_id": "org-A"},
        )
        tracker.record(
            thread_id="t2", agent_id="agent", model="grok-4.1",
            provider="xai", prompt_tokens=100, completion_tokens=50, latency_ms=100.0,
            metadata={"org_id": "org-B"},
        )

        await asyncio.sleep(1.5)

        events = await store.query(org_id="org-A")
        assert len(events) == 1
        assert events[0]["thread_id"] == "t1"

    async def test_pagination(self, store):
        """Test limit and offset for pagination."""
        tracker = get_usage_tracker()

        for i in range(10):
            tracker.record(
                thread_id=f"t{i:02d}", agent_id="agent", model="grok-4.1",
                provider="xai", prompt_tokens=100, completion_tokens=50, latency_ms=100.0,
            )

        await asyncio.sleep(1.5)

        # Get first page
        page1 = await store.query(limit=3, offset=0)
        assert len(page1) == 3

        # Get second page
        page2 = await store.query(limit=3, offset=3)
        assert len(page2) == 3

        # Different events
        assert page1[0]["thread_id"] != page2[0]["thread_id"]


class TestBillingSummary:
    """Test billing summary aggregation."""

    async def test_billing_totals(self, store):
        """Billing summary calculates correct totals."""
        tracker = get_usage_tracker()

        tracker.record(
            thread_id="t1", agent_id="agent", model="grok-4.1",
            provider="xai", prompt_tokens=1000, completion_tokens=500, latency_ms=100.0,
        )
        tracker.record(
            thread_id="t2", agent_id="agent", model="grok-4.1",
            provider="xai", prompt_tokens=2000, completion_tokens=1000, latency_ms=100.0,
        )

        await asyncio.sleep(1.5)

        summary = await store.get_billing_summary()

        assert summary.total_tokens == 4500  # 1500 + 3000
        assert summary.prompt_tokens == 3000
        assert summary.completion_tokens == 1500
        assert summary.request_count == 2

    async def test_billing_by_model(self, store):
        """Billing summary includes breakdown by model."""
        tracker = get_usage_tracker()

        tracker.record(
            thread_id="t1", agent_id="agent", model="grok-4.1",
            provider="xai", prompt_tokens=1000, completion_tokens=500, latency_ms=100.0,
        )
        tracker.record(
            thread_id="t2", agent_id="agent", model="claude-sonnet-4",
            provider="anthropic", prompt_tokens=2000, completion_tokens=1000, latency_ms=100.0,
        )

        await asyncio.sleep(1.5)

        summary = await store.get_billing_summary()

        assert "grok-4.1" in summary.by_model
        assert "claude-sonnet-4" in summary.by_model
        assert summary.by_model["grok-4.1"]["total_tokens"] == 1500
        assert summary.by_model["claude-sonnet-4"]["total_tokens"] == 3000

    async def test_billing_by_agent(self, store):
        """Billing summary includes breakdown by agent."""
        tracker = get_usage_tracker()

        tracker.record(
            thread_id="t1", agent_id="greeter", model="grok-4.1",
            provider="xai", prompt_tokens=1000, completion_tokens=500, latency_ms=100.0,
        )
        tracker.record(
            thread_id="t2", agent_id="shouter", model="grok-4.1",
            provider="xai", prompt_tokens=500, completion_tokens=250, latency_ms=100.0,
        )

        await asyncio.sleep(1.5)

        summary = await store.get_billing_summary()

        assert "greeter" in summary.by_agent
        assert "shouter" in summary.by_agent
        assert summary.by_agent["greeter"]["total_tokens"] == 1500
        assert summary.by_agent["shouter"]["total_tokens"] == 750

    async def test_billing_filtered_by_org(self, store):
        """Billing summary can be filtered by org_id."""
        tracker = get_usage_tracker()

        tracker.record(
            thread_id="t1", agent_id="agent", model="grok-4.1",
            provider="xai", prompt_tokens=1000, completion_tokens=500, latency_ms=100.0,
            metadata={"org_id": "org-A"},
        )
        tracker.record(
            thread_id="t2", agent_id="agent", model="grok-4.1",
            provider="xai", prompt_tokens=2000, completion_tokens=1000, latency_ms=100.0,
            metadata={"org_id": "org-B"},
        )

        await asyncio.sleep(1.5)

        summary_a = await store.get_billing_summary(org_id="org-A")
        summary_b = await store.get_billing_summary(org_id="org-B")

        assert summary_a.total_tokens == 1500
        assert summary_b.total_tokens == 3000


class TestDailyUsage:
    """Test daily usage aggregation."""

    async def test_daily_aggregation(self, store):
        """Daily usage aggregates by date."""
        tracker = get_usage_tracker()

        # Record multiple events (all same day since timestamps are auto-generated)
        for i in range(3):
            tracker.record(
                thread_id=f"t{i}", agent_id="agent", model="grok-4.1",
                provider="xai", prompt_tokens=100, completion_tokens=50, latency_ms=100.0,
            )

        await asyncio.sleep(1.5)

        days = await store.get_daily_usage()

        assert len(days) == 1  # All same day
        assert days[0]["total_tokens"] == 450
        assert days[0]["request_count"] == 3

    async def test_daily_returns_date(self, store):
        """Daily usage includes date field."""
        tracker = get_usage_tracker()

        tracker.record(
            thread_id="t1", agent_id="agent", model="grok-4.1",
            provider="xai", prompt_tokens=100, completion_tokens=50, latency_ms=100.0,
        )

        await asyncio.sleep(1.5)

        days = await store.get_daily_usage()

        assert len(days) == 1
        # Date should be in YYYY-MM-DD format
        assert len(days[0]["date"]) == 10
        assert days[0]["date"].count("-") == 2


class TestStoreLifecycle:
    """Test store lifecycle management."""

    async def test_close_flushes_pending(self, temp_db):
        """Closing store flushes pending writes."""
        reset_usage_tracker()
        reset_usage_store()

        store = UsageStore(db_path=temp_db)
        await store.initialize()

        tracker = get_usage_tracker()
        tracker.record(
            thread_id="t1", agent_id="agent", model="grok-4.1",
            provider="xai", prompt_tokens=100, completion_tokens=50, latency_ms=100.0,
        )

        # Close immediately - should flush
        await store.close()

        # Reopen and verify data persisted
        store2 = UsageStore(db_path=temp_db)
        await store2.initialize()

        events = await store2.query()
        assert len(events) == 1

        await store2.close()
