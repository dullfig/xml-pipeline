"""
payload_extraction.py — Extract the inner payload from the validated <message> envelope.

After envelope_validation_step confirms a correct outer <message> envelope,
this step removes the envelope elements (<thread>, <from>, optional <to>, etc.)
and isolates the single child element that is the actual payload.

The payload is expected to be exactly one root element (the capability-specific XML).
If zero or multiple payload roots are found, we set a clear error — this protects
against malformed or ambiguous messages.

Part of AgentServer v2.1 message pump.
"""

from lxml import etree
from agentserver.message_bus.message_state import MessageState

# Envelope namespace for easy reference
_ENVELOPE_NS = "https://xml-pipeline.org/ns/envelope/v1"
_MESSAGE_TAG = f"{{{ _ENVELOPE_NS }}}message"


async def payload_extraction_step(state: MessageState) -> MessageState:
    """
    Extract the single payload element from the validated envelope.

    Expected structure:
      <message xmlns="https://xml-pipeline.org/ns/envelope/v1">
        <thread>uuid</thread>
        <from>sender</from>
        <!-- optional <to>receiver</to> -->
        <payload_root>   ← this is the one we want
          ...
        </payload_root>
      </message>

    On success: state.payload_tree is set to the payload Element.
    On failure: state.error is set with a clear diagnostic.
    """
    if state.envelope_tree is None:
        state.error = "payload_extraction_step: no envelope_tree (previous step failed)"
        return state

    # Basic sanity — root must be <message> in correct namespace (already checked by schema,
    # but we double-check for defence in depth)
    if state.envelope_tree.tag != _MESSAGE_TAG:
        state.error = f"payload_extraction_step: root tag is not <message> in envelope namespace"
        return state

    # Find all direct children that are not envelope control elements
    # Envelope control elements are: thread, from, to (optional)
    payload_candidates = [
        child
        for child in state.envelope_tree
        if not (
            child.tag in {
                f"{{{ _ENVELOPE_NS }}}thread",
                f"{{{ _ENVELOPE_NS }}}from",
                f"{{{ _ENVELOPE_NS }}}to",
            }
        )
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
    payload_element = payload_candidates[0]

    # Optional: capture provenance from envelope for later use
    # (these will be trustworthy because envelope was validated)
    thread_elem = state.envelope_tree.find(f"{{{ _ENVELOPE_NS }}}thread")
    from_elem = state.envelope_tree.find(f"{{{ _ENVELOPE_NS }}}from")

    if thread_elem is not None and thread_elem.text:
        state.thread_id = thread_elem.text.strip()

    if from_elem is not None and from_elem.text:
        state.from_id = from_elem.text.strip()

    state.payload_tree = payload_element

    return state