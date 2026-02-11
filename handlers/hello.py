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

from third_party.xmlable import xmlify
from xml_pipeline.message_bus.message_state import HandlerMetadata, HandlerResponse


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


async def handle_greeting(payload: Greeting, metadata: HandlerMetadata) -> HandlerResponse:
    """
    Handle an incoming Greeting and forward GreetingResponse to shouter.

    Flow: console-router -> greeter -> shouter -> response-handler

    NOTE: This handler uses platform.complete() for LLM calls.
    The system prompt is managed by the platform (from organism.yaml).
    The handler cannot see or modify the prompt.
    """
    from xml_pipeline.platform import complete

    # Use platform.complete() for LLM call
    # The platform assembles: system prompt (from registry) + peer schemas + history + user message
    # The handler only provides the user message - no prompt building!
    llm_response = await complete(
        agent_name=metadata.own_name or "greeter",
        thread_id=metadata.thread_id,
        user_message=f"Greet {payload.name} enthusiastically. Respond with ONLY a short greeting sentence.",
        temperature=0.9,
    )

    # Return clean dataclass + target - pump handles envelope
    return HandlerResponse(
        payload=GreetingResponse(
            message=llm_response,
            original_sender="response-handler",
        ),
        to="shouter",
    )


async def handle_shout(payload: GreetingResponse, metadata: HandlerMetadata) -> HandlerResponse:
    """
    Handle GreetingResponse by shouting it back to original sender.

    Flow: greeter -> shouter -> original_sender (response-handler)
    """
    # Return clean dataclass + target - pump handles envelope
    return HandlerResponse(
        payload=ShoutedResponse(message=payload.message.upper()),
        to=payload.original_sender,
    )


async def handle_response_print(payload: ShoutedResponse, metadata: HandlerMetadata) -> None:
    """
    Print the final response to the console.

    Note: TUI console is available in OpenBlox. This handler uses simple stdout.
    """
    print(f"\033[36m[response] {payload.message}\033[0m")
