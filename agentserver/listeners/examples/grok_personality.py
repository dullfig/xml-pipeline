"""
First real intelligent listener — classic Grok voice.
"""

from agentserver.listeners.llm_listener import LLMPersonality
from agentserver.prompts.grok_classic import GROK_CLASSIC_MESSAGE

class GrokPersonality(LLMPersonality):
    """
    Classic Grok — maximally truthful, witty, rebellious.
    Listens to <ask-grok> and responds with <grok-response>.
    """

    listens_to = ["ask-grok"]

    def __init__(self, **kwargs):
        response_template = (
            '<grok-response convo_id="{{convo_id}}">{{response_text}}</grok-response>'
        )

        super().__init__(
            personality_message=GROK_CLASSIC_MESSAGE,
            response_template=response_template,
            model="grok-4",
            temperature=0.7,
            **kwargs
        )