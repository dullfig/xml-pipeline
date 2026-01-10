"""
payload_extraction.py — Extract the inner payload from the validated <message> envelope.

After envelope_validation_step confirms a correct outer <message> envelope,
this step extracts metadata from <meta> and isolates the single payload element.

The payload is expected to be exactly one root element (the capability-specific XML).
If zero or multiple payload roots are found, we set a clear error — this protects
against malformed or ambiguous messages.

Part of AgentServer v2.1 message pump.
"""

from lxml import etree
from agentserver.message_bus.message_state import MessageState

# Envelope namespace for easy reference
_ENVELOPE_NS = "https://xml-pipeline.org/ns/envelope/v1"
_MESSAGE_TAG = f"{{{_ENVELOPE_NS}}}message"
_META_TAG = f"{{{_ENVELOPE_NS}}}meta"
_FROM_TAG = f"{{{_ENVELOPE_NS}}}from"
_TO_TAG = f"{{{_ENVELOPE_NS}}}to"
_THREAD_TAG = f"{{{_ENVELOPE_NS}}}thread"


async def payload_extraction_step(state: MessageState) -> MessageState:
    """
    Extract the single payload element from the validated envelope.

    Expected structure (per envelope.xsd):
      <message xmlns="https://xml-pipeline.org/ns/envelope/v1">
        <meta>
          <from>sender</from>
          <to>receiver</to>        <!-- optional -->
          <thread>uuid</thread>
        </meta>
        <payload_root xmlns="...">   ← this is what we extract
          ...
        </payload_root>
      </message>

    On success: state.payload_tree is set to the payload Element.
    On failure: state.error is set with a clear diagnostic.
    """
    if state.envelope_tree is None:
        state.error = "payload_extraction_step: no envelope_tree (previous step failed)"
        return state

    # Basic sanity — root must be <message> in correct namespace
    if state.envelope_tree.tag != _MESSAGE_TAG:
        state.error = "payload_extraction_step: root tag is not <message> in envelope namespace"
        return state

    # Find <meta> block and extract provenance
    meta_elem = state.envelope_tree.find(_META_TAG)
    if meta_elem is None:
        state.error = "payload_extraction_step: missing <meta> block in envelope"
        return state

    # Extract from_id (required)
    from_elem = meta_elem.find(_FROM_TAG)
    if from_elem is not None and from_elem.text:
        state.from_id = from_elem.text.strip()
    else:
        state.error = "payload_extraction_step: missing <from> in <meta>"
        return state

    # Extract thread_id (required)
    thread_elem = meta_elem.find(_THREAD_TAG)
    if thread_elem is not None and thread_elem.text:
        state.thread_id = thread_elem.text.strip()
    else:
        state.error = "payload_extraction_step: missing <thread> in <meta>"
        return state

    # Optional: extract <to> for direct routing
    to_elem = meta_elem.find(_TO_TAG)
    if to_elem is not None and to_elem.text:
        state.to_id = to_elem.text.strip()

    # Find all direct children that are NOT <meta> — those are payload candidates
    payload_candidates = [
        child for child in state.envelope_tree
        if child.tag != _META_TAG
    ]

    if len(payload_candidates) == 0:
        state.error = "payload_extraction_step: no payload element found inside <message>"
        return state

    if len(payload_candidates) > 1:
        state.error = (
            "payload_extraction_step: multiple payload roots found — "
            "exactly one capability payload element is allowed"
        )
        return state

    # Success — exactly one payload element
    state.payload_tree = payload_candidates[0]

    return state