"""
totp.py — RFC 6238 TOTP implementation using stdlib only.

Provides TOTP (Time-based One-Time Password) generation and verification
for the OOB privileged channel as a second factor alongside Ed25519 signing.

No external dependencies — uses hmac + hashlib from stdlib.

Usage:
    from xml_pipeline.crypto.totp import generate_secret, verify_totp

    secret = generate_secret()
    # ... user enrolls via provisioning URI ...
    if verify_totp(secret, user_provided_token):
        # authenticated
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import struct
import time
import urllib.parse


def generate_secret(length: int = 20) -> str:
    """
    Generate a random TOTP secret encoded as base32.

    Args:
        length: Number of random bytes (default 20 = 160 bits, per RFC 4226).

    Returns:
        Base32-encoded secret string (no padding).
    """
    raw = os.urandom(length)
    return base64.b32encode(raw).decode("ascii").rstrip("=")


def get_provisioning_uri(
    secret: str,
    name: str,
    issuer: str = "xml-pipeline",
) -> str:
    """
    Build an otpauth:// URI for QR code enrollment.

    Args:
        secret: Base32-encoded secret.
        name: Account name (e.g., organism name).
        issuer: Issuer string shown in authenticator app.

    Returns:
        otpauth://totp/... URI string.
    """
    label = urllib.parse.quote(f"{issuer}:{name}", safe="")
    params = urllib.parse.urlencode({
        "secret": secret,
        "issuer": issuer,
        "algorithm": "SHA1",
        "digits": "6",
        "period": "30",
    })
    return f"otpauth://totp/{label}?{params}"


def compute_totp(
    secret: str,
    *,
    drift: int = 0,
    time_step: int = 30,
    digits: int = 6,
    now: float | None = None,
) -> str:
    """
    Compute the current TOTP code.

    Args:
        secret: Base32-encoded secret.
        drift: Time step offset (0 = current, -1 = previous, +1 = next).
        time_step: Duration of each time step in seconds (default 30).
        digits: Number of output digits (default 6).
        now: Override current time (for testing). Defaults to time.time().

    Returns:
        Zero-padded TOTP code string (e.g., "012345").
    """
    # Decode secret (add padding if needed)
    padded = secret + "=" * (-len(secret) % 8)
    key = base64.b32decode(padded.upper())

    # Compute counter
    t = now if now is not None else time.time()
    counter = int(t // time_step) + drift

    # HMAC-SHA1 over 8-byte big-endian counter
    counter_bytes = struct.pack(">Q", counter)
    mac = hmac.new(key, counter_bytes, hashlib.sha1).digest()

    # Dynamic truncation (RFC 4226 section 5.4)
    offset = mac[-1] & 0x0F
    code_int = struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF

    # Modulo for desired digits
    code = code_int % (10 ** digits)
    return str(code).zfill(digits)


def verify_totp(
    secret: str,
    token: str,
    *,
    window: int = 1,
    time_step: int = 30,
    digits: int = 6,
    now: float | None = None,
) -> bool:
    """
    Verify a TOTP token with clock-drift tolerance.

    Checks the token against current time step and ±window adjacent steps.

    Args:
        secret: Base32-encoded secret.
        token: User-provided TOTP code to verify.
        window: Number of time steps to check on each side (default 1 = ±30s).
        time_step: Duration of each time step in seconds (default 30).
        digits: Expected number of digits (default 6).
        now: Override current time (for testing).

    Returns:
        True if token matches any code in the window.
    """
    if not token or len(token) != digits:
        return False
    if not token.isdigit():
        return False

    for drift in range(-window, window + 1):
        expected = compute_totp(
            secret, drift=drift, time_step=time_step, digits=digits, now=now,
        )
        if hmac.compare_digest(token, expected):
            return True

    return False
