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
from agentserver.message_bus.message_state import HandlerMetadata, HandlerResponse


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

    Demonstrates TodoUntil pattern:
    1. Register a watcher for ShoutedResponse from shouter
    2. Send GreetingResponse to shouter
    3. When ShoutedResponse appears, eyebrow is raised
    4. On next invocation, greeter sees nudge and can close the todo

    NOTE: This handler uses platform.complete() for LLM calls.
    The system prompt is managed by the platform (from organism.yaml).
    The handler cannot see or modify the prompt.
    """
    from agentserver.platform import complete
    from agentserver.message_bus.todo_registry import get_todo_registry

    # Check for any raised todos and close them
    todo_registry = get_todo_registry()
    if metadata.todo_nudge:
        # We have raised todos - check and close them
        raised = todo_registry.get_raised_for(metadata.thread_id, metadata.own_name or "greeter")
        for watcher in raised:
            todo_registry.close(watcher.id)

    # Register a todo watcher - we want to know when shouter responds
    todo_registry.register(
        thread_id=metadata.thread_id,
        issuer=metadata.own_name or "greeter",
        wait_for="ShoutedResponse",
        from_listener="shouter",
        description=f"waiting for shouter to process greeting for {payload.name}",
    )

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
    Print the final response to stdout.

    This is a simple terminal handler for the SecureConsole flow.
    """
    # Print on fresh line with color formatting, then reprint prompt
    print(f"\n\033[36m[response] {payload.message}\033[0m")
    print("> ", end="", flush=True)  # Reprint prompt
    return None
