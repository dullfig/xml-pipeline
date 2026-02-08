"""
crypto — Ed25519 identity keys for signing and verification.

This module provides:
- Identity key generation and loading
- Envelope signing using Exclusive C14N
- Signature verification for incoming messages
- Federation peer authentication

Usage:
    from xml_pipeline.crypto import Identity, sign_envelope, verify_envelope

    # Load organism identity
    identity = Identity.load("config/identity/private.ed25519")

    # Sign an envelope
    signed_envelope = sign_envelope(envelope_tree, identity)

    # Verify with peer's public key
    is_valid = verify_envelope(envelope_tree, peer_public_key)
"""

from xml_pipeline.crypto.identity import (
    Identity,
    generate_identity,
    load_public_key,
)

from xml_pipeline.crypto.signing import (
    sign_envelope,
    verify_envelope,
    extract_signature,
    SIGNATURE_NAMESPACE,
)

from xml_pipeline.crypto.totp import (
    generate_secret as generate_totp_secret,
    get_provisioning_uri,
    compute_totp,
    verify_totp,
)

__all__ = [
    # Identity
    "Identity",
    "generate_identity",
    "load_public_key",
    # Signing
    "sign_envelope",
    "verify_envelope",
    "extract_signature",
    "SIGNATURE_NAMESPACE",
    # TOTP
    "generate_totp_secret",
    "get_provisioning_uri",
    "compute_totp",
    "verify_totp",
]
