"""
Tests for the AgentServer API.

Tests the REST API endpoints and WebSocket connections.
"""

import asyncio
import os
import pytest
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, AsyncMock, patch

# Skip all tests if FastAPI not available
pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from xml_pipeline.server.models import (
    AgentState,
    AgentInfo,
    ThreadStatus,
    ThreadInfo,
    MessageInfo,
    OrganismInfo,
    OrganismStatus,
    SubscribeRequest,
)
from xml_pipeline.server.state import ServerState, AgentRuntimeState, ThreadRuntimeState
from xml_pipeline.server.api import create_router
from xml_pipeline.server.app import create_app


# ============================================================================
# Mock StreamPump
# ============================================================================

@dataclass
class MockListener:
    """Mock listener for testing."""
    name: str
    description: str
    is_agent: bool = False
    peers: List[str] = None
    payload_class: type = None
    schema: Any = None
    root_tag: str = ""

    def __post_init__(self):
        if self.peers is None:
            self.peers = []
        if self.payload_class is None:
            self.payload_class = type("MockPayload", (), {"__module__": "test", "__name__": "MockPayload"})
        if not self.root_tag:
            self.root_tag = f"{self.name.lower()}.mockpayload"


@dataclass
class MockConfig:
    """Mock config for testing."""
    name: str = "test-organism"
    port: int = 8765
    thread_scheduling: str = "breadth-first"
    max_concurrent_pipelines: int = 50
    max_concurrent_handlers: int = 20
    auth_api_key_env: str = ""


class MockStreamPump:
    """Mock StreamPump for testing."""

    def __init__(self):
        self.config = MockConfig()
        self.identity = None
        self.listeners: Dict[str, MockListener] = {
            "greeter": MockListener(
                name="greeter",
                description="Greeting agent",
                is_agent=True,
                peers=["shouter"],
            ),
            "shouter": MockListener(
                name="shouter",
                description="Shouting handler",
                is_agent=False,
            ),
        }
        self._event_callbacks = []

    def subscribe_events(self, callback):
        self._event_callbacks.append(callback)

    def unsubscribe_events(self, callback):
        if callback in self._event_callbacks:
            self._event_callbacks.remove(callback)


# ============================================================================
# Test Fixtures
# ============================================================================

@pytest.fixture
def mock_pump():
    """Create a mock StreamPump."""
    return MockStreamPump()


@pytest.fixture
def server_state(mock_pump):
    """Create ServerState with mock pump."""
    return ServerState(mock_pump)


@pytest.fixture
def test_client(mock_pump):
    """Create FastAPI test client."""
    app = create_app(mock_pump)
    return TestClient(app)


# ============================================================================
# Test Models
# ============================================================================

class TestModels:
    """Test Pydantic model serialization."""

    def test_agent_info_camel_case(self):
        """Test AgentInfo serializes to camelCase."""
        agent = AgentInfo(
            name="greeter",
            description="Test agent",
            is_agent=True,
            peers=["shouter"],
            payload_class="test.Greeting",
            state=AgentState.IDLE,
            current_thread=None,
            queue_depth=0,
            message_count=5,
        )
        data = agent.model_dump(by_alias=True)
        assert "isAgent" in data
        assert "payloadClass" in data
        assert "currentThread" in data
        assert "queueDepth" in data
        assert "messageCount" in data

    def test_thread_info_camel_case(self):
        """Test ThreadInfo serializes to camelCase."""
        from datetime import datetime, timezone
        thread = ThreadInfo(
            id="test-uuid",
            status=ThreadStatus.ACTIVE,
            participants=["greeter", "shouter"],
            message_count=3,
            created_at=datetime.now(timezone.utc),
        )
        data = thread.model_dump(by_alias=True)
        assert "messageCount" in data
        assert "createdAt" in data
        assert "lastActivity" in data

    def test_organism_info_camel_case(self):
        """Test OrganismInfo serializes to camelCase."""
        info = OrganismInfo(
            name="test-organism",
            status=OrganismStatus.RUNNING,
            uptime_seconds=3600.0,
            agent_count=2,
            active_threads=1,
            total_messages=10,
            identity_configured=False,
        )
        data = info.model_dump(by_alias=True)
        assert "uptimeSeconds" in data
        assert "agentCount" in data
        assert "activeThreads" in data
        assert "totalMessages" in data
        assert "identityConfigured" in data


# ============================================================================
# Test ServerState
# ============================================================================

class TestServerState:
    """Test ServerState functionality."""

    def test_init_agents_from_pump(self, server_state):
        """Test agents are initialized from pump listeners."""
        assert "greeter" in server_state._agents
        assert "shouter" in server_state._agents
        assert server_state._agents["greeter"].is_agent is True
        assert server_state._agents["shouter"].is_agent is False

    def test_get_organism_info(self, server_state):
        """Test getting organism info."""
        info = server_state.get_organism_info()
        assert info.name == "test-organism"
        assert info.agent_count == 2
        assert info.status == OrganismStatus.STARTING

    def test_set_running(self, server_state):
        """Test setting running status."""
        server_state.set_running()
        info = server_state.get_organism_info()
        assert info.status == OrganismStatus.RUNNING

    def test_get_agents(self, server_state):
        """Test getting all agents."""
        agents = server_state.get_agents()
        assert len(agents) == 2
        names = [a.name for a in agents]
        assert "greeter" in names
        assert "shouter" in names

    def test_get_agent(self, server_state):
        """Test getting single agent."""
        agent = server_state.get_agent("greeter")
        assert agent is not None
        assert agent.name == "greeter"
        assert agent.is_agent is True

    def test_get_agent_not_found(self, server_state):
        """Test getting non-existent agent."""
        agent = server_state.get_agent("nonexistent")
        assert agent is None

    @pytest.mark.asyncio
    async def test_record_message(self, server_state):
        """Test recording a message."""
        msg_id = await server_state.record_message(
            thread_id="test-thread",
            from_id="greeter",
            to_id="shouter",
            payload_type="GreetingResponse",
            payload={"message": "Hello!"},
        )
        assert msg_id is not None

        # Check message was recorded
        messages, total = server_state.get_messages(thread_id="test-thread")
        assert total == 1
        assert messages[0].from_id == "greeter"
        assert messages[0].to_id == "shouter"

    @pytest.mark.asyncio
    async def test_update_agent_state(self, server_state):
        """Test updating agent state."""
        await server_state.update_agent_state("greeter", AgentState.PROCESSING, "thread-1")

        agent = server_state.get_agent("greeter")
        assert agent.state == AgentState.PROCESSING
        assert agent.current_thread == "thread-1"

    @pytest.mark.asyncio
    async def test_complete_thread(self, server_state):
        """Test completing a thread."""
        # First record a message to create the thread
        await server_state.record_message(
            thread_id="test-thread",
            from_id="greeter",
            to_id="shouter",
            payload_type="Test",
            payload={},
        )

        # Complete the thread
        await server_state.complete_thread("test-thread", ThreadStatus.COMPLETED)

        thread = server_state.get_thread("test-thread")
        assert thread.status == ThreadStatus.COMPLETED

    def test_get_threads_with_filter(self, server_state):
        """Test filtering threads by status."""
        # No threads initially
        threads, total = server_state.get_threads(status=ThreadStatus.ACTIVE)
        assert total == 0

    def test_get_organism_config(self, server_state):
        """Test getting organism config."""
        config = server_state.get_organism_config()
        assert config["name"] == "test-organism"
        assert config["port"] == 8765


# ============================================================================
# Test REST API
# ============================================================================

class TestRestAPI:
    """Test REST API endpoints."""

    def test_health_check(self, test_client):
        """Test health check endpoint."""
        response = test_client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert "organism" in data

    def test_get_organism(self, test_client):
        """Test GET /api/v1/organism."""
        response = test_client.get("/api/v1/organism")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "test-organism"
        assert "agentCount" in data

    def test_get_organism_config(self, test_client):
        """Test GET /api/v1/organism/config."""
        response = test_client.get("/api/v1/organism/config")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "test-organism"

    def test_list_agents(self, test_client):
        """Test GET /api/v1/agents."""
        response = test_client.get("/api/v1/agents")
        assert response.status_code == 200
        data = response.json()
        assert "agents" in data
        assert data["count"] == 2

    def test_get_agent(self, test_client):
        """Test GET /api/v1/agents/{name}."""
        response = test_client.get("/api/v1/agents/greeter")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "greeter"
        assert data["isAgent"] is True

    def test_get_agent_not_found(self, test_client):
        """Test GET /api/v1/agents/{name} with non-existent agent."""
        response = test_client.get("/api/v1/agents/nonexistent")
        assert response.status_code == 404

    def test_get_agent_config(self, test_client):
        """Test GET /api/v1/agents/{name}/config."""
        response = test_client.get("/api/v1/agents/greeter/config")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "greeter"
        assert "isAgent" in data

    def test_list_threads(self, test_client):
        """Test GET /api/v1/threads."""
        response = test_client.get("/api/v1/threads")
        assert response.status_code == 200
        data = response.json()
        assert "threads" in data
        assert "count" in data
        assert "total" in data

    def test_list_threads_with_invalid_status(self, test_client):
        """Test GET /api/v1/threads with invalid status filter."""
        response = test_client.get("/api/v1/threads?status=invalid")
        assert response.status_code == 400

    def test_list_messages(self, test_client):
        """Test GET /api/v1/messages."""
        response = test_client.get("/api/v1/messages")
        assert response.status_code == 200
        data = response.json()
        assert "messages" in data
        assert "count" in data

    def test_inject_message(self, test_client):
        """Test POST /api/v1/inject."""
        response = test_client.post(
            "/api/v1/inject",
            json={
                "to": "greeter",
                "payload": {"name": "Dan"},
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert "threadId" in data
        assert "messageId" in data

    def test_inject_message_unknown_agent(self, test_client):
        """Test POST /api/v1/inject with unknown agent."""
        response = test_client.post(
            "/api/v1/inject",
            json={
                "to": "nonexistent",
                "payload": {"name": "Dan"},
            },
        )
        assert response.status_code == 400

    def test_pause_agent(self, test_client):
        """Test POST /api/v1/agents/{name}/pause."""
        response = test_client.post("/api/v1/agents/greeter/pause")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["state"] == "paused"

    def test_resume_agent(self, test_client):
        """Test POST /api/v1/agents/{name}/resume."""
        response = test_client.post("/api/v1/agents/greeter/resume")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["state"] == "idle"

    def test_stop_organism(self, test_client):
        """Test POST /api/v1/organism/stop."""
        response = test_client.post("/api/v1/organism/stop")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True


# ============================================================================
# Test WebSocket
# ============================================================================

class TestWebSocket:
    """Test WebSocket endpoints."""

    def test_websocket_connect(self, test_client):
        """Test WebSocket connection."""
        with test_client.websocket_connect("/ws") as websocket:
            # Should receive connected event with state snapshot
            data = websocket.receive_json()
            assert data["event"] == "connected"
            assert "organism" in data
            assert "agents" in data
            assert "threads" in data

    def test_websocket_subscribe(self, test_client):
        """Test WebSocket subscribe command."""
        with test_client.websocket_connect("/ws") as websocket:
            # Receive initial connected event
            websocket.receive_json()

            # Send subscribe command
            websocket.send_json({
                "cmd": "subscribe",
                "agents": ["greeter"],
                "events": ["message"],
            })

            # Should receive subscribed confirmation
            data = websocket.receive_json()
            assert data["event"] == "subscribed"

    def test_websocket_inject(self, test_client):
        """Test WebSocket inject command."""
        with test_client.websocket_connect("/ws") as websocket:
            # Receive initial connected event
            websocket.receive_json()

            # Send inject command
            websocket.send_json({
                "cmd": "inject",
                "to": "greeter",
                "payload": {"name": "Dan"},
            })

            # Should receive injected confirmation
            data = websocket.receive_json()
            assert data["event"] == "injected"
            assert "thread_id" in data
            assert "message_id" in data

    def test_websocket_inject_unknown_agent(self, test_client):
        """Test WebSocket inject with unknown agent."""
        with test_client.websocket_connect("/ws") as websocket:
            # Receive initial connected event
            websocket.receive_json()

            # Send inject command to unknown agent
            websocket.send_json({
                "cmd": "inject",
                "to": "nonexistent",
                "payload": {},
            })

            # Should receive error
            data = websocket.receive_json()
            assert data["event"] == "error"

    def test_websocket_unknown_command(self, test_client):
        """Test WebSocket with unknown command."""
        with test_client.websocket_connect("/ws") as websocket:
            # Receive initial connected event
            websocket.receive_json()

            # Send unknown command
            websocket.send_json({
                "cmd": "unknown_command",
            })

            # Should receive error
            data = websocket.receive_json()
            assert data["event"] == "error"

    def test_websocket_messages_stream(self, test_client):
        """Test WebSocket messages stream endpoint."""
        with test_client.websocket_connect("/ws/messages") as websocket:
            # Send subscribe command
            websocket.send_json({
                "cmd": "subscribe",
                "filter": {
                    "agents": ["greeter"],
                },
            })

            # Should receive subscribed confirmation
            data = websocket.receive_json()
            assert data["event"] == "subscribed"
            assert "filter" in data


# ============================================================================
# Test Capability Introspection
# ============================================================================

class TestCapabilityIntrospection:
    """Test capability introspection endpoints."""

    def test_list_capabilities(self, test_client):
        """Test GET /capabilities lists all registered listeners."""
        response = test_client.get("/api/v1/capabilities")
        assert response.status_code == 200

        data = response.json()
        assert "capabilities" in data
        assert "count" in data
        assert data["count"] == 2  # greeter and shouter

        names = [c["name"] for c in data["capabilities"]]
        assert "greeter" in names
        assert "shouter" in names

    def test_list_capabilities_includes_details(self, test_client):
        """Test capability listing includes required fields."""
        response = test_client.get("/api/v1/capabilities")
        data = response.json()

        # Find greeter
        greeter = next(c for c in data["capabilities"] if c["name"] == "greeter")

        assert "description" in greeter
        assert "isAgent" in greeter
        assert greeter["isAgent"] is True
        assert "peers" in greeter
        assert "shouter" in greeter["peers"]
        assert "rootTag" in greeter

    def test_list_capabilities_sorted(self, test_client):
        """Test capabilities are sorted by name."""
        response = test_client.get("/api/v1/capabilities")
        data = response.json()

        names = [c["name"] for c in data["capabilities"]]
        assert names == sorted(names)

    def test_get_capability_detail(self, test_client):
        """Test GET /capabilities/{name} returns detailed info."""
        response = test_client.get("/api/v1/capabilities/greeter")
        assert response.status_code == 200

        data = response.json()
        assert data["name"] == "greeter"
        assert data["description"] == "Greeting agent"
        assert data["isAgent"] is True
        assert "shouter" in data["peers"]
        assert "payloadClass" in data
        assert "rootTag" in data

    def test_get_capability_includes_example(self, test_client):
        """Test capability detail includes example XML."""
        response = test_client.get("/api/v1/capabilities/greeter")
        data = response.json()

        # Example XML may or may not be present depending on payload class
        assert "exampleXml" in data

    def test_get_capability_not_found(self, test_client):
        """Test GET /capabilities/{name} returns 404 for unknown capability."""
        response = test_client.get("/api/v1/capabilities/nonexistent")
        assert response.status_code == 404

    def test_get_capability_shouter(self, test_client):
        """Test non-agent capability details."""
        response = test_client.get("/api/v1/capabilities/shouter")
        assert response.status_code == 200

        data = response.json()
        assert data["name"] == "shouter"
        assert data["isAgent"] is False
        assert data["peers"] == []


class TestCapabilityIntrospectionState:
    """Test capability introspection via ServerState."""

    def test_get_capabilities_returns_list(self, server_state):
        """Test get_capabilities returns CapabilityInfo list."""
        capabilities = server_state.get_capabilities()
        assert len(capabilities) == 2
        assert all(hasattr(c, "name") for c in capabilities)
        assert all(hasattr(c, "root_tag") for c in capabilities)

    def test_get_capability_returns_detail(self, server_state):
        """Test get_capability returns CapabilityDetail."""
        detail = server_state.get_capability("greeter")
        assert detail is not None
        assert detail.name == "greeter"
        assert detail.is_agent is True
        assert "shouter" in detail.peers

    def test_get_capability_not_found_returns_none(self, server_state):
        """Test get_capability returns None for unknown."""
        detail = server_state.get_capability("nonexistent")
        assert detail is None


# ============================================================================
# Test Usage/Gas Tracking API
# ============================================================================

class TestUsageAPI:
    """Test usage/gas tracking endpoints."""

    def test_get_usage_overview(self, test_client):
        """Test GET /api/v1/usage returns overview."""
        # Reset trackers for clean state
        from xml_pipeline.llm import reset_usage_tracker
        from xml_pipeline.message_bus import reset_budget_registry

        reset_usage_tracker()
        reset_budget_registry()

        response = test_client.get("/api/v1/usage")
        assert response.status_code == 200

        data = response.json()
        assert "usage" in data

        usage = data["usage"]
        assert "totals" in usage
        assert "byAgent" in usage
        assert "byModel" in usage
        assert "activeThreads" in usage

        totals = usage["totals"]
        assert "totalTokens" in totals
        assert "promptTokens" in totals
        assert "completionTokens" in totals
        assert "requestCount" in totals
        assert "totalCost" in totals
        assert "avgLatencyMs" in totals

    def test_get_usage_with_data(self, test_client):
        """Test usage reflects recorded data."""
        from xml_pipeline.llm import get_usage_tracker, reset_usage_tracker

        reset_usage_tracker()
        tracker = get_usage_tracker()

        # Record some usage
        tracker.record(
            thread_id="test-thread",
            agent_id="greeter",
            model="grok-4.1",
            provider="xai",
            prompt_tokens=100,
            completion_tokens=50,
            latency_ms=250.0,
        )

        response = test_client.get("/api/v1/usage")
        assert response.status_code == 200

        data = response.json()
        totals = data["usage"]["totals"]
        assert totals["totalTokens"] == 150
        assert totals["promptTokens"] == 100
        assert totals["completionTokens"] == 50
        assert totals["requestCount"] == 1

        # Check by-agent breakdown
        by_agent = data["usage"]["byAgent"]
        assert len(by_agent) == 1
        assert by_agent[0]["agentId"] == "greeter"
        assert by_agent[0]["totalTokens"] == 150

        # Check by-model breakdown
        by_model = data["usage"]["byModel"]
        assert len(by_model) == 1
        assert by_model[0]["model"] == "grok-4.1"
        assert by_model[0]["totalTokens"] == 150

    def test_get_thread_budgets_empty(self, test_client):
        """Test GET /api/v1/usage/threads with no threads."""
        from xml_pipeline.message_bus import reset_budget_registry

        reset_budget_registry()

        response = test_client.get("/api/v1/usage/threads")
        assert response.status_code == 200

        data = response.json()
        assert "threads" in data
        assert "count" in data
        assert "defaultMaxTokens" in data
        assert data["count"] == 0

    def test_get_thread_budgets_with_data(self, test_client):
        """Test thread budgets reflect consumption."""
        from xml_pipeline.message_bus import get_budget_registry, reset_budget_registry

        reset_budget_registry()
        registry = get_budget_registry()

        # Consume some tokens
        registry.consume("thread-1", 5000, 2000)
        registry.consume("thread-2", 10000, 5000)

        response = test_client.get("/api/v1/usage/threads")
        assert response.status_code == 200

        data = response.json()
        assert data["count"] == 2

        # Threads sorted by percent used (descending)
        threads = data["threads"]
        assert threads[0]["percentUsed"] >= threads[1]["percentUsed"]

        # Find thread-2 (should have higher usage)
        thread2 = next(t for t in threads if t["threadId"] == "thread-2")
        assert thread2["totalTokens"] == 15000
        assert thread2["promptTokens"] == 10000
        assert thread2["completionTokens"] == 5000

    def test_get_single_thread_budget(self, test_client):
        """Test GET /api/v1/usage/threads/{thread_id}."""
        from xml_pipeline.message_bus import get_budget_registry, reset_budget_registry

        reset_budget_registry()
        registry = get_budget_registry()
        registry.consume("my-thread", 3000, 1500)

        response = test_client.get("/api/v1/usage/threads/my-thread")
        assert response.status_code == 200

        data = response.json()
        assert data["threadId"] == "my-thread"
        assert data["totalTokens"] == 4500
        assert data["promptTokens"] == 3000
        assert data["completionTokens"] == 1500
        assert data["maxTokens"] == 100000  # default
        assert data["remaining"] == 95500
        assert data["percentUsed"] == 4.5
        assert data["isExhausted"] is False

    def test_get_single_thread_budget_not_found(self, test_client):
        """Test GET /usage/threads/{id} returns 404 for unknown thread."""
        from xml_pipeline.message_bus import reset_budget_registry

        reset_budget_registry()

        response = test_client.get("/api/v1/usage/threads/nonexistent")
        assert response.status_code == 404

    def test_get_agent_usage(self, test_client):
        """Test GET /api/v1/usage/agents/{agent_id}."""
        from xml_pipeline.llm import get_usage_tracker, reset_usage_tracker

        reset_usage_tracker()
        tracker = get_usage_tracker()

        tracker.record(
            thread_id="t1",
            agent_id="researcher",
            model="grok-4.1",
            provider="xai",
            prompt_tokens=1000,
            completion_tokens=500,
            latency_ms=100.0,
        )
        tracker.record(
            thread_id="t2",
            agent_id="researcher",
            model="grok-4.1",
            provider="xai",
            prompt_tokens=2000,
            completion_tokens=1000,
            latency_ms=150.0,
        )

        response = test_client.get("/api/v1/usage/agents/researcher")
        assert response.status_code == 200

        data = response.json()
        assert data["agentId"] == "researcher"
        assert data["totalTokens"] == 4500
        assert data["promptTokens"] == 3000
        assert data["completionTokens"] == 1500
        assert data["requestCount"] == 2

    def test_get_agent_usage_empty(self, test_client):
        """Test GET /usage/agents/{id} for agent with no usage."""
        from xml_pipeline.llm import reset_usage_tracker

        reset_usage_tracker()

        response = test_client.get("/api/v1/usage/agents/unknown")
        assert response.status_code == 200

        data = response.json()
        assert data["agentId"] == "unknown"
        assert data["totalTokens"] == 0
        assert data["requestCount"] == 0

    def test_get_model_usage(self, test_client):
        """Test GET /api/v1/usage/models/{model}."""
        from xml_pipeline.llm import get_usage_tracker, reset_usage_tracker

        reset_usage_tracker()
        tracker = get_usage_tracker()

        tracker.record(
            thread_id="t1",
            agent_id="a1",
            model="claude-sonnet-4",
            provider="anthropic",
            prompt_tokens=500,
            completion_tokens=200,
            latency_ms=80.0,
        )

        response = test_client.get("/api/v1/usage/models/claude-sonnet-4")
        assert response.status_code == 200

        data = response.json()
        assert data["model"] == "claude-sonnet-4"
        assert data["totalTokens"] == 700
        assert data["requestCount"] == 1

    def test_reset_usage(self, test_client):
        """Test POST /api/v1/usage/reset."""
        from xml_pipeline.llm import get_usage_tracker

        tracker = get_usage_tracker()
        tracker.record(
            thread_id="t1",
            agent_id="a1",
            model="test",
            provider="test",
            prompt_tokens=1000,
            completion_tokens=500,
            latency_ms=100.0,
        )

        response = test_client.post("/api/v1/usage/reset")
        assert response.status_code == 200

        data = response.json()
        assert data["success"] is True

        # Verify usage was reset
        response = test_client.get("/api/v1/usage")
        data = response.json()
        assert data["usage"]["totals"]["totalTokens"] == 0
        assert data["usage"]["totals"]["requestCount"] == 0

    def test_usage_cost_estimation(self, test_client):
        """Test that usage includes cost estimates."""
        from xml_pipeline.llm import get_usage_tracker, reset_usage_tracker

        reset_usage_tracker()
        tracker = get_usage_tracker()

        # Use known model with pricing
        tracker.record(
            thread_id="t1",
            agent_id="a1",
            model="grok-4.1",  # $3/M prompt, $15/M completion
            provider="xai",
            prompt_tokens=1_000_000,  # $3
            completion_tokens=1_000_000,  # $15
            latency_ms=100.0,
        )

        response = test_client.get("/api/v1/usage")
        data = response.json()

        # Cost should be approximately $18
        assert data["usage"]["totals"]["totalCost"] == 18.0


# ============================================================================
# Test ConnectionManager (unit tests)
# ============================================================================

class TestConnectionManager:
    """Test ConnectionManager subscription filtering logic."""

    def _make_mgr(self):
        from xml_pipeline.server.websocket import ConnectionManager, ConnectionLimiter
        return ConnectionManager(ConnectionLimiter())

    def test_should_send_no_filters(self):
        """With no subscription filters, all events should be sent."""
        mgr = self._make_mgr()

        ws = MagicMock()
        mgr.subscriptions[ws] = SubscribeRequest()

        assert mgr.should_send(ws, {"event": "message"}) is True
        assert mgr.should_send(ws, {"event": "agent_state"}) is True

    def test_should_send_event_type_filter(self):
        """Filter by event type."""
        mgr = self._make_mgr()

        ws = MagicMock()
        mgr.subscriptions[ws] = SubscribeRequest(events=["message"])

        assert mgr.should_send(ws, {"event": "message"}) is True
        assert mgr.should_send(ws, {"event": "agent_state"}) is False

    def test_should_send_thread_filter(self):
        """Filter by thread ID."""
        mgr = self._make_mgr()

        ws = MagicMock()
        mgr.subscriptions[ws] = SubscribeRequest(threads=["thread-1"])

        assert mgr.should_send(ws, {"event": "x", "thread_id": "thread-1"}) is True
        assert mgr.should_send(ws, {"event": "x", "thread_id": "thread-2"}) is False

    def test_should_send_agent_filter(self):
        """Filter by agent name."""
        mgr = self._make_mgr()

        ws = MagicMock()
        mgr.subscriptions[ws] = SubscribeRequest(agents=["greeter"])

        assert mgr.should_send(ws, {"event": "x", "agent": "greeter"}) is True
        assert mgr.should_send(ws, {"event": "x", "agent": "shouter"}) is False

    def test_should_send_message_from_to_filter(self):
        """For message events, filter by from/to matching agents."""
        mgr = self._make_mgr()

        ws = MagicMock()
        mgr.subscriptions[ws] = SubscribeRequest(agents=["greeter"])

        # from matches
        assert mgr.should_send(ws, {
            "event": "message",
            "message": {"from": "greeter", "to": "shouter"},
        }) is True

        # to matches
        assert mgr.should_send(ws, {
            "event": "message",
            "message": {"from": "shouter", "to": "greeter"},
        }) is True

        # neither matches
        assert mgr.should_send(ws, {
            "event": "message",
            "message": {"from": "shouter", "to": "logger"},
        }) is False

    def test_should_send_payload_type_filter(self):
        """Filter by payload type."""
        mgr = self._make_mgr()

        ws = MagicMock()
        mgr.subscriptions[ws] = SubscribeRequest(payload_types=["Greeting"])

        assert mgr.should_send(ws, {
            "event": "message",
            "message": {"payloadType": "Greeting"},
        }) is True

        assert mgr.should_send(ws, {
            "event": "message",
            "message": {"payloadType": "ShoutResponse"},
        }) is False

    @pytest.mark.asyncio
    async def test_broadcast_to_matching(self):
        """Broadcast sends only to matching connections."""
        mgr = self._make_mgr()

        ws1 = AsyncMock()
        ws2 = AsyncMock()
        mgr.active_connections = {ws1, ws2}
        mgr.subscriptions[ws1] = SubscribeRequest(events=["message"])
        mgr.subscriptions[ws2] = SubscribeRequest(events=["agent_state"])

        await mgr.broadcast({"event": "message"})

        ws1.send_json.assert_called_once()
        ws2.send_json.assert_not_called()

    @pytest.mark.asyncio
    async def test_broadcast_cleans_disconnected(self):
        """Broadcast cleans up connections that raise exceptions."""
        mgr = self._make_mgr()

        ws = AsyncMock()
        ws.send_json.side_effect = Exception("disconnected")
        mgr.active_connections = {ws}
        mgr.subscriptions[ws] = SubscribeRequest()

        await mgr.broadcast({"event": "test"})

        assert ws not in mgr.active_connections
        assert ws not in mgr.subscriptions

    def test_disconnect(self):
        """Disconnect removes connection from both sets."""
        mgr = self._make_mgr()

        ws = MagicMock()
        mgr.active_connections.add(ws)
        mgr.subscriptions[ws] = SubscribeRequest()

        mgr.disconnect(ws)

        assert ws not in mgr.active_connections
        assert ws not in mgr.subscriptions


# ============================================================================
# Test MessageStreamManager (unit tests)
# ============================================================================

class TestMessageStreamManager:
    """Test MessageStreamManager filtering logic."""

    def _make_mgr(self):
        from xml_pipeline.server.websocket import MessageStreamManager, ConnectionLimiter
        return MessageStreamManager(ConnectionLimiter())

    def test_should_send_no_filter(self):
        """With no filter, all messages should be sent."""
        mgr = self._make_mgr()

        ws = MagicMock()
        mgr.filters[ws] = {}

        assert mgr.should_send(ws, {"from": "greeter"}) is True

    def test_filter_by_agents(self):
        """Filter messages by agent from/to."""
        mgr = self._make_mgr()

        ws = MagicMock()
        mgr.filters[ws] = {"agents": ["greeter"]}

        assert mgr.should_send(ws, {"from": "greeter", "to": "shouter"}) is True
        assert mgr.should_send(ws, {"from": "shouter", "to": "greeter"}) is True
        assert mgr.should_send(ws, {"from": "shouter", "to": "logger"}) is False

    def test_filter_by_threads(self):
        """Filter messages by thread ID."""
        mgr = self._make_mgr()

        ws = MagicMock()
        mgr.filters[ws] = {"threads": ["t1"]}

        assert mgr.should_send(ws, {"thread_id": "t1"}) is True
        assert mgr.should_send(ws, {"thread_id": "t2"}) is False

    def test_filter_by_payload_types(self):
        """Filter messages by payload type."""
        mgr = self._make_mgr()

        ws = MagicMock()
        mgr.filters[ws] = {"payload_types": ["Greeting"]}

        assert mgr.should_send(ws, {"payloadType": "Greeting"}) is True
        assert mgr.should_send(ws, {"payloadType": "Other"}) is False

    @pytest.mark.asyncio
    async def test_broadcast_message(self):
        """Broadcast sends to matching connections."""
        mgr = self._make_mgr()

        ws1 = AsyncMock()
        ws2 = AsyncMock()
        mgr.active_connections = {ws1, ws2}
        mgr.filters[ws1] = {"agents": ["greeter"]}
        mgr.filters[ws2] = {"agents": ["logger"]}

        await mgr.broadcast_message({"from": "greeter", "to": "shouter"})

        ws1.send_json.assert_called_once()
        ws2.send_json.assert_not_called()

    @pytest.mark.asyncio
    async def test_broadcast_cleans_disconnected(self):
        """Broadcast cleans up failed connections."""
        mgr = self._make_mgr()

        ws = AsyncMock()
        ws.send_json.side_effect = Exception("disconnected")
        mgr.active_connections = {ws}
        mgr.filters[ws] = {}

        await mgr.broadcast_message({"from": "greeter"})

        assert ws not in mgr.active_connections

    def test_disconnect(self):
        """Disconnect removes connection from both sets."""
        mgr = self._make_mgr()

        ws = MagicMock()
        mgr.active_connections.add(ws)
        mgr.filters[ws] = {}

        mgr.disconnect(ws)

        assert ws not in mgr.active_connections
        assert ws not in mgr.filters


# ============================================================================
# Test WebSocket Integration (via TestClient)
# ============================================================================

class TestWebSocketIntegration:
    """Integration tests for WebSocket endpoints via TestClient."""

    def test_ws_connect_receives_snapshot(self, test_client):
        """WebSocket /ws sends connected event with state snapshot."""
        with test_client.websocket_connect("/ws") as websocket:
            data = websocket.receive_json()
            assert data["event"] == "connected"
            assert "organism" in data
            assert "agents" in data
            assert isinstance(data["agents"], list)
            assert "threads" in data

    def test_ws_subscribe_updates_filters(self, test_client):
        """Subscribe command updates connection filters."""
        with test_client.websocket_connect("/ws") as websocket:
            websocket.receive_json()  # consume connected event

            websocket.send_json({
                "cmd": "subscribe",
                "agents": ["greeter"],
                "events": ["message", "agent_state"],
            })

            data = websocket.receive_json()
            assert data["event"] == "subscribed"
            assert "filters" in data

    def test_ws_inject_missing_to(self, test_client):
        """Inject without 'to' returns error."""
        with test_client.websocket_connect("/ws") as websocket:
            websocket.receive_json()

            websocket.send_json({"cmd": "inject", "payload": {}})

            data = websocket.receive_json()
            assert data["event"] == "error"
            assert "to" in data["error"].lower()

    def test_ws_unknown_command_error(self, test_client):
        """Unknown command returns error."""
        with test_client.websocket_connect("/ws") as websocket:
            websocket.receive_json()

            websocket.send_json({"cmd": "do_something"})

            data = websocket.receive_json()
            assert data["event"] == "error"
            assert "do_something" in data["error"]

    def test_ws_messages_subscribe(self, test_client):
        """WebSocket /ws/messages subscribe command works."""
        with test_client.websocket_connect("/ws/messages") as websocket:
            websocket.send_json({
                "cmd": "subscribe",
                "filter": {"agents": ["greeter"], "threads": ["t1"]},
            })

            data = websocket.receive_json()
            assert data["event"] == "subscribed"
            assert data["filter"]["agents"] == ["greeter"]

    def test_ws_messages_unknown_command(self, test_client):
        """Unknown command on /ws/messages returns error."""
        with test_client.websocket_connect("/ws/messages") as websocket:
            websocket.send_json({"cmd": "bad"})

            data = websocket.receive_json()
            assert data["event"] == "error"


# ============================================================================
# Test REST API Authentication (security hitlist #40)
# ============================================================================

class TestRestAPIAuth:
    """Test API key authentication on mutating endpoints."""

    API_KEY = "test-secret-key-12345"

    @pytest.fixture(autouse=True)
    def _enable_auth(self):
        """Enable auth for each test, reset after."""
        from xml_pipeline.server.auth import configure_auth
        with patch.dict(os.environ, {"TEST_API_KEY": self.API_KEY}):
            configure_auth("TEST_API_KEY")
            yield
        # Reset auth to disabled
        configure_auth("")

    @pytest.fixture
    def auth_client(self):
        """Test client with auth enabled."""
        pump = MockStreamPump()
        pump.config.auth_api_key_env = "TEST_API_KEY"
        app = create_app(pump)
        return TestClient(app)

    def _auth_header(self) -> dict:
        return {"Authorization": f"Bearer {self.API_KEY}"}

    # ── 401 without auth header ───────────────────────────────────────

    def test_inject_requires_auth(self, auth_client):
        resp = auth_client.post("/api/v1/inject", json={"to": "greeter", "payload": {}})
        assert resp.status_code == 401

    def test_kill_requires_auth(self, auth_client):
        resp = auth_client.post("/api/v1/threads/some-id/kill")
        assert resp.status_code == 401

    def test_pause_requires_auth(self, auth_client):
        resp = auth_client.post("/api/v1/agents/greeter/pause")
        assert resp.status_code == 401

    def test_resume_requires_auth(self, auth_client):
        resp = auth_client.post("/api/v1/agents/greeter/resume")
        assert resp.status_code == 401

    def test_reload_requires_auth(self, auth_client):
        resp = auth_client.post("/api/v1/organism/reload")
        assert resp.status_code == 401

    def test_stop_requires_auth(self, auth_client):
        resp = auth_client.post("/api/v1/organism/stop")
        assert resp.status_code == 401

    def test_usage_reset_requires_auth(self, auth_client):
        resp = auth_client.post("/api/v1/usage/reset")
        assert resp.status_code == 401

    # ── 403 with wrong token ─────────────────────────────────────────

    def test_inject_wrong_token(self, auth_client):
        resp = auth_client.post(
            "/api/v1/inject",
            json={"to": "greeter", "payload": {}},
            headers={"Authorization": "Bearer wrong-key"},
        )
        assert resp.status_code == 403

    # ── 200 with correct token ───────────────────────────────────────

    def test_inject_with_auth(self, auth_client):
        resp = auth_client.post(
            "/api/v1/inject",
            json={"to": "greeter", "payload": {"name": "Dan"}},
            headers=self._auth_header(),
        )
        assert resp.status_code == 200

    def test_pause_with_auth(self, auth_client):
        resp = auth_client.post(
            "/api/v1/agents/greeter/pause",
            headers=self._auth_header(),
        )
        assert resp.status_code == 200

    def test_stop_with_auth(self, auth_client):
        resp = auth_client.post(
            "/api/v1/organism/stop",
            headers=self._auth_header(),
        )
        assert resp.status_code == 200

    # ── GET endpoints remain open ────────────────────────────────────

    def test_get_organism_no_auth_needed(self, auth_client):
        resp = auth_client.get("/api/v1/organism")
        assert resp.status_code == 200

    def test_get_agents_no_auth_needed(self, auth_client):
        resp = auth_client.get("/api/v1/agents")
        assert resp.status_code == 200

    def test_health_no_auth_needed(self, auth_client):
        resp = auth_client.get("/health")
        assert resp.status_code == 200

    # ── WebSocket inject auth ────────────────────────────────────────

    def test_ws_inject_requires_token(self, auth_client):
        """WebSocket inject without token returns error when auth enabled."""
        with auth_client.websocket_connect("/ws") as websocket:
            websocket.receive_json()  # connected event
            websocket.send_json({
                "cmd": "inject",
                "to": "greeter",
                "payload": {"name": "Dan"},
            })
            data = websocket.receive_json()
            assert data["event"] == "error"
            assert "auth" in data["error"].lower()

    def test_ws_inject_with_token(self, auth_client):
        """WebSocket inject with valid token succeeds."""
        with auth_client.websocket_connect("/ws") as websocket:
            websocket.receive_json()  # connected event
            websocket.send_json({
                "cmd": "inject",
                "to": "greeter",
                "payload": {"name": "Dan"},
                "token": self.API_KEY,
            })
            data = websocket.receive_json()
            assert data["event"] == "injected"
