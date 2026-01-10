"""
hello.py — Multi-agent hello world handlers for testing the message pump.

This module demonstrates a multi-agent flow:
  user -> greeter -> shouter -> user

Payload classes:
- Greeting: Initial request with a name
- GreetingResponse: Greeter's response
- ShoutedResponse: Shouter's ALL CAPS version

Handlers:
- handle_greeting: Receives Greeting, sends GreetingResponse to shouter
- handle_shout: Receives GreetingResponse, sends ShoutedResponse to original sender

Usage in organism.yaml:
    listeners:
      - name: greeter
        payload_class: handlers.hello.Greeting
        handler: handlers.hello.handle_greeting
      - name: shouter
        payload_class: handlers.hello.GreetingResponse
        handler: handlers.hello.handle_shout
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
    """Greeter's response - will be forwarded to shouter."""
    message: str
    original_sender: str  # Track who started the conversation


@xmlify
@dataclass
class ShoutedResponse:
    """Shouter's ALL CAPS response - sent back to original sender."""
    message: str


def wrap_in_envelope(payload_bytes: bytes, from_id: str, to_id: str, thread_id: str) -> bytes:
    """Wrap a payload in a proper message envelope.

    Adds xmlns="" to payload to prevent it inheriting envelope namespace.
    """
    payload_str = payload_bytes.decode('utf-8')

    # Add xmlns="" to payload root to keep it out of envelope namespace
    if 'xmlns=' not in payload_str:
        idx = payload_str.index('>')
        payload_str = payload_str[:idx] + ' xmlns=""' + payload_str[idx:]

    return f"""<message xmlns="{ENVELOPE_NS}">
  <meta>
    <from>{from_id}</from>
    <to>{to_id}</to>
    <thread>{thread_id}</thread>
  </meta>
  {payload_str}
</message>""".encode('utf-8')


async def handle_greeting(payload: Greeting, metadata: HandlerMetadata) -> bytes:
    """
    Handle an incoming Greeting and forward GreetingResponse to shouter.

    Flow: user -> greeter -> shouter
    """
    # Create response, tracking original sender for later
    response = GreetingResponse(
        message=f"Hello, {payload.name}!",
        original_sender=metadata.from_id,
    )

    # Serialize to XML
    response_tree = response.xml_value("GreetingResponse")
    payload_bytes = etree.tostring(response_tree, encoding='utf-8')

    # Forward to shouter (not back to sender)
    return wrap_in_envelope(
        payload_bytes=payload_bytes,
        from_id=metadata.own_name or "greeter",
        to_id="shouter",  # Forward to shouter agent
        thread_id=metadata.thread_id,
    )


async def handle_shout(payload: GreetingResponse, metadata: HandlerMetadata) -> bytes:
    """
    Handle GreetingResponse by shouting it back to original sender.

    Flow: greeter -> shouter -> user
    """
    # Create ALL CAPS response
    response = ShoutedResponse(message=payload.message.upper())

    # Serialize to XML
    response_tree = response.xml_value("ShoutedResponse")
    payload_bytes = etree.tostring(response_tree, encoding='utf-8')

    # Send back to original sender (tracked in payload)
    return wrap_in_envelope(
        payload_bytes=payload_bytes,
        from_id=metadata.own_name or "shouter",
        to_id=payload.original_sender,  # Back to whoever started the conversation
        thread_id=metadata.thread_id,
    )
