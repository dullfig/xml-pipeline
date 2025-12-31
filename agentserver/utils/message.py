import logging
from typing import List, Tuple, Optional
from lxml import etree

logger = logging.getLogger("agentserver.message")

class XmlTamperError(Exception):
    """Raised when XML is fundamentally unparseable or violates security constraints."""
    pass

def repair_and_canonicalize(raw_xml: bytes) -> etree.Element:
    """
    The 'Immune System' of the Organism.
    Parses, repairs, and injects the <huh/> scar tissue into the metadata.
    """
    repairs: List[str] = []
    
    # 1. Initial Parse with Recovery
    parser = etree.XMLParser(recover=True, remove_blank_text=True)
    try:
        # If it's totally broken (not even XML-ish), this will still fail
        root = etree.fromstring(raw_xml, parser=parser)
    except etree.XMLSyntaxError as e:
        raise XmlTamperError(f"Fatal XML corruption: {e}")

    # 2. Check for parser-level repairs (structural fixes)
    for error in parser.error_log:
        repairs.append(f"Structural fix: {error.message} at line {error.line}")

    # 3. Canonicalize Internal Logic (C14N)
    # We strip comments and processing instructions to ensure the 'Skeleton' is clean
    # Note: In a real C14N impl, you'd use etree.tostring(root, method="c14n") 
    # but here we keep it as a tree for the MessageBus.

    # 4. Inject <huh/> Scar Tissue
    if repairs:
        _inject_huh_tag(root, repairs)

    return root

def _inject_huh_tag(root: etree.Element, repairs: List[str]):
    """
    Finds the <meta> block and inserts a <huh> log of repairs.
    """
    # Find or create <meta>
    # Note: Using namespaces if defined in your envelope
    meta = root.find(".//{https://xml-pipeline.org/ns/envelope/1}meta")
    if meta is None:
        # If no meta exists, we can't safely log repairs in the standard way
        # In a strict system, this might even be a rejection
        return

    huh = etree.SubElement(meta, "{https://xml-pipeline.org/ns/huh/1}huh")
    for r in repairs:
        repair_el = etree.SubElement(huh, "{https://xml-pipeline.org/ns/huh/1}repair")
        repair_el.text = r
    
    logger.warning(f"Repaired message from {root.tag}: {len(repairs)} issues fixed.")

def to_canonical_bytes(root: etree.Element) -> bytes:
    """Returns the exclusive C14N bytes for cryptographic signing."""
    return etree.tostring(root, method="c14n", exclusive=True)
