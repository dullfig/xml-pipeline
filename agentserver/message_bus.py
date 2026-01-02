# agentserver/message_bus.py
# Refactored January 01, 2026 – MessageBus with run() pump and out-of-band shutdown

import asyncio
import logging
from typing import AsyncIterator, Callable, Dict, Optional, Awaitable

from lxml import etree

from .xml_listener import XMLListener
from .utils.message import repair_and_canonicalize, XmlTamperError

# Constants for Internal Physics
ENV_NS = "https://xml-pipeline.org/ns/envelope/1"
ENV = f"{{{ENV_NS}}}"
LOG_TAG = "{https://xml-pipeline.org/ns/logger/1}log"

logger = logging.getLogger("agentserver.bus")


class MessageBus:
    """The sovereign message carrier.

    - Routes canonical XML trees by root tag and <to/> meta.
    - Pure dispatch: tree → optional response tree.
    - Active pump via run(): handles serialization and egress.
    - Out-of-band shutdown via asyncio.Event (fast-path, flood-immune).
    """

    def __init__(self, log_hook: Callable[[etree._Element], None]):
        # root_tag -> {agent_name -> XMLListener}
        self.listeners: Dict[str, Dict[str, XMLListener]] = {}
        # Global lookup for directed <to/> routing
        self.global_names: Dict[str, XMLListener] = {}

        # The Sovereign Witness hook
        self._log_hook = log_hook

        # Out-of-band shutdown signal (set only by AgentServer on privileged command)
        self.shutdown_event = asyncio.Event()

    async def register_listener(self, listener: XMLListener) -> None:
        """Register an organ. Enforces global identity uniqueness."""
        if listener.agent_name in self.global_names:
            raise ValueError(f"Identity collision: {listener.agent_name}")

        self.global_names[listener.agent_name] = listener
        for tag in listener.listens_to:
            tag_dict = self.listeners.setdefault(tag, {})
            tag_dict[listener.agent_name] = listener

        logger.info(f"Registered organ: {listener.agent_name}")

    async def deliver_bytes(self, raw_xml: bytes, client_id: Optional[str] = None) -> None:
        """Air Lock: ingest raw bytes, repair/canonicalize, inject into core."""
        try:
            envelope_tree = repair_and_canonicalize(raw_xml)
            await self.dispatch(envelope_tree, client_id)
        except XmlTamperError as e:
            logger.warning(f"Air Lock Reject: {e}")

    async def dispatch(
        self,
        envelope_tree: etree._Element,
        client_id: Optional[str] = None,
    ) -> etree._Element | None:
        """Pure routing heart. Returns validated response tree or None."""
        # 1. WITNESS – every canonical envelope is seen
        self._log_hook(envelope_tree)

        # 2. Extract envelope metadata
        meta = envelope_tree.find(f"{ENV}meta")
        if meta is None:
            return None
        from_name = meta.findtext(f"{ENV}from")
        to_name = meta.findtext(f"{ENV}to")
        thread_id = meta.findtext(f"{ENV}thread_id") or meta.findtext(f"{ENV}thread")

        # Find payload (first non-meta child)
        payload_elem = next((c for c in envelope_tree if c.tag != f"{ENV}meta"), None)
        if payload_elem is None:
            return None
        payload_tag = payload_elem.tag

        # 3. AUTONOMIC REFLEX: explicit <log/>
        if payload_tag == LOG_TAG:
            self._log_hook(envelope_tree)  # extra vent
            # Minimal ack envelope
            ack = etree.Element(f"{ENV}message")
            meta_ack = etree.SubElement(ack, f"{ENV}meta")
            etree.SubElement(meta_ack, f"{ENV}from").text = "system"
            if from_name:
                etree.SubElement(meta_ack, f"{ENV}to").text = from_name
            if thread_id:
                etree.SubElement(meta_ack, f"{ENV}thread_id").text = thread_id
            etree.SubElement(ack, "logged", status="success")
            return ack

        # 4. ROUTING
        listeners_for_tag = self.listeners.get(payload_tag, {})
        response_tree: Optional[etree._Element] = None
        responding_agent_name = "unknown"

        if to_name:
            # Directed
            target = listeners_for_tag.get(to_name) or self.global_names.get(to_name)
            if target:
                responding_agent_name = target.agent_name
                response_tree = await target.handle(envelope_tree, thread_id, from_name or client_id)
        else:
            # Broadcast – first non-None wins (current policy)
            tasks = [
                l.handle(envelope_tree, thread_id, from_name or client_id)
                for l in listeners_for_tag.values()
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for listener, result in zip(listeners_for_tag.values(), results):
                if isinstance(result, etree._Element):
                    responding_agent_name = listener.agent_name
                    response_tree = result
                    break  # first-wins

        # 5. IDENTITY INSPECTION – prevent spoofing
        if response_tree is not None:
            actual_from = response_tree.findtext(f"{ENV}meta/{ENV}from")
            if actual_from != responding_agent_name:
                logger.critical(
                    f"IDENTITY THEFT BLOCKED: expected {responding_agent_name}, got {actual_from}"
                )
                return None

        return response_tree

    async def run(
        self,
        inbound: AsyncIterator[etree._Element],
        outbound: Callable[[bytes], Awaitable[None]],
        client_id: Optional[str] = None,
    ) -> None:
        """Active pump for a connection. Handles serialization and egress."""
        try:
            async for envelope_tree in inbound:
                if self.shutdown_event.is_set():
                    break

                response_tree = await self.dispatch(envelope_tree, client_id)
                if response_tree is not None:
                    serialized = etree.tostring(
                        response_tree, encoding="utf-8", pretty_print=True
                    )
                    await outbound(serialized)
        finally:
            # Optional final courtesy message on clean exit
            goodbye = b"<message xmlns='https://xml-pipeline.org/ns/envelope/1'><goodbye reason='connection-closed'/></message>"
            try:
                await outbound(goodbye)
            except Exception:
                pass  # connection already gone