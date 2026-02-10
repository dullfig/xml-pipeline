from lxml import etree
from xml_pipeline.message_bus.message_state import MessageState

# lxml parser configured for maximum tolerance + recovery
_RECOVERY_PARSER = etree.XMLParser(
    recover=True,           # Try to recover from malformed XML
    remove_blank_text=True, # Normalize whitespace
    resolve_entities=False, # Security: don't resolve external entities
    no_network=True,        # Security: block DTD network resolution
    huge_tree=False,        # Default is safe
)

async def repair_step(state: MessageState) -> MessageState:
    """
    First pipeline step: repair malformed ingress bytes into a recoverable lxml ElementTree.

    Takes raw_bytes from ingress (or multi-payload extraction) and attempts to produce
    a valid envelope_tree. Uses lxml's recovery mode to tolerate dirty streams.

    Always returns a MessageState (even on total failure — injects diagnostic error).
    """
    if state.raw_bytes is None:
        state.error = "repair_step: no raw_bytes available"
        return state

    try:
        # lxml recovery parser turns most garbage into something parseable
        tree = etree.fromstring(state.raw_bytes, parser=_RECOVERY_PARSER)

        if tree is None:
            raise ValueError("Parser returned None — unrecoverable XML")

        state.envelope_tree = tree
        # Optional: free memory early — raw bytes no longer needed after repair
        state.raw_bytes = None

    except Exception as exc:
        # Even if recovery fails completely, we capture the diagnostic
        state.error = f"repair_step failed: {exc}"
        # We still set envelope_tree to None so later steps know to short-circuit
        state.envelope_tree = None

    return state