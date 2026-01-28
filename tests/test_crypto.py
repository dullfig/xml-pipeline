"""
test_crypto.py — Tests for Ed25519 identity and envelope signing.

Tests:
1. Key generation and loading
2. Envelope signing
3. Signature verification
4. Tamper detection
5. Edge cases
"""

import tempfile
from pathlib import Path

import pytest
from lxml import etree

from xml_pipeline.crypto.identity import (
    Identity,
    IdentityError,
    generate_identity,
    load_public_key,
    verify_with_public_key,
)
from xml_pipeline.crypto.signing import (
    SIGNATURE_NAMESPACE,
    SignatureError,
    extract_signature,
    is_signed,
    sign_envelope,
    strip_signature,
    verify_envelope,
    verify_envelope_with_identity,
)
from xml_pipeline.message_bus.envelope import (
    build_envelope,
    envelope_to_bytes,
    extract_meta,
    extract_payload,
)


class TestIdentityGeneration:
    """Test identity key generation."""

    def test_generate_creates_valid_keypair(self):
        """generate_identity() should create a valid Ed25519 keypair."""
        identity = generate_identity()

        assert identity.private_key is not None
        assert identity.public_key is not None
        assert identity.path == "<generated>"

    def test_generated_key_can_sign_and_verify(self):
        """Generated identity should be able to sign and verify."""
        identity = generate_identity()
        data = b"test message"

        signature = identity.sign(data)

        assert len(signature) == 64  # Ed25519 signatures are 64 bytes
        assert identity.verify(signature, data) is True

    def test_signature_fails_for_wrong_data(self):
        """Verification should fail for modified data."""
        identity = generate_identity()
        data = b"original message"
        signature = identity.sign(data)

        assert identity.verify(signature, b"modified message") is False

    def test_sign_base64_produces_valid_encoding(self):
        """sign_base64() should produce valid base64."""
        import base64

        identity = generate_identity()
        data = b"test"

        sig_b64 = identity.sign_base64(data)

        # Should decode without error
        decoded = base64.b64decode(sig_b64)
        assert len(decoded) == 64


class TestIdentityPersistence:
    """Test saving and loading identity keys."""

    def test_save_and_load_roundtrip(self):
        """Identity should survive save/load cycle."""
        identity = generate_identity()
        data = b"test data to sign"
        original_sig = identity.sign(data)

        with tempfile.TemporaryDirectory() as tmpdir:
            private_path = Path(tmpdir) / "test.key"
            public_path = Path(tmpdir) / "test.pub"

            identity.save(private_path, public_path)

            # Files should exist
            assert private_path.exists()
            assert public_path.exists()

            # Load and verify
            loaded = Identity.load(private_path)
            loaded_sig = loaded.sign(data)

            # Signatures should match
            assert original_sig == loaded_sig

    def test_load_nonexistent_raises_error(self):
        """Loading non-existent key should raise IdentityError."""
        with pytest.raises(IdentityError, match="not found"):
            Identity.load("/nonexistent/path/key.pem")

    def test_load_invalid_key_raises_error(self):
        """Loading invalid key data should raise IdentityError."""
        with tempfile.NamedTemporaryFile(mode="wb", suffix=".pem", delete=False) as f:
            f.write(b"not a valid key")
            f.flush()

            with pytest.raises(IdentityError, match="Failed to load"):
                Identity.load(f.name)


class TestPublicKeyLoading:
    """Test loading public keys for verification."""

    def test_load_public_key(self):
        """load_public_key() should load a valid public key."""
        identity = generate_identity()

        with tempfile.TemporaryDirectory() as tmpdir:
            private_path = Path(tmpdir) / "test.key"
            public_path = Path(tmpdir) / "test.pub"
            identity.save(private_path, public_path)

            # Load public key
            pubkey = load_public_key(public_path)

            # Should verify signatures
            data = b"test"
            sig = identity.sign(data)
            assert verify_with_public_key(pubkey, sig, data) is True

    def test_load_public_key_nonexistent(self):
        """Loading non-existent public key should raise error."""
        with pytest.raises(IdentityError, match="not found"):
            load_public_key("/nonexistent/pubkey.pub")


class TestEnvelopeSigning:
    """Test envelope signing with Ed25519."""

    @pytest.fixture
    def sample_envelope(self):
        """Create a sample unsigned envelope."""
        ns = "https://xml-pipeline.org/ns/envelope/v1"
        nsmap = {None: ns}

        message = etree.Element(f"{{{ns}}}message", nsmap=nsmap)
        meta = etree.SubElement(message, f"{{{ns}}}meta")

        from_elem = etree.SubElement(meta, f"{{{ns}}}from")
        from_elem.text = "greeter"

        thread_elem = etree.SubElement(meta, f"{{{ns}}}thread")
        thread_elem.text = "test-thread-uuid"

        # Add a payload
        payload = etree.SubElement(message, "Greeting")
        name_elem = etree.SubElement(payload, "name")
        name_elem.text = "Alice"

        return message

    @pytest.fixture
    def identity(self):
        """Create a test identity."""
        return generate_identity()

    def test_sign_envelope_adds_signature(self, sample_envelope, identity):
        """sign_envelope() should add a signature element."""
        signed = sign_envelope(sample_envelope, identity)

        sig = extract_signature(signed)
        assert sig is not None
        assert len(sig) > 0

    def test_sign_envelope_creates_copy_by_default(self, sample_envelope, identity):
        """sign_envelope() should not modify original by default."""
        original_bytes = etree.tostring(sample_envelope)

        sign_envelope(sample_envelope, identity)

        # Original should be unchanged
        assert etree.tostring(sample_envelope) == original_bytes

    def test_sign_envelope_in_place(self, sample_envelope, identity):
        """sign_envelope(in_place=True) should modify original."""
        sign_envelope(sample_envelope, identity, in_place=True)

        sig = extract_signature(sample_envelope)
        assert sig is not None

    def test_signed_envelope_verifies(self, sample_envelope, identity):
        """Signed envelope should verify with same identity."""
        signed = sign_envelope(sample_envelope, identity)

        assert verify_envelope_with_identity(signed, identity) is True

    def test_signed_envelope_verifies_with_public_key(self, sample_envelope, identity):
        """Signed envelope should verify with public key."""
        signed = sign_envelope(sample_envelope, identity)

        assert verify_envelope(signed, identity.public_key) is True

    def test_tampered_envelope_fails_verification(self, sample_envelope, identity):
        """Modifying signed envelope should fail verification."""
        signed = sign_envelope(sample_envelope, identity)

        # Tamper with the payload
        ns = "https://xml-pipeline.org/ns/envelope/v1"
        meta = signed.find(f"{{{ns}}}meta")
        from_elem = meta.find(f"{{{ns}}}from")
        from_elem.text = "evil-agent"

        assert verify_envelope(signed, identity.public_key) is False

    def test_wrong_key_fails_verification(self, sample_envelope, identity):
        """Verification with wrong key should fail."""
        signed = sign_envelope(sample_envelope, identity)

        other_identity = generate_identity()
        assert verify_envelope(signed, other_identity.public_key) is False

    def test_unsigned_envelope_fails_verification(self, sample_envelope, identity):
        """Unsigned envelope should fail verification."""
        assert verify_envelope(sample_envelope, identity.public_key) is False


class TestSignatureHelpers:
    """Test signature utility functions."""

    @pytest.fixture
    def signed_envelope(self):
        """Create a signed envelope."""
        identity = generate_identity()
        envelope = build_envelope(
            payload=b"<Test>data</Test>",
            from_id="sender",
            thread_id="thread-123",
        )
        return sign_envelope(envelope, identity)

    def test_is_signed_true_for_signed(self, signed_envelope):
        """is_signed() should return True for signed envelope."""
        assert is_signed(signed_envelope) is True

    def test_is_signed_false_for_unsigned(self):
        """is_signed() should return False for unsigned envelope."""
        envelope = build_envelope(
            payload=b"<Test>data</Test>",
            from_id="sender",
            thread_id="thread-123",
        )
        assert is_signed(envelope) is False

    def test_extract_signature_returns_base64(self, signed_envelope):
        """extract_signature() should return base64 string."""
        import base64

        sig = extract_signature(signed_envelope)

        assert sig is not None
        decoded = base64.b64decode(sig)
        assert len(decoded) == 64

    def test_strip_signature_removes_sig(self, signed_envelope):
        """strip_signature() should remove signature element."""
        stripped = strip_signature(signed_envelope)

        assert is_signed(stripped) is False

    def test_strip_signature_preserves_original(self, signed_envelope):
        """strip_signature() should not modify original by default."""
        strip_signature(signed_envelope)

        assert is_signed(signed_envelope) is True


class TestEnvelopeBuilder:
    """Test envelope construction utilities."""

    def test_build_envelope_creates_valid_structure(self):
        """build_envelope() should create valid envelope structure."""
        envelope = build_envelope(
            payload=b"<Greeting><name>Alice</name></Greeting>",
            from_id="greeter",
            thread_id="thread-abc",
            to_id="shouter",
        )

        meta = extract_meta(envelope)
        assert meta["from_id"] == "greeter"
        assert meta["to_id"] == "shouter"
        assert meta["thread_id"] == "thread-abc"

        payload = extract_payload(envelope)
        assert payload is not None
        assert payload.tag == "Greeting"

    def test_build_envelope_without_to(self):
        """build_envelope() should work without to_id."""
        envelope = build_envelope(
            payload=b"<Test/>",
            from_id="sender",
            thread_id="thread-123",
        )

        meta = extract_meta(envelope)
        assert meta["from_id"] == "sender"
        assert meta["to_id"] is None

    def test_build_envelope_with_signing(self):
        """build_envelope() should sign when identity provided."""
        identity = generate_identity()

        envelope = build_envelope(
            payload=b"<Test/>",
            from_id="sender",
            thread_id="thread-123",
            identity=identity,
        )

        assert is_signed(envelope) is True
        assert verify_envelope(envelope, identity.public_key) is True

    def test_envelope_to_bytes_produces_xml(self):
        """envelope_to_bytes() should produce valid XML bytes."""
        envelope = build_envelope(
            payload=b"<Test/>",
            from_id="sender",
            thread_id="thread-123",
        )

        xml_bytes = envelope_to_bytes(envelope)

        assert xml_bytes.startswith(b"<?xml")
        assert b"<message" in xml_bytes


class TestSigningEdgeCases:
    """Test edge cases in signing."""

    def test_re_signing_replaces_signature(self):
        """Signing an already-signed envelope should replace signature."""
        identity1 = generate_identity()
        identity2 = generate_identity()

        envelope = build_envelope(
            payload=b"<Test/>",
            from_id="sender",
            thread_id="thread-123",
        )

        # Sign with first identity
        signed1 = sign_envelope(envelope, identity1)
        sig1 = extract_signature(signed1)

        # Re-sign with second identity
        signed2 = sign_envelope(signed1, identity2)
        sig2 = extract_signature(signed2)

        # Signatures should be different
        assert sig1 != sig2

        # Should verify with second identity only
        assert verify_envelope(signed2, identity1.public_key) is False
        assert verify_envelope(signed2, identity2.public_key) is True

    def test_sign_envelope_missing_meta_raises(self):
        """Signing envelope without meta should raise error."""
        identity = generate_identity()

        # Create envelope without meta
        envelope = etree.Element("message")
        payload = etree.SubElement(envelope, "Test")
        payload.text = "data"

        with pytest.raises(SignatureError, match="missing <meta>"):
            sign_envelope(envelope, identity)

    def test_empty_payload_signs_correctly(self):
        """Empty payload should still sign correctly."""
        identity = generate_identity()

        envelope = build_envelope(
            payload=b"<Empty/>",
            from_id="sender",
            thread_id="thread-123",
        )

        signed = sign_envelope(envelope, identity)
        assert verify_envelope(signed, identity.public_key) is True


class TestPublicKeyFormats:
    """Test public key export formats."""

    def test_get_public_key_pem(self):
        """get_public_key_pem() should return valid PEM."""
        identity = generate_identity()

        pem = identity.get_public_key_pem()

        assert pem.startswith(b"-----BEGIN PUBLIC KEY-----")
        assert pem.endswith(b"-----END PUBLIC KEY-----\n")

    def test_get_public_key_base64(self):
        """get_public_key_base64() should return compact base64."""
        import base64

        identity = generate_identity()

        b64 = identity.get_public_key_base64()

        # Should decode to 32 bytes (Ed25519 public key size)
        decoded = base64.b64decode(b64)
        assert len(decoded) == 32
