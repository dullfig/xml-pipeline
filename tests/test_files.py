"""Tests for file tools security validation.

Tests the path validation, traversal blocking, sandbox enforcement,
and security gate in xml_pipeline/tools/files.py.
"""

import os
import tempfile
from pathlib import Path

import pytest

from xml_pipeline.tools.files import (
    MAX_FILE_SIZE,
    MAX_LISTING_ENTRIES,
    TOOL_ENABLED,
    _DISABLED_MSG,
    _allowed_paths,
    _validate_path,
    configure_allowed_paths,
    delete_file,
    list_dir,
    read_file,
    write_file,
)


# ═══════════════════════════════════════════════════════════════════════════
# TestToolDisabled — security gate
# ═══════════════════════════════════════════════════════════════════════════

class TestToolDisabled:
    """The file tools are disabled by default pending security audit."""

    def test_tool_disabled_flag(self) -> None:
        assert TOOL_ENABLED is False

    async def test_read_file_returns_disabled_error(self) -> None:
        result = await read_file(path="/etc/passwd")
        assert result.success is False
        assert "disabled" in result.error.lower()

    async def test_write_file_returns_disabled_error(self) -> None:
        result = await write_file(path="/tmp/test.txt", content="hello")
        assert result.success is False
        assert result.error == _DISABLED_MSG

    async def test_list_dir_returns_disabled_error(self) -> None:
        result = await list_dir(path="/tmp")
        assert result.success is False
        assert result.error == _DISABLED_MSG

    async def test_delete_file_returns_disabled_error(self) -> None:
        result = await delete_file(path="/tmp/test.txt")
        assert result.success is False
        assert result.error == _DISABLED_MSG


# ═══════════════════════════════════════════════════════════════════════════
# TestValidatePath — path validation logic (testable while disabled)
# ═══════════════════════════════════════════════════════════════════════════

class TestValidatePathNoSandbox:
    """_validate_path() with no sandbox configured (open mode)."""

    def setup_method(self) -> None:
        configure_allowed_paths([])

    def test_absolute_path_allowed(self) -> None:
        error, resolved = _validate_path(os.path.abspath("."))
        assert error is None
        assert resolved is not None

    def test_relative_path_resolves(self) -> None:
        error, resolved = _validate_path(".")
        assert error is None
        assert resolved is not None
        assert resolved.is_absolute()


class TestValidatePathSandbox:
    """_validate_path() with allowed_paths configured (sandbox mode)."""

    def setup_method(self) -> None:
        self.sandbox = tempfile.mkdtemp()
        configure_allowed_paths([self.sandbox])

    def teardown_method(self) -> None:
        configure_allowed_paths([])
        import shutil
        shutil.rmtree(self.sandbox, ignore_errors=True)

    def test_path_inside_sandbox_allowed(self) -> None:
        allowed = os.path.join(self.sandbox, "file.txt")
        error, resolved = _validate_path(allowed)
        assert error is None
        assert resolved is not None

    def test_path_outside_sandbox_blocked(self) -> None:
        error, resolved = _validate_path("/etc/passwd")
        assert error is not None
        assert "not in allowed" in error.lower()
        assert resolved is None

    def test_subdirectory_inside_sandbox_allowed(self) -> None:
        sub = os.path.join(self.sandbox, "sub", "deep", "file.txt")
        error, resolved = _validate_path(sub)
        assert error is None

    def test_sibling_directory_blocked(self) -> None:
        sibling = os.path.join(os.path.dirname(self.sandbox), "other", "file.txt")
        error, resolved = _validate_path(sibling)
        assert error is not None


class TestValidatePathTraversal:
    """_validate_path() blocks path traversal attempts."""

    def setup_method(self) -> None:
        self.sandbox = tempfile.mkdtemp()
        configure_allowed_paths([self.sandbox])

    def teardown_method(self) -> None:
        configure_allowed_paths([])
        import shutil
        shutil.rmtree(self.sandbox, ignore_errors=True)

    def test_dotdot_in_path_blocked(self) -> None:
        """Direct '..' in path string is rejected."""
        traversal = os.path.join(self.sandbox, "..", "etc", "passwd")
        error, resolved = _validate_path(traversal)
        assert error is not None
        assert "traversal" in error.lower()

    def test_dotdot_prefix_blocked(self) -> None:
        error, resolved = _validate_path("../../../etc/passwd")
        assert error is not None

    def test_dotdot_encoded_not_special(self) -> None:
        """Literal '.' characters in filenames are fine."""
        safe = os.path.join(self.sandbox, "file.v2.1.txt")
        error, resolved = _validate_path(safe)
        assert error is None


# ═══════════════════════════════════════════════════════════════════════════
# TestConstants
# ═══════════════════════════════════════════════════════════════════════════

class TestConstants:
    """Security-relevant constants."""

    def test_max_file_size(self) -> None:
        assert MAX_FILE_SIZE == 10 * 1024 * 1024

    def test_max_listing_entries(self) -> None:
        assert MAX_LISTING_ENTRIES == 1000
