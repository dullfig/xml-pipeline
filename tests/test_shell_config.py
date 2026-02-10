"""Tests for shell configuration parsing (shell: YAML section + os_user on peer tables).

Tests config_loader.py parsing of the shell: section and os_user field
on peer tables, and OrganismConfig.shell_config.
"""

import pytest
import yaml

from xml_pipeline.message_bus.config_loader import ConfigLoader
from xml_pipeline.message_bus.pump_config import OrganismConfig


# ═══════════════════════════════════════════════════════════════════════════
# TestShellConfigParsing
# ═══════════════════════════════════════════════════════════════════════════

class TestShellConfigParsing:
    """ConfigLoader parses the shell: YAML section."""

    def test_shell_section_parsed(self) -> None:
        raw = {
            "organism": {"name": "test"},
            "shell": {
                "enabled": True,
                "default_os_user": "xp-sandbox",
                "xp_exec_path": "/opt/bin/xp-exec",
                "totp_secret_env": "SHELL_TOTP",
            },
        }
        config = ConfigLoader._parse(raw)
        assert config.shell_config["enabled"] is True
        assert config.shell_config["default_os_user"] == "xp-sandbox"
        assert config.shell_config["xp_exec_path"] == "/opt/bin/xp-exec"
        assert config.shell_config["totp_secret_env"] == "SHELL_TOTP"

    def test_shell_section_defaults(self) -> None:
        raw = {
            "organism": {"name": "test"},
            "shell": {"enabled": True},
        }
        config = ConfigLoader._parse(raw)
        assert config.shell_config["enabled"] is True
        assert config.shell_config["default_os_user"] == ""
        assert config.shell_config["xp_exec_path"] == "/usr/local/bin/xp-exec"
        assert config.shell_config["totp_secret_env"] == ""

    def test_shell_disabled_by_default(self) -> None:
        """Absent shell: section = empty dict."""
        raw = {"organism": {"name": "test"}}
        config = ConfigLoader._parse(raw)
        assert config.shell_config == {}

    def test_organism_config_shell_config_default(self) -> None:
        """OrganismConfig.shell_config defaults to empty dict."""
        config = OrganismConfig(name="test")
        assert config.shell_config == {}


# ═══════════════════════════════════════════════════════════════════════════
# TestOsUserOnPeerTables
# ═══════════════════════════════════════════════════════════════════════════

class TestOsUserOnPeerTables:
    """os_user field is parsed from peer table configs."""

    def test_os_user_on_peer_table(self) -> None:
        raw = {
            "organism": {"name": "test"},
            "peer_tables": [
                {
                    "name": "admin",
                    "os_user": "xp-admin",
                    "entries": [
                        {"listener": "researcher", "peers": ["calc"]},
                    ],
                },
            ],
        }
        config = ConfigLoader._parse(raw)
        assert len(config.peer_table_configs) == 1
        assert config.peer_table_configs[0]["os_user"] == "xp-admin"

    def test_os_user_optional(self) -> None:
        """os_user is optional — absent means not included in config."""
        raw = {
            "organism": {"name": "test"},
            "peer_tables": [
                {
                    "name": "basic",
                    "entries": [
                        {"listener": "researcher", "peers": ["calc"]},
                    ],
                },
            ],
        }
        config = ConfigLoader._parse(raw)
        assert "os_user" not in config.peer_table_configs[0]

    def test_multiple_tables_different_os_users(self) -> None:
        raw = {
            "organism": {"name": "test"},
            "peer_tables": [
                {
                    "name": "admin",
                    "os_user": "xp-admin",
                    "entries": [{"listener": "r", "peers": ["a"]}],
                },
                {
                    "name": "viewer",
                    "parent": "admin",
                    "os_user": "xp-sandbox",
                    "entries": [{"listener": "r", "peers": ["a"]}],
                },
            ],
        }
        config = ConfigLoader._parse(raw)
        assert config.peer_table_configs[0]["os_user"] == "xp-admin"
        assert config.peer_table_configs[1]["os_user"] == "xp-sandbox"
