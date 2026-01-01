"""
Base class for all LLM-powered personalities.

Enforces:
- Immutable no-paperclippers manifesto as first system message
- Owner-provided personality prompt
- Strict response template instructions
- Per-conversation history
- Direct use of the centralized LLMConnectionPool
"""

from __future__ import annotations

import logging
from typing import Dict, List

from lxml import etree

from agentserver.xml_listener import XMLListener
from agentserver.listeners.llm_connection import llm_pool
from agentserver.prompts.no_paperclippers import MANIFESTO_MESSAGE

logger = logging.getLogger(__name__)


class LLMPersonality(XMLListener):
    """
    Abstract base for all LLM-based listeners.
    Concrete subclasses only provide listens_to and personality prompt.
    """

    # Subclasses must set this
    listens_to: List[str] = []

    def __init__(
        self,
        *,
        personality_message: dict,
        response_template: str,
        model: str = "grok-4",
        temperature: float = 0.7,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.personality_message = personality_message
        self.response_template = response_template
        self.model = model
        self.temperature = temperature

        # v1: strict instructions via prompt only (schema validation coming later)
        self.output_instructions = {
            "role": "system",
            "content": (
                "You MUST respond using EXACTLY this XML structure. "
                "Replace {{convo_id}} and {{response_text}} with the correct values. "
                "Never add extra tags, attributes, or text outside this structure. "
                "Use <![CDATA[...]]> if your response contains <, >, or &.\n\n"
                f"{response_template.strip()}"
            )
        }

        self.conversations: Dict[str, List[dict]] = {}

    def _build_messages(self, convo_id: str) -> List[dict]:
        history = self.conversations.get(convo_id, [])
        return [
            MANIFESTO_MESSAGE,         # immutable safety
            self.personality_message,  # flavor
            self.output_instructions,  # strict format
        ] + history

    async def handle(self, msg: etree.Element, convo_id: str) -> etree.Element | None:
        user_text = "".join(msg.itertext()).strip()
        if not user_text:
            return None

        history = self.conversations.setdefault(convo_id, [])
        history.append({"role": "user", "content": user_text})

        messages = self._build_messages(convo_id)

        try:
            completion = await llm_pool.complete(
                model=self.model,
                messages=messages,
                temperature=self.temperature,
            )
        except Exception as e:
            logger.error("LLM call failed for convo %s: %s", convo_id, e)
            completion = "I'm having trouble thinking right now. Please try again."

        history.append({"role": "assistant", "content": completion})

        # Build response using the exact template
        response_xml_str = self.response_template.replace("{{convo_id}}", convo_id).replace("{{response_text}}", completion)

        try:
            response_elem = etree.fromstring(response_xml_str)
        except Exception as e:
            logger.warning("Failed to build response XML, falling back: %s", e)
            response_elem = etree.Element("grok-response", convo_id=convo_id)
            response_elem.text = completion

        return response_elem