"""
c14n.py — Canonicalization step for the full <message> envelope.

After repair, the envelope_tree may have different but semantically equivalent
representations (attribute order, namespace prefixes, whitespace, etc.).

This step produces Exclusive XML Canonicalization (C14N 1.1) bytes that are
identical for equivalent documents — essential for validation and signing.

Part of AgentServer v2.1 message pump.
"""

from lxml import etree
from xml_pipeline.message_bus.message_state import MessageState


async def c14n_step(state: MessageState) -> MessageState:
    """
    Canonicalize the envelope_tree to Exclusive C14N form.

    If repair_step succeeded, this step normalizes the tree so that:
      - Validation against envelope.xsd is deterministic
      - Future signing/federation comparisons are reliable

    On failure, sets state.error and continues (downstream steps will short-circuit).
    """
    if state.envelope_tree is None:
        state.error = "c14n_step: no envelope_tree (previous repair failed)"
        return state

    try:
        # lxml's tostring with method="c14n" implements Exclusive XML Canonicalization
        # (the same form we require on egress)
        c14n_bytes = etree.tostring(
            state.envelope_tree,
            method="c14n",                  # Exclusive C14N 1.0 (lxml default)
            exclusive=True,
            with_comments=False,            # Comments not part of canonical form
            strip_text=False,
        )

        # Re-parse the canonical bytes to get a clean tree (prefixes normalized, etc.)
        # This ensures downstream steps see a consistent document
        _secure_parser = etree.XMLParser(
            resolve_entities=False, no_network=True, huge_tree=False,
        )
        clean_tree = etree.fromstring(c14n_bytes, parser=_secure_parser)

        state.envelope_tree = clean_tree
        # raw_bytes already cleared by repair_step

    except Exception as exc:  # pylint: disable=broad-except
        state.error = f"c14n_step failed: {exc}"
        state.envelope_tree = None

    return state