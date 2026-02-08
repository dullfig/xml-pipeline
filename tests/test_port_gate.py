"""Tests for the network port gate.

Tests the PortGate security gate that enforces port declarations from
organism.yaml. Only declared ports can be opened — empty config = zero
ports allowed (secure default).
"""

import pytest

from xml_pipeline.network.port_gate import (
    PortAllocation,
    PortDeniedError,
    PortGate,
    get_port_gate,
    set_port_gate,
    reset_port_gate,
)
from xml_pipeline.message_bus.pump_config import OrganismConfig
from xml_pipeline.message_bus.config_loader import ConfigLoader


# ═══════════════════════════════════════════════════════════════════════════
# TestPortAllocation — frozen dataclass
# ═══════════════════════════════════════════════════════════════════════════

class TestPortAllocation:
    """PortAllocation is a frozen dataclass with sensible defaults."""

    def test_frozen(self) -> None:
        alloc = PortAllocation(port=9000, listener="webhook")
        with pytest.raises(AttributeError):
            alloc.port = 9001  # type: ignore[misc]

    def test_defaults(self) -> None:
        alloc = PortAllocation(port=9000)
        assert alloc.bind == "127.0.0.1"
        assert alloc.listener == ""
        assert alloc.protocol == "tcp"

    def test_equality(self) -> None:
        a = PortAllocation(port=9000, bind="0.0.0.0", listener="ws", protocol="tcp")
        b = PortAllocation(port=9000, bind="0.0.0.0", listener="ws", protocol="tcp")
        assert a == b

    def test_inequality(self) -> None:
        a = PortAllocation(port=9000, listener="ws")
        b = PortAllocation(port=9001, listener="ws")
        assert a != b


# ═══════════════════════════════════════════════════════════════════════════
# TestPortGateRequest — request validation
# ═══════════════════════════════════════════════════════════════════════════

class TestPortGateRequest:
    """PortGate.request() validates against declared allocations."""

    def _gate(self) -> PortGate:
        return PortGate([
            PortAllocation(port=9000, bind="127.0.0.1", listener="webhook", protocol="tcp"),
            PortAllocation(port=9001, bind="0.0.0.0", listener="api", protocol="tcp"),
            PortAllocation(port=9002, bind="127.0.0.1", listener="stream", protocol="udp"),
        ])

    def test_exact_match_succeeds(self) -> None:
        gate = self._gate()
        alloc = gate.request(9000, "127.0.0.1", "webhook", "tcp")
        assert alloc.port == 9000
        assert alloc.listener == "webhook"

    def test_wrong_port_denied(self) -> None:
        gate = self._gate()
        with pytest.raises(PortDeniedError, match="not declared"):
            gate.request(8888, "127.0.0.1", "webhook", "tcp")

    def test_wrong_bind_denied(self) -> None:
        gate = self._gate()
        with pytest.raises(PortDeniedError, match="not declared"):
            gate.request(9000, "0.0.0.0", "webhook", "tcp")

    def test_wrong_listener_denied(self) -> None:
        gate = self._gate()
        with pytest.raises(PortDeniedError, match="not declared"):
            gate.request(9000, "127.0.0.1", "other-listener", "tcp")

    def test_wrong_protocol_denied(self) -> None:
        gate = self._gate()
        with pytest.raises(PortDeniedError, match="not declared"):
            gate.request(9000, "127.0.0.1", "webhook", "udp")

    def test_already_in_use(self) -> None:
        gate = self._gate()
        gate.request(9000, "127.0.0.1", "webhook", "tcp")
        with pytest.raises(PortDeniedError, match="already in use"):
            gate.request(9000, "127.0.0.1", "webhook", "tcp")

    def test_multiple_allocations(self) -> None:
        gate = self._gate()
        a1 = gate.request(9000, "127.0.0.1", "webhook", "tcp")
        a2 = gate.request(9001, "0.0.0.0", "api", "tcp")
        assert a1.port == 9000
        assert a2.port == 9001

    def test_udp_protocol_match(self) -> None:
        gate = self._gate()
        alloc = gate.request(9002, "127.0.0.1", "stream", "udp")
        assert alloc.protocol == "udp"

    def test_default_protocol_tcp(self) -> None:
        gate = self._gate()
        # Default protocol is tcp, which should match the tcp allocation
        alloc = gate.request(9000, "127.0.0.1", "webhook")
        assert alloc.protocol == "tcp"

    def test_denied_error_message_informative(self) -> None:
        gate = self._gate()
        with pytest.raises(PortDeniedError) as exc_info:
            gate.request(7777, "127.0.0.1", "unknown", "tcp")
        assert "7777" in str(exc_info.value)
        assert "network.ports" in str(exc_info.value)


# ═══════════════════════════════════════════════════════════════════════════
# TestPortGateRelease — release tracking
# ═══════════════════════════════════════════════════════════════════════════

class TestPortGateRelease:
    """PortGate.release() frees ports for reuse."""

    def test_release_tracked_port(self) -> None:
        gate = PortGate([PortAllocation(port=9000, listener="ws")])
        gate.request(9000, "127.0.0.1", "ws")
        assert len(gate.active_ports()) == 1
        gate.release(9000)
        assert len(gate.active_ports()) == 0

    def test_release_unknown_noop(self) -> None:
        gate = PortGate([])
        gate.release(9999)  # Should not raise

    def test_re_request_after_release(self) -> None:
        gate = PortGate([PortAllocation(port=9000, listener="ws")])
        gate.request(9000, "127.0.0.1", "ws")
        gate.release(9000)
        alloc = gate.request(9000, "127.0.0.1", "ws")
        assert alloc.port == 9000


# ═══════════════════════════════════════════════════════════════════════════
# TestPortGateShutdown — bulk release
# ═══════════════════════════════════════════════════════════════════════════

class TestPortGateShutdown:
    """PortGate.shutdown() releases all active ports."""

    def test_releases_all(self) -> None:
        gate = PortGate([
            PortAllocation(port=9000, listener="a"),
            PortAllocation(port=9001, listener="b"),
        ])
        gate.request(9000, "127.0.0.1", "a")
        gate.request(9001, "127.0.0.1", "b")
        assert len(gate.active_ports()) == 2
        gate.shutdown()
        assert len(gate.active_ports()) == 0

    def test_idempotent(self) -> None:
        gate = PortGate([])
        gate.shutdown()
        gate.shutdown()  # Should not raise

    def test_active_empty_after(self) -> None:
        gate = PortGate([PortAllocation(port=9000, listener="x")])
        gate.request(9000, "127.0.0.1", "x")
        gate.shutdown()
        assert gate.active_ports() == []


# ═══════════════════════════════════════════════════════════════════════════
# TestPortGateSecureDefault — empty allocations = deny all
# ═══════════════════════════════════════════════════════════════════════════

class TestPortGateSecureDefault:
    """Empty allocations means zero ports can be opened."""

    def test_empty_denies_all(self) -> None:
        gate = PortGate([])
        with pytest.raises(PortDeniedError):
            gate.request(80, "0.0.0.0", "anything")

    def test_empty_denies_common_ports(self) -> None:
        gate = PortGate([])
        for port in [80, 443, 8080, 8443, 3000, 5000]:
            with pytest.raises(PortDeniedError):
                gate.request(port, "127.0.0.1", "any")


# ═══════════════════════════════════════════════════════════════════════════
# TestPortGateSingleton — global singleton functions
# ═══════════════════════════════════════════════════════════════════════════

class TestPortGateSingleton:
    """get/set/reset_port_gate manage the global singleton."""

    def setup_method(self) -> None:
        reset_port_gate()

    def teardown_method(self) -> None:
        reset_port_gate()

    def test_get_before_set_raises(self) -> None:
        with pytest.raises(RuntimeError, match="not initialized"):
            get_port_gate()

    def test_set_then_get(self) -> None:
        gate = PortGate([])
        set_port_gate(gate)
        assert get_port_gate() is gate

    def test_reset_clears(self) -> None:
        set_port_gate(PortGate([]))
        reset_port_gate()
        with pytest.raises(RuntimeError):
            get_port_gate()


# ═══════════════════════════════════════════════════════════════════════════
# TestConfigParsing — YAML network: section parsing
# ═══════════════════════════════════════════════════════════════════════════

class TestConfigParsing:
    """ConfigLoader parses network: section into OrganismConfig.network_ports."""

    def test_full_port_declaration(self) -> None:
        raw = {
            "organism": {"name": "test"},
            "network": {
                "ports": [
                    {"port": 9000, "bind": "0.0.0.0", "listener": "webhook", "protocol": "tcp"},
                ]
            },
        }
        config = ConfigLoader._parse(raw)
        assert len(config.network_ports) == 1
        p = config.network_ports[0]
        assert p["port"] == 9000
        assert p["bind"] == "0.0.0.0"
        assert p["listener"] == "webhook"
        assert p["protocol"] == "tcp"

    def test_port_defaults(self) -> None:
        raw = {
            "organism": {"name": "test"},
            "network": {
                "ports": [{"port": 9000}],
            },
        }
        config = ConfigLoader._parse(raw)
        p = config.network_ports[0]
        assert p["bind"] == "127.0.0.1"
        assert p["listener"] == ""
        assert p["protocol"] == "tcp"

    def test_empty_network_section(self) -> None:
        raw = {
            "organism": {"name": "test"},
            "network": {},
        }
        config = ConfigLoader._parse(raw)
        assert config.network_ports == []

    def test_missing_network_section(self) -> None:
        raw = {"organism": {"name": "test"}}
        config = ConfigLoader._parse(raw)
        assert config.network_ports == []

    def test_multiple_ports(self) -> None:
        raw = {
            "organism": {"name": "test"},
            "network": {
                "ports": [
                    {"port": 9000, "listener": "a"},
                    {"port": 9001, "listener": "b"},
                    {"port": 9002, "listener": "c"},
                ]
            },
        }
        config = ConfigLoader._parse(raw)
        assert len(config.network_ports) == 3
        ports = [p["port"] for p in config.network_ports]
        assert ports == [9000, 9001, 9002]


# ═══════════════════════════════════════════════════════════════════════════
# TestPumpIntegration — PortGate lifecycle in StreamPump
# ═══════════════════════════════════════════════════════════════════════════

class TestPumpIntegration:
    """PortGate is created in start() and cleaned up in shutdown()."""

    def setup_method(self) -> None:
        reset_port_gate()

    def teardown_method(self) -> None:
        reset_port_gate()

    @pytest.mark.asyncio
    async def test_start_creates_port_gate(self) -> None:
        from xml_pipeline.message_bus.stream_pump import StreamPump

        pump = StreamPump(name="test-pg")
        pump.config.network_ports = [
            {"port": 9000, "bind": "127.0.0.1", "listener": "ws", "protocol": "tcp"},
        ]
        await pump.start()
        gate = get_port_gate()
        assert gate is not None
        # The declared port should be requestable
        alloc = gate.request(9000, "127.0.0.1", "ws", "tcp")
        assert alloc.port == 9000

    @pytest.mark.asyncio
    async def test_shutdown_resets_port_gate(self) -> None:
        from xml_pipeline.message_bus.stream_pump import StreamPump

        pump = StreamPump(name="test-pg2")
        await pump.start()
        # Gate should exist
        gate = get_port_gate()
        assert gate is not None
        # Drain boot message so queue.join() won't block
        while not pump.queue.empty():
            pump.queue.get_nowait()
            pump.queue.task_done()
        await pump.shutdown()
        # Gate should be cleared
        with pytest.raises(RuntimeError):
            get_port_gate()

    @pytest.mark.asyncio
    async def test_empty_config_secure_default(self) -> None:
        from xml_pipeline.message_bus.stream_pump import StreamPump

        pump = StreamPump(name="test-pg3")
        await pump.start()
        gate = get_port_gate()
        with pytest.raises(PortDeniedError):
            gate.request(9000, "127.0.0.1", "anything")
