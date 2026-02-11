"""
auth.py — API key authentication for the AgentServer REST API.

Protects mutating endpoints (inject, reload, kill, stop, pause, resume, reset)
with Bearer token authentication. GET endpoints remain open for monitoring.

Security hitlist #40.
"""

from __future__ import annotations

import logging
import os
import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

logger = logging.getLogger(__name__)

# Module-level state
_api_key: str = ""
_auth_enabled: bool = False

_bearer_scheme = HTTPBearer(auto_error=False)


def configure_auth(api_key_env: str) -> None:
    """Configure API authentication from an environment variable name.

    Args:
        api_key_env: Name of the env var holding the API key.
                     Empty string or unset env var → auth disabled.
    """
    global _api_key, _auth_enabled

    if not api_key_env:
        _api_key = ""
        _auth_enabled = False
        return

    key = os.environ.get(api_key_env, "")
    if not key:
        _api_key = ""
        _auth_enabled = False
        logger.warning(
            "auth.api_key_env=%r is set but env var is empty/missing — "
            "REST API auth is DISABLED",
            api_key_env,
        )
        return

    _api_key = key
    _auth_enabled = True
    logger.info("REST API authentication enabled (key from %s)", api_key_env)


async def require_auth(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> None:
    """FastAPI dependency that enforces Bearer token auth on mutating endpoints.

    No-op when auth is disabled (no api_key_env configured).
    """
    if not _auth_enabled:
        return

    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not secrets.compare_digest(credentials.credentials, _api_key):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API key",
        )


def verify_token(token: str) -> bool:
    """Verify a token string. For use in WebSocket inject auth."""
    if not _auth_enabled:
        return True
    if not token:
        return False
    return secrets.compare_digest(token, _api_key)


def is_auth_enabled() -> bool:
    """Check whether API authentication is currently enabled."""
    return _auth_enabled
