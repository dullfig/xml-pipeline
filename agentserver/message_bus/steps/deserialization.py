"""
deserialization.py — Convert validated payload_tree into typed dataclass instance.

After xsd_validation_step confirms the payload conforms to the contract,
this step uses our customized xmlable routines to deserialize the lxml Element
directly in memory — no temporary files needed.

Part of AgentServer v2.1 message pump.
"""

from lxml.etree import _Element
from agentserver.message_bus.message_state import MessageState

# Import the customized parse_element from your forked xmlable
from third_party.xmlable import parse_element  # adjust path if needed


async def deserialization_step(state: MessageState) -> MessageState:
    """
    Deserialize the validated payload_tree into the listener's @xmlify dataclass.

    Requires:
      - state.payload_tree: validated lxml Element
      - state.metadata["payload_class"]: the target dataclass

    Uses the custom parse_element routine for direct in-memory deserialization.
    """
    if state.payload_tree is None:
        state.error = "deserialization_step: no payload_tree (previous step failed)"
        return state

    payload_class = state.metadata.get("payload_class")
    if payload_class is None:
        state.error = "deserialization_step: no payload_class in metadata (listener misconfigured)"
        return state

    try:
        # Direct in-memory deserialization — fast and clean
        instance = parse_element(payload_class, state.payload_tree)
        state.payload = instance

    except Exception as exc:  # pylint: disable=broad-except
        state.error = f"deserialization_step failed: {exc}"

    return state