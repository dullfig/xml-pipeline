"""
hello.py — Hello World handler for testing the message pump.

This module provides:
- Greeting: payload class (what the handler receives)
- GreetingResponse: response payload (what the handler returns)
- handle_greeting: async handler function

Usage in organism.yaml:
    listeners:
      - name: greeter
        payload_class: handlers.hello.Greeting
        handler: handlers.hello.handle_greeting
        description: Responds with a greeting message
"""

from dataclasses import dataclass
from lxml import etree

from third_party.xmlable import xmlify
from agentserver.message_bus.message_state import HandlerMetadata


# Envelope namespace
ENVELOPE_NS = "https://xml-pipeline.org/ns/envelope/v1"


@xmlify
@dataclass
class Greeting:
    """Incoming greeting request."""
    name: str


@xmlify
@dataclass
class GreetingResponse:
    """Outgoing greeting response."""
    message: str


def wrap_in_envelope(payload_bytes: bytes, from_id: str, to_id: str, thread_id: str) -> bytes:
    """Wrap a payload in a proper message envelope."""
    return f"""<message xmlns="{ENVELOPE_NS}">
  <meta>
    <from>{from_id}</from>
    <to>{to_id}</to>
    <thread>{thread_id}</thread>
  </meta>
  {payload_bytes.decode('utf-8')}
</message>""".encode('utf-8')


async def handle_greeting(payload: Greeting, metadata: HandlerMetadata) -> bytes:
    """
    Handle an incoming Greeting and respond with a GreetingResponse.

    Args:
        payload: The deserialized Greeting instance
        metadata: Contains thread_id, from_id, own_name

    Returns:
        XML bytes of the response envelope
    """
    # Create response
    response = GreetingResponse(message=f"Hello, {payload.name}!")

    # Serialize to XML
    response_tree = response.xml_value("GreetingResponse")
    payload_bytes = etree.tostring(response_tree, encoding='utf-8')

    # Wrap in envelope - respond back to sender
    return wrap_in_envelope(
        payload_bytes=payload_bytes,
        from_id=metadata.own_name or "greeter",
        to_id=metadata.from_id,  # Send back to whoever sent the greeting
        thread_id=metadata.thread_id,
    )
