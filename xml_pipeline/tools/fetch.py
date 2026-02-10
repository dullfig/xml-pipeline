"""
Fetch tool - HTTP requests with security controls.

Uses httpx for async HTTP operations. Also provides @xmlify payload classes
and a handler for use as a message-bus listener (following calculator pattern).

Note: Do NOT use `from __future__ import annotations` here — it breaks
xmlable's type introspection on @xmlify dataclasses.
"""

import base64
import ipaddress
import socket
from dataclasses import dataclass
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import httpx

from third_party.xmlable import xmlify

from xml_pipeline.message_bus.message_state import HandlerMetadata, HandlerResponse

from .base import tool, ToolResult


# Security configuration
MAX_RESPONSE_SIZE = 10 * 1024 * 1024  # 10 MB
MAX_REDIRECTS = 5
DEFAULT_TIMEOUT = 30
ALLOWED_SCHEMES = {"http", "https"}
ALLOWED_METHODS = {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"}
BLOCKED_HOSTS = {
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "::1",
    "metadata.google.internal",  # GCP metadata
    "169.254.169.254",  # AWS/Azure/GCP metadata
}


# ---------------------------------------------------------------------------
# Security helpers (unchanged)
# ---------------------------------------------------------------------------


def _is_dangerous_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Check if an IP address is private, loopback, link-local, or reserved."""
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _is_private_ip(hostname: str) -> bool:
    """Check if hostname resolves to a private/internal IP.

    Always resolves through DNS to catch hex/octal IP encoding bypasses.
    """
    try:
        # Always resolve hostname to canonical IP first (catches hex/octal tricks)
        ip_str = socket.gethostbyname(hostname)
        ip = ipaddress.ip_address(ip_str)
        return _is_dangerous_ip(ip)
    except (socket.gaierror, socket.herror):
        pass

    # Fallback: try parsing as literal IP (handles cases where DNS is unavailable)
    try:
        ip = ipaddress.ip_address(hostname)
        return _is_dangerous_ip(ip)
    except ValueError:
        pass

    # Can't resolve — block by default for security
    return True


def _validate_url(url: str, allow_internal: bool = False) -> Optional[str]:
    """Validate URL for security. Returns error message or None if OK."""
    try:
        parsed = urlparse(url)
    except Exception:
        return "Invalid URL format"

    if parsed.scheme not in ALLOWED_SCHEMES:
        return f"Scheme '{parsed.scheme}' not allowed. Use http or https."

    if not parsed.netloc:
        return "URL must have a host"

    hostname = parsed.hostname or ""

    if hostname in BLOCKED_HOSTS:
        return f"Host '{hostname}' is blocked"

    if not allow_internal and _is_private_ip(hostname):
        return "Access to internal/private IPs is not allowed"

    return None


# ---------------------------------------------------------------------------
# Core fetch function (shared by @tool and handler)
# ---------------------------------------------------------------------------


async def _do_fetch(
    url: str,
    method: str = "GET",
    headers: Optional[Dict[str, str]] = None,
    body: Optional[str] = None,
    timeout: int = DEFAULT_TIMEOUT,
    allow_internal: bool = False,
) -> Dict[str, Any]:
    """
    Perform an HTTP fetch with security validation.

    Shared by both the @tool wrapper and the message-bus handler.

    Returns:
        Dict with keys: url, status_code, body, content_type

    Raises:
        ValueError: On URL/method validation failure.
        httpx.TimeoutException: On timeout.
        httpx.HTTPError: On HTTP-level errors.
    """
    # Validate URL
    if error := _validate_url(url, allow_internal):
        raise ValueError(error)

    # Validate method
    method = method.upper()
    if method not in ALLOWED_METHODS:
        raise ValueError(f"Method '{method}' not allowed")

    # Clamp timeout
    timeout = min(max(1, timeout), 300)

    async with httpx.AsyncClient(
        follow_redirects=False,
        timeout=httpx.Timeout(timeout),
    ) as client:
        # Manual redirect loop: re-validate each Location against SSRF rules
        current_url = url
        for _ in range(MAX_REDIRECTS):
            response = await client.request(
                method,
                current_url,
                headers=headers,
                content=body,
            )

            if response.status_code not in (301, 302, 303, 307, 308):
                break

            location = response.headers.get("location")
            if not location:
                break

            # Resolve relative redirects against current URL
            current_url = str(response.url.join(location))

            # Re-validate the redirect target against SSRF rules
            if redirect_error := _validate_url(current_url, allow_internal):
                raise ValueError(
                    f"Redirect blocked: {redirect_error} (from {url})"
                )

            # 303 converts to GET per HTTP spec
            if response.status_code == 303:
                method = "GET"
                body = None
        else:
            raise ValueError(f"Too many redirects (max {MAX_REDIRECTS})")

        # Check response size
        body_bytes = response.content
        if len(body_bytes) > MAX_RESPONSE_SIZE:
            raise ValueError(f"Response exceeded {MAX_RESPONSE_SIZE} bytes")

        # Try to decode as text
        try:
            body_text = body_bytes.decode("utf-8")
        except UnicodeDecodeError:
            body_text = base64.b64encode(body_bytes).decode("ascii")

        content_type = response.headers.get("content-type", "")

        return {
            "url": str(response.url),
            "status_code": response.status_code,
            "body": body_text,
            "content_type": content_type,
        }


# ---------------------------------------------------------------------------
# @tool wrapper (existing API, now uses httpx via _do_fetch)
# ---------------------------------------------------------------------------


@tool
async def fetch_url(
    url: str,
    method: str = "GET",
    headers: Optional[Dict[str, str]] = None,
    body: Optional[str] = None,
    timeout: int = DEFAULT_TIMEOUT,
    allow_internal: bool = False,
) -> ToolResult:
    """
    Fetch content from a URL.

    Args:
        url: The URL to fetch
        method: HTTP method (GET, POST, PUT, DELETE, PATCH, HEAD)
        headers: Optional HTTP headers
        body: Optional request body for POST/PUT/PATCH
        timeout: Request timeout in seconds (default: 30, max: 300)
        allow_internal: Allow internal/private IPs (default: false)

    Returns:
        status_code, headers, body, url (final URL after redirects)

    Security:
        - Only http/https schemes allowed
        - No access to localhost, metadata endpoints, or private IPs by default
        - Response size limited to 10 MB
        - Timeout enforced
    """
    try:
        result = await _do_fetch(
            url=url,
            method=method,
            headers=headers,
            body=body,
            timeout=timeout,
            allow_internal=allow_internal,
        )
        return ToolResult(success=True, data=result)
    except ValueError as e:
        return ToolResult(success=False, error=str(e))
    except httpx.TimeoutException:
        return ToolResult(success=False, error=f"Request timed out after {timeout}s")
    except httpx.HTTPError as e:
        return ToolResult(success=False, error=f"HTTP error: {e}")
    except Exception as e:
        return ToolResult(success=False, error=f"Fetch error: {e}")


# ---------------------------------------------------------------------------
# Message-bus payload classes
# ---------------------------------------------------------------------------


@xmlify
@dataclass
class FetchRequest:
    """Fetch content from a URL via HTTP."""
    url: str
    method: str = "GET"
    body: str = ""
    headers: str = ""          # Newline-separated "Key: Value" pairs
    timeout: int = 30
    allow_internal: int = 0    # 0=false, 1=true (xmlify-safe boolean)


@xmlify
@dataclass
class FetchResponse:
    """Result of an HTTP fetch operation."""
    url: str
    status_code: int
    body: str
    content_type: str
    error: str                 # Empty on success


# ---------------------------------------------------------------------------
# Header parsing helper
# ---------------------------------------------------------------------------


def _parse_headers(raw: str) -> Optional[Dict[str, str]]:
    """
    Parse newline-separated 'Key: Value' header pairs.

    Handles Authorization headers with colons in values by splitting on
    first ': ' occurrence only.

    Returns:
        Dict of headers, or None if raw is empty.
    """
    if not raw or not raw.strip():
        return None

    result: Dict[str, str] = {}
    for line in raw.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        # Split on first ': ' only (handles colons in values)
        if ": " in line:
            key, value = line.split(": ", 1)
            result[key] = value
        elif ":" in line:
            key, value = line.split(":", 1)
            result[key.strip()] = value.strip()
    return result if result else None


# ---------------------------------------------------------------------------
# Message-bus handler
# ---------------------------------------------------------------------------


async def handle_fetch(
    payload: FetchRequest, metadata: HandlerMetadata
) -> HandlerResponse:
    """Handler for the fetch listener — always responds to caller."""
    try:
        headers = _parse_headers(payload.headers)
        result = await _do_fetch(
            url=payload.url,
            method=payload.method,
            headers=headers,
            body=payload.body or None,
            timeout=payload.timeout,
            allow_internal=bool(payload.allow_internal),
        )
        return HandlerResponse.respond(
            payload=FetchResponse(
                url=result["url"],
                status_code=result["status_code"],
                body=result["body"],
                content_type=result["content_type"],
                error="",
            )
        )
    except Exception as e:
        return HandlerResponse.respond(
            payload=FetchResponse(
                url=payload.url,
                status_code=0,
                body="",
                content_type="",
                error=str(e),
            )
        )
