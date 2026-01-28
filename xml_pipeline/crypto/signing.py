"""
signing.py — Envelope signing and verification using Exclusive C14N + Ed25519.

Signing Process:
1. Remove any existing signature element from meta
2. Canonicalize the envelope using Exclusive C14N
3. Sign the canonical bytes with Ed25519
4. Insert signature element into meta block

Verification Process:
1. Extract and remove signature element from meta
2. Canonicalize the envelope (without signature)
3. Verify signature against canonical bytes

The signature element uses namespace: https://xml-pipeline.org/ns/sig/v1
This fits within the xs:any in envelope.xsd meta block.
"""

from __future__ import annotations

import base64
import copy
from typing import TYPE_CHECKING

from lxml import etree

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    from xml_pipeline.crypto.identity import Identity

# Namespace for signature element
SIGNATURE_NAMESPACE = "https://xml-pipeline.org/ns/sig/v1"
SIGNATURE_TAG = f"{{{SIGNATURE_NAMESPACE}}}sig"

# Envelope namespace
ENVELOPE_NAMESPACE = "https://xml-pipeline.org/ns/envelope/v1"
META_TAG = f"{{{ENVELOPE_NAMESPACE}}}meta"


class SignatureError(Exception):
    """Raised when signing or verification fails."""

    pass


def _find_meta(envelope: etree._Element) -> etree._Element | None:
    """Find the meta element in an envelope."""
    # Try with namespace first
    meta = envelope.find(f"{{{ENVELOPE_NAMESPACE}}}meta")
    if meta is not None:
        return meta

    # Try without namespace (for flexibility)
    meta = envelope.find("meta")
    return meta


def _remove_signature(envelope: etree._Element) -> etree._Element | None:
    """
    Remove signature element from envelope's meta block.

    Returns the removed signature element, or None if not found.
    Modifies envelope in place.
    """
    meta = _find_meta(envelope)
    if meta is None:
        return None

    # Look for signature element
    sig = meta.find(f"{{{SIGNATURE_NAMESPACE}}}sig")
    if sig is not None:
        meta.remove(sig)
        return sig

    # Try without namespace
    sig = meta.find("sig")
    if sig is not None:
        meta.remove(sig)
        return sig

    return None


def _canonicalize(tree: etree._Element) -> bytes:
    """
    Canonicalize an XML element using Exclusive C14N.

    This produces deterministic output suitable for signing.
    """
    return etree.tostring(tree, method="c14n2", exclusive=True)


def sign_envelope(
    envelope: etree._Element,
    identity: Identity,
    *,
    in_place: bool = False,
) -> etree._Element:
    """
    Sign an envelope using the organism's identity.

    The signature is inserted as a <sig> element in the meta block,
    using namespace https://xml-pipeline.org/ns/sig/v1.

    Args:
        envelope: The message envelope to sign
        identity: Organism identity with private key
        in_place: If True, modify envelope directly; if False, work on a copy

    Returns:
        Signed envelope (same object if in_place=True, copy otherwise)

    Raises:
        SignatureError: If envelope structure is invalid
    """
    if not in_place:
        envelope = copy.deepcopy(envelope)

    # Find meta block
    meta = _find_meta(envelope)
    if meta is None:
        raise SignatureError("Envelope missing <meta> block")

    # Remove any existing signature (for re-signing)
    _remove_signature(envelope)

    # Canonicalize the envelope (without signature)
    canonical_bytes = _canonicalize(envelope)

    # Sign the canonical bytes
    signature_b64 = identity.sign_base64(canonical_bytes)

    # Create signature element
    sig_element = etree.SubElement(
        meta,
        SIGNATURE_TAG,
        nsmap={"sig": SIGNATURE_NAMESPACE},
    )
    sig_element.text = signature_b64

    return envelope


def extract_signature(envelope: etree._Element) -> str | None:
    """
    Extract the base64 signature from an envelope without modifying it.

    Args:
        envelope: The message envelope

    Returns:
        Base64-encoded signature string, or None if not signed
    """
    meta = _find_meta(envelope)
    if meta is None:
        return None

    # Look for signature element
    sig = meta.find(f"{{{SIGNATURE_NAMESPACE}}}sig")
    if sig is not None and sig.text:
        return sig.text.strip()

    # Try without namespace
    sig = meta.find("sig")
    if sig is not None and sig.text:
        return sig.text.strip()

    return None


def verify_envelope(
    envelope: etree._Element,
    public_key: Ed25519PublicKey,
) -> bool:
    """
    Verify an envelope's signature using a public key.

    Args:
        envelope: The signed message envelope
        public_key: Ed25519 public key of the signer

    Returns:
        True if signature is valid, False otherwise

    Note:
        Returns False for unsigned envelopes (no signature present).
        To check if envelope is signed, use extract_signature() first.
    """
    # Work on a copy to avoid modifying the original
    envelope_copy = copy.deepcopy(envelope)

    # Extract and remove signature
    sig_element = _remove_signature(envelope_copy)
    if sig_element is None or not sig_element.text:
        return False

    try:
        signature_b64 = sig_element.text.strip()
        signature = base64.b64decode(signature_b64)
    except Exception:
        return False

    # Canonicalize the envelope (without signature)
    canonical_bytes = _canonicalize(envelope_copy)

    # Verify signature
    try:
        public_key.verify(signature, canonical_bytes)
        return True
    except Exception:
        return False


def verify_envelope_with_identity(
    envelope: etree._Element,
    identity: Identity,
) -> bool:
    """
    Verify an envelope using an Identity's public key.

    Convenience wrapper around verify_envelope for local verification.

    Args:
        envelope: The signed message envelope
        identity: Identity containing public key

    Returns:
        True if signature is valid, False otherwise
    """
    return verify_envelope(envelope, identity.public_key)


def is_signed(envelope: etree._Element) -> bool:
    """
    Check if an envelope has a signature.

    Args:
        envelope: Message envelope to check

    Returns:
        True if envelope contains a signature element
    """
    return extract_signature(envelope) is not None


def strip_signature(envelope: etree._Element, *, in_place: bool = False) -> etree._Element:
    """
    Remove signature from an envelope.

    Useful for processing after verification.

    Args:
        envelope: The signed envelope
        in_place: If True, modify directly; if False, work on copy

    Returns:
        Envelope without signature
    """
    if not in_place:
        envelope = copy.deepcopy(envelope)

    _remove_signature(envelope)
    return envelope
