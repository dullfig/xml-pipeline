from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from lxml.etree import _Element as Element
else:
    Element = Any  # Runtime: don't need the actual type

"""
default_listener_steps = [
    repair_step,                  # raw bytes → repaired bytes
    c14n_step,                    # bytes → lxml Element
    envelope_validation_step,     # Element → validated Element
    payload_extraction_step,      # Element → payload Element
    xsd_validation_step,          # payload Element + cached XSD → validated
    deserialization_step,         # payload Element → dataclass instance
    routing_resolution_step,      # attaches target_listeners (or error)
]
"""

@dataclass
class HandlerMetadata:
    """Trustworthy context passed to every handler."""
    thread_id: str
    from_id: str
    own_name: str | None = None          # Only for agent: true listeners
    is_self_call: bool = False           # Convenience flag
    usage_instructions: str = ""         # Peer schemas for LLM prompts
    todo_nudge: str = ""                 # Raised eyebrows: "your todos appear complete"


class _ResponseMarker:
    """Sentinel indicating 'respond to caller'."""
    pass

RESPOND_TO_CALLER = _ResponseMarker()


@dataclass
class HandlerResponse:
    """
    Clean return type for handlers.

    Handlers return this instead of raw XML bytes.
    The pump handles envelope wrapping.

    Usage:
        # Forward to specific listener:
        return HandlerResponse(payload=MyPayload(...), to="target")

        # Respond back to caller (prunes call chain):
        return HandlerResponse.respond(MyPayload(...))
    """
    payload: Any                           # @xmlify dataclass instance
    to: str | _ResponseMarker              # Target listener name, or RESPOND_TO_CALLER

    @classmethod
    def respond(cls, payload: Any) -> 'HandlerResponse':
        """
        Create a response back to the caller.

        The pump will look up the call chain, prune it, and route
        back to whoever called this handler.
        """
        return cls(payload=payload, to=RESPOND_TO_CALLER)

    @property
    def is_response(self) -> bool:
        """Check if this is a response (back to caller) vs forward (to named target)."""
        return isinstance(self.to, _ResponseMarker)


@dataclass
class SystemError:
    """
    System error sent back to agent for retry.

    Generic message that doesn't reveal topology.
    Keeps thread alive so agent can try again.
    """
    code: str              # Generic code: "routing", "validation", "timeout"
    message: str           # Human-readable, non-revealing message
    retry_allowed: bool = True

    def to_xml(self) -> str:
        """Manual XML serialization (avoids xmlify issues with future annotations)."""
        return f"""<SystemError xmlns="">
  <code>{self.code}</code>
  <message>{self.message}</message>
  <retry-allowed>{str(self.retry_allowed).lower()}</retry-allowed>
</SystemError>"""


# Standard error messages (intentionally generic)
ROUTING_ERROR = SystemError(
    code="routing",
    message="Message could not be delivered. Please verify your target and try again.",
    retry_allowed=True,
)


@dataclass
class MessageState:
    """Universal intermediate representation flowing through all pipelines."""
    raw_bytes: bytes | None = None
    envelope_tree: Element | None = None
    payload_tree: Element | None = None
    payload: Any | None = None           # Deserialized @xmlify instance

    thread_id: str | None = None
    from_id: str | None = None
    to_id: str | None = None  # Target listener name for routing

    target_listeners: list['Listener'] | None = None   # Forward reference

    error: str | None = None

    metadata: dict[str, Any] = field(default_factory=dict)  # Extension point