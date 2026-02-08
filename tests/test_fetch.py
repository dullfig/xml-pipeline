"""Tests for fetch tool security validation.

Tests the URL validation, private IP detection, and SSRF protection in
xml_pipeline/tools/fetch.py. Does NOT make real HTTP requests — only tests
the security gate logic.
"""

import socket
from unittest.mock import patch

import pytest

from xml_pipeline.tools.fetch import (
    ALLOWED_SCHEMES,
    BLOCKED_HOSTS,
    MAX_RESPONSE_SIZE,
    _is_private_ip,
    _validate_url,
    fetch_url,
)


# ═══════════════════════════════════════════════════════════════════════════
# TestValidateUrl — URL security validation
# ═══════════════════════════════════════════════════════════════════════════

class TestValidateUrl:
    """_validate_url() blocks dangerous URLs before any HTTP call is made."""

    # ── Allowed ──────────────────────────────────────────────────────────

    def test_https_url_allowed(self) -> None:
        assert _validate_url("https://example.com/api") is None

    def test_http_url_allowed(self) -> None:
        assert _validate_url("http://example.com/page") is None

    def test_url_with_port_allowed(self) -> None:
        assert _validate_url("https://example.com:8443/api") is None

    def test_url_with_path_and_query_allowed(self) -> None:
        assert _validate_url("https://example.com/path?q=hello&page=1") is None

    # ── Scheme blocking ──────────────────────────────────────────────────

    def test_ftp_scheme_blocked(self) -> None:
        error = _validate_url("ftp://example.com/file")
        assert error is not None
        assert "not allowed" in error

    def test_file_scheme_blocked(self) -> None:
        error = _validate_url("file:///etc/passwd")
        assert error is not None
        assert "not allowed" in error

    def test_javascript_scheme_blocked(self) -> None:
        error = _validate_url("javascript:alert(1)")
        assert error is not None

    def test_data_scheme_blocked(self) -> None:
        error = _validate_url("data:text/html,<script>alert(1)</script>")
        assert error is not None

    def test_empty_scheme_blocked(self) -> None:
        error = _validate_url("://example.com")
        assert error is not None

    def test_allowed_schemes_set(self) -> None:
        assert ALLOWED_SCHEMES == {"http", "https"}

    # ── Host blocking ────────────────────────────────────────────────────

    def test_localhost_blocked(self) -> None:
        error = _validate_url("http://localhost/admin")
        assert error is not None
        assert "blocked" in error.lower()

    def test_127_0_0_1_blocked(self) -> None:
        error = _validate_url("http://127.0.0.1/admin")
        assert error is not None

    def test_0_0_0_0_blocked(self) -> None:
        error = _validate_url("http://0.0.0.0/")
        assert error is not None

    def test_ipv6_loopback_blocked(self) -> None:
        error = _validate_url("http://[::1]/admin")
        assert error is not None

    def test_aws_metadata_blocked(self) -> None:
        error = _validate_url("http://169.254.169.254/latest/meta-data/")
        assert error is not None

    def test_gcp_metadata_blocked(self) -> None:
        error = _validate_url("http://metadata.google.internal/")
        assert error is not None

    def test_all_blocked_hosts_present(self) -> None:
        expected = {
            "localhost", "127.0.0.1", "0.0.0.0", "::1",
            "metadata.google.internal", "169.254.169.254",
        }
        assert BLOCKED_HOSTS == expected

    # ── Private IP blocking ──────────────────────────────────────────────

    @patch("xml_pipeline.tools.fetch._is_private_ip", return_value=True)
    def test_private_ip_blocked_by_default(self, mock_priv: object) -> None:
        error = _validate_url("http://10.0.0.1/internal")
        assert error is not None
        assert "internal" in error.lower() or "private" in error.lower()

    @patch("xml_pipeline.tools.fetch._is_private_ip", return_value=True)
    def test_private_ip_allowed_with_flag(self, mock_priv: object) -> None:
        error = _validate_url("http://10.0.0.1/internal", allow_internal=True)
        assert error is None

    # ── Malformed URLs ───────────────────────────────────────────────────

    def test_no_host_blocked(self) -> None:
        error = _validate_url("http://")
        assert error is not None

    def test_empty_string_blocked(self) -> None:
        error = _validate_url("")
        assert error is not None


# ═══════════════════════════════════════════════════════════════════════════
# TestIsPrivateIp — IP address classification
# ═══════════════════════════════════════════════════════════════════════════

class TestIsPrivateIp:
    """_is_private_ip() detects RFC1918, loopback, and link-local addresses."""

    # ── Direct IP addresses ──────────────────────────────────────────────

    def test_10_x_private(self) -> None:
        assert _is_private_ip("10.0.0.1") is True

    def test_172_16_private(self) -> None:
        assert _is_private_ip("172.16.0.1") is True

    def test_192_168_private(self) -> None:
        assert _is_private_ip("192.168.1.1") is True

    def test_127_0_0_1_loopback(self) -> None:
        assert _is_private_ip("127.0.0.1") is True

    def test_169_254_link_local(self) -> None:
        assert _is_private_ip("169.254.1.1") is True

    def test_ipv6_loopback(self) -> None:
        assert _is_private_ip("::1") is True

    # ── Public IPs ───────────────────────────────────────────────────────

    def test_public_ip_not_private(self) -> None:
        assert _is_private_ip("8.8.8.8") is False

    def test_public_ip_1_1_1_1(self) -> None:
        assert _is_private_ip("1.1.1.1") is False

    # ── Hostname resolution ──────────────────────────────────────────────

    @patch("socket.gethostbyname", return_value="10.0.0.5")
    def test_hostname_resolving_to_private(self, mock_dns: object) -> None:
        assert _is_private_ip("internal.corp.example.com") is True

    @patch("socket.gethostbyname", return_value="93.184.216.34")
    def test_hostname_resolving_to_public(self, mock_dns: object) -> None:
        assert _is_private_ip("example.com") is False

    @patch("socket.gethostbyname", side_effect=socket.gaierror("no such host"))
    def test_unresolvable_hostname_blocked(self, mock_dns: object) -> None:
        """Unresolvable hostnames treated as private (fail-closed)."""
        assert _is_private_ip("nonexistent.invalid") is True


# ═══════════════════════════════════════════════════════════════════════════
# TestFetchUrlTool — @tool function validation gates
# ═══════════════════════════════════════════════════════════════════════════

class TestFetchUrlTool:
    """fetch_url() validates inputs before making any HTTP request."""

    async def test_invalid_url_returns_error(self) -> None:
        result = await fetch_url(url="not-a-url")
        assert result.success is False

    async def test_blocked_method_returns_error(self) -> None:
        result = await fetch_url(url="https://example.com", method="TRACE")
        assert result.success is False
        # Either blocked by method validation or by missing aiohttp
        assert "not allowed" in result.error.lower() or "aiohttp" in result.error.lower()

    async def test_file_scheme_returns_error(self) -> None:
        result = await fetch_url(url="file:///etc/passwd")
        assert result.success is False

    async def test_localhost_returns_error(self) -> None:
        result = await fetch_url(url="http://localhost/admin")
        assert result.success is False

    async def test_metadata_endpoint_returns_error(self) -> None:
        result = await fetch_url(url="http://169.254.169.254/latest/meta-data/")
        assert result.success is False

    async def test_max_response_size_constant(self) -> None:
        assert MAX_RESPONSE_SIZE == 10 * 1024 * 1024
