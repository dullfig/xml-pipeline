"""
test_token_budget.py — Tests for token budget and usage tracking.

Tests:
1. ThreadBudgetRegistry - per-thread token limits
2. UsageTracker - billing/gas usage events
3. LLMRouter integration - budget enforcement
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch, MagicMock

from xml_pipeline.message_bus.budget_registry import (
    ThreadBudget,
    ThreadBudgetRegistry,
    BudgetExhaustedError,
    BudgetThresholdCrossed,
    DEFAULT_WARNING_THRESHOLDS,
    get_budget_registry,
    configure_budget_registry,
    reset_budget_registry,
)
from xml_pipeline.llm.usage_tracker import (
    UsageEvent,
    UsageTracker,
    UsageTotals,
    estimate_cost,
    get_usage_tracker,
    reset_usage_tracker,
)


# ============================================================================
# ThreadBudget Tests
# ============================================================================

class TestThreadBudget:
    """Test ThreadBudget dataclass."""

    def test_initial_state(self):
        """New budget should have zero usage."""
        budget = ThreadBudget(max_tokens=10000)
        assert budget.total_tokens == 0
        assert budget.remaining == 10000
        assert budget.is_exhausted is False

    def test_consume_tokens(self):
        """Consuming tokens should update totals."""
        budget = ThreadBudget(max_tokens=10000)
        budget.consume(prompt_tokens=500, completion_tokens=300)

        assert budget.prompt_tokens == 500
        assert budget.completion_tokens == 300
        assert budget.total_tokens == 800
        assert budget.remaining == 9200
        assert budget.request_count == 1

    def test_can_consume_within_budget(self):
        """can_consume should return True if within budget."""
        budget = ThreadBudget(max_tokens=1000)
        budget.consume(prompt_tokens=400)

        assert budget.can_consume(500) is True
        assert budget.can_consume(600) is True
        assert budget.can_consume(601) is False

    def test_is_exhausted(self):
        """is_exhausted should return True when budget exceeded."""
        budget = ThreadBudget(max_tokens=1000)
        budget.consume(prompt_tokens=1000)

        assert budget.is_exhausted is True
        assert budget.remaining == 0

    def test_remaining_never_negative(self):
        """remaining should never go negative."""
        budget = ThreadBudget(max_tokens=100)
        budget.consume(prompt_tokens=200)

        assert budget.remaining == 0
        assert budget.total_tokens == 200


# ============================================================================
# ThreadBudgetRegistry Tests
# ============================================================================

class TestThreadBudgetRegistry:
    """Test ThreadBudgetRegistry."""

    @pytest.fixture(autouse=True)
    def reset(self):
        """Reset global registry before each test."""
        reset_budget_registry()
        yield
        reset_budget_registry()

    def test_default_budget_creation(self):
        """Getting budget for new thread should create one."""
        registry = ThreadBudgetRegistry(max_tokens_per_thread=50000)
        budget = registry.get_budget("thread-1")

        assert budget.max_tokens == 50000
        assert budget.total_tokens == 0

    def test_configure_max_tokens(self):
        """configure() should update default for new threads."""
        registry = ThreadBudgetRegistry(max_tokens_per_thread=10000)
        budget1 = registry.get_budget("thread-1")

        registry.configure(max_tokens_per_thread=20000)
        budget2 = registry.get_budget("thread-2")

        assert budget1.max_tokens == 10000  # Original unchanged
        assert budget2.max_tokens == 20000  # New default

    def test_check_budget_success(self):
        """check_budget should pass when within budget."""
        registry = ThreadBudgetRegistry(max_tokens_per_thread=10000)

        result = registry.check_budget("thread-1", estimated_tokens=5000)
        assert result is True

    def test_check_budget_exhausted(self):
        """check_budget should raise when budget exhausted."""
        registry = ThreadBudgetRegistry(max_tokens_per_thread=1000)
        budget, _ = registry.consume("thread-1", prompt_tokens=1000)

        with pytest.raises(BudgetExhaustedError) as exc_info:
            registry.check_budget("thread-1", estimated_tokens=100)

        assert "budget exhausted" in str(exc_info.value)
        assert exc_info.value.thread_id == "thread-1"
        assert exc_info.value.used == 1000
        assert exc_info.value.max_tokens == 1000

    def test_check_budget_would_exceed(self):
        """check_budget should raise when estimate would exceed."""
        registry = ThreadBudgetRegistry(max_tokens_per_thread=1000)
        budget, _ = registry.consume("thread-1", prompt_tokens=600)

        with pytest.raises(BudgetExhaustedError):
            registry.check_budget("thread-1", estimated_tokens=500)

    def test_consume_returns_budget_and_thresholds(self):
        """consume() should return (budget, crossed_thresholds) tuple."""
        registry = ThreadBudgetRegistry(max_tokens_per_thread=10000)

        budget, crossed = registry.consume("thread-1", prompt_tokens=100, completion_tokens=50)

        assert budget.total_tokens == 150
        assert budget.request_count == 1
        assert crossed == []  # 1.5% - no threshold crossed

    def test_get_usage(self):
        """get_usage should return dict with all stats."""
        registry = ThreadBudgetRegistry(max_tokens_per_thread=10000)
        budget, _ = registry.consume("thread-1", prompt_tokens=500, completion_tokens=200)
        budget, _ = registry.consume("thread-1", prompt_tokens=300, completion_tokens=100)

        usage = registry.get_usage("thread-1")

        assert usage["prompt_tokens"] == 800
        assert usage["completion_tokens"] == 300
        assert usage["total_tokens"] == 1100
        assert usage["remaining"] == 8900
        assert usage["request_count"] == 2

    def test_get_all_usage(self):
        """get_all_usage should return all threads."""
        registry = ThreadBudgetRegistry(max_tokens_per_thread=10000)
        budget, _ = registry.consume("thread-1", prompt_tokens=100)
        budget, _ = registry.consume("thread-2", prompt_tokens=200)

        all_usage = registry.get_all_usage()

        assert len(all_usage) == 2
        assert "thread-1" in all_usage
        assert "thread-2" in all_usage

    def test_reset_thread(self):
        """reset_thread should remove budget for thread."""
        registry = ThreadBudgetRegistry(max_tokens_per_thread=10000)
        budget, _ = registry.consume("thread-1", prompt_tokens=500)
        registry.reset_thread("thread-1")

        # Getting budget should create new one with zero usage
        budget = registry.get_budget("thread-1")
        assert budget.total_tokens == 0

    def test_cleanup_thread(self):
        """cleanup_thread should return and remove budget."""
        registry = ThreadBudgetRegistry(max_tokens_per_thread=10000)
        budget, _ = registry.consume("thread-1", prompt_tokens=500)

        final_budget = registry.cleanup_thread("thread-1")

        assert final_budget.total_tokens == 500
        assert registry.cleanup_thread("thread-1") is None  # Already cleaned

    def test_global_registry(self):
        """Global registry should be singleton."""
        registry1 = get_budget_registry()
        registry2 = get_budget_registry()

        assert registry1 is registry2

    def test_global_configure(self):
        """configure_budget_registry should update global."""
        configure_budget_registry(max_tokens_per_thread=75000)
        registry = get_budget_registry()

        budget = registry.get_budget("new-thread")
        assert budget.max_tokens == 75000


# ============================================================================
# UsageTracker Tests
# ============================================================================

class TestUsageTracker:
    """Test UsageTracker for billing/metering."""

    @pytest.fixture(autouse=True)
    def reset(self):
        """Reset global tracker before each test."""
        reset_usage_tracker()
        yield
        reset_usage_tracker()

    def test_record_creates_event(self):
        """record() should create and return UsageEvent."""
        tracker = UsageTracker()

        event = tracker.record(
            thread_id="thread-1",
            agent_id="greeter",
            model="grok-4.1",
            provider="xai",
            prompt_tokens=500,
            completion_tokens=200,
            latency_ms=150.5,
        )

        assert event.thread_id == "thread-1"
        assert event.agent_id == "greeter"
        assert event.model == "grok-4.1"
        assert event.total_tokens == 700
        assert event.timestamp is not None

    def test_record_estimates_cost(self):
        """record() should estimate cost for known models."""
        tracker = UsageTracker()

        event = tracker.record(
            thread_id="thread-1",
            agent_id="agent",
            model="grok-4.1",
            provider="xai",
            prompt_tokens=1_000_000,  # 1M prompt
            completion_tokens=1_000_000,  # 1M completion
            latency_ms=1000,
        )

        # grok-4.1: $3/1M prompt + $15/1M completion = $18
        assert event.estimated_cost == 18.0

    def test_subscriber_receives_events(self):
        """Subscribers should receive events on record."""
        tracker = UsageTracker()
        received = []

        tracker.subscribe(lambda e: received.append(e))

        tracker.record(
            thread_id="t1",
            agent_id="agent",
            model="gpt-4o",
            provider="openai",
            prompt_tokens=100,
            completion_tokens=50,
            latency_ms=50,
        )

        assert len(received) == 1
        assert received[0].thread_id == "t1"

    def test_unsubscribe(self):
        """unsubscribe should stop receiving events."""
        tracker = UsageTracker()
        received = []
        callback = lambda e: received.append(e)

        tracker.subscribe(callback)
        tracker.record(thread_id="t1", agent_id=None, model="m", provider="p",
                       prompt_tokens=10, completion_tokens=10, latency_ms=10)

        tracker.unsubscribe(callback)
        tracker.record(thread_id="t2", agent_id=None, model="m", provider="p",
                       prompt_tokens=10, completion_tokens=10, latency_ms=10)

        assert len(received) == 1

    def test_get_totals(self):
        """get_totals should return aggregate stats."""
        tracker = UsageTracker()

        tracker.record(thread_id="t1", agent_id="a1", model="m1", provider="p",
                       prompt_tokens=100, completion_tokens=50, latency_ms=100)
        tracker.record(thread_id="t2", agent_id="a2", model="m2", provider="p",
                       prompt_tokens=200, completion_tokens=100, latency_ms=200)

        totals = tracker.get_totals()

        assert totals["prompt_tokens"] == 300
        assert totals["completion_tokens"] == 150
        assert totals["total_tokens"] == 450
        assert totals["request_count"] == 2
        assert totals["avg_latency_ms"] == 150.0

    def test_get_agent_totals(self):
        """get_agent_totals should return per-agent stats."""
        tracker = UsageTracker()

        tracker.record(thread_id="t1", agent_id="greeter", model="m", provider="p",
                       prompt_tokens=100, completion_tokens=50, latency_ms=100)
        tracker.record(thread_id="t2", agent_id="greeter", model="m", provider="p",
                       prompt_tokens=100, completion_tokens=50, latency_ms=100)
        tracker.record(thread_id="t3", agent_id="shouter", model="m", provider="p",
                       prompt_tokens=200, completion_tokens=100, latency_ms=200)

        greeter = tracker.get_agent_totals("greeter")
        shouter = tracker.get_agent_totals("shouter")

        assert greeter["total_tokens"] == 300
        assert greeter["request_count"] == 2
        assert shouter["total_tokens"] == 300
        assert shouter["request_count"] == 1

    def test_get_model_totals(self):
        """get_model_totals should return per-model stats."""
        tracker = UsageTracker()

        tracker.record(thread_id="t1", agent_id="a", model="grok-4.1", provider="xai",
                       prompt_tokens=1000, completion_tokens=500, latency_ms=100)
        tracker.record(thread_id="t2", agent_id="a", model="claude-sonnet-4", provider="anthropic",
                       prompt_tokens=500, completion_tokens=250, latency_ms=100)

        grok = tracker.get_model_totals("grok-4.1")
        claude = tracker.get_model_totals("claude-sonnet-4")

        assert grok["total_tokens"] == 1500
        assert claude["total_tokens"] == 750

    def test_metadata_passed_through(self):
        """Metadata should be included in events."""
        tracker = UsageTracker()
        received = []
        tracker.subscribe(lambda e: received.append(e))

        tracker.record(
            thread_id="t1",
            agent_id="a",
            model="m",
            provider="p",
            prompt_tokens=10,
            completion_tokens=10,
            latency_ms=10,
            metadata={"org_id": "org-123", "user_id": "user-456"},
        )

        assert received[0].metadata["org_id"] == "org-123"
        assert received[0].metadata["user_id"] == "user-456"


# ============================================================================
# Cost Estimation Tests
# ============================================================================

class TestCostEstimation:
    """Test cost estimation for various models."""

    def test_grok_cost(self):
        """Grok models should use correct pricing."""
        cost = estimate_cost("grok-4.1", prompt_tokens=1_000_000, completion_tokens=1_000_000)
        # $3/1M prompt + $15/1M completion = $18
        assert cost == 18.0

    def test_claude_opus_cost(self):
        """Claude Opus should use correct pricing."""
        cost = estimate_cost("claude-opus-4", prompt_tokens=1_000_000, completion_tokens=1_000_000)
        # $15/1M prompt + $75/1M completion = $90
        assert cost == 90.0

    def test_gpt4o_cost(self):
        """GPT-4o should use correct pricing."""
        cost = estimate_cost("gpt-4o", prompt_tokens=1_000_000, completion_tokens=1_000_000)
        # $2.5/1M prompt + $10/1M completion = $12.5
        assert cost == 12.5

    def test_unknown_model_returns_none(self):
        """Unknown model should return None."""
        cost = estimate_cost("unknown-model", prompt_tokens=1000, completion_tokens=500)
        assert cost is None

    def test_small_usage_cost(self):
        """Small token counts should produce fractional costs."""
        cost = estimate_cost("gpt-4o-mini", prompt_tokens=1000, completion_tokens=500)
        # 1000 tokens * $0.15/1M = $0.00015
        # 500 tokens * $0.6/1M = $0.0003
        # Total = $0.00045
        assert cost == pytest.approx(0.00045, rel=1e-4)


# ============================================================================
# LLMRouter Integration Tests (Mocked)
# ============================================================================

class TestLLMRouterBudgetIntegration:
    """Test LLMRouter budget enforcement."""

    @pytest.fixture(autouse=True)
    def reset_all(self):
        """Reset all global registries."""
        reset_budget_registry()
        reset_usage_tracker()
        yield
        reset_budget_registry()
        reset_usage_tracker()

    @pytest.mark.asyncio
    async def test_complete_consumes_budget(self):
        """LLM complete should consume from thread budget."""
        from xml_pipeline.llm.router import LLMRouter
        from xml_pipeline.llm.backend import LLMResponse

        # Create mock backend
        mock_backend = Mock()
        mock_backend.name = "mock"
        mock_backend.provider = "test"
        mock_backend.serves_model = Mock(return_value=True)
        mock_backend.priority = 1
        mock_backend.load = 0
        mock_backend.complete = AsyncMock(return_value=LLMResponse(
            content="Hello!",
            model="test-model",
            usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            finish_reason="stop",
        ))

        # Configure budget
        configure_budget_registry(max_tokens_per_thread=10000)
        budget_registry = get_budget_registry()

        # Create router with mock backend
        router = LLMRouter()
        router.backends.append(mock_backend)

        # Make request
        response = await router.complete(
            model="test-model",
            messages=[{"role": "user", "content": "Hi"}],
            thread_id="test-thread-123",
        )

        assert response.content == "Hello!"

        # Verify budget consumed
        usage = budget_registry.get_usage("test-thread-123")
        assert usage["prompt_tokens"] == 100
        assert usage["completion_tokens"] == 50
        assert usage["total_tokens"] == 150

    @pytest.mark.asyncio
    async def test_complete_emits_usage_event(self):
        """LLM complete should emit usage event."""
        from xml_pipeline.llm.router import LLMRouter
        from xml_pipeline.llm.backend import LLMResponse

        mock_backend = Mock()
        mock_backend.name = "mock"
        mock_backend.provider = "test"
        mock_backend.serves_model = Mock(return_value=True)
        mock_backend.priority = 1
        mock_backend.load = 0
        mock_backend.complete = AsyncMock(return_value=LLMResponse(
            content="Hello!",
            model="test-model",
            usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            finish_reason="stop",
        ))

        # Subscribe to usage events
        tracker = get_usage_tracker()
        received_events = []
        tracker.subscribe(lambda e: received_events.append(e))

        # Create router and make request
        router = LLMRouter()
        router.backends.append(mock_backend)

        await router.complete(
            model="test-model",
            messages=[{"role": "user", "content": "Hi"}],
            thread_id="test-thread",
            agent_id="greeter",
            metadata={"org_id": "test-org"},
        )

        # Verify event emitted
        assert len(received_events) == 1
        event = received_events[0]
        assert event.thread_id == "test-thread"
        assert event.agent_id == "greeter"
        assert event.total_tokens == 150
        assert event.metadata["org_id"] == "test-org"

    @pytest.mark.asyncio
    async def test_complete_raises_when_budget_exhausted(self):
        """LLM complete should raise when budget exhausted."""
        from xml_pipeline.llm.router import LLMRouter

        # Configure small budget and exhaust it
        configure_budget_registry(max_tokens_per_thread=100)
        budget_registry = get_budget_registry()
        budget, _ = budget_registry.consume("test-thread", prompt_tokens=100)

        mock_backend = Mock()
        mock_backend.name = "mock"
        mock_backend.serves_model = Mock(return_value=True)
        mock_backend.priority = 1

        router = LLMRouter()
        router.backends.append(mock_backend)

        with pytest.raises(BudgetExhaustedError) as exc_info:
            await router.complete(
                model="test-model",
                messages=[{"role": "user", "content": "Hi"}],
                thread_id="test-thread",
            )

        assert "budget exhausted" in str(exc_info.value)
        # Backend should NOT have been called
        mock_backend.complete.assert_not_called()

    @pytest.mark.asyncio
    async def test_complete_without_thread_id_skips_budget(self):
        """LLM complete without thread_id should skip budget check."""
        from xml_pipeline.llm.router import LLMRouter
        from xml_pipeline.llm.backend import LLMResponse

        mock_backend = Mock()
        mock_backend.name = "mock"
        mock_backend.provider = "test"
        mock_backend.serves_model = Mock(return_value=True)
        mock_backend.priority = 1
        mock_backend.load = 0
        mock_backend.complete = AsyncMock(return_value=LLMResponse(
            content="Hello!",
            model="test-model",
            usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            finish_reason="stop",
        ))

        router = LLMRouter()
        router.backends.append(mock_backend)

        # Should not raise - no budget checking
        response = await router.complete(
            model="test-model",
            messages=[{"role": "user", "content": "Hi"}],
            # No thread_id
        )

        assert response.content == "Hello!"


# ============================================================================
# Budget Cleanup Tests
# ============================================================================

class TestBudgetCleanup:
    """Test budget cleanup when threads complete."""

    @pytest.fixture(autouse=True)
    def reset_all(self):
        """Reset all global registries."""
        reset_budget_registry()
        yield
        reset_budget_registry()

    def test_cleanup_thread_returns_budget(self):
        """cleanup_thread should return the budget before removing it."""
        registry = ThreadBudgetRegistry()
        budget, _ = registry.consume("thread-1", prompt_tokens=500, completion_tokens=200)

        final = registry.cleanup_thread("thread-1")

        assert final is not None
        assert final.prompt_tokens == 500
        assert final.completion_tokens == 200
        assert final.total_tokens == 700

    def test_cleanup_thread_removes_budget(self):
        """cleanup_thread should remove the budget from registry."""
        registry = ThreadBudgetRegistry()
        budget, _ = registry.consume("thread-1", prompt_tokens=500, completion_tokens=200)

        registry.cleanup_thread("thread-1")

        # Budget should no longer exist
        assert not registry.has_budget("thread-1")
        assert registry.get_usage("thread-1") is None

    def test_cleanup_nonexistent_thread_returns_none(self):
        """cleanup_thread for unknown thread should return None."""
        registry = ThreadBudgetRegistry()

        result = registry.cleanup_thread("nonexistent")

        assert result is None

    def test_global_cleanup(self):
        """Test cleanup via global registry."""
        configure_budget_registry(max_tokens_per_thread=10000)
        registry = get_budget_registry()

        # Consume some tokens
        budget, _ = registry.consume("test-thread", prompt_tokens=1000, completion_tokens=500)
        assert registry.has_budget("test-thread")

        # Cleanup
        final = registry.cleanup_thread("test-thread")

        assert final.total_tokens == 1500
        assert not registry.has_budget("test-thread")


# ============================================================================
# Budget Threshold Tests
# ============================================================================

class TestBudgetThresholds:
    """Test budget threshold crossing detection."""

    @pytest.fixture(autouse=True)
    def reset_all(self):
        """Reset all global registries."""
        reset_budget_registry()
        yield
        reset_budget_registry()

    def test_no_threshold_crossed_below_first(self):
        """No thresholds crossed when under 75%."""
        registry = ThreadBudgetRegistry(max_tokens_per_thread=1000)

        # 70% usage - below first threshold
        budget, crossed = registry.consume("thread-1", prompt_tokens=700)

        assert crossed == []
        assert budget.percent_used == 70.0

    def test_warning_threshold_crossed_at_75(self):
        """75% threshold should trigger 'warning' severity."""
        registry = ThreadBudgetRegistry(max_tokens_per_thread=1000)

        # 75% usage - exactly at threshold
        budget, crossed = registry.consume("thread-1", prompt_tokens=750)

        assert len(crossed) == 1
        assert crossed[0].threshold_percent == 75
        assert crossed[0].severity == "warning"
        assert crossed[0].percent_used == 75.0
        assert crossed[0].tokens_used == 750
        assert crossed[0].tokens_remaining == 250

    def test_critical_threshold_crossed_at_90(self):
        """90% threshold should trigger 'critical' severity."""
        registry = ThreadBudgetRegistry(max_tokens_per_thread=1000)

        # First consume to 76% (triggers warning)
        budget, crossed = registry.consume("thread-1", prompt_tokens=760)
        assert len(crossed) == 1
        assert crossed[0].severity == "warning"

        # Now consume to 91% (triggers critical)
        budget, crossed = registry.consume("thread-1", prompt_tokens=150)
        assert len(crossed) == 1
        assert crossed[0].threshold_percent == 90
        assert crossed[0].severity == "critical"

    def test_final_threshold_crossed_at_95(self):
        """95% threshold should trigger 'final' severity."""
        registry = ThreadBudgetRegistry(max_tokens_per_thread=1000)

        # Jump directly to 96%
        budget, crossed = registry.consume("thread-1", prompt_tokens=960)

        # Should have crossed all three thresholds
        assert len(crossed) == 3
        severities = {c.severity for c in crossed}
        assert severities == {"warning", "critical", "final"}

    def test_thresholds_only_triggered_once(self):
        """Each threshold should only be triggered once per thread."""
        registry = ThreadBudgetRegistry(max_tokens_per_thread=1000)

        # Cross the 75% threshold
        budget, crossed1 = registry.consume("thread-1", prompt_tokens=760)
        assert len(crossed1) == 1

        # Stay at 76% with another consume - should not trigger again
        budget, crossed2 = registry.consume("thread-1", prompt_tokens=0)
        assert crossed2 == []

        # Consume more to stay between 75-90% - should not trigger
        budget, crossed3 = registry.consume("thread-1", prompt_tokens=50)  # Now at 81%
        assert crossed3 == []

    def test_multiple_thresholds_in_single_consume(self):
        """A single large consume can cross multiple thresholds."""
        registry = ThreadBudgetRegistry(max_tokens_per_thread=1000)

        # Single consume from 0% to 92% - crosses 75% and 90%
        budget, crossed = registry.consume("thread-1", prompt_tokens=920)

        assert len(crossed) == 2
        thresholds = {c.threshold_percent for c in crossed}
        assert thresholds == {75, 90}

    def test_threshold_data_accuracy(self):
        """Threshold crossing data should be accurate."""
        registry = ThreadBudgetRegistry(max_tokens_per_thread=10000)

        # Consume to 75.5%
        budget, crossed = registry.consume("thread-1", prompt_tokens=7550)

        assert len(crossed) == 1
        threshold = crossed[0]
        assert threshold.threshold_percent == 75
        assert threshold.percent_used == 75.5
        assert threshold.tokens_used == 7550
        assert threshold.tokens_remaining == 2450
        assert threshold.max_tokens == 10000

    def test_percent_used_property(self):
        """percent_used property should calculate correctly."""
        budget = ThreadBudget(max_tokens=1000)

        assert budget.percent_used == 0.0

        budget.consume(prompt_tokens=500)
        assert budget.percent_used == 50.0

        budget.consume(prompt_tokens=250)
        assert budget.percent_used == 75.0

    def test_percent_used_with_zero_max(self):
        """percent_used should handle zero max_tokens gracefully."""
        budget = ThreadBudget(max_tokens=0)
        assert budget.percent_used == 0.0

    def test_triggered_thresholds_tracked_per_thread(self):
        """Different threads should track thresholds independently."""
        registry = ThreadBudgetRegistry(max_tokens_per_thread=1000)

        # Thread 1 crosses 75%
        budget1, crossed1 = registry.consume("thread-1", prompt_tokens=760)
        assert len(crossed1) == 1

        # Thread 2 crosses 75% independently
        budget2, crossed2 = registry.consume("thread-2", prompt_tokens=800)
        assert len(crossed2) == 1

        # Both should have triggered their thresholds
        assert 75 in budget1.triggered_thresholds
        assert 75 in budget2.triggered_thresholds


# ============================================================================
# BudgetWarning Injection Tests
# ============================================================================

class TestBudgetWarningInjection:
    """Test BudgetWarning messages are injected when thresholds crossed."""

    @pytest.fixture(autouse=True)
    def reset_all(self):
        """Reset all global registries."""
        reset_budget_registry()
        reset_usage_tracker()
        yield
        reset_budget_registry()
        reset_usage_tracker()

    @pytest.mark.asyncio
    async def test_warning_injected_on_threshold_crossing(self):
        """LLM complete should inject BudgetWarning when threshold crossed."""
        from xml_pipeline.llm.router import LLMRouter
        from xml_pipeline.llm.backend import LLMResponse
        from xml_pipeline.primitives.budget_warning import BudgetWarning

        # Configure budget for 1000 tokens
        configure_budget_registry(max_tokens_per_thread=1000)

        # Create mock backend that returns 760 tokens (crosses 75% threshold)
        mock_backend = Mock()
        mock_backend.name = "mock"
        mock_backend.provider = "test"
        mock_backend.serves_model = Mock(return_value=True)
        mock_backend.priority = 1
        mock_backend.load = 0
        mock_backend.complete = AsyncMock(return_value=LLMResponse(
            content="Hello!",
            model="test-model",
            usage={"prompt_tokens": 700, "completion_tokens": 60, "total_tokens": 760},
            finish_reason="stop",
        ))

        router = LLMRouter()
        router.backends.append(mock_backend)

        # Mock the pump to capture injected warnings
        injected_messages = []

        async def mock_inject(raw_bytes, thread_id, from_id):
            injected_messages.append({
                "raw_bytes": raw_bytes,
                "thread_id": thread_id,
                "from_id": from_id,
            })

        mock_pump = Mock()
        mock_pump.inject = AsyncMock(side_effect=mock_inject)
        mock_pump._wrap_in_envelope = Mock(return_value=b"<envelope>warning</envelope>")

        with patch('xml_pipeline.message_bus.stream_pump.get_stream_pump', return_value=mock_pump):
            await router.complete(
                model="test-model",
                messages=[{"role": "user", "content": "Hi"}],
                thread_id="test-thread",
                agent_id="greeter",
            )

        # Should have injected one warning (75% threshold)
        assert len(injected_messages) == 1
        assert injected_messages[0]["from_id"] == "system.budget"
        assert injected_messages[0]["thread_id"] == "test-thread"

    @pytest.mark.asyncio
    async def test_multiple_warnings_injected(self):
        """Multiple thresholds crossed should inject multiple warnings."""
        from xml_pipeline.llm.router import LLMRouter
        from xml_pipeline.llm.backend import LLMResponse

        configure_budget_registry(max_tokens_per_thread=1000)

        # Backend returns 960 tokens (crosses 75%, 90%, and 95%)
        mock_backend = Mock()
        mock_backend.name = "mock"
        mock_backend.provider = "test"
        mock_backend.serves_model = Mock(return_value=True)
        mock_backend.priority = 1
        mock_backend.load = 0
        mock_backend.complete = AsyncMock(return_value=LLMResponse(
            content="Big response",
            model="test-model",
            usage={"prompt_tokens": 900, "completion_tokens": 60, "total_tokens": 960},
            finish_reason="stop",
        ))

        router = LLMRouter()
        router.backends.append(mock_backend)

        injected_messages = []

        mock_pump = Mock()
        mock_pump.inject = AsyncMock(side_effect=lambda *args, **kwargs: injected_messages.append(args))
        mock_pump._wrap_in_envelope = Mock(return_value=b"<envelope>warning</envelope>")

        with patch('xml_pipeline.message_bus.stream_pump.get_stream_pump', return_value=mock_pump):
            await router.complete(
                model="test-model",
                messages=[{"role": "user", "content": "Hi"}],
                thread_id="test-thread",
                agent_id="agent",
            )

        # Should have injected 3 warnings (75%, 90%, 95%)
        assert len(injected_messages) == 3

    @pytest.mark.asyncio
    async def test_no_warning_when_below_threshold(self):
        """No warning should be injected when below all thresholds."""
        from xml_pipeline.llm.router import LLMRouter
        from xml_pipeline.llm.backend import LLMResponse

        configure_budget_registry(max_tokens_per_thread=10000)

        # Backend returns 100 tokens (only 1%)
        mock_backend = Mock()
        mock_backend.name = "mock"
        mock_backend.provider = "test"
        mock_backend.serves_model = Mock(return_value=True)
        mock_backend.priority = 1
        mock_backend.load = 0
        mock_backend.complete = AsyncMock(return_value=LLMResponse(
            content="Small response",
            model="test-model",
            usage={"prompt_tokens": 80, "completion_tokens": 20, "total_tokens": 100},
            finish_reason="stop",
        ))

        router = LLMRouter()
        router.backends.append(mock_backend)

        mock_pump = Mock()
        mock_pump.inject = AsyncMock()

        with patch('xml_pipeline.message_bus.stream_pump.get_stream_pump', return_value=mock_pump):
            await router.complete(
                model="test-model",
                messages=[{"role": "user", "content": "Hi"}],
                thread_id="test-thread",
            )

        # No warnings should be injected
        mock_pump.inject.assert_not_called()

    @pytest.mark.asyncio
    async def test_warning_includes_correct_severity(self):
        """Warning messages should have correct severity based on threshold."""
        from xml_pipeline.llm.router import LLMRouter
        from xml_pipeline.llm.backend import LLMResponse
        from xml_pipeline.primitives.budget_warning import BudgetWarning

        configure_budget_registry(max_tokens_per_thread=1000)

        mock_backend = Mock()
        mock_backend.name = "mock"
        mock_backend.provider = "test"
        mock_backend.serves_model = Mock(return_value=True)
        mock_backend.priority = 1
        mock_backend.load = 0
        mock_backend.complete = AsyncMock(return_value=LLMResponse(
            content="Response",
            model="test-model",
            usage={"prompt_tokens": 910, "completion_tokens": 0, "total_tokens": 910},
            finish_reason="stop",
        ))

        router = LLMRouter()
        router.backends.append(mock_backend)

        captured_payloads = []

        def capture_wrap(payload, from_id, to_id, thread_id):
            captured_payloads.append({
                "payload": payload,
                "from_id": from_id,
                "to_id": to_id,
            })
            return b"<envelope/>"

        mock_pump = Mock()
        mock_pump.inject = AsyncMock()
        mock_pump._wrap_in_envelope = Mock(side_effect=capture_wrap)

        with patch('xml_pipeline.message_bus.stream_pump.get_stream_pump', return_value=mock_pump):
            await router.complete(
                model="test-model",
                messages=[{"role": "user", "content": "Hi"}],
                thread_id="test-thread",
                agent_id="agent",
            )

        # Should have captured 2 payloads (75% and 90%)
        assert len(captured_payloads) == 2

        severities = [p["payload"].severity for p in captured_payloads]
        assert "warning" in severities
        assert "critical" in severities

        # Check the critical warning has appropriate message
        critical = next(p for p in captured_payloads if p["payload"].severity == "critical")
        assert "WARNING" in critical["payload"].message
        assert "Consider wrapping up" in critical["payload"].message

    @pytest.mark.asyncio
    async def test_warning_graceful_without_pump(self):
        """Warning injection should gracefully handle missing pump."""
        from xml_pipeline.llm.router import LLMRouter
        from xml_pipeline.llm.backend import LLMResponse

        configure_budget_registry(max_tokens_per_thread=1000)

        mock_backend = Mock()
        mock_backend.name = "mock"
        mock_backend.provider = "test"
        mock_backend.serves_model = Mock(return_value=True)
        mock_backend.priority = 1
        mock_backend.load = 0
        mock_backend.complete = AsyncMock(return_value=LLMResponse(
            content="Response",
            model="test-model",
            usage={"prompt_tokens": 800, "completion_tokens": 0, "total_tokens": 800},
            finish_reason="stop",
        ))

        router = LLMRouter()
        router.backends.append(mock_backend)

        # Pump not initialized - should not raise
        with patch('xml_pipeline.message_bus.stream_pump.get_stream_pump', side_effect=RuntimeError("Not initialized")):
            response = await router.complete(
                model="test-model",
                messages=[{"role": "user", "content": "Hi"}],
                thread_id="test-thread",
            )

        # Should still complete successfully
        assert response.content == "Response"
