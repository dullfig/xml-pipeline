"""
xml_listener.py — The Sovereign Contract for All Capabilities (v1.3)
"""

from __future__ import annotations
from typing import Optional, Type, Callable
from pydantic import BaseModel

class XMLListener:
    """
    Base class for all reactive capabilities.
    Now supports Autonomous Registration via Pydantic payload classes.
    """

    def __init__(
        self,
        name: str,
        payload_class: Type[BaseModel],
        handler: Callable[[dict], bytes],
        description: Optional[str] = None
    ):
        self.agent_name = name
        self.payload_class = payload_class
        self.handler = handler
        self.description = description or payload_class.__doc__ or "No description provided."

        # In v1.3, the root tag is derived from the payload class name
        self.root_tag = payload_class.__name__
        self.listens_to = [self.root_tag]

    async def handle(
        self,
        payload_dict: dict,
        thread_id: str,
        sender_name: str,
    ) -> Optional[bytes]:
        """
        React to a pre-validated dictionary payload.
        Returns raw response XML bytes.
        """
        # 1. Execute the handler logic
        # Note: In v1.3, the Bus/Lark handles the XML -> Dict conversion
        return await self.handler(payload_dict)

    def generate_xsd(self) -> str:
        """
        Autonomous XSD Synthesis.
        Inspects the payload_class and generates an XSD string.
        """
        # Logic to iterate over self.payload_class.model_fields
        # and build the <xs:element> definitions.
        pass

    def generate_prompt_fragment(self) -> str:
        """
        Prompt Synthesis (The 'Mente').
        Generates the tool usage instructions for other agents.
        """
        fragment = [
            f"Capability: {self.agent_name}",
            f"Root Tag: <{self.root_tag}>",
            f"Description: {self.description}",
            "\nParameters:"
        ]

        for name, field in self.payload_class.model_fields.items():
            field_type = field.annotation.__name__
            field_desc = field.description or "No description"
            fragment.append(f"  - {name} ({field_type}): {field_desc}")

        return "\n".join(fragment)

    def make_response_envelope(
        self,
        payload_bytes: bytes,
        thread_id: str,
        to: Optional[str] = None
    ) -> bytes:
        """
        Wraps response bytes in a standard envelope.
        """
        # Logic to build the <message> meta block and append the payload_bytes
        pass