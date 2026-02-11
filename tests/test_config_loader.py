"""
Tests for config_loader.py security fixes:
  #43 — Handler integrity .pyc bypass
  #45 — Import path consecutive dots
  #48 — Port range validation
"""

from __future__ import annotations

import importlib
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from xml_pipeline.message_bus.config_loader import ConfigLoader
from xml_pipeline.message_bus.pump_config import ListenerConfig


# ============================================================================
# #45 — Import path validation (consecutive dots / per-segment)
# ============================================================================


class TestImportPathValidation:
    """Test _validate_import_path rejects malicious paths."""

    def test_valid_dotted_path_accepted(self):
        ConfigLoader._validate_import_path("foo.bar.baz", "test")

    def test_single_segment_accepted(self):
        ConfigLoader._validate_import_path("module", "test")

    def test_consecutive_dots_rejected(self):
        with pytest.raises(ValueError, match="consecutive dots"):
            ConfigLoader._validate_import_path("foo..bar", "test")

    def test_triple_dots_rejected(self):
        with pytest.raises(ValueError, match="consecutive dots"):
            ConfigLoader._validate_import_path("a...evil.module", "test")

    def test_dunder_segment_rejected(self):
        with pytest.raises(ValueError, match="Dunder"):
            ConfigLoader._validate_import_path("foo.__builtins__", "test")

    def test_segment_with_dash_rejected(self):
        with pytest.raises(ValueError, match="dotted Python identifiers"):
            ConfigLoader._validate_import_path("foo.bar-baz", "test")

    def test_segment_starting_with_digit_rejected(self):
        with pytest.raises(ValueError, match="dotted Python identifiers"):
            ConfigLoader._validate_import_path("foo.3evil", "test")

    def test_empty_string_rejected(self):
        with pytest.raises(ValueError, match="dotted Python identifiers"):
            ConfigLoader._validate_import_path("", "test")


# ============================================================================
# #43 — Handler integrity .pyc handling
# ============================================================================


class TestHandlerIntegrityPyc:
    """Test handler SHA-256 integrity check with .pyc scenarios."""

    def test_normal_py_hashing_works(self, tmp_path):
        """Normal .py file hashing still works correctly."""
        import hashlib
        handler_src = "async def handle(p, m): return None\n"
        py_file = tmp_path / "handler.py"
        py_file.write_text(handler_src)
        expected_hash = hashlib.sha256(py_file.read_bytes()).hexdigest()

        # Create a mock module with __file__ pointing to .py
        mock_module = MagicMock()
        mock_module.__file__ = str(py_file)
        mock_module.handle = MagicMock()

        lc = ListenerConfig(
            name="test",
            payload_class_path="test.Payload",
            handler_path="test.handle",
            description="test",
            handler_sha256=expected_hash,
        )
        lc.payload_class = MagicMock()
        lc.handler = mock_module.handle

        with patch.object(importlib, "import_module", return_value=mock_module):
            # Should not raise
            ConfigLoader._resolve_imports(lc)

    def test_pyc_triggers_source_lookup(self, tmp_path):
        """When __file__ is .pyc, source .py is found and hashed."""
        import hashlib
        handler_src = "async def handle(p, m): return None\n"
        py_file = tmp_path / "handler.py"
        py_file.write_text(handler_src)
        expected_hash = hashlib.sha256(py_file.read_bytes()).hexdigest()

        # Create a fake .pyc path
        pycache = tmp_path / "__pycache__"
        pycache.mkdir()
        pyc_file = pycache / "handler.cpython-311.pyc"
        pyc_file.write_bytes(b"fake bytecode")

        mock_module = MagicMock()
        mock_module.__file__ = str(pyc_file)
        mock_module.handle = MagicMock()

        lc = ListenerConfig(
            name="test",
            payload_class_path="test.Payload",
            handler_path="test.handle",
            description="test",
            handler_sha256=expected_hash,
        )
        lc.payload_class = MagicMock()
        lc.handler = mock_module.handle

        with (
            patch.object(importlib, "import_module", return_value=mock_module),
            patch.object(
                importlib.util,
                "source_from_cache",
                return_value=str(py_file),
            ),
        ):
            # Should not raise — hashes the .py source
            ConfigLoader._resolve_imports(lc)

    def test_pyc_without_source_raises(self, tmp_path):
        """When .pyc exists but .py source is missing, raise ValueError."""
        pycache = tmp_path / "__pycache__"
        pycache.mkdir()
        pyc_file = pycache / "handler.cpython-311.pyc"
        pyc_file.write_bytes(b"fake bytecode")

        mock_module = MagicMock()
        mock_module.__file__ = str(pyc_file)
        mock_module.handle = MagicMock()

        lc = ListenerConfig(
            name="test",
            payload_class_path="test.Payload",
            handler_path="test.handle",
            description="test",
            handler_sha256="deadbeef" * 8,
        )
        lc.payload_class = MagicMock()
        lc.handler = mock_module.handle

        nonexistent_py = str(tmp_path / "handler.py")

        with (
            patch.object(importlib, "import_module", return_value=mock_module),
            patch.object(
                importlib.util,
                "source_from_cache",
                return_value=nonexistent_py,
            ),
        ):
            with pytest.raises(ValueError, match="source .py not found"):
                ConfigLoader._resolve_imports(lc)


# ============================================================================
# #48 — Port range validation
# ============================================================================


class TestPortRangeValidation:
    """Test port range validation in config loader."""

    def test_valid_port_accepted(self):
        ConfigLoader._validate_port(8765, "test")

    def test_port_1_accepted(self):
        ConfigLoader._validate_port(1, "test")

    def test_port_65535_accepted(self):
        ConfigLoader._validate_port(65535, "test")

    def test_port_0_rejected(self):
        with pytest.raises(ValueError, match="Invalid port 0"):
            ConfigLoader._validate_port(0, "test")

    def test_port_negative_rejected(self):
        with pytest.raises(ValueError, match="Invalid port -1"):
            ConfigLoader._validate_port(-1, "test")

    def test_port_too_large_rejected(self):
        with pytest.raises(ValueError, match="Invalid port 99999"):
            ConfigLoader._validate_port(99999, "test")

    def test_full_config_invalid_organism_port(self, tmp_path):
        """Full config parse rejects invalid organism port."""
        config_file = tmp_path / "organism.yaml"
        config_file.write_text(textwrap.dedent("""\
            organism:
              name: test
              port: 99999
            listeners: []
        """))
        with pytest.raises(ValueError, match="Invalid port 99999"):
            ConfigLoader.load(str(config_file))

    def test_full_config_invalid_network_port(self, tmp_path):
        """Full config parse rejects invalid network port."""
        config_file = tmp_path / "organism.yaml"
        config_file.write_text(textwrap.dedent("""\
            organism:
              name: test
            network:
              ports:
                - port: 0
                  listener: test-listener
            listeners: []
        """))
        with pytest.raises(ValueError, match="Invalid port 0"):
            ConfigLoader.load(str(config_file))
