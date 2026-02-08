"""
protocol.py — XML builders for OOB privileged responses and push events.

All output conforms to privileged-msg.xsd (namespace: https://xml-pipeline.org/privileged-msg).
"""

from __future__ import annotations

from datetime import datetime, timezone

from lxml import etree

NS = "https://xml-pipeline.org/privileged-msg"
NSMAP = {"pm": NS}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _el(parent: etree._Element, tag: str, text: str | None = None) -> etree._Element:
    """Create a child element in the privileged-msg namespace."""
    child = etree.SubElement(parent, f"{{{NS}}}{tag}")
    if text is not None:
        child.text = text
    return child


def build_response(request_id: str, payload_el: etree._Element) -> bytes:
    """Build a <privileged-response> envelope wrapping a payload element."""
    root = etree.Element(f"{{{NS}}}privileged-response", nsmap=NSMAP)
    root.set("version", "1.1")
    root.set("timestamp", _now_iso())
    if request_id:
        root.set("request-id", request_id)

    payload_wrapper = _el(root, "payload")
    payload_wrapper.append(payload_el)

    return etree.tostring(root, xml_declaration=True, encoding="UTF-8")


def build_ack(request_id: str, message: str = "", success: bool = True) -> bytes:
    """Build a <privileged-response> with an <ack> payload."""
    ack = etree.Element(f"{{{NS}}}ack", nsmap=NSMAP)
    ack.set("success", "true" if success else "false")
    if message:
        _el(ack, "message", message)
    return build_response(request_id, ack)


def build_error(request_id: str, code: str, message: str, details: str = "") -> bytes:
    """Build a <privileged-response> with an <error> payload."""
    error = etree.Element(f"{{{NS}}}error", nsmap=NSMAP)
    _el(error, "code", code)
    _el(error, "message", message)
    if details:
        _el(error, "details", details)
    return build_response(request_id, error)


def build_listeners_response(
    request_id: str,
    listeners: list[dict],
) -> bytes:
    """Build a <listeners> response with listener info dicts."""
    container = etree.Element(f"{{{NS}}}listeners", nsmap=NSMAP)
    container.set("count", str(len(listeners)))

    for info in listeners:
        li = _el(container, "listener")
        _el(li, "name", info["name"])
        _el(li, "class", info.get("class", ""))
        if info.get("description"):
            _el(li, "description", info["description"])
        if info.get("is_agent"):
            _el(li, "is-agent", "true")
        _el(li, "root-tag", info.get("root_tag", ""))
        if info.get("peers"):
            peers_el = _el(li, "peers")
            for p in info["peers"]:
                _el(peers_el, "peer", p)

    return build_response(request_id, container)


def build_listener_schema_response(
    request_id: str,
    name: str,
    schema_str: str,
    fmt: str = "xsd",
) -> bytes:
    """Build a <listener-schema> response."""
    ls = etree.Element(f"{{{NS}}}listener-schema", nsmap=NSMAP)
    ls.set("format", fmt)
    _el(ls, "name", name)
    _el(ls, "schema", schema_str)
    return build_response(request_id, ls)


def build_thread_created_response(
    request_id: str,
    thread_id: str,
    routed_to: str,
) -> bytes:
    """Build a <thread-created> response."""
    tc = etree.Element(f"{{{NS}}}thread-created", nsmap=NSMAP)
    _el(tc, "thread-id", thread_id)
    _el(tc, "routed-to", routed_to)
    return build_response(request_id, tc)


def build_status_response(
    request_id: str,
    name: str,
    uptime_seconds: int,
    state: str,
    listener_count: int,
    active_threads: int,
    messages_processed: int = 0,
    queue_depth: int = 0,
) -> bytes:
    """Build a <status> response."""
    status = etree.Element(f"{{{NS}}}status", nsmap=NSMAP)
    _el(status, "name", name)
    _el(status, "uptime-seconds", str(uptime_seconds))
    _el(status, "state", state)
    _el(status, "listener-count", str(listener_count))
    _el(status, "active-threads", str(active_threads))
    _el(status, "messages-processed", str(messages_processed))
    _el(status, "queue-depth", str(queue_depth))
    return build_response(request_id, status)


def build_subscription_response(request_id: str, subscription_id: str) -> bytes:
    """Build a <subscription> response."""
    sub = etree.Element(f"{{{NS}}}subscription", nsmap=NSMAP)
    _el(sub, "subscription-id", subscription_id)
    return build_response(request_id, sub)


def build_push(
    event_type: str,
    payload_el: etree._Element,
    subscription_id: str = "",
) -> bytes:
    """Build a <privileged-push> envelope."""
    root = etree.Element(f"{{{NS}}}privileged-push", nsmap=NSMAP)
    root.set("version", "1.1")
    root.set("timestamp", _now_iso())

    payload_wrapper = _el(root, "payload")
    if subscription_id:
        payload_wrapper.set("subscription-id", subscription_id)
    payload_wrapper.append(payload_el)

    return etree.tostring(root, xml_declaration=True, encoding="UTF-8")


def build_thread_push(
    thread_id: str,
    event_type: str,
    *,
    from_id: str = "",
    to_id: str = "",
    payload_type: str = "",
    subscription_id: str = "",
) -> bytes:
    """Build a <privileged-push> with a <thread-event> payload."""
    te = etree.Element(f"{{{NS}}}thread-event", nsmap=NSMAP)
    _el(te, "thread-id", thread_id)
    _el(te, "event-type", event_type)
    if from_id:
        _el(te, "from", from_id)
    if to_id:
        _el(te, "to", to_id)
    if payload_type:
        _el(te, "payload-type", payload_type)
    return build_push("thread-event", te, subscription_id)


def build_listener_push(
    event_type: str,
    name: str,
    details: str = "",
    subscription_id: str = "",
) -> bytes:
    """Build a <privileged-push> with a <listener-event> payload."""
    le = etree.Element(f"{{{NS}}}listener-event", nsmap=NSMAP)
    _el(le, "event-type", event_type)
    _el(le, "name", name)
    if details:
        _el(le, "details", details)
    return build_push("listener-event", le, subscription_id)


def build_system_push(
    event_type: str,
    message: str = "",
    details: str = "",
    subscription_id: str = "",
) -> bytes:
    """Build a <privileged-push> with a <system-event> payload."""
    se = etree.Element(f"{{{NS}}}system-event", nsmap=NSMAP)
    _el(se, "event-type", event_type)
    if message:
        _el(se, "message", message)
    if details:
        _el(se, "details", details)
    return build_push("system-event", se, subscription_id)
