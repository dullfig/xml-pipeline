"""
envelope.py — Envelope construction and manipulation utilities.

The universal envelope format:
    <message xmlns="https://xml-pipeline.org/ns/envelope/v1">
      <meta>
        <from>sender</from>
        <to>receiver</to>
        <thread>uuid</thread>
        <sig xmlns="https://xml-pipeline.org/ns/sig/v1">base64...</sig>
      </meta>
      <Payload>...</Payload>
    </message>

This module provides utilities for:
- Building envelopes from payloads
- Extracting metadata from envelopes
- Signing and verification integration
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lxml import etree

if TYPE_CHECKING:
    from xml_pipeline.crypto.identity import Identity

# Namespaces
ENVELOPE_NAMESPACE = "https://xml-pipeline.org/ns/envelope/v1"
ENVELOPE_NSMAP = {None: ENVELOPE_NAMESPACE}


def build_envelope(
    payload: bytes | etree._Element,
    from_id: str,
    thread_id: str,
    to_id: str | None = None,
    identity: Identity | None = None,
) -> etree._Element:
    """
    Build a message envelope around a payload.

    Args:
        payload: XML payload as bytes or element
        from_id: Sender identifier (listener name)
        thread_id: Thread UUID
        to_id: Optional target listener name
        identity: Optional identity for signing

    Returns:
        Complete message envelope element
    """
    # Create message root
    message = etree.Element(
        f"{{{ENVELOPE_NAMESPACE}}}message",
        nsmap=ENVELOPE_NSMAP,
    )

    # Create meta block
    meta = etree.SubElement(message, f"{{{ENVELOPE_NAMESPACE}}}meta")

    # Add required fields
    from_elem = etree.SubElement(meta, f"{{{ENVELOPE_NAMESPACE}}}from")
    from_elem.text = from_id

    if to_id:
        to_elem = etree.SubElement(meta, f"{{{ENVELOPE_NAMESPACE}}}to")
        to_elem.text = to_id

    thread_elem = etree.SubElement(meta, f"{{{ENVELOPE_NAMESPACE}}}thread")
    thread_elem.text = thread_id

    # Add payload
    if isinstance(payload, bytes):
        # Parse payload bytes
        parser = etree.XMLParser(
            recover=True, resolve_entities=False, no_network=True,
        )
        payload_elem = etree.fromstring(payload, parser=parser)
    else:
        payload_elem = payload

    message.append(payload_elem)

    # Sign if identity provided
    if identity is not None:
        from xml_pipeline.crypto.signing import sign_envelope

        message = sign_envelope(message, identity, in_place=True)

    return message


def envelope_to_bytes(envelope: etree._Element) -> bytes:
    """
    Serialize an envelope to bytes.

    Uses UTF-8 encoding with XML declaration.
    """
    return etree.tostring(envelope, encoding="utf-8", xml_declaration=True)


def extract_meta(envelope: etree._Element) -> dict[str, str | None]:
    """
    Extract metadata from an envelope.

    Returns:
        Dict with keys: from_id, to_id, thread_id
    """
    result: dict[str, str | None] = {
        "from_id": None,
        "to_id": None,
        "thread_id": None,
    }

    # Find meta element
    meta = envelope.find(f"{{{ENVELOPE_NAMESPACE}}}meta")
    if meta is None:
        meta = envelope.find("meta")

    if meta is None:
        return result

    # Extract fields
    from_elem = meta.find(f"{{{ENVELOPE_NAMESPACE}}}from")
    if from_elem is None:
        from_elem = meta.find("from")
    if from_elem is not None and from_elem.text:
        result["from_id"] = from_elem.text.strip()

    to_elem = meta.find(f"{{{ENVELOPE_NAMESPACE}}}to")
    if to_elem is None:
        to_elem = meta.find("to")
    if to_elem is not None and to_elem.text:
        result["to_id"] = to_elem.text.strip()

    thread_elem = meta.find(f"{{{ENVELOPE_NAMESPACE}}}thread")
    if thread_elem is None:
        thread_elem = meta.find("thread")
    if thread_elem is not None and thread_elem.text:
        result["thread_id"] = thread_elem.text.strip()

    return result


def extract_payload(envelope: etree._Element) -> etree._Element | None:
    """
    Extract the payload element from an envelope.

    The payload is the first non-meta child of the message.

    Returns:
        Payload element, or None if not found
    """
    for child in envelope:
        # Skip meta element
        tag = child.tag
        if isinstance(tag, str):
            if tag.endswith("}meta") or tag == "meta":
                continue
            return child
    return None
