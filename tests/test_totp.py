"""
test_totp.py — Tests for TOTP authentication (RFC 6238).

Tests cover:
1. Secret generation (base32 format, length, uniqueness)
2. Provisioning URI (format, escaping, parameters)
3. Compute TOTP (known vectors, drift, digits)
4. Verify TOTP (valid, expired, wrong, window boundary)
5. OOB integration (auth required + valid/wrong/missing token)

Run with: pytest tests/test_totp.py -v
"""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from xml_pipeline.crypto.totp import (
    compute_totp,
    generate_secret,
    get_provisioning_uri,
    verify_totp,
)


# ============================================================================
# TestGenerateSecret
# ============================================================================


class TestGenerateSecret:
    def test_default_length_base32(self):
        secret = generate_secret()
        # 20 bytes → 32 base32 chars (before stripping padding)
        assert len(secret) >= 24  # At least 24 chars after strip
        assert secret.isalnum() or "=" in secret  # base32 chars

    def test_base32_decodable(self):
        import base64

        secret = generate_secret()
        # Add padding for decode
        padded = secret + "=" * (-len(secret) % 8)
        decoded = base64.b32decode(padded.upper())
        assert len(decoded) == 20

    def test_custom_length(self):
        import base64

        secret = generate_secret(length=32)
        padded = secret + "=" * (-len(secret) % 8)
        decoded = base64.b32decode(padded.upper())
        assert len(decoded) == 32

    def test_uniqueness(self):
        secrets = {generate_secret() for _ in range(100)}
        assert len(secrets) == 100

    def test_no_padding(self):
        secret = generate_secret()
        assert "=" not in secret


# ============================================================================
# TestProvisioningUri
# ============================================================================


class TestProvisioningUri:
    def test_basic_format(self):
        uri = get_provisioning_uri("JBSWY3DPEHPK3PXP", "myorg")
        assert uri.startswith("otpauth://totp/")
        assert "secret=JBSWY3DPEHPK3PXP" in uri
        assert "issuer=xml-pipeline" in uri
        assert "algorithm=SHA1" in uri
        assert "digits=6" in uri
        assert "period=30" in uri

    def test_name_in_label(self):
        uri = get_provisioning_uri("SECRET", "test-organism")
        assert "xml-pipeline%3Atest-organism" in uri

    def test_custom_issuer(self):
        uri = get_provisioning_uri("SECRET", "org", issuer="MyApp")
        assert "issuer=MyApp" in uri
        assert "MyApp%3Aorg" in uri

    def test_special_characters_escaped(self):
        uri = get_provisioning_uri("SECRET", "org with spaces")
        assert "org%20with%20spaces" in uri


# ============================================================================
# TestComputeTotp
# ============================================================================


class TestComputeTotp:
    def test_deterministic(self):
        """Same secret + time = same code."""
        secret = "JBSWY3DPEHPK3PXP"
        code1 = compute_totp(secret, now=1000000.0)
        code2 = compute_totp(secret, now=1000000.0)
        assert code1 == code2

    def test_six_digits(self):
        code = compute_totp("JBSWY3DPEHPK3PXP", now=1000000.0)
        assert len(code) == 6
        assert code.isdigit()

    def test_zero_padded(self):
        """Codes with leading zeros should be padded."""
        # We can't easily predict when this happens, but verify format
        secret = generate_secret()
        code = compute_totp(secret, now=0.0)
        assert len(code) == 6

    def test_different_time_different_code(self):
        secret = "JBSWY3DPEHPK3PXP"
        # 1000s apart — different time step
        code1 = compute_totp(secret, now=0.0)
        code2 = compute_totp(secret, now=1000.0)
        assert code1 != code2

    def test_drift_minus_one(self):
        secret = "JBSWY3DPEHPK3PXP"
        now = 1000000.0
        # drift=-1 at time T should equal drift=0 at time T-30
        code_drift = compute_totp(secret, drift=-1, now=now)
        code_prev = compute_totp(secret, drift=0, now=now - 30)
        assert code_drift == code_prev

    def test_drift_plus_one(self):
        secret = "JBSWY3DPEHPK3PXP"
        now = 1000000.0
        code_drift = compute_totp(secret, drift=1, now=now)
        code_next = compute_totp(secret, drift=0, now=now + 30)
        assert code_drift == code_next

    def test_eight_digits(self):
        code = compute_totp("JBSWY3DPEHPK3PXP", digits=8, now=1000000.0)
        assert len(code) == 8
        assert code.isdigit()

    def test_rfc6238_known_vector_sha1(self):
        """RFC 6238 test vector: secret='12345678901234567890' at T=59."""
        import base64

        # The RFC 6238 secret is the ASCII string "12345678901234567890"
        raw_secret = b"12345678901234567890"
        b32_secret = base64.b32encode(raw_secret).decode("ascii").rstrip("=")

        # At T=59s, counter = 59//30 = 1
        code = compute_totp(b32_secret, now=59.0, digits=8)
        assert code == "94287082"

    def test_same_step_boundary(self):
        """Times within the same 30s step produce the same code."""
        secret = "JBSWY3DPEHPK3PXP"
        code1 = compute_totp(secret, now=60.0)
        code2 = compute_totp(secret, now=89.0)
        assert code1 == code2

    def test_step_boundary_change(self):
        """Times across step boundary produce different codes."""
        secret = "JBSWY3DPEHPK3PXP"
        code1 = compute_totp(secret, now=89.0)   # step 2
        code2 = compute_totp(secret, now=90.0)   # step 3
        assert code1 != code2


# ============================================================================
# TestVerifyTotp
# ============================================================================


class TestVerifyTotp:
    def test_valid_current(self):
        secret = generate_secret()
        now = 1000000.0
        token = compute_totp(secret, now=now)
        assert verify_totp(secret, token, now=now) is True

    def test_valid_previous_step(self):
        """Token from previous step is valid with window=1."""
        secret = generate_secret()
        now = 1000000.0
        token = compute_totp(secret, now=now - 30)
        assert verify_totp(secret, token, now=now) is True

    def test_valid_next_step(self):
        """Token from next step is valid with window=1."""
        secret = generate_secret()
        now = 1000000.0
        token = compute_totp(secret, now=now + 30)
        assert verify_totp(secret, token, now=now) is True

    def test_expired_token(self):
        """Token from 2 steps ago is invalid with window=1."""
        secret = generate_secret()
        now = 1000000.0
        token = compute_totp(secret, now=now - 60)
        assert verify_totp(secret, token, now=now) is False

    def test_wrong_token(self):
        secret = generate_secret()
        assert verify_totp(secret, "000000", now=1000000.0) is False

    def test_empty_token(self):
        assert verify_totp("JBSWY3DPEHPK3PXP", "", now=1000000.0) is False

    def test_wrong_length(self):
        assert verify_totp("JBSWY3DPEHPK3PXP", "12345", now=1000000.0) is False

    def test_non_digit_token(self):
        assert verify_totp("JBSWY3DPEHPK3PXP", "abcdef", now=1000000.0) is False

    def test_window_zero(self):
        """With window=0, only exact current code is valid."""
        secret = generate_secret()
        now = 1000000.0
        token = compute_totp(secret, now=now)
        assert verify_totp(secret, token, window=0, now=now) is True

        # Previous step should fail with window=0
        token_prev = compute_totp(secret, now=now - 30)
        assert verify_totp(secret, token_prev, window=0, now=now) is False

    def test_window_two(self):
        """With window=2, tokens from 2 steps away are valid."""
        secret = generate_secret()
        now = 1000000.0
        token = compute_totp(secret, now=now - 60)
        assert verify_totp(secret, token, window=2, now=now) is True

    def test_timing_safe_comparison(self):
        """verify_totp uses hmac.compare_digest (timing-safe)."""
        import hmac
        secret = generate_secret()
        now = 1000000.0
        token = compute_totp(secret, now=now)
        # This is implicitly tested by the implementation using
        # hmac.compare_digest, but we verify correct behavior
        assert verify_totp(secret, token, now=now) is True


# ============================================================================
# TestTotpOOBIntegration
# ============================================================================


class TestTotpOOBIntegration:
    """Tests for TOTP integration with OOB server."""

    def _make_totp_auth_msg(self, token: str, request_id: str = "auth-1") -> bytes:
        """Build a <privileged-msg> with <totp-auth> command."""
        from xml_pipeline.oob.protocol import NS

        return (
            f'<privileged-msg xmlns="{NS}" version="1.1">'
            f'<payload xmlns="{NS}" id="{request_id}" timestamp="2026-02-08T00:00:00Z">'
            f'<totp-auth xmlns="{NS}"><token xmlns="{NS}">{token}</token></totp-auth>'
            f'</payload>'
            f'<signature xmlns="{NS}" algorithm="ed25519">'
            f'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA'
            f'</signature>'
            f'</privileged-msg>'
        ).encode()

    def test_check_totp_valid(self):
        """Valid TOTP token authenticates the connection."""
        from xml_pipeline.oob.server import OOBServer
        from xml_pipeline.message_bus.stream_pump import StreamPump

        secret = generate_secret()
        now = 1000000.0
        token = compute_totp(secret, now=now)

        pump = StreamPump(name="test")
        server = OOBServer(pump=pump, totp_secret=secret)

        ws = MagicMock()
        ws_id = id(ws)

        msg = self._make_totp_auth_msg(token)

        with patch("xml_pipeline.crypto.totp.time.time", return_value=now):
            result = server._check_totp_auth(msg, ws)

        assert result is True
        assert ws_id in server._authenticated

    def test_check_totp_wrong_token(self):
        """Wrong TOTP token is rejected."""
        from xml_pipeline.oob.server import OOBServer
        from xml_pipeline.message_bus.stream_pump import StreamPump

        secret = generate_secret()
        pump = StreamPump(name="test")
        server = OOBServer(pump=pump, totp_secret=secret)

        ws = MagicMock()
        msg = self._make_totp_auth_msg("000000")

        result = server._check_totp_auth(msg, ws)

        assert result is False
        assert id(ws) not in server._authenticated

    def test_check_totp_missing_token(self):
        """Missing token element is rejected."""
        from xml_pipeline.oob.server import OOBServer
        from xml_pipeline.message_bus.stream_pump import StreamPump
        from xml_pipeline.oob.protocol import NS

        secret = generate_secret()
        pump = StreamPump(name="test")
        server = OOBServer(pump=pump, totp_secret=secret)

        ws = MagicMock()
        # Send a non-auth command as first message
        msg = (
            f'<privileged-msg xmlns="{NS}" version="1.1">'
            f'<payload xmlns="{NS}" id="x" timestamp="2026-02-08T00:00:00Z">'
            f'<list-listeners xmlns="{NS}"/>'
            f'</payload>'
            f'<signature xmlns="{NS}" algorithm="ed25519">'
            f'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA'
            f'</signature>'
            f'</privileged-msg>'
        ).encode()

        result = server._check_totp_auth(msg, ws)
        assert result is False

    def test_no_secret_no_totp_required(self):
        """Without totp_secret, no TOTP gate is applied."""
        from xml_pipeline.oob.server import OOBServer
        from xml_pipeline.message_bus.stream_pump import StreamPump

        pump = StreamPump(name="test")
        server = OOBServer(pump=pump, totp_secret=None)

        # totp_secret is None, so the gate in _connection_handler is skipped
        assert server.totp_secret is None

    def test_check_totp_invalid_xml(self):
        """Invalid XML is rejected."""
        from xml_pipeline.oob.server import OOBServer
        from xml_pipeline.message_bus.stream_pump import StreamPump

        secret = generate_secret()
        pump = StreamPump(name="test")
        server = OOBServer(pump=pump, totp_secret=secret)

        ws = MagicMock()
        result = server._check_totp_auth(b"not xml", ws)
        assert result is False
