"""
envelope_validation.py — Validates the canonicalized <message> envelope against envelope.xsd.

After repair_step and c14n_step, we have a normalized envelope_tree.
This step enforces the outer <message> structure (thread, from, optional to, etc.)
using the strict envelope.xsd schema.

Failure here is serious — invalid envelope means the message is malformed at the protocol level,
so we set a clear error and let downstream steps handle it (typically route to system pipeline
for diagnostic <huh>).

Part of AgentServer v2.1 message pump.
"""

from lxml import etree
from agentserver.message_bus.message_state import MessageState

# Load envelope.xsd once at module import (startup time)
# In real implementation, move this to a config loader or bus init
_ENVELOPE_XSD = etree.XMLSchema(file="agentserver/schema/envelope.xsd")


async def envelope_validation_step(state: MessageState) -> MessageState:
    """
    Validate the canonicalized envelope_tree against the fixed envelope.xsd schema.

    Requirements:
    - Must be a valid <message> with required <thread> and <from>
    - Optional <to>, etc.
    - Namespace must match https://xml-pipeline.org/ns/envelope/v1

    On failure: sets state.error with schema validation details.
    Downstream steps should short-circuit if error is set.
    """
    if state.envelope_tree is None:
        state.error = "envelope_validation_step: no envelope_tree (previous step failed)"
        return state

    try:
        # lxml schema validation — raises XMLSchemaError on failure
        _ENVELOPE_XSD.assertValid(state.envelope_tree)

        # Optional extra checks (can be removed later if redundant)
        if state.envelope_tree.tag != "{https://xml-pipeline.org/ns/envelope/v1}message":
            raise ValueError("Root element is not <message> in expected namespace")

    except etree.DocumentInvalid as exc:
        # Schema violation — collect all error messages for diagnostics
        error_lines = []
        for error in _ENVELOPE_XSD.error_log:
            error_lines.append(f"{error.level_name}: {error.message} (line {error.line})")
        state.error = "envelope_validation_step: invalid envelope\n" + "\n".join(error_lines)

    except Exception as exc:  # pylint: disable=broad-except
        state.error = f"envelope_validation_step failed: {exc}"

    return state