"""
pipeline.py — Standalone pipeline helper functions.

Stream pipeline utilities for message processing: step wrapping,
multi-payload extraction, and factory functions for XSD validation
and deserialization steps.
"""

from __future__ import annotations

from typing import AsyncIterable, Callable

from lxml import etree

from xml_pipeline.message_bus.message_state import MessageState


def wrap_step(step_fn: Callable) -> Callable:
    """
    Wrap an existing async step function for use with pipe.map.

    Existing steps: async def step(state) -> state
    We keep them as-is since pipe.map handles the iteration.
    """
    return step_fn


async def extract_payloads(state: MessageState) -> AsyncIterable[MessageState]:
    """
    Fan-out step: Extract 1..N payloads from handler response.

    This is used with pipe.flatmap — yields multiple states for each input.
    """
    if state.raw_bytes is None:
        yield state
        return

    try:
        # Wrap in dummy to handle multiple roots
        wrapped = b"<dummy>" + state.raw_bytes + b"</dummy>"
        tree = etree.fromstring(wrapped, parser=etree.XMLParser(recover=True))

        children = list(tree)
        if not children:
            yield state
            return

        for child in children:
            payload_bytes = etree.tostring(child)
            yield MessageState(
                raw_bytes=payload_bytes,
                thread_id=state.thread_id,
                from_id=state.from_id,
                metadata=state.metadata.copy(),
            )

    except Exception:
        # On parse failure, pass through as-is
        yield state


def make_xsd_validation(schema: etree.XMLSchema) -> Callable:
    """Factory for XSD validation step with schema baked in."""
    async def validate(state: MessageState) -> MessageState:
        if state.payload_tree is None or state.error:
            return state
        try:
            schema.assertValid(state.payload_tree)
        except etree.DocumentInvalid as e:
            state.error = f"XSD validation failed: {e}"
        return state
    return validate


def make_deserialization(payload_class: type) -> Callable:
    """Factory for deserialization step with class baked in."""
    from third_party.xmlable import parse_element

    async def deserialize(state: MessageState) -> MessageState:
        if state.payload_tree is None or state.error:
            return state
        try:
            state.payload = parse_element(payload_class, state.payload_tree)
        except Exception as e:
            state.error = f"Deserialization failed: {e}"
        return state
    return deserialize
