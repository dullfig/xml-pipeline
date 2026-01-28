"""
Tests for the AgentServer API.

Tests the REST API endpoints and WebSocket connections.
"""

import asyncio
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
