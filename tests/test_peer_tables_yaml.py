"""
test_peer_tables_yaml.py — Tests for YAML peer table declarations and ceiling enforcement.

Tests cover:
1. Parsing peer tables from YAML config
2. Ceiling enforcement (register within/exceeding ceiling)
3. Inheritance chain (parent → child → grandchild)
4. Bootstrap registration in topological order
5. Validation (unknown listener, unknown parent, circular chains)
6. OOB ceiling enforcement

Run with: pytest tests/test_peer_tables_yaml.py -v
"""

import asyncio
from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest
from third_party.xmlable import xmlify

from xml_pipeline.message_bus.stream_pump import StreamPump
from xml_pipeline.message_bus.config_loader import ConfigLoader
from xml_pipeline.message_bus.pump_config import OrganismConfig
from xml_pipeline.message_bus.thread_registry import get_registry, reset_registry


# ============================================================================
# Test fixtures
# ============================================================================


@xmlify
@dataclass
class PTPayload:
    """Payload for peer table tests."""
    value: str


@xmlify
@dataclass
class PTResult:
    """Result payload for peer table tests."""
    result: str


async def _pt_agent_handler(payload, metadata):
    from xml_pipeline.message_bus.message_state import HandlerResponse
    return HandlerResponse(payload=PTResult(result="ok"), to=metadata.from_id)


async def _pt_tool_handler(payload, metadata):
    return None


def _make_pump_with_listeners() -> StreamPump:
    """Create a StreamPump with listeners that have defined peers."""
    pump = StreamPump(name="test-pt", oob_enabled=False)
    reset_registry()

    # Register an agent with broad peers (the ceiling)
    pump.register(
        "concierge", _pt_agent_handler, PTPayload,
        description="Main agent", agent=True,
        peers=["calculator", "search", "billing", "audit"],
    )
    # Register tool listeners
    pump.register("calculator", _pt_tool_handler, PTPayload, description="Calculator")
    pump.register("search", _pt_tool_handler, PTPayload, description="Search")
    pump.register("billing", _pt_tool_handler, PTPayload, description="Billing")
    pump.register("audit", _pt_tool_handler, PTPayload, description="Audit")

    return pump


# ============================================================================
# TestPeerTableParsing
# ============================================================================


class TestPeerTableParsing:
    """Test YAML config parsing for peer tables."""

    def test_parse_single_table(self):
        raw = {
            "organism": {"name": "test"},
            "listeners": [],
            "peer_tables": [
                {
                    "name": "premium",
                    "entries": [
                        {"listener": "concierge", "peers": ["calculator", "search"]},
                    ],
                },
            ],
        }
        config = ConfigLoader._parse(raw)
        assert len(config.peer_table_configs) == 1
        pt = config.peer_table_configs[0]
        assert pt["name"] == "premium"
        assert pt["parent"] is None
        assert pt["peers"] == {"concierge": ["calculator", "search"]}

    def test_parse_multiple_tables(self):
        raw = {
            "organism": {"name": "test"},
            "listeners": [],
            "peer_tables": [
                {"name": "admin", "entries": [
                    {"listener": "concierge", "peers": ["calculator", "search"]}
                ]},
                {"name": "basic", "entries": [
                    {"listener": "concierge", "peers": ["calculator"]}
                ]},
            ],
        }
        config = ConfigLoader._parse(raw)
        assert len(config.peer_table_configs) == 2
        assert config.peer_table_configs[0]["name"] == "admin"
        assert config.peer_table_configs[1]["name"] == "basic"

    def test_parse_table_with_parent(self):
        raw = {
            "organism": {"name": "test"},
            "listeners": [],
            "peer_tables": [
                {"name": "admin", "entries": [
                    {"listener": "concierge", "peers": ["calculator", "search"]}
                ]},
                {"name": "viewer", "parent": "admin", "entries": [
                    {"listener": "concierge", "peers": ["calculator"]}
                ]},
            ],
        }
        config = ConfigLoader._parse(raw)
        assert config.peer_table_configs[0]["parent"] is None
        assert config.peer_table_configs[1]["parent"] == "admin"

    def test_parse_empty_entries(self):
        raw = {
            "organism": {"name": "test"},
            "listeners": [],
            "peer_tables": [
                {"name": "empty", "entries": []},
            ],
        }
        config = ConfigLoader._parse(raw)
        assert config.peer_table_configs[0]["peers"] == {}

    def test_parse_no_peer_tables(self):
        raw = {
            "organism": {"name": "test"},
            "listeners": [],
        }
        config = ConfigLoader._parse(raw)
        assert config.peer_table_configs == []


# ============================================================================
# TestCeilingEnforcement
# ============================================================================


class TestCeilingEnforcement:
    """Test that peer tables can only subtract from the ceiling."""

    def test_register_within_ceiling(self):
        pump = _make_pump_with_listeners()
        # concierge.peers = [calculator, search, billing, audit]
        # Registering a subset should work
        pump.register_peer_table("premium", {
            "concierge": ["calculator", "search"],
        })
        assert "premium" in pump._peer_tables
        assert pump._peer_tables["premium"]["concierge"] == ["calculator", "search"]

    def test_register_full_ceiling(self):
        pump = _make_pump_with_listeners()
        # All of concierge's peers — should work (equal to ceiling)
        pump.register_peer_table("admin", {
            "concierge": ["calculator", "search", "billing", "audit"],
        })
        assert "admin" in pump._peer_tables

    def test_register_exceeding_ceiling(self):
        pump = _make_pump_with_listeners()
        with pytest.raises(ValueError, match="exceeds ceiling"):
            pump.register_peer_table("bogus", {
                "concierge": ["calculator", "search", "secret_tool"],
            })

    def test_register_unknown_peer_exceeds_ceiling(self):
        pump = _make_pump_with_listeners()
        with pytest.raises(ValueError, match="exceeds ceiling"):
            pump.register_peer_table("bad", {
                "concierge": ["nonexistent"],
            })

    def test_register_duplicate_name(self):
        pump = _make_pump_with_listeners()
        pump.register_peer_table("first", {"concierge": ["calculator"]})
        with pytest.raises(KeyError, match="already registered"):
            pump.register_peer_table("first", {"concierge": ["search"]})

    def test_grant_within_ceiling(self):
        pump = _make_pump_with_listeners()
        pump.register_peer_table("tier", {
            "concierge": ["calculator"],
        })
        # Grant search (which is in the YAML ceiling)
        updated = pump.modify_peer_table("tier", "concierge", grant=["search"])
        assert "search" in updated

    def test_grant_exceeding_ceiling(self):
        pump = _make_pump_with_listeners()
        pump.register_peer_table("tier", {
            "concierge": ["calculator"],
        })
        with pytest.raises(ValueError, match="exceeds ceiling"):
            pump.modify_peer_table("tier", "concierge", grant=["secret_tool"])

    def test_revoke_always_ok(self):
        pump = _make_pump_with_listeners()
        pump.register_peer_table("tier", {
            "concierge": ["calculator", "search"],
        })
        updated = pump.modify_peer_table("tier", "concierge", revoke=["search"])
        assert updated == ["calculator"]

    def test_revoke_nonexistent_peer_ok(self):
        """Revoking a peer that's not in the list is a no-op."""
        pump = _make_pump_with_listeners()
        pump.register_peer_table("tier", {
            "concierge": ["calculator"],
        })
        updated = pump.modify_peer_table("tier", "concierge", revoke=["nonexistent"])
        assert updated == ["calculator"]

    def test_grant_restore_revoked_within_ceiling(self):
        """Re-granting a previously revoked peer (within ceiling) should work."""
        pump = _make_pump_with_listeners()
        pump.register_peer_table("tier", {
            "concierge": ["calculator", "search"],
        })
        pump.modify_peer_table("tier", "concierge", revoke=["search"])
        updated = pump.modify_peer_table("tier", "concierge", grant=["search"])
        assert "search" in updated


# ============================================================================
# TestInheritanceChain
# ============================================================================


class TestInheritanceChain:
    """Test parent-child inheritance hierarchy."""

    def test_two_level_chain(self):
        pump = _make_pump_with_listeners()
        pump.register_peer_table("admin", {
            "concierge": ["calculator", "search", "billing"],
        })
        pump.register_peer_table("viewer", {
            "concierge": ["calculator"],
        }, parent="admin")

        assert pump._peer_table_parents["viewer"] == "admin"
        assert pump._peer_tables["viewer"]["concierge"] == ["calculator"]

    def test_three_level_chain(self):
        pump = _make_pump_with_listeners()
        pump.register_peer_table("admin", {
            "concierge": ["calculator", "search", "billing"],
        })
        pump.register_peer_table("operator", {
            "concierge": ["calculator", "search"],
        }, parent="admin")
        pump.register_peer_table("viewer", {
            "concierge": ["calculator"],
        }, parent="operator")

        assert pump._peer_table_parents["operator"] == "admin"
        assert pump._peer_table_parents["viewer"] == "operator"

    def test_child_cannot_exceed_parent(self):
        pump = _make_pump_with_listeners()
        pump.register_peer_table("admin", {
            "concierge": ["calculator", "search"],
        })
        with pytest.raises(ValueError, match="exceeds ceiling.*table 'admin'"):
            pump.register_peer_table("bad_child", {
                "concierge": ["calculator", "billing"],  # billing not in admin
            }, parent="admin")

    def test_grandchild_cannot_exceed_grandparent(self):
        """Grandchild's ceiling is its parent (operator), not grandparent (admin)."""
        pump = _make_pump_with_listeners()
        pump.register_peer_table("admin", {
            "concierge": ["calculator", "search", "billing"],
        })
        pump.register_peer_table("operator", {
            "concierge": ["calculator", "search"],
        }, parent="admin")
        # viewer's ceiling is operator (which lacks billing)
        with pytest.raises(ValueError, match="exceeds ceiling.*table 'operator'"):
            pump.register_peer_table("viewer", {
                "concierge": ["calculator", "billing"],  # billing not in operator
            }, parent="operator")

    def test_parent_not_registered(self):
        pump = _make_pump_with_listeners()
        with pytest.raises(KeyError, match="Parent table 'nonexistent' not registered"):
            pump.register_peer_table("orphan", {
                "concierge": ["calculator"],
            }, parent="nonexistent")

    def test_child_grant_within_parent_ceiling(self):
        pump = _make_pump_with_listeners()
        pump.register_peer_table("admin", {
            "concierge": ["calculator", "search", "billing"],
        })
        pump.register_peer_table("operator", {
            "concierge": ["calculator"],
        }, parent="admin")
        # Grant search — it's in admin's ceiling
        updated = pump.modify_peer_table("operator", "concierge", grant=["search"])
        assert "search" in updated

    def test_child_grant_exceeding_parent_ceiling(self):
        pump = _make_pump_with_listeners()
        pump.register_peer_table("admin", {
            "concierge": ["calculator", "search"],
        })
        pump.register_peer_table("operator", {
            "concierge": ["calculator"],
        }, parent="admin")
        # Grant billing — NOT in admin's ceiling
        with pytest.raises(ValueError, match="exceeds ceiling.*table 'admin'"):
            pump.modify_peer_table("operator", "concierge", grant=["billing"])


# ============================================================================
# TestPeerTableBootstrap
# ============================================================================


class TestPeerTableBootstrap:
    """Test peer table registration from config during start()."""

    def test_tables_registered_in_topo_order(self):
        """Parent is registered before child even if config order is reversed."""
        pump = _make_pump_with_listeners()
        pump.config.peer_table_configs = [
            {
                "name": "viewer",
                "parent": "admin",
                "peers": {"concierge": ["calculator"]},
            },
            {
                "name": "admin",
                "parent": None,
                "peers": {"concierge": ["calculator", "search"]},
            },
        ]
        pump._register_config_peer_tables()

        assert "admin" in pump._peer_tables
        assert "viewer" in pump._peer_tables
        assert pump._peer_table_parents["viewer"] == "admin"

    def test_single_table_bootstrap(self):
        pump = _make_pump_with_listeners()
        pump.config.peer_table_configs = [
            {
                "name": "basic",
                "parent": None,
                "peers": {"concierge": ["calculator"]},
            },
        ]
        pump._register_config_peer_tables()
        assert "basic" in pump._peer_tables

    def test_circular_parent_detected(self):
        pump = _make_pump_with_listeners()
        pump.config.peer_table_configs = [
            {"name": "a", "parent": "b", "peers": {"concierge": ["calculator"]}},
            {"name": "b", "parent": "a", "peers": {"concierge": ["calculator"]}},
        ]
        with pytest.raises(ValueError, match="Circular peer table parent chain"):
            pump._register_config_peer_tables()

    def test_self_referencing_parent_detected(self):
        pump = _make_pump_with_listeners()
        pump.config.peer_table_configs = [
            {"name": "a", "parent": "a", "peers": {"concierge": ["calculator"]}},
        ]
        with pytest.raises(ValueError, match="Circular peer table parent chain"):
            pump._register_config_peer_tables()

    def test_missing_parent_reference(self):
        pump = _make_pump_with_listeners()
        pump.config.peer_table_configs = [
            {"name": "child", "parent": "missing", "peers": {"concierge": ["calculator"]}},
        ]
        with pytest.raises(KeyError, match="referenced as parent but not defined"):
            pump._register_config_peer_tables()

    def test_empty_config_no_error(self):
        pump = _make_pump_with_listeners()
        pump.config.peer_table_configs = []
        pump._register_config_peer_tables()
        assert len(pump._peer_tables) == 0


# ============================================================================
# TestPeerTableValidation
# ============================================================================


class TestPeerTableValidation:
    """Test edge cases and validation."""

    def test_listener_not_in_pump(self):
        """Registering table for non-existent listener: ceiling is empty → all peers fail."""
        pump = _make_pump_with_listeners()
        with pytest.raises(ValueError, match="exceeds ceiling"):
            pump.register_peer_table("bad", {
                "nonexistent_listener": ["calculator"],
            })

    def test_empty_peers_ok(self):
        """Table with no listeners is valid (useless but valid)."""
        pump = _make_pump_with_listeners()
        pump.register_peer_table("empty", {})
        assert pump._peer_tables["empty"] == {}

    def test_empty_peer_list_ok(self):
        """Listener with empty peer list in table is valid (revokes everything)."""
        pump = _make_pump_with_listeners()
        pump.register_peer_table("restricted", {
            "concierge": [],
        })
        assert pump._peer_tables["restricted"]["concierge"] == []

    def test_modify_nonexistent_table(self):
        pump = _make_pump_with_listeners()
        with pytest.raises(KeyError, match="not registered"):
            pump.modify_peer_table("nonexistent", "concierge", grant=["calc"])


# ============================================================================
# TestOOBCeilingEnforcement
# ============================================================================


class TestOOBCeilingEnforcement:
    """Test that OOB register-peer-table and modify-peer-table respect ceiling."""

    def _make_pump_with_table(self) -> StreamPump:
        pump = _make_pump_with_listeners()
        pump.register_peer_table("admin", {
            "concierge": ["calculator", "search"],
        })
        return pump

    def test_oob_register_within_ceiling(self):
        from xml_pipeline.oob.handlers import handle_register_peer_table
        from lxml import etree
        from xml_pipeline.oob.protocol import NS

        pump = _make_pump_with_listeners()

        # Build command element
        cmd_xml = (
            f'<register-peer-table xmlns="{NS}">'
            f'<name xmlns="{NS}">basic</name>'
            f'<entries xmlns="{NS}">'
            f'<entry xmlns="{NS}">'
            f'<listener xmlns="{NS}">concierge</listener>'
            f'<peers xmlns="{NS}"><peer xmlns="{NS}">calculator</peer></peers>'
            f'</entry>'
            f'</entries>'
            f'</register-peer-table>'
        )
        el = etree.fromstring(cmd_xml.encode())
        result = handle_register_peer_table(pump, el, "req-1")
        # Should succeed — check for ack
        assert b"success" in result or b"ack" in result

    def test_oob_register_exceeding_ceiling(self):
        from xml_pipeline.oob.handlers import handle_register_peer_table
        from lxml import etree
        from xml_pipeline.oob.protocol import NS

        pump = _make_pump_with_listeners()

        cmd_xml = (
            f'<register-peer-table xmlns="{NS}">'
            f'<name xmlns="{NS}">bad</name>'
            f'<entries xmlns="{NS}">'
            f'<entry xmlns="{NS}">'
            f'<listener xmlns="{NS}">concierge</listener>'
            f'<peers xmlns="{NS}"><peer xmlns="{NS}">secret_tool</peer></peers>'
            f'</entry>'
            f'</entries>'
            f'</register-peer-table>'
        )
        el = etree.fromstring(cmd_xml.encode())
        result = handle_register_peer_table(pump, el, "req-1")
        # Should fail — check for error
        assert b"REGISTRATION_ERROR" in result or b"error" in result

    def test_oob_register_with_parent(self):
        from xml_pipeline.oob.handlers import handle_register_peer_table
        from lxml import etree
        from xml_pipeline.oob.protocol import NS

        pump = self._make_pump_with_table()

        cmd_xml = (
            f'<register-peer-table xmlns="{NS}">'
            f'<name xmlns="{NS}">viewer</name>'
            f'<parent xmlns="{NS}">admin</parent>'
            f'<entries xmlns="{NS}">'
            f'<entry xmlns="{NS}">'
            f'<listener xmlns="{NS}">concierge</listener>'
            f'<peers xmlns="{NS}"><peer xmlns="{NS}">calculator</peer></peers>'
            f'</entry>'
            f'</entries>'
            f'</register-peer-table>'
        )
        el = etree.fromstring(cmd_xml.encode())
        result = handle_register_peer_table(pump, el, "req-1")
        assert b"success" in result or b"ack" in result
        assert pump._peer_table_parents.get("viewer") == "admin"

    def test_oob_modify_grant_exceeding_ceiling(self):
        from xml_pipeline.oob.handlers import handle_modify_peer_table
        from lxml import etree
        from xml_pipeline.oob.protocol import NS

        pump = self._make_pump_with_table()

        cmd_xml = (
            f'<modify-peer-table xmlns="{NS}">'
            f'<name xmlns="{NS}">admin</name>'
            f'<listener xmlns="{NS}">concierge</listener>'
            f'<grant xmlns="{NS}"><peer xmlns="{NS}">secret_tool</peer></grant>'
            f'</modify-peer-table>'
        )
        el = etree.fromstring(cmd_xml.encode())
        result = handle_modify_peer_table(pump, el, "req-1")
        assert b"MODIFICATION_ERROR" in result or b"error" in result
