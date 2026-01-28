"""
test_hot_reload.py — Tests for hot-reload functionality.

Tests that StreamPump can reload its configuration at runtime,
adding/removing/updating listeners without restarting.
"""

import pytest
import tempfile
import yaml
from pathlib import Path
from dataclasses import dataclass

from third_party.xmlable import xmlify


# ============================================================================
# Test Payloads
# ============================================================================

@xmlify
@dataclass
class TestPayloadA:
    """Test payload A."""
    value: str


@xmlify
@dataclass
class TestPayloadB:
    """Test payload B."""
    count: int


@xmlify
@dataclass
class TestPayloadC:
    """Test payload C."""
    name: str


# ============================================================================
# Test Handlers
# ============================================================================

async def handler_a(payload, metadata):
    """Handler A."""
    return None


async def handler_b(payload, metadata):
    """Handler B."""
    return None


async def handler_c(payload, metadata):
    """Handler C."""
    return None


async def handler_a_updated(payload, metadata):
    """Handler A (updated)."""
    return None


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def base_config():
    """Base configuration with one listener."""
    return {
        "organism": {"name": "test-organism"},
        "listeners": [
            {
                "name": "listener-a",
                "payload_class": "tests.test_hot_reload.TestPayloadA",
                "handler": "tests.test_hot_reload.handler_a",
                "description": "Listener A",
            }
        ],
    }


@pytest.fixture
def config_file(base_config):
    """Create a temporary config file."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False
    ) as f:
        yaml.dump(base_config, f)
        f.flush()
        yield Path(f.name)
    # Cleanup
    Path(f.name).unlink(missing_ok=True)


@pytest.fixture
def pump(config_file):
    """Create a StreamPump from the config file."""
    from xml_pipeline.message_bus import StreamPump, ConfigLoader

    config = ConfigLoader.load(str(config_file))
    pump = StreamPump(config, config_path=str(config_file))
    pump.register_all()
    return pump


# ============================================================================
# Tests: Reload Adds New Listeners
# ============================================================================

class TestReloadAddListeners:
    """Test that reload adds new listeners."""

    def test_add_new_listener(self, pump, config_file):
        """New listeners in config should be registered."""
        # Verify initial state
        assert "listener-a" in pump.listeners
        assert "listener-b" not in pump.listeners

        # Update config to add listener-b
        new_config = {
            "organism": {"name": "test-organism"},
            "listeners": [
                {
                    "name": "listener-a",
                    "payload_class": "tests.test_hot_reload.TestPayloadA",
                    "handler": "tests.test_hot_reload.handler_a",
                    "description": "Listener A",
                },
                {
                    "name": "listener-b",
                    "payload_class": "tests.test_hot_reload.TestPayloadB",
                    "handler": "tests.test_hot_reload.handler_b",
                    "description": "Listener B",
                },
            ],
        }
        with open(config_file, "w") as f:
            yaml.dump(new_config, f)

        # Reload
        result = pump.reload_config()

        # Verify
        assert result.success
        assert "listener-b" in result.added_listeners
        assert "listener-b" in pump.listeners
        assert pump.listeners["listener-b"].description == "Listener B"

    def test_add_multiple_listeners(self, pump, config_file):
        """Multiple new listeners can be added at once."""
        new_config = {
            "organism": {"name": "test-organism"},
            "listeners": [
                {
                    "name": "listener-a",
                    "payload_class": "tests.test_hot_reload.TestPayloadA",
                    "handler": "tests.test_hot_reload.handler_a",
                    "description": "Listener A",
                },
                {
                    "name": "listener-b",
                    "payload_class": "tests.test_hot_reload.TestPayloadB",
                    "handler": "tests.test_hot_reload.handler_b",
                    "description": "Listener B",
                },
                {
                    "name": "listener-c",
                    "payload_class": "tests.test_hot_reload.TestPayloadC",
                    "handler": "tests.test_hot_reload.handler_c",
                    "description": "Listener C",
                },
            ],
        }
        with open(config_file, "w") as f:
            yaml.dump(new_config, f)

        result = pump.reload_config()

        assert result.success
        assert set(result.added_listeners) == {"listener-b", "listener-c"}
        assert "listener-b" in pump.listeners
        assert "listener-c" in pump.listeners


# ============================================================================
# Tests: Reload Removes Listeners
# ============================================================================

class TestReloadRemoveListeners:
    """Test that reload removes listeners no longer in config."""

    def test_remove_listener(self, config_file):
        """Listeners not in new config should be unregistered."""
        from xml_pipeline.message_bus import StreamPump, ConfigLoader

        # Create pump with two listeners
        initial_config = {
            "organism": {"name": "test-organism"},
            "listeners": [
                {
                    "name": "listener-a",
                    "payload_class": "tests.test_hot_reload.TestPayloadA",
                    "handler": "tests.test_hot_reload.handler_a",
                    "description": "Listener A",
                },
                {
                    "name": "listener-b",
                    "payload_class": "tests.test_hot_reload.TestPayloadB",
                    "handler": "tests.test_hot_reload.handler_b",
                    "description": "Listener B",
                },
            ],
        }
        with open(config_file, "w") as f:
            yaml.dump(initial_config, f)

        config = ConfigLoader.load(str(config_file))
        pump = StreamPump(config, config_path=str(config_file))
        pump.register_all()

        assert "listener-a" in pump.listeners
        assert "listener-b" in pump.listeners

        # Update config to remove listener-b
        new_config = {
            "organism": {"name": "test-organism"},
            "listeners": [
                {
                    "name": "listener-a",
                    "payload_class": "tests.test_hot_reload.TestPayloadA",
                    "handler": "tests.test_hot_reload.handler_a",
                    "description": "Listener A",
                },
            ],
        }
        with open(config_file, "w") as f:
            yaml.dump(new_config, f)

        # Reload
        result = pump.reload_config()

        # Verify
        assert result.success
        assert "listener-b" in result.removed_listeners
        assert "listener-b" not in pump.listeners
        assert "listener-a" in pump.listeners  # Still exists


# ============================================================================
# Tests: Reload Updates Listeners
# ============================================================================

class TestReloadUpdateListeners:
    """Test that reload updates changed listeners."""

    def test_update_handler(self, pump, config_file):
        """Changed handler should be updated."""
        old_handler = pump.listeners["listener-a"].handler

        # Update config with new handler
        new_config = {
            "organism": {"name": "test-organism"},
            "listeners": [
                {
                    "name": "listener-a",
                    "payload_class": "tests.test_hot_reload.TestPayloadA",
                    "handler": "tests.test_hot_reload.handler_a_updated",
                    "description": "Listener A",
                },
            ],
        }
        with open(config_file, "w") as f:
            yaml.dump(new_config, f)

        result = pump.reload_config()

        assert result.success
        assert "listener-a" in result.updated_listeners
        assert pump.listeners["listener-a"].handler != old_handler

    def test_update_description(self, pump, config_file):
        """Changed description should be updated."""
        # Update config with new description
        new_config = {
            "organism": {"name": "test-organism"},
            "listeners": [
                {
                    "name": "listener-a",
                    "payload_class": "tests.test_hot_reload.TestPayloadA",
                    "handler": "tests.test_hot_reload.handler_a",
                    "description": "Updated description",
                },
            ],
        }
        with open(config_file, "w") as f:
            yaml.dump(new_config, f)

        result = pump.reload_config()

        assert result.success
        assert "listener-a" in result.updated_listeners
        assert pump.listeners["listener-a"].description == "Updated description"

    def test_update_peers(self, pump, config_file):
        """Changed peers should be updated."""
        # Update config with peers
        new_config = {
            "organism": {"name": "test-organism"},
            "listeners": [
                {
                    "name": "listener-a",
                    "payload_class": "tests.test_hot_reload.TestPayloadA",
                    "handler": "tests.test_hot_reload.handler_a",
                    "description": "Listener A",
                    "agent": True,
                    "peers": ["listener-b"],
                },
            ],
        }
        with open(config_file, "w") as f:
            yaml.dump(new_config, f)

        result = pump.reload_config()

        assert result.success
        assert "listener-a" in result.updated_listeners
        assert pump.listeners["listener-a"].peers == ["listener-b"]

    def test_unchanged_listener_not_updated(self, pump, config_file):
        """Unchanged listeners should not appear in updated list."""
        # Reload same config
        result = pump.reload_config()

        assert result.success
        assert "listener-a" not in result.updated_listeners
        assert "listener-a" not in result.added_listeners
        assert "listener-a" not in result.removed_listeners


# ============================================================================
# Tests: Reload Events
# ============================================================================

class TestReloadEvents:
    """Test that reload emits events."""

    def test_reload_emits_event(self, pump, config_file):
        """Reload should emit ReloadEvent."""
        from xml_pipeline.message_bus import ReloadEvent

        events = []

        def capture_event(event):
            events.append(event)

        pump.subscribe_events(capture_event)

        # Add a listener
        new_config = {
            "organism": {"name": "test-organism"},
            "listeners": [
                {
                    "name": "listener-a",
                    "payload_class": "tests.test_hot_reload.TestPayloadA",
                    "handler": "tests.test_hot_reload.handler_a",
                    "description": "Listener A",
                },
                {
                    "name": "listener-b",
                    "payload_class": "tests.test_hot_reload.TestPayloadB",
                    "handler": "tests.test_hot_reload.handler_b",
                    "description": "Listener B",
                },
            ],
        }
        with open(config_file, "w") as f:
            yaml.dump(new_config, f)

        pump.reload_config()

        # Check event was emitted
        reload_events = [e for e in events if isinstance(e, ReloadEvent)]
        assert len(reload_events) == 1
        assert reload_events[0].success
        assert "listener-b" in reload_events[0].added_listeners


# ============================================================================
# Tests: Reload Error Handling
# ============================================================================

class TestReloadErrors:
    """Test reload error handling."""

    def test_reload_no_config_path(self):
        """Reload without config path should fail gracefully."""
        from xml_pipeline.message_bus import StreamPump, OrganismConfig

        config = OrganismConfig(name="test")
        pump = StreamPump(config)  # No config_path

        result = pump.reload_config()

        assert not result.success
        assert "No config path" in result.error

    def test_reload_invalid_config(self, pump, config_file):
        """Reload with invalid config should fail gracefully."""
        # Write invalid YAML
        with open(config_file, "w") as f:
            f.write("invalid: yaml: content: [")

        result = pump.reload_config()

        assert not result.success
        assert result.error is not None

    def test_reload_missing_file(self, pump):
        """Reload with missing file should fail gracefully."""
        pump.config_path = "/nonexistent/path.yaml"

        result = pump.reload_config()

        assert not result.success
        assert result.error is not None


# ============================================================================
# Tests: System Listeners Protected
# ============================================================================

class TestSystemListenersProtected:
    """Test that system listeners are not affected by reload."""

    def test_system_listeners_not_removed(self, config_file):
        """System listeners should not be removed during reload."""
        from xml_pipeline.message_bus import StreamPump, ConfigLoader, ListenerConfig

        # Load config
        config = ConfigLoader.load(str(config_file))
        pump = StreamPump(config, config_path=str(config_file))

        # Manually add a system listener (simulating bootstrap)
        @xmlify
        @dataclass
        class SystemPayload:
            msg: str

        async def system_handler(p, m):
            return None

        system_lc = ListenerConfig(
            name="system.test",
            payload_class_path="tests.test_hot_reload.TestPayloadA",
            handler_path="tests.test_hot_reload.handler_a",
            description="System test listener",
            payload_class=SystemPayload,
            handler=system_handler,
        )
        pump.register_listener(system_lc)
        pump.register_all()

        assert "system.test" in pump.listeners

        # Reload with empty config (would remove all non-system listeners)
        new_config = {
            "organism": {"name": "test-organism"},
            "listeners": [],
        }
        with open(config_file, "w") as f:
            yaml.dump(new_config, f)

        result = pump.reload_config()

        # System listener should still exist
        assert result.success
        assert "system.test" in pump.listeners
        assert "system.test" not in result.removed_listeners


# ============================================================================
# Tests: Routing Table Updates
# ============================================================================

class TestRoutingTableUpdates:
    """Test that routing table is updated correctly."""

    def test_routing_table_updated_on_add(self, pump, config_file):
        """Routing table should include new listeners."""
        # Add listener-b
        new_config = {
            "organism": {"name": "test-organism"},
            "listeners": [
                {
                    "name": "listener-a",
                    "payload_class": "tests.test_hot_reload.TestPayloadA",
                    "handler": "tests.test_hot_reload.handler_a",
                    "description": "Listener A",
                },
                {
                    "name": "listener-b",
                    "payload_class": "tests.test_hot_reload.TestPayloadB",
                    "handler": "tests.test_hot_reload.handler_b",
                    "description": "Listener B",
                },
            ],
        }
        with open(config_file, "w") as f:
            yaml.dump(new_config, f)

        pump.reload_config()

        # Check routing table
        expected_root_tag = "listener-b.testpayloadb"
        assert expected_root_tag in pump.routing_table

    def test_routing_table_updated_on_remove(self, config_file):
        """Routing table should not include removed listeners."""
        from xml_pipeline.message_bus import StreamPump, ConfigLoader

        # Create pump with two listeners
        initial_config = {
            "organism": {"name": "test-organism"},
            "listeners": [
                {
                    "name": "listener-a",
                    "payload_class": "tests.test_hot_reload.TestPayloadA",
                    "handler": "tests.test_hot_reload.handler_a",
                    "description": "Listener A",
                },
                {
                    "name": "listener-b",
                    "payload_class": "tests.test_hot_reload.TestPayloadB",
                    "handler": "tests.test_hot_reload.handler_b",
                    "description": "Listener B",
                },
            ],
        }
        with open(config_file, "w") as f:
            yaml.dump(initial_config, f)

        config = ConfigLoader.load(str(config_file))
        pump = StreamPump(config, config_path=str(config_file))
        pump.register_all()

        # Verify both in routing table
        assert "listener-b.testpayloadb" in pump.routing_table

        # Remove listener-b
        new_config = {
            "organism": {"name": "test-organism"},
            "listeners": [
                {
                    "name": "listener-a",
                    "payload_class": "tests.test_hot_reload.TestPayloadA",
                    "handler": "tests.test_hot_reload.handler_a",
                    "description": "Listener A",
                },
            ],
        }
        with open(config_file, "w") as f:
            yaml.dump(new_config, f)

        pump.reload_config()

        # Check routing table no longer has listener-b
        assert "listener-b.testpayloadb" not in pump.routing_table
