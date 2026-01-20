"""
TextInput — Generic text message for external/human input.

This primitive allows external sources (console, webhook, API) to send
simple text messages to listeners without needing to know their schema.

Listeners that want to accept human input should handle TextInput.
"""

# Note: Do NOT use `from __future__ import annotations` here
# as it breaks the xmlify decorator which needs concrete types

from dataclasses import dataclass, field
from typing import Optional

from third_party.xmlable import xmlify


@xmlify
@dataclass
class TextInput:
    """
    Generic text input from external sources.

    Attributes:
        text: The message content
        source: Origin of the message (console, webhook, api)
        user: Authenticated user who sent it (if any)
    """
    text: str
    source: str = "console"
    user: str = ""  # Empty string instead of Optional for xmlify compatibility


@xmlify
@dataclass
class TextOutput:
    """
    Generic text output for responses to external sources.

    Used when a listener wants to send a simple text response
    back to the console/webhook/api.
    """
    text: str
    status: str = "ok"  # ok, error, pending
