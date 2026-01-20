"""
xsd_validation.py — Validate the extracted payload against the listener-specific XSD.

After payload_extraction_step isolates the payload_tree and provenance,
this step validates the payload against the XSD that was auto-generated
from the listener's @xmlify dataclass at registration time.

The XSD is cached and pre-loaded. The schema object is injected into
state.metadata["schema"] when the listener's pipeline is built.

Failure here means the payload violates the declared contract — we collect
detailed errors for diagnostics.

Part of AgentServer v2.1 message pump.
"""

from lxml import etree
from xml_pipeline.message_bus.message_state import MessageState


async def xsd_validation_step(state: MessageState) -> MessageState:
    """
    Validate state.payload_tree against the listener's cached XSD schema.

    Requires:
      - state.payload_tree set
      - state.metadata["schema"] containing a pre-loaded etree.XMLSchema

    On success: payload is guaranteed to match the contract
    On failure: state.error contains detailed validation messages
    """
    if state.payload_tree is None:
        state.error = "xsd_validation_step: no payload_tree (previous extraction failed)"
        return state

    schema = state.metadata.get("schema")
    if schema is None:
        state.error = "xsd_validation_step: no XSD schema in metadata (listener pipeline misconfigured)"
        return state

    if not isinstance(schema, etree.XMLSchema):
        state.error = "xsd_validation_step: metadata['schema'] is not an XMLSchema object"
        return state

    try:
        # assertValid raises DocumentInvalid with full error log
        schema.assertValid(state.payload_tree)

    except etree.DocumentInvalid:
        # Collect all errors for clear diagnostics
        error_lines = []
        for error in schema.error_log:
            error_lines.append(f"{error.level_name}: {error.message} (line {error.line})")
        state.error = "xsd_validation_step: payload failed contract validation\n" + "\n".join(error_lines)

    except Exception as exc:  # pylint: disable=broad-except
        state.error = f"xsd_validation_step: unexpected error during validation: {exc}"

    return state