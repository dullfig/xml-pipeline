"""Tests for fetch tool security validation and httpx-based execution.

Tests the URL validation, private IP detection, SSRF protection, httpx-based
_do_fetch() core, @tool wrapper, payload classes, handler, and header parsing.
"""

import socket
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from xml_pipeline.tools.fetch import (
    ALLOWED_METHODS,
    ALLOWED_SCHEMES,
    BLOCKED_HOSTS,
    DEFAULT_TIMEOUT,
    MAX_REQUEST_BODY,
    MAX_RESPONSE_SIZE,
    FetchRequest,
    FetchResponse,
    _do_fetch,
    _is_private_ip,
    _parse_headers,
    _validate_url,
    fetch_url,
    handle_fetch,
)
from xml_pipeline.message_bus.message_state import HandlerMetadata, HandlerResponse


# ═══════════════════════════════════════════════════════════════════════════
# Helpers — httpx mock factory
# ═══════════════════════════════════════════════════════════════════════════

def _mock_httpx_client(
    *,
    url: str = "https://example.com",
    status_code: int = 200,
    content: bytes = b"Hello",
    headers: dict | None = None,
    side_effect: Exception | None = None,
):
    """Build a patched httpx.AsyncClient context manager."""
    mock_response = MagicMock()
    mock_response.url = url
    mock_response.status_code = status_code
    mock_response.content = content
    mock_response.headers = headers or {"content-type": "text/html"}

    mock_client = AsyncMock()
    if side_effect:
        mock_client.request = AsyncMock(side_effect=side_effect)
    else:
        mock_client.request = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    return patch("xml_pipeline.tools.fetch.httpx.AsyncClient", return_value=mock_client), mock_client


def _metadata() -> HandlerMetadata:
    """Build minimal HandlerMetadata for handler tests."""
    return HandlerMetadata(
        thread_id="test-thread-001",
        from_id="test-caller",
        own_name=None,
        is_self_call=False,
        usage_instructions="",
        todo_nudge="",
    )


# ═══════════════════════════════════════════════════════════════════════════
# TestValidateUrl — URL security validation (kept from original)
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
            "::ffff:127.0.0.1", "::ffff:169.254.169.254",
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
# TestIsPrivateIp — IP address classification (kept from original)
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

    @patch("socket.getaddrinfo", return_value=[
        (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("10.0.0.5", 0)),
    ])
    def test_hostname_resolving_to_private(self, mock_dns: object) -> None:
        assert _is_private_ip("internal.corp.example.com") is True

    @patch("socket.getaddrinfo", return_value=[
        (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0)),
    ])
    def test_hostname_resolving_to_public(self, mock_dns: object) -> None:
        assert _is_private_ip("example.com") is False

    @patch("socket.getaddrinfo", side_effect=socket.gaierror("no such host"))
    def test_unresolvable_hostname_blocked(self, mock_dns: object) -> None:
        """Unresolvable hostnames treated as private (fail-closed)."""
        assert _is_private_ip("nonexistent.invalid") is True


# ═══════════════════════════════════════════════════════════════════════════
# TestIPv6SSRF — IPv6 bypass protection (security hitlist #42)
# ═══════════════════════════════════════════════════════════════════════════

class TestIPv6SSRF:
    """_is_private_ip() and _validate_url() block IPv6 SSRF bypasses."""

    # ── Direct IPv6 literals ──────────────────────────────────────────────

    def test_ipv6_loopback_blocked(self) -> None:
        assert _is_private_ip("::1") is True

    def test_ipv6_link_local_blocked(self) -> None:
        assert _is_private_ip("fe80::1") is True

    def test_ipv6_unique_local_blocked(self) -> None:
        assert _is_private_ip("fd00::1") is True

    def test_ipv6_unspecified_blocked(self) -> None:
        assert _is_private_ip("::") is True

    def test_ipv6_mapped_loopback_blocked(self) -> None:
        assert _is_private_ip("::ffff:127.0.0.1") is True

    def test_ipv6_mapped_metadata_blocked(self) -> None:
        assert _is_private_ip("::ffff:169.254.169.254") is True

    def test_ipv6_mapped_private_blocked(self) -> None:
        assert _is_private_ip("::ffff:10.0.0.1") is True

    def test_ipv6_multicast_blocked(self) -> None:
        assert _is_private_ip("ff02::1") is True

    # ── URL-level IPv6 blocking ───────────────────────────────────────────

    def test_url_ipv6_loopback_blocked(self) -> None:
        error = _validate_url("http://[::1]/")
        assert error is not None

    def test_url_ipv6_mapped_metadata_blocked(self) -> None:
        error = _validate_url("http://[::ffff:169.254.169.254]/")
        assert error is not None

    def test_url_ipv6_link_local_blocked(self) -> None:
        error = _validate_url("http://[fe80::1]/")
        assert error is not None

    # ── DNS resolution to IPv6 ────────────────────────────────────────────

    @patch("socket.getaddrinfo", return_value=[
        (socket.AF_INET6, socket.SOCK_STREAM, 0, "", ("::1", 0, 0, 0)),
    ])
    def test_hostname_resolving_to_ipv6_loopback(self, mock_dns: object) -> None:
        assert _is_private_ip("evil.example.com") is True

    @patch("socket.getaddrinfo", return_value=[
        (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0)),
        (socket.AF_INET6, socket.SOCK_STREAM, 0, "", ("fe80::1", 0, 0, 0)),
    ])
    def test_dual_stack_safe_ipv4_dangerous_ipv6_blocked(self, mock_dns: object) -> None:
        """If any address is dangerous, block the whole hostname."""
        assert _is_private_ip("dual.example.com") is True

    @patch("socket.getaddrinfo", return_value=[
        (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0)),
        (socket.AF_INET6, socket.SOCK_STREAM, 0, "", ("2607:f8b0:4004:800::200e", 0, 0, 0)),
    ])
    def test_dual_stack_both_public_allowed(self, mock_dns: object) -> None:
        assert _is_private_ip("safe.example.com") is False

    @patch("socket.getaddrinfo", return_value=[])
    def test_empty_dns_result_blocked(self, mock_dns: object) -> None:
        """Empty getaddrinfo result → fail-closed."""
        assert _is_private_ip("empty.example.com") is True

    def test_zone_id_stripping(self) -> None:
        """Zone IDs in IPv6 addresses should be stripped before checking."""
        # fe80::1%eth0 is link-local → blocked
        assert _is_private_ip("fe80::1%eth0") is True


# ═══════════════════════════════════════════════════════════════════════════
# TestFetchUrlTool — @tool function validation gates (kept from original)
# ═══════════════════════════════════════════════════════════════════════════

class TestFetchUrlTool:
    """fetch_url() validates inputs before making any HTTP request."""

    async def test_invalid_url_returns_error(self) -> None:
        result = await fetch_url(url="not-a-url")
        assert result.success is False

    async def test_blocked_method_returns_error(self) -> None:
        result = await fetch_url(url="https://example.com", method="TRACE")
        assert result.success is False
        assert "not allowed" in result.error.lower()

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


# ═══════════════════════════════════════════════════════════════════════════
# TestDoFetch — core _do_fetch() with mocked httpx
# ═══════════════════════════════════════════════════════════════════════════

class TestDoFetch:
    """_do_fetch() performs HTTP requests via httpx with security validation."""

    @pytest.mark.asyncio
    @patch("xml_pipeline.tools.fetch._is_private_ip", return_value=False)
    async def test_get_success(self, _mock_ip: object) -> None:
        patcher, mock_client = _mock_httpx_client(
            url="https://example.com",
            status_code=200,
            content=b"Hello World",
            headers={"content-type": "text/html"},
        )
        with patcher:
            result = await _do_fetch("https://example.com")
        assert result["status_code"] == 200
        assert result["body"] == "Hello World"
        assert result["content_type"] == "text/html"

    @pytest.mark.asyncio
    @patch("xml_pipeline.tools.fetch._is_private_ip", return_value=False)
    async def test_post_with_body(self, _mock_ip: object) -> None:
        patcher, mock_client = _mock_httpx_client(
            url="https://api.example.com/data",
            status_code=201,
            content=b'{"id": 1}',
        )
        with patcher:
            result = await _do_fetch(
                "https://api.example.com/data",
                method="POST",
                body='{"name": "test"}',
            )
        assert result["status_code"] == 201
        mock_client.request.assert_called_once()
        call_kwargs = mock_client.request.call_args
        assert call_kwargs[0][0] == "POST"
        assert call_kwargs[1].get("content") == '{"name": "test"}'

    @pytest.mark.asyncio
    @patch("xml_pipeline.tools.fetch._is_private_ip", return_value=False)
    async def test_redirect_url_captured(self, _mock_ip: object) -> None:
        patcher, _ = _mock_httpx_client(
            url="https://final.example.com/page",
            status_code=200,
            content=b"Redirected",
        )
        with patcher:
            result = await _do_fetch("https://example.com/old")
        assert result["url"] == "https://final.example.com/page"

    @pytest.mark.asyncio
    @patch("xml_pipeline.tools.fetch._is_private_ip", return_value=False)
    async def test_timeout_propagates(self, _mock_ip: object) -> None:
        import httpx
        patcher, _ = _mock_httpx_client(side_effect=httpx.ReadTimeout("timeout"))
        with patcher:
            with pytest.raises(httpx.ReadTimeout):
                await _do_fetch("https://example.com", timeout=5)

    @pytest.mark.asyncio
    @patch("xml_pipeline.tools.fetch._is_private_ip", return_value=False)
    async def test_size_limit_enforced(self, _mock_ip: object) -> None:
        big_content = b"x" * (MAX_RESPONSE_SIZE + 1)
        patcher, _ = _mock_httpx_client(content=big_content)
        with patcher:
            with pytest.raises(ValueError, match="exceeded"):
                await _do_fetch("https://example.com")

    @pytest.mark.asyncio
    @patch("xml_pipeline.tools.fetch._is_private_ip", return_value=False)
    async def test_binary_content_base64(self, _mock_ip: object) -> None:
        import base64
        binary = bytes(range(256))
        patcher, _ = _mock_httpx_client(content=binary)
        with patcher:
            result = await _do_fetch("https://example.com/binary")
        # Should be base64-encoded since it can't decode as UTF-8
        decoded = base64.b64decode(result["body"])
        assert decoded == binary

    @pytest.mark.asyncio
    async def test_method_validation_error(self) -> None:
        with pytest.raises(ValueError, match="not allowed"):
            await _do_fetch("https://example.com", method="TRACE")

    @pytest.mark.asyncio
    async def test_ssrf_blocked(self) -> None:
        with pytest.raises(ValueError, match="blocked"):
            await _do_fetch("http://localhost/admin")


# ═══════════════════════════════════════════════════════════════════════════
# TestHandleFetch — message-bus handler
# ═══════════════════════════════════════════════════════════════════════════

class TestHandleFetch:
    """handle_fetch() wraps _do_fetch() and always responds to caller."""

    @pytest.mark.asyncio
    @patch("xml_pipeline.tools.fetch._is_private_ip", return_value=False)
    async def test_success_response(self, _mock_ip: object) -> None:
        patcher, _ = _mock_httpx_client(
            url="https://example.com",
            status_code=200,
            content=b"OK",
            headers={"content-type": "text/plain"},
        )
        with patcher:
            resp = await handle_fetch(
                FetchRequest(url="https://example.com"),
                _metadata(),
            )
        assert isinstance(resp, HandlerResponse)
        assert resp.is_response is True
        payload = resp.payload
        assert isinstance(payload, FetchResponse)
        assert payload.status_code == 200
        assert payload.body == "OK"
        assert payload.error == ""

    @pytest.mark.asyncio
    async def test_error_response(self) -> None:
        resp = await handle_fetch(
            FetchRequest(url="http://localhost/admin"),
            _metadata(),
        )
        payload = resp.payload
        assert isinstance(payload, FetchResponse)
        assert payload.status_code == 0
        assert payload.error != ""
        assert "blocked" in payload.error.lower()

    @pytest.mark.asyncio
    @patch("xml_pipeline.tools.fetch._is_private_ip", return_value=False)
    async def test_header_parsing(self, _mock_ip: object) -> None:
        patcher, mock_client = _mock_httpx_client()
        with patcher:
            await handle_fetch(
                FetchRequest(
                    url="https://example.com",
                    headers="Authorization: Bearer token123\nX-Custom: value",
                ),
                _metadata(),
            )
        call_kwargs = mock_client.request.call_args
        headers = call_kwargs[1].get("headers")
        assert headers == {"Authorization": "Bearer token123", "X-Custom": "value"}

    @pytest.mark.asyncio
    async def test_always_responds(self) -> None:
        """Handler always uses .respond() regardless of success/failure."""
        resp = await handle_fetch(
            FetchRequest(url="not-a-url"),
            _metadata(),
        )
        assert resp.is_response is True

    @pytest.mark.asyncio
    @patch("xml_pipeline.tools.fetch._is_private_ip", return_value=False)
    async def test_timeout_param_forwarded(self, _mock_ip: object) -> None:
        import httpx as httpx_mod
        patcher, _ = _mock_httpx_client(side_effect=httpx_mod.ReadTimeout("timeout"))
        with patcher:
            resp = await handle_fetch(
                FetchRequest(url="https://example.com", timeout=5),
                _metadata(),
            )
        payload = resp.payload
        assert payload.error != ""
        assert payload.status_code == 0

    @pytest.mark.asyncio
    async def test_invalid_url_error(self) -> None:
        resp = await handle_fetch(
            FetchRequest(url="ftp://bad-scheme.com"),
            _metadata(),
        )
        assert resp.payload.error != ""
        assert "not allowed" in resp.payload.error.lower()


# ═══════════════════════════════════════════════════════════════════════════
# TestParseHeaders — header string parsing
# ═══════════════════════════════════════════════════════════════════════════

class TestParseHeaders:
    """_parse_headers() splits newline-separated 'Key: Value' pairs."""

    def test_empty_returns_none(self) -> None:
        assert _parse_headers("") is None
        assert _parse_headers("   ") is None

    def test_single_header(self) -> None:
        result = _parse_headers("Content-Type: application/json")
        assert result == {"Content-Type": "application/json"}

    def test_multiple_headers(self) -> None:
        raw = "Authorization: Bearer tok\nX-Custom: hello\nAccept: text/html"
        result = _parse_headers(raw)
        assert result == {
            "Authorization": "Bearer tok",
            "X-Custom": "hello",
            "Accept": "text/html",
        }

    def test_colon_in_value(self) -> None:
        """Authorization: Bearer abc:def:ghi should preserve colons in value."""
        result = _parse_headers("Authorization: Bearer abc:def:ghi")
        assert result == {"Authorization": "Bearer abc:def:ghi"}


# ═══════════════════════════════════════════════════════════════════════════
# TestFetchPayloadRoundtrip — xmlify serialization
# ═══════════════════════════════════════════════════════════════════════════

class TestFetchPayloadRoundtrip:
    """FetchRequest and FetchResponse serialize to/from XML correctly."""

    def test_fetch_request_roundtrip(self) -> None:
        from lxml import etree
        from third_party.xmlable import parse_element

        req = FetchRequest(url="https://example.com", method="POST", body='{"a":1}')
        tree = req.xml_value("FetchRequest")
        parsed = parse_element(FetchRequest, tree)
        assert parsed.url == "https://example.com"
        assert parsed.method == "POST"
        assert parsed.body == '{"a":1}'

    def test_fetch_response_roundtrip(self) -> None:
        from lxml import etree
        from third_party.xmlable import parse_element

        resp = FetchResponse(
            url="https://example.com",
            status_code=200,
            body="Hello",
            content_type="text/html",
            error="",
        )
        tree = resp.xml_value("FetchResponse")
        parsed = parse_element(FetchResponse, tree)
        assert parsed.status_code == 200
        assert parsed.body == "Hello"
        assert parsed.error == ""

    def test_fetch_request_defaults(self) -> None:
        req = FetchRequest(url="https://example.com")
        assert req.method == "GET"
        assert req.body == ""
        assert req.headers == ""
        assert req.timeout == 30
        assert req.allow_internal == 0

    def test_fetch_request_all_fields(self) -> None:
        req = FetchRequest(
            url="https://api.example.com/data",
            method="PUT",
            body='{"update": true}',
            headers="Authorization: Bearer x",
            timeout=60,
            allow_internal=1,
        )
        assert req.url == "https://api.example.com/data"
        assert req.method == "PUT"
        assert req.timeout == 60
        assert req.allow_internal == 1


# ═══════════════════════════════════════════════════════════════════════════
# TestHttpxTool — @tool wrapper with mocked httpx
# ═══════════════════════════════════════════════════════════════════════════

class TestHttpxTool:
    """fetch_url @tool wrapper uses httpx via _do_fetch()."""

    @pytest.mark.asyncio
    @patch("xml_pipeline.tools.fetch._is_private_ip", return_value=False)
    async def test_get_via_tool(self, _mock_ip: object) -> None:
        patcher, _ = _mock_httpx_client(
            url="https://example.com",
            status_code=200,
            content=b"tool response",
            headers={"content-type": "text/html"},
        )
        with patcher:
            result = await fetch_url(url="https://example.com")
        assert result.success is True
        assert result.data["status_code"] == 200
        assert result.data["body"] == "tool response"

    @pytest.mark.asyncio
    @patch("xml_pipeline.tools.fetch._is_private_ip", return_value=False)
    async def test_post_via_tool(self, _mock_ip: object) -> None:
        patcher, _ = _mock_httpx_client(
            url="https://api.example.com",
            status_code=201,
            content=b'{"created": true}',
        )
        with patcher:
            result = await fetch_url(
                url="https://api.example.com",
                method="POST",
                body='{"name": "test"}',
            )
        assert result.success is True
        assert result.data["status_code"] == 201

    @pytest.mark.asyncio
    @patch("xml_pipeline.tools.fetch._is_private_ip", return_value=False)
    async def test_timeout_error_via_tool(self, _mock_ip: object) -> None:
        import httpx as httpx_mod
        patcher, _ = _mock_httpx_client(side_effect=httpx_mod.ReadTimeout("timeout"))
        with patcher:
            result = await fetch_url(url="https://example.com", timeout=5)
        assert result.success is False
        assert "timed out" in result.error.lower()

    @pytest.mark.asyncio
    @patch("xml_pipeline.tools.fetch._is_private_ip", return_value=False)
    async def test_connection_error_via_tool(self, _mock_ip: object) -> None:
        import httpx as httpx_mod
        patcher, _ = _mock_httpx_client(
            side_effect=httpx_mod.ConnectError("connection refused")
        )
        with patcher:
            result = await fetch_url(url="https://example.com")
        assert result.success is False
        assert "error" in result.error.lower()


# ═══════════════════════════════════════════════════════════════════════════
# TestConstants — verify security constants
# ═══════════════════════════════════════════════════════════════════════════

class TestConstants:
    """Security constants are correctly defined."""

    def test_default_timeout(self) -> None:
        assert DEFAULT_TIMEOUT == 30

    def test_allowed_methods(self) -> None:
        assert ALLOWED_METHODS == {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"}

    def test_max_response_size(self) -> None:
        assert MAX_RESPONSE_SIZE == 10 * 1024 * 1024

    def test_max_request_body(self) -> None:
        assert MAX_REQUEST_BODY == 1_048_576


# ============================================================================
# #47 — Request body size limit
# ============================================================================


class TestRequestBodySizeLimit:
    """Test that request body size is limited."""

    async def test_body_exceeding_limit_raises(self) -> None:
        huge_body = "x" * (MAX_REQUEST_BODY + 1)
        with pytest.raises(ValueError, match="Request body exceeds maximum size"):
            await _do_fetch(
                url="https://example.com",
                method="POST",
                body=huge_body,
            )

    @patch("xml_pipeline.tools.fetch._is_private_ip", return_value=False)
    async def test_body_at_limit_accepted(self, _mock_ip) -> None:
        """Body exactly at the limit should not raise the size error."""
        body_at_limit = "x" * MAX_REQUEST_BODY
        # Will fail with a connection error (not body size error)
        # since we don't mock the HTTP client, but that proves
        # the body size check passed
        try:
            await _do_fetch(
                url="https://example.com",
                method="POST",
                body=body_at_limit,
            )
        except Exception as e:
            # Should NOT be the body size error
            assert "Request body exceeds" not in str(e)
