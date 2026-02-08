"""
handlers.py — Command dispatch for OOB privileged channel.

Each handler receives (pump, command_element, request_id) and returns response bytes.
"""

from __future__ import annotations

import importlib
import logging
import time
import uuid
from typing import TYPE_CHECKING

from lxml import etree

from xml_pipeline.oob.protocol import (
    build_ack,
    build_error,
    build_listeners_response,
    build_listener_schema_response,
    build_status_response,
    build_subscription_response,
    build_thread_created_response,
)

if TYPE_CHECKING:
    from xml_pipeline.message_bus.stream_pump import StreamPump

logger = logging.getLogger(__name__)

NS = "https://xml-pipeline.org/privileged-msg"


def _text(el: etree._Element, tag: str) -> str:
    """Get text content of a child element (namespace-aware with fallback)."""
    child = el.find(f"{{{NS}}}{tag}")
    if child is None:
        child = el.find(tag)
    if child is not None and child.text:
        return child.text.strip()
    return ""


def _bool(el: etree._Element, tag: str, default: bool = False) -> bool:
    """Get boolean from child element text."""
    val = _text(el, tag)
    if not val:
        return default
    return val.lower() in ("true", "1", "yes")


def _peers_list(el: etree._Element) -> list[str]:
    """Extract <peers><peer>...</peer></peers> list."""
    peers_el = el.find(f"{{{NS}}}peers")
    if peers_el is None:
        peers_el = el.find("peers")
    if peers_el is None:
        return []
    result = []
    for peer in peers_el:
        local = etree.QName(peer.tag).localname if "}" in peer.tag else peer.tag
        if local == "peer" and peer.text:
            result.append(peer.text.strip())
    return result


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def handle_list_listeners(pump: StreamPump, el: etree._Element, request_id: str) -> bytes:
    """Handle <list-listeners> — return all registered listeners."""
    listeners_info = []
    for name, listener in pump.listeners.items():
        info = {
            "name": name,
            "class": f"{listener.payload_class.__module__}.{listener.payload_class.__qualname__}"
                     if listener.payload_class and listener.payload_class is not object else "",
            "description": listener.description,
            "is_agent": listener.is_agent,
            "root_tag": listener.root_tag,
            "peers": listener.peers,
        }
        listeners_info.append(info)
    return build_listeners_response(request_id, listeners_info)


def handle_register_listener(pump: StreamPump, el: etree._Element, request_id: str) -> bytes:
    """Handle <register-listener> — dynamically register a new listener."""
    name = _text(el, "name")
    class_path = _text(el, "class")
    handler_path = _text(el, "handler")
    description = _text(el, "description")
    is_agent = _bool(el, "is-agent")
    broadcast = _bool(el, "broadcast")
    peers = _peers_list(el)

    if not name:
        return build_error(request_id, "INVALID_REQUEST", "Missing <name>")
    if not class_path:
        return build_error(request_id, "INVALID_REQUEST", "Missing <class>")
    if not handler_path:
        return build_error(request_id, "INVALID_REQUEST", "Missing <handler>")

    if name in pump.listeners:
        return build_error(request_id, "ALREADY_EXISTS", f"Listener '{name}' already registered")

    # Dynamic import
    try:
        mod_name, cls_name = class_path.rsplit(".", 1)
        payload_class = getattr(importlib.import_module(mod_name), cls_name)

        mod_name, fn_name = handler_path.rsplit(".", 1)
        handler = getattr(importlib.import_module(mod_name), fn_name)
    except Exception as e:
        return build_error(request_id, "IMPORT_ERROR", f"Failed to import: {e}")

    try:
        pump.register(
            name=name,
            handler=handler,
            payload_class=payload_class,
            description=description,
            agent=is_agent,
            peers=peers,
            broadcast=broadcast,
        )
    except Exception as e:
        return build_error(request_id, "REGISTRATION_ERROR", str(e))

    # Rebuild usage_instructions for agents that might reference this new listener
    for listener in pump.listeners.values():
        if listener.is_agent and listener.peers and name in listener.peers:
            listener.usage_instructions = pump._build_usage_instructions(listener)

    # Emit reload event
    from xml_pipeline.message_bus.events import ReloadEvent
    pump._emit_event(ReloadEvent(
        success=True,
        added_listeners=[name],
    ))

    logger.info(f"OOB: registered listener '{name}'")
    return build_ack(request_id, f"Listener '{name}' registered")


def handle_unregister_listener(pump: StreamPump, el: etree._Element, request_id: str) -> bytes:
    """Handle <unregister-listener> — remove a listener by name."""
    name = _text(el, "name")
    if not name:
        return build_error(request_id, "INVALID_REQUEST", "Missing <name>")

    # Prevent removing system listeners
    if name.startswith("system."):
        return build_error(request_id, "FORBIDDEN", "Cannot remove system listeners")

    removed = pump.unregister_listener(name)
    if not removed:
        return build_error(request_id, "NOT_FOUND", f"Listener '{name}' not found")

    from xml_pipeline.message_bus.events import ReloadEvent
    pump._emit_event(ReloadEvent(
        success=True,
        removed_listeners=[name],
    ))

    logger.info(f"OOB: unregistered listener '{name}'")
    return build_ack(request_id, f"Listener '{name}' removed")


def handle_get_listener_schema(pump: StreamPump, el: etree._Element, request_id: str) -> bytes:
    """Handle <get-listener-schema> — return XSD or example for a listener."""
    name = _text(el, "name")
    fmt = _text(el, "format") or "xsd"

    if not name:
        return build_error(request_id, "INVALID_REQUEST", "Missing <name>")

    listener = pump.listeners.get(name)
    if not listener:
        return build_error(request_id, "NOT_FOUND", f"Listener '{name}' not found")

    if fmt == "example":
        # Build example from dataclass fields
        if hasattr(listener.payload_class, "__dataclass_fields__"):
            fields = listener.payload_class.__dataclass_fields__
            lines = [f"<{listener.payload_class.__name__}>"]
            for fname in fields:
                lines.append(f"  <{fname}>...</{fname}>")
            lines.append(f"</{listener.payload_class.__name__}>")
            schema_str = "\n".join(lines)
        else:
            schema_str = "<!-- No dataclass fields available -->"
    else:
        # XSD
        if hasattr(listener.payload_class, "xsd"):
            xsd_tree = listener.payload_class.xsd()
            schema_str = etree.tostring(xsd_tree, pretty_print=True, encoding="unicode")
        else:
            schema_str = "<!-- No XSD available for this listener -->"

    return build_listener_schema_response(request_id, name, schema_str, fmt)


async def handle_inject_message(pump: StreamPump, el: etree._Element, request_id: str) -> bytes:
    """Handle <inject-message> — inject a message into the pump."""
    target = _text(el, "to")
    thread_id = _text(el, "thread-id") or None
    from_id = _text(el, "from") or "oob"

    if not target:
        return build_error(request_id, "INVALID_REQUEST", "Missing <to>")

    if target not in pump.listeners:
        return build_error(request_id, "NOT_FOUND", f"Listener '{target}' not found")

    # Extract the injected payload XML
    payload_el = el.find(f"{{{NS}}}payload")
    if payload_el is None:
        payload_el = el.find("payload")
    if payload_el is None:
        return build_error(request_id, "INVALID_REQUEST", "Missing <payload>")

    # Get the actual payload content (first child of the inject payload element)
    payload_children = list(payload_el)
    if payload_children:
        payload_xml = etree.tostring(payload_children[0], encoding="unicode")
    elif payload_el.text:
        payload_xml = payload_el.text
    else:
        return build_error(request_id, "INVALID_REQUEST", "Empty <payload>")

    # Wrap in envelope and inject
    from xml_pipeline.message_bus.thread_registry import get_registry
    registry = get_registry()

    if thread_id is None:
        thread_id = registry.extend_chain(
            registry.root_uuid or "",
            target,
        )

    # Build envelope manually
    envelope_ns = "https://xml-pipeline.org/ns/envelope/v1"
    envelope = etree.Element(f"{{{envelope_ns}}}message", nsmap={None: envelope_ns})
    meta = etree.SubElement(envelope, f"{{{envelope_ns}}}meta")
    from_el = etree.SubElement(meta, f"{{{envelope_ns}}}from")
    from_el.text = from_id
    to_el = etree.SubElement(meta, f"{{{envelope_ns}}}to")
    to_el.text = target
    thread_el = etree.SubElement(meta, f"{{{envelope_ns}}}thread")
    thread_el.text = thread_id

    # Append payload content
    try:
        payload_tree = etree.fromstring(payload_xml.encode() if isinstance(payload_xml, str) else payload_xml)
        payload_tree.set("xmlns", "")
        envelope.append(payload_tree)
    except etree.XMLSyntaxError:
        # Raw text payload - wrap in element
        return build_error(request_id, "INVALID_PAYLOAD", "Payload is not valid XML")

    envelope_bytes = etree.tostring(envelope, xml_declaration=True, encoding="UTF-8")
    await pump._inject_raw(envelope_bytes, thread_id=thread_id, from_id=from_id)

    return build_thread_created_response(request_id, thread_id, target)


def handle_get_status(pump: StreamPump, el: etree._Element, request_id: str) -> bytes:
    """Handle <get-status> — return organism status."""
    from xml_pipeline.message_bus.thread_registry import get_registry

    registry = get_registry()
    chains = registry.debug_dump()

    state = "running" if pump._running else "idle"
    uptime = int(time.time() - pump._start_time) if hasattr(pump, "_start_time") else 0

    return build_status_response(
        request_id=request_id,
        name=pump.config.name,
        uptime_seconds=uptime,
        state=state,
        listener_count=len(pump.listeners),
        active_threads=len(chains),
        queue_depth=pump.queue.qsize(),
    )


async def handle_shutdown(pump: StreamPump, el: etree._Element, request_id: str) -> bytes:
    """Handle <shutdown> — trigger graceful pump shutdown."""
    mode = _text(el, "mode") or "graceful"
    reason = _text(el, "reason") or "OOB shutdown request"

    logger.info(f"OOB: shutdown requested (mode={mode}, reason={reason})")

    # Send ack first, then shutdown
    response = build_ack(request_id, f"Shutdown initiated ({mode})")

    # Schedule shutdown to happen after response is sent
    import asyncio
    asyncio.get_event_loop().call_soon(
        lambda: asyncio.ensure_future(pump.shutdown())
    )

    return response


def handle_subscribe_thread(pump: StreamPump, el: etree._Element, request_id: str) -> tuple[bytes, str]:
    """
    Handle <subscribe-thread> — register for push events.

    Returns (response_bytes, subscription_id) so the server can track the subscription.
    """
    thread_id = _text(el, "thread-id") or None  # None = all threads
    listener_filter = _text(el, "listener") or None
    include_payloads = _bool(el, "include-payloads")

    subscription_id = str(uuid.uuid4())
    response = build_subscription_response(request_id, subscription_id)
    return response, subscription_id


def handle_unsubscribe(pump: StreamPump, el: etree._Element, request_id: str) -> bytes:
    """Handle <unsubscribe> — remove a push event subscription."""
    subscription_id = _text(el, "subscription-id")
    if not subscription_id:
        return build_error(request_id, "INVALID_REQUEST", "Missing <subscription-id>")
    # Actual removal is handled by the server (it manages subscriptions)
    return build_ack(request_id, f"Subscription '{subscription_id}' removed")


def handle_register_peer_table(pump: StreamPump, el: etree._Element, request_id: str) -> bytes:
    """Handle <register-peer-table> — register a named peer table (privilege tier)."""
    name = _text(el, "name")
    if not name:
        return build_error(request_id, "INVALID_REQUEST", "Missing <name>")

    if name in pump._peer_tables:
        return build_error(request_id, "ALREADY_EXISTS", f"Peer table '{name}' already registered")

    # Parse optional parent
    parent = _text(el, "parent") or None

    # Parse entries: { listener_name: [peers] }
    peers: dict[str, list[str]] = {}
    entries_el = el.find(f"{{{NS}}}entries")
    if entries_el is None:
        entries_el = el.find("entries")
    if entries_el is not None:
        for entry in entries_el:
            local = etree.QName(entry.tag).localname if "}" in entry.tag else entry.tag
            if local == "entry":
                listener_name = _text(entry, "listener")
                entry_peers = _peers_list(entry)
                if listener_name:
                    peers[listener_name] = entry_peers

    try:
        pump.register_peer_table(name, peers, parent=parent)
    except Exception as e:
        return build_error(request_id, "REGISTRATION_ERROR", str(e))

    from xml_pipeline.message_bus.events import ReloadEvent
    pump._emit_event(ReloadEvent(
        success=True,
        added_listeners=[f"table:{name}"],
    ))

    logger.info(f"OOB: registered peer table '{name}'")
    return build_ack(request_id, f"Peer table '{name}' registered")


def handle_modify_peer_table(pump: StreamPump, el: etree._Element, request_id: str) -> bytes:
    """Handle <modify-peer-table> — grant/revoke peers within a table."""
    name = _text(el, "name")
    listener_name = _text(el, "listener")

    if not name:
        return build_error(request_id, "INVALID_REQUEST", "Missing <name>")
    if not listener_name:
        return build_error(request_id, "INVALID_REQUEST", "Missing <listener>")
    if name not in pump._peer_tables:
        return build_error(request_id, "NOT_FOUND", f"Peer table '{name}' not found")

    # Parse grant list
    grant: list[str] = []
    grant_el = el.find(f"{{{NS}}}grant")
    if grant_el is None:
        grant_el = el.find("grant")
    if grant_el is not None:
        for peer in grant_el:
            local = etree.QName(peer.tag).localname if "}" in peer.tag else peer.tag
            if local == "peer" and peer.text:
                grant.append(peer.text.strip())

    # Parse revoke list
    revoke: list[str] = []
    revoke_el = el.find(f"{{{NS}}}revoke")
    if revoke_el is None:
        revoke_el = el.find("revoke")
    if revoke_el is not None:
        for peer in revoke_el:
            local = etree.QName(peer.tag).localname if "}" in peer.tag else peer.tag
            if local == "peer" and peer.text:
                revoke.append(peer.text.strip())

    try:
        updated = pump.modify_peer_table(name, listener_name, grant=grant, revoke=revoke)
    except Exception as e:
        return build_error(request_id, "MODIFICATION_ERROR", str(e))

    logger.info(f"OOB: modified peer table '{name}' for '{listener_name}' -> {updated}")
    return build_ack(request_id, f"Peer table '{name}' updated for '{listener_name}': {updated}")


# ---------------------------------------------------------------------------
# Dispatch table
# ---------------------------------------------------------------------------

SYNC_HANDLERS = {
    "list-listeners": handle_list_listeners,
    "register-listener": handle_register_listener,
    "unregister-listener": handle_unregister_listener,
    "get-listener-schema": handle_get_listener_schema,
    "get-status": handle_get_status,
    "subscribe-thread": handle_subscribe_thread,
    "unsubscribe": handle_unsubscribe,
    "register-peer-table": handle_register_peer_table,
    "modify-peer-table": handle_modify_peer_table,
}

ASYNC_HANDLERS = {
    "inject-message": handle_inject_message,
    "shutdown": handle_shutdown,
}
