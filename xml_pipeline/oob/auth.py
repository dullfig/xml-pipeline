"""
auth.py — Request parsing and Ed25519 signature verification for OOB channel.

The privileged-msg protocol requires:
  1. <privileged-msg version="1.1">
       <payload id="..." timestamp="..."> ... command ... </payload>
       <signature algorithm="ed25519">base64sig</signature>
     </privileged-msg>

  2. Signature is Ed25519 over Exclusive C14N of the <payload> element.
  3. If no identity is configured (dev mode), signature verification is skipped.
"""

from __future__ import annotations

import base64
import logging
from typing import TYPE_CHECKING

from lxml import etree

if TYPE_CHECKING:
    from xml_pipeline.crypto.identity import Identity

logger = logging.getLogger(__name__)

NS = "https://xml-pipeline.org/privileged-msg"


class OOBAuthError(Exception):
    """Raised when request parsing or signature verification fails."""
    pass


def parse_request(raw_xml: bytes) -> tuple[str, etree._Element, str]:
    """
    Parse a <privileged-msg> envelope.

    Returns:
        (command_name, command_element, request_id)
        command_name is the tag of the first child of <payload> (e.g. "list-listeners")
        command_element is that child element
        request_id is the "id" attribute on <payload> (or "")

    Raises:
        OOBAuthError: If XML is malformed or structure is invalid
    """
    try:
        tree = etree.fromstring(raw_xml)
    except etree.XMLSyntaxError as e:
        raise OOBAuthError(f"Malformed XML: {e}") from e

    # Accept both namespaced and non-namespaced root
    local_name = etree.QName(tree.tag).localname if "}" in tree.tag else tree.tag
    if local_name != "privileged-msg":
        raise OOBAuthError(f"Expected <privileged-msg>, got <{local_name}>")

    # Find <payload>
    payload = tree.find(f"{{{NS}}}payload")
    if payload is None:
        payload = tree.find("payload")
    if payload is None:
        raise OOBAuthError("Missing <payload> element")

    request_id = payload.get("id", "")

    # Find the command (first child of payload)
    children = list(payload)
    if not children:
        raise OOBAuthError("Empty <payload> — no command found")

    command_el = children[0]
    # Strip namespace from command tag to get clean name
    command_name = etree.QName(command_el.tag).localname if "}" in command_el.tag else command_el.tag

    return command_name, command_el, request_id


def _get_payload_element(tree: etree._Element) -> etree._Element | None:
    """Find the <payload> element in the parsed tree."""
    payload = tree.find(f"{{{NS}}}payload")
    if payload is None:
        payload = tree.find("payload")
    return payload


def _get_signature_text(tree: etree._Element) -> str | None:
    """Extract the base64 signature text from <signature> element."""
    sig = tree.find(f"{{{NS}}}signature")
    if sig is None:
        sig = tree.find("signature")
    if sig is not None and sig.text:
        return sig.text.strip()
    return None


def verify_request(raw_xml: bytes, identity: Identity | None) -> bool:
    """
    Verify the Ed25519 signature on a <privileged-msg>.

    If identity is None (dev mode), verification is skipped and returns True.

    The signature is computed over the Exclusive C14N of the <payload> element.

    Args:
        raw_xml: Complete <privileged-msg> XML bytes
        identity: Organism identity (or None to skip verification)

    Returns:
        True if signature is valid (or identity is None)

    Raises:
        OOBAuthError: If signature is missing when identity is configured, or invalid
    """
    if identity is None:
        return True

    try:
        tree = etree.fromstring(raw_xml)
    except etree.XMLSyntaxError as e:
        raise OOBAuthError(f"Malformed XML: {e}") from e

    payload = _get_payload_element(tree)
    if payload is None:
        raise OOBAuthError("Missing <payload> for signature verification")

    sig_text = _get_signature_text(tree)
    if not sig_text:
        raise OOBAuthError("Missing <signature> — all privileged messages must be signed")

    try:
        sig_bytes = base64.b64decode(sig_text)
    except Exception as e:
        raise OOBAuthError(f"Invalid base64 signature: {e}") from e

    # Canonicalize the payload element
    canonical = etree.tostring(payload, method="c14n2", exclusive=True)

    if not identity.verify(sig_bytes, canonical):
        raise OOBAuthError("Signature verification failed")

    return True


def sign_payload(payload_el: etree._Element, identity: Identity) -> str:
    """
    Sign a <payload> element and return base64 signature.

    Used by clients to construct signed <privileged-msg> envelopes.

    Args:
        payload_el: The <payload> element to sign
        identity: Identity with private key

    Returns:
        Base64-encoded Ed25519 signature
    """
    canonical = etree.tostring(payload_el, method="c14n2", exclusive=True)
    return identity.sign_base64(canonical)
