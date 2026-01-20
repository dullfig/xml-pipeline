"""
Fetch tool - HTTP requests with security controls.

Uses aiohttp for async HTTP operations.
"""

from __future__ import annotations

import ipaddress
import socket
from typing import Optional, Dict
from urllib.parse import urlparse

from .base import tool, ToolResult

# Try to import aiohttp - optional dependency
try:
    import aiohttp
    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False


# Security configuration
MAX_RESPONSE_SIZE = 10 * 1024 * 1024  # 10 MB
DEFAULT_TIMEOUT = 30
ALLOWED_SCHEMES = {"http", "https"}
BLOCKED_HOSTS = {
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "::1",
    "metadata.google.internal",  # GCP metadata
    "169.254.169.254",  # AWS/Azure/GCP metadata
}


def _is_private_ip(hostname: str) -> bool:
    """Check if hostname resolves to a private/internal IP."""
    try:
        # Try to parse as IP address first
        try:
            ip = ipaddress.ip_address(hostname)
            return ip.is_private or ip.is_loopback or ip.is_link_local
        except ValueError:
            pass

        # Resolve hostname to IP
        ip_str = socket.gethostbyname(hostname)
        ip = ipaddress.ip_address(ip_str)
        return ip.is_private or ip.is_loopback or ip.is_link_local
    except (socket.gaierror, socket.herror):
        # Can't resolve - block by default for security
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
        return f"Access to internal/private IPs is not allowed"

    return None


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
    if not AIOHTTP_AVAILABLE:
        return ToolResult(
            success=False,
            error="aiohttp not installed. Install with: pip install xml-pipeline[server]"
        )

    # Validate URL
    if error := _validate_url(url, allow_internal):
        return ToolResult(success=False, error=error)

    # Validate method
    method = method.upper()
    allowed_methods = {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"}
    if method not in allowed_methods:
        return ToolResult(success=False, error=f"Method '{method}' not allowed")

    # Clamp timeout
    timeout = min(max(1, timeout), 300)

    try:
        client_timeout = aiohttp.ClientTimeout(total=timeout)
        async with aiohttp.ClientSession(timeout=client_timeout) as session:
            async with session.request(
                method,
                url,
                headers=headers,
                data=body,
            ) as resp:
                # Check response size before reading
                content_length = resp.headers.get("Content-Length")
                if content_length and int(content_length) > MAX_RESPONSE_SIZE:
                    return ToolResult(
                        success=False,
                        error=f"Response too large: {content_length} bytes (max: {MAX_RESPONSE_SIZE})"
                    )

                # Read response with size limit
                body_bytes = await resp.content.read(MAX_RESPONSE_SIZE + 1)
                if len(body_bytes) > MAX_RESPONSE_SIZE:
                    return ToolResult(
                        success=False,
                        error=f"Response exceeded {MAX_RESPONSE_SIZE} bytes"
                    )

                # Try to decode as text
                try:
                    body_text = body_bytes.decode("utf-8")
                except UnicodeDecodeError:
                    # Return base64 for binary content
                    import base64
                    body_text = base64.b64encode(body_bytes).decode("ascii")

                return ToolResult(success=True, data={
                    "status_code": resp.status,
                    "headers": dict(resp.headers),
                    "body": body_text,
                    "url": str(resp.url),  # Final URL after redirects
                })

    except aiohttp.ClientError as e:
        return ToolResult(success=False, error=f"HTTP error: {e}")
    except TimeoutError:
        return ToolResult(success=False, error=f"Request timed out after {timeout}s")
    except Exception as e:
        return ToolResult(success=False, error=f"Fetch error: {e}")
