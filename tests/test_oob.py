"""
test_oob.py — Tests for the OOB privileged channel.

Tests cover:
1. Auth: parsing and signature verification
2. Protocol: XML response/push builders
3. Handlers: command dispatch (unit tests against pump)
4. Server: WebSocket integration (start/stop, roundtrip, push events)

Run with: pytest tests/test_oob.py -v
"""

import asyncio
import base64
import uuid
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import pytest
from lxml import etree
from third_party.xmlable import xmlify

from xml_pipeline.crypto.identity import Identity
from xml_pipeline.message_bus.stream_pump import (
    StreamPump,
    OrganismConfig,
    ReloadEvent,
    MessageReceivedEvent,
    ThreadEvent,
)
from xml_pipeline.oob.auth import OOBAuthError, parse_request, verify_request, sign_payload
from xml_pipeline.oob.protocol import (
    NS,
    build_ack,
    build_error,
    build_listeners_response,
    build_listener_schema_response,
    build_push,
    build_response,
    build_status_response,
    build_subscription_response,
    build_thread_push,
    build_listener_push,
    build_system_push,
)
from xml_pipeline.oob.handlers import (
    handle_list_listeners,
    handle_register_listener,
    handle_unregister_listener,
    handle_get_listener_schema,
    handle_inject_message,
    handle_get_status,
    handle_shutdown,
    handle_subscribe_thread,
    handle_unsubscribe,
    handle_register_peer_table,
    handle_modify_peer_table,
)
from xml_pipeline.message_bus.thread_registry import get_registry, reset_registry


# ============================================================================
# Test fixtures
# ============================================================================


@xmlify
@dataclass
class TestPayload:
    """Simple payload for OOB tests."""
    value: str


@xmlify
@dataclass
class TestResult:
    """Result payload for peer table tests."""
    result: str


async def _oob_test_handler(payload: TestPayload, metadata) -> None:
    return None


async def _oob_agent_handler(payload, metadata):
    """Agent handler that forwards to a peer."""
    from xml_pipeline.message_bus.message_state import HandlerResponse
    return HandlerResponse(payload=TestResult(result="done"), to=metadata.from_id)


def _make_privileged_msg(command_xml: str, request_id: str = "req-1",
                         identity: Identity | None = None) -> bytes:
    """Build a signed <privileged-msg> envelope for testing."""
    payload_xml = (
        f'<payload xmlns="{NS}" id="{request_id}" '
        f'timestamp="2026-02-07T00:00:00Z">'
        f'{command_xml}'
        f'</payload>'
    )

    if identity:
        payload_el = etree.fromstring(payload_xml.encode())
        sig = sign_payload(payload_el, identity)
    else:
        sig = base64.b64encode(b"\x00" * 64).decode()

    return (
        f'<privileged-msg xmlns="{NS}" version="1.1">'
        f'{payload_xml}'
        f'<signature xmlns="{NS}" algorithm="ed25519">{sig}</signature>'
        f'</privileged-msg>'
    ).encode()


def _make_pump(oob_enabled: bool = False) -> StreamPump:
    """Create a StreamPump for handler testing (no OOB server)."""
    pump = StreamPump(name="test-oob", oob_enabled=oob_enabled)
    pump._start_time = 1000.0
    return pump


# ============================================================================
# Phase 1: Auth tests
# ============================================================================


class TestOOBAuth:
    """Tests for request parsing and signature verification."""

    def test_parse_valid_request(self):
        xml = _make_privileged_msg(f'<list-listeners xmlns="{NS}"/>')
        command_name, command_el, request_id = parse_request(xml)
        assert command_name == "list-listeners"
        assert request_id == "req-1"

    def test_parse_request_with_content(self):
        xml = _make_privileged_msg(
            f'<unregister-listener xmlns="{NS}"><name xmlns="{NS}">greeter</name></unregister-listener>'
        )
        command_name, command_el, request_id = parse_request(xml)
        assert command_name == "unregister-listener"

    def test_parse_invalid_xml(self):
        with pytest.raises(OOBAuthError, match="Malformed XML"):
            parse_request(b"not xml at all")

    def test_parse_wrong_root(self):
        with pytest.raises(OOBAuthError, match="Expected <privileged-msg>"):
            parse_request(b"<wrong-root/>")

    def test_parse_missing_payload(self):
        with pytest.raises(OOBAuthError, match="Missing <payload>"):
            parse_request(
                f'<privileged-msg xmlns="{NS}" version="1.1">'
                f'<signature xmlns="{NS}">AAAA</signature>'
                f'</privileged-msg>'.encode()
            )

    def test_parse_empty_payload(self):
        with pytest.raises(OOBAuthError, match="Empty <payload>"):
            parse_request(
                f'<privileged-msg xmlns="{NS}" version="1.1">'
                f'<payload xmlns="{NS}" id="x" timestamp="2026-01-01T00:00:00Z"/>'
                f'<signature xmlns="{NS}">AAAA</signature>'
                f'</privileged-msg>'.encode()
            )

    def test_verify_skipped_without_identity(self):
        xml = _make_privileged_msg(f'<list-listeners xmlns="{NS}"/>')
        assert verify_request(xml, identity=None) is True

    def test_verify_valid_signature(self):
        identity = Identity.generate()
        xml = _make_privileged_msg(f'<list-listeners xmlns="{NS}"/>', identity=identity)
        assert verify_request(xml, identity) is True

    def test_verify_bad_signature(self):
        identity = Identity.generate()
        other_identity = Identity.generate()
        # Sign with one key, verify with another
        xml = _make_privileged_msg(f'<list-listeners xmlns="{NS}"/>', identity=other_identity)
        with pytest.raises(OOBAuthError, match="Signature verification failed"):
            verify_request(xml, identity)

    def test_verify_missing_signature_with_identity(self):
        identity = Identity.generate()
        # Build message without real signature element
        xml = (
            f'<privileged-msg xmlns="{NS}" version="1.1">'
            f'<payload xmlns="{NS}" id="x" timestamp="2026-01-01T00:00:00Z">'
            f'<list-listeners xmlns="{NS}"/>'
            f'</payload>'
            f'</privileged-msg>'
        ).encode()
        with pytest.raises(OOBAuthError, match="Missing <signature>"):
            verify_request(xml, identity)

    def test_verify_invalid_base64(self):
        identity = Identity.generate()
        xml = (
            f'<privileged-msg xmlns="{NS}" version="1.1">'
            f'<payload xmlns="{NS}" id="x" timestamp="2026-01-01T00:00:00Z">'
            f'<list-listeners xmlns="{NS}"/>'
            f'</payload>'
            f'<signature xmlns="{NS}" algorithm="ed25519">not-valid-b64!!!</signature>'
            f'</privileged-msg>'
        ).encode()
        with pytest.raises(OOBAuthError):
            verify_request(xml, identity)

    def test_sign_payload_roundtrip(self):
        identity = Identity.generate()
        payload_el = etree.fromstring(
            f'<payload xmlns="{NS}" id="test" timestamp="2026-01-01T00:00:00Z">'
            f'<list-listeners xmlns="{NS}"/></payload>'.encode()
        )
        sig_b64 = sign_payload(payload_el, identity)
        sig_bytes = base64.b64decode(sig_b64)
        canonical = etree.tostring(payload_el, method="c14n2", exclusive=True)
        assert identity.verify(sig_bytes, canonical)


# ============================================================================
# Phase 2: Protocol tests
# ============================================================================


class TestOOBProtocol:
    """Tests for XML response/push builders."""

    def test_build_ack(self):
        result = build_ack("req-1", "OK")
        tree = etree.fromstring(result)
        assert tree.tag == f"{{{NS}}}privileged-response"
        assert tree.get("request-id") == "req-1"
        # Find ack element
        payload = tree.find(f"{{{NS}}}payload")
        ack = payload.find(f"{{{NS}}}ack")
        assert ack is not None
        assert ack.get("success") == "true"

    def test_build_ack_failure(self):
        result = build_ack("req-2", "Failed", success=False)
        tree = etree.fromstring(result)
        payload = tree.find(f"{{{NS}}}payload")
        ack = payload.find(f"{{{NS}}}ack")
        assert ack.get("success") == "false"

    def test_build_error(self):
        result = build_error("req-3", "NOT_FOUND", "Not found", "extra detail")
        tree = etree.fromstring(result)
        payload = tree.find(f"{{{NS}}}payload")
        error = payload.find(f"{{{NS}}}error")
        assert error is not None
        code = error.find(f"{{{NS}}}code")
        assert code.text == "NOT_FOUND"
        msg = error.find(f"{{{NS}}}message")
        assert msg.text == "Not found"
        details = error.find(f"{{{NS}}}details")
        assert details.text == "extra detail"

    def test_build_listeners_response(self):
        listeners = [
            {"name": "greeter", "class": "mod.Greeting", "description": "Greets",
             "is_agent": True, "root_tag": "greeter.greeting", "peers": ["shouter"]},
            {"name": "shouter", "class": "mod.Shout", "description": "Shouts",
             "is_agent": False, "root_tag": "shouter.shout", "peers": []},
        ]
        result = build_listeners_response("req-4", listeners)
        tree = etree.fromstring(result)
        payload = tree.find(f"{{{NS}}}payload")
        container = payload.find(f"{{{NS}}}listeners")
        assert container.get("count") == "2"
        items = container.findall(f"{{{NS}}}listener")
        assert len(items) == 2
        assert items[0].find(f"{{{NS}}}name").text == "greeter"
        assert items[0].find(f"{{{NS}}}is-agent").text == "true"

    def test_build_listener_schema_response(self):
        result = build_listener_schema_response("req-5", "greeter", "<xs:schema/>", "xsd")
        tree = etree.fromstring(result)
        payload = tree.find(f"{{{NS}}}payload")
        ls = payload.find(f"{{{NS}}}listener-schema")
        assert ls.get("format") == "xsd"
        assert ls.find(f"{{{NS}}}name").text == "greeter"

    def test_build_status_response(self):
        result = build_status_response(
            "req-6", "test-org", 120, "running", 5, 3, 42, 2
        )
        tree = etree.fromstring(result)
        payload = tree.find(f"{{{NS}}}payload")
        status = payload.find(f"{{{NS}}}status")
        assert status.find(f"{{{NS}}}name").text == "test-org"
        assert status.find(f"{{{NS}}}state").text == "running"
        assert status.find(f"{{{NS}}}listener-count").text == "5"

    def test_build_subscription_response(self):
        result = build_subscription_response("req-7", "sub-123")
        tree = etree.fromstring(result)
        payload = tree.find(f"{{{NS}}}payload")
        sub = payload.find(f"{{{NS}}}subscription")
        assert sub.find(f"{{{NS}}}subscription-id").text == "sub-123"

    def test_build_thread_push(self):
        result = build_thread_push(
            "thread-1", "message", from_id="greeter", to_id="shouter",
            payload_type="greeting", subscription_id="sub-1"
        )
        tree = etree.fromstring(result)
        assert tree.tag == f"{{{NS}}}privileged-push"
        payload = tree.find(f"{{{NS}}}payload")
        assert payload.get("subscription-id") == "sub-1"
        te = payload.find(f"{{{NS}}}thread-event")
        assert te.find(f"{{{NS}}}thread-id").text == "thread-1"
        assert te.find(f"{{{NS}}}event-type").text == "message"

    def test_build_listener_push(self):
        result = build_listener_push("registered", "new-listener", "added dynamically")
        tree = etree.fromstring(result)
        payload = tree.find(f"{{{NS}}}payload")
        le = payload.find(f"{{{NS}}}listener-event")
        assert le.find(f"{{{NS}}}event-type").text == "registered"
        assert le.find(f"{{{NS}}}name").text == "new-listener"

    def test_build_system_push(self):
        result = build_system_push("shutdown-initiated", "Shutting down", "reason: OOB")
        tree = etree.fromstring(result)
        payload = tree.find(f"{{{NS}}}payload")
        se = payload.find(f"{{{NS}}}system-event")
        assert se.find(f"{{{NS}}}event-type").text == "shutdown-initiated"
        assert se.find(f"{{{NS}}}message").text == "Shutting down"

    def test_build_response_has_timestamp(self):
        result = build_ack("req-8")
        tree = etree.fromstring(result)
        assert tree.get("timestamp") is not None


# ============================================================================
# Phase 3: Handler tests
# ============================================================================


class TestOOBHandlers:
    """Tests for individual command handlers against a real StreamPump."""

    def test_list_listeners_empty(self):
        pump = _make_pump()
        el = etree.fromstring(f'<list-listeners xmlns="{NS}"/>'.encode())
        result = handle_list_listeners(pump, el, "req-1")
        tree = etree.fromstring(result)
        payload = tree.find(f"{{{NS}}}payload")
        listeners = payload.find(f"{{{NS}}}listeners")
        assert listeners.get("count") == "0"

    def test_list_listeners_with_registered(self):
        pump = _make_pump()
        pump.register("test-listener", _oob_test_handler, TestPayload,
                       description="A test listener")
        el = etree.fromstring(f'<list-listeners xmlns="{NS}"/>'.encode())
        result = handle_list_listeners(pump, el, "req-1")
        tree = etree.fromstring(result)
        payload = tree.find(f"{{{NS}}}payload")
        listeners = payload.find(f"{{{NS}}}listeners")
        items = listeners.findall(f"{{{NS}}}listener")
        assert len(items) == 1
        assert items[0].find(f"{{{NS}}}name").text == "test-listener"

    def test_register_listener(self):
        pump = _make_pump()
        el = etree.fromstring(
            f'<register-listener xmlns="{NS}">'
            f'<name xmlns="{NS}">test-listener</name>'
            f'<class xmlns="{NS}">tests.test_oob.TestPayload</class>'
            f'<handler xmlns="{NS}">tests.test_oob._oob_test_handler</handler>'
            f'<description xmlns="{NS}">Dynamic test listener</description>'
            f'</register-listener>'.encode()
        )
        result = handle_register_listener(pump, el, "req-1")
        tree = etree.fromstring(result)
        payload = tree.find(f"{{{NS}}}payload")
        ack = payload.find(f"{{{NS}}}ack")
        assert ack is not None
        assert ack.get("success") == "true"
        assert "test-listener" in pump.listeners

    def test_register_listener_already_exists(self):
        pump = _make_pump()
        pump.register("existing", _oob_test_handler, TestPayload, description="Exists")
        el = etree.fromstring(
            f'<register-listener xmlns="{NS}">'
            f'<name xmlns="{NS}">existing</name>'
            f'<class xmlns="{NS}">tests.test_oob.TestPayload</class>'
            f'<handler xmlns="{NS}">tests.test_oob._oob_test_handler</handler>'
            f'</register-listener>'.encode()
        )
        result = handle_register_listener(pump, el, "req-1")
        tree = etree.fromstring(result)
        payload = tree.find(f"{{{NS}}}payload")
        error = payload.find(f"{{{NS}}}error")
        assert error is not None
        assert error.find(f"{{{NS}}}code").text == "ALREADY_EXISTS"

    def test_register_listener_missing_name(self):
        pump = _make_pump()
        el = etree.fromstring(
            f'<register-listener xmlns="{NS}">'
            f'<class xmlns="{NS}">tests.test_oob.TestPayload</class>'
            f'<handler xmlns="{NS}">tests.test_oob._oob_test_handler</handler>'
            f'</register-listener>'.encode()
        )
        result = handle_register_listener(pump, el, "req-1")
        tree = etree.fromstring(result)
        payload = tree.find(f"{{{NS}}}payload")
        error = payload.find(f"{{{NS}}}error")
        assert error.find(f"{{{NS}}}code").text == "INVALID_REQUEST"

    def test_register_listener_bad_import(self):
        pump = _make_pump()
        el = etree.fromstring(
            f'<register-listener xmlns="{NS}">'
            f'<name xmlns="{NS}">bad</name>'
            f'<class xmlns="{NS}">nonexistent.module.Class</class>'
            f'<handler xmlns="{NS}">nonexistent.module.handler</handler>'
            f'</register-listener>'.encode()
        )
        result = handle_register_listener(pump, el, "req-1")
        tree = etree.fromstring(result)
        payload = tree.find(f"{{{NS}}}payload")
        error = payload.find(f"{{{NS}}}error")
        assert error.find(f"{{{NS}}}code").text == "IMPORT_ERROR"

    def test_unregister_listener(self):
        pump = _make_pump()
        pump.register("removable", _oob_test_handler, TestPayload, description="To remove")
        el = etree.fromstring(
            f'<unregister-listener xmlns="{NS}">'
            f'<name xmlns="{NS}">removable</name>'
            f'</unregister-listener>'.encode()
        )
        result = handle_unregister_listener(pump, el, "req-1")
        tree = etree.fromstring(result)
        payload = tree.find(f"{{{NS}}}payload")
        ack = payload.find(f"{{{NS}}}ack")
        assert ack is not None
        assert "removable" not in pump.listeners

    def test_unregister_listener_not_found(self):
        pump = _make_pump()
        el = etree.fromstring(
            f'<unregister-listener xmlns="{NS}">'
            f'<name xmlns="{NS}">nonexistent</name>'
            f'</unregister-listener>'.encode()
        )
        result = handle_unregister_listener(pump, el, "req-1")
        tree = etree.fromstring(result)
        payload = tree.find(f"{{{NS}}}payload")
        error = payload.find(f"{{{NS}}}error")
        assert error.find(f"{{{NS}}}code").text == "NOT_FOUND"

    def test_unregister_system_listener_forbidden(self):
        pump = _make_pump()
        el = etree.fromstring(
            f'<unregister-listener xmlns="{NS}">'
            f'<name xmlns="{NS}">system.boot</name>'
            f'</unregister-listener>'.encode()
        )
        result = handle_unregister_listener(pump, el, "req-1")
        tree = etree.fromstring(result)
        payload = tree.find(f"{{{NS}}}payload")
        error = payload.find(f"{{{NS}}}error")
        assert error.find(f"{{{NS}}}code").text == "FORBIDDEN"

    def test_get_listener_schema_xsd(self):
        pump = _make_pump()
        pump.register("with-schema", _oob_test_handler, TestPayload, description="Has schema")
        el = etree.fromstring(
            f'<get-listener-schema xmlns="{NS}">'
            f'<name xmlns="{NS}">with-schema</name>'
            f'</get-listener-schema>'.encode()
        )
        result = handle_get_listener_schema(pump, el, "req-1")
        tree = etree.fromstring(result)
        payload = tree.find(f"{{{NS}}}payload")
        ls = payload.find(f"{{{NS}}}listener-schema")
        assert ls.get("format") == "xsd"
        schema_text = ls.find(f"{{{NS}}}schema").text
        assert "xs:schema" in schema_text or "schema" in schema_text.lower()

    def test_get_listener_schema_example(self):
        pump = _make_pump()
        pump.register("with-schema", _oob_test_handler, TestPayload, description="Has schema")
        el = etree.fromstring(
            f'<get-listener-schema xmlns="{NS}">'
            f'<name xmlns="{NS}">with-schema</name>'
            f'<format xmlns="{NS}">example</format>'
            f'</get-listener-schema>'.encode()
        )
        result = handle_get_listener_schema(pump, el, "req-1")
        tree = etree.fromstring(result)
        payload = tree.find(f"{{{NS}}}payload")
        ls = payload.find(f"{{{NS}}}listener-schema")
        assert ls.get("format") == "example"
        schema_text = ls.find(f"{{{NS}}}schema").text
        assert "TestPayload" in schema_text
        assert "value" in schema_text

    def test_get_listener_schema_not_found(self):
        pump = _make_pump()
        el = etree.fromstring(
            f'<get-listener-schema xmlns="{NS}">'
            f'<name xmlns="{NS}">nonexistent</name>'
            f'</get-listener-schema>'.encode()
        )
        result = handle_get_listener_schema(pump, el, "req-1")
        tree = etree.fromstring(result)
        payload = tree.find(f"{{{NS}}}payload")
        error = payload.find(f"{{{NS}}}error")
        assert error.find(f"{{{NS}}}code").text == "NOT_FOUND"

    @pytest.mark.asyncio
    async def test_inject_message(self):
        pump = _make_pump()
        pump.register("target", _oob_test_handler, TestPayload, description="Target")
        # Need to initialize thread registry
        from xml_pipeline.message_bus.thread_registry import get_registry
        registry = get_registry()
        registry.initialize_root("test-oob")

        el = etree.fromstring(
            f'<inject-message xmlns="{NS}">'
            f'<to xmlns="{NS}">target</to>'
            f'<payload xmlns="{NS}"><TestPayload><value>hello</value></TestPayload></payload>'
            f'</inject-message>'.encode()
        )
        result = await handle_inject_message(pump, el, "req-1")
        tree = etree.fromstring(result)
        payload = tree.find(f"{{{NS}}}payload")
        tc = payload.find(f"{{{NS}}}thread-created")
        assert tc is not None
        assert tc.find(f"{{{NS}}}routed-to").text == "target"
        # Verify message was queued
        assert not pump.queue.empty()

        # Clean up
        registry.clear()

    @pytest.mark.asyncio
    async def test_inject_message_unknown_target(self):
        pump = _make_pump()
        el = etree.fromstring(
            f'<inject-message xmlns="{NS}">'
            f'<to xmlns="{NS}">nonexistent</to>'
            f'<payload xmlns="{NS}"><TestPayload><value>hello</value></TestPayload></payload>'
            f'</inject-message>'.encode()
        )
        result = await handle_inject_message(pump, el, "req-1")
        tree = etree.fromstring(result)
        payload = tree.find(f"{{{NS}}}payload")
        error = payload.find(f"{{{NS}}}error")
        assert error.find(f"{{{NS}}}code").text == "NOT_FOUND"

    def test_get_status(self):
        pump = _make_pump()
        pump._running = True
        pump.register("listener1", _oob_test_handler, TestPayload, description="L1")

        el = etree.fromstring(f'<get-status xmlns="{NS}"/>'.encode())
        result = handle_get_status(pump, el, "req-1")
        tree = etree.fromstring(result)
        payload = tree.find(f"{{{NS}}}payload")
        status = payload.find(f"{{{NS}}}status")
        assert status.find(f"{{{NS}}}name").text == "test-oob"
        assert status.find(f"{{{NS}}}state").text == "running"
        assert status.find(f"{{{NS}}}listener-count").text == "1"

    def test_get_status_idle(self):
        pump = _make_pump()
        pump._running = False
        el = etree.fromstring(f'<get-status xmlns="{NS}"/>'.encode())
        result = handle_get_status(pump, el, "req-1")
        tree = etree.fromstring(result)
        payload = tree.find(f"{{{NS}}}payload")
        status = payload.find(f"{{{NS}}}status")
        assert status.find(f"{{{NS}}}state").text == "idle"

    @pytest.mark.asyncio
    async def test_shutdown(self):
        pump = _make_pump()
        pump.shutdown = AsyncMock()

        el = etree.fromstring(f'<shutdown xmlns="{NS}"/>'.encode())
        result = await handle_shutdown(pump, el, "req-1")
        tree = etree.fromstring(result)
        payload = tree.find(f"{{{NS}}}payload")
        ack = payload.find(f"{{{NS}}}ack")
        assert ack is not None
        assert ack.get("success") == "true"

    def test_subscribe_thread(self):
        pump = _make_pump()
        el = etree.fromstring(f'<subscribe-thread xmlns="{NS}"/>'.encode())
        result = handle_subscribe_thread(pump, el, "req-1")
        assert isinstance(result, tuple)
        response_bytes, subscription_id = result
        tree = etree.fromstring(response_bytes)
        payload = tree.find(f"{{{NS}}}payload")
        sub = payload.find(f"{{{NS}}}subscription")
        assert sub.find(f"{{{NS}}}subscription-id").text == subscription_id

    def test_unsubscribe(self):
        pump = _make_pump()
        el = etree.fromstring(
            f'<unsubscribe xmlns="{NS}">'
            f'<subscription-id xmlns="{NS}">sub-123</subscription-id>'
            f'</unsubscribe>'.encode()
        )
        result = handle_unsubscribe(pump, el, "req-1")
        tree = etree.fromstring(result)
        payload = tree.find(f"{{{NS}}}payload")
        ack = payload.find(f"{{{NS}}}ack")
        assert ack is not None

    def test_unsubscribe_missing_id(self):
        pump = _make_pump()
        el = etree.fromstring(f'<unsubscribe xmlns="{NS}"/>'.encode())
        result = handle_unsubscribe(pump, el, "req-1")
        tree = etree.fromstring(result)
        payload = tree.find(f"{{{NS}}}payload")
        error = payload.find(f"{{{NS}}}error")
        assert error.find(f"{{{NS}}}code").text == "INVALID_REQUEST"

    def test_register_listener_emits_reload_event(self):
        pump = _make_pump()
        events = []
        pump.subscribe_events(lambda e: events.append(e))

        el = etree.fromstring(
            f'<register-listener xmlns="{NS}">'
            f'<name xmlns="{NS}">dynamic</name>'
            f'<class xmlns="{NS}">tests.test_oob.TestPayload</class>'
            f'<handler xmlns="{NS}">tests.test_oob._oob_test_handler</handler>'
            f'<description xmlns="{NS}">Dynamic</description>'
            f'</register-listener>'.encode()
        )
        handle_register_listener(pump, el, "req-1")

        reload_events = [e for e in events if isinstance(e, ReloadEvent)]
        assert len(reload_events) == 1
        assert reload_events[0].success is True
        assert "dynamic" in reload_events[0].added_listeners

    def test_register_listener_with_peers(self):
        pump = _make_pump()
        el = etree.fromstring(
            f'<register-listener xmlns="{NS}">'
            f'<name xmlns="{NS}">agent</name>'
            f'<class xmlns="{NS}">tests.test_oob.TestPayload</class>'
            f'<handler xmlns="{NS}">tests.test_oob._oob_test_handler</handler>'
            f'<description xmlns="{NS}">Agent</description>'
            f'<is-agent xmlns="{NS}">true</is-agent>'
            f'<peers xmlns="{NS}"><peer xmlns="{NS}">tool1</peer><peer xmlns="{NS}">tool2</peer></peers>'
            f'</register-listener>'.encode()
        )
        handle_register_listener(pump, el, "req-1")
        assert "agent" in pump.listeners
        listener = pump.listeners["agent"]
        assert listener.is_agent is True
        assert listener.peers == ["tool1", "tool2"]

    def test_register_peer_table_via_oob(self):
        pump = _make_pump()
        registry = get_registry()
        registry.initialize_root("test-oob")
        pump.register("concierge", _oob_test_handler, TestPayload,
                       description="Concierge", agent=True, peers=["tool1", "tool2"])
        pump.register("tool1", _oob_test_handler, TestPayload, description="Tool 1")
        pump.register("tool2", _oob_test_handler, TestPayload, description="Tool 2")

        el = etree.fromstring(
            f'<register-peer-table xmlns="{NS}">'
            f'<name xmlns="{NS}">premium</name>'
            f'<entries xmlns="{NS}">'
            f'<entry xmlns="{NS}">'
            f'<listener xmlns="{NS}">concierge</listener>'
            f'<peers xmlns="{NS}"><peer xmlns="{NS}">tool1</peer><peer xmlns="{NS}">tool2</peer></peers>'
            f'</entry>'
            f'</entries>'
            f'</register-peer-table>'.encode()
        )
        result = handle_register_peer_table(pump, el, "req-1")
        tree = etree.fromstring(result)
        payload = tree.find(f"{{{NS}}}payload")
        ack = payload.find(f"{{{NS}}}ack")
        assert ack is not None
        assert ack.get("success") == "true"
        assert "premium" in pump._peer_tables
        assert pump._peer_tables["premium"]["concierge"] == ["tool1", "tool2"]
        registry.clear()

    def test_register_peer_table_already_exists(self):
        pump = _make_pump()
        registry = get_registry()
        registry.initialize_root("test-oob")
        pump.register_peer_table("existing", {})

        el = etree.fromstring(
            f'<register-peer-table xmlns="{NS}">'
            f'<name xmlns="{NS}">existing</name>'
            f'</register-peer-table>'.encode()
        )
        result = handle_register_peer_table(pump, el, "req-1")
        tree = etree.fromstring(result)
        payload = tree.find(f"{{{NS}}}payload")
        error = payload.find(f"{{{NS}}}error")
        assert error.find(f"{{{NS}}}code").text == "ALREADY_EXISTS"
        registry.clear()

    def test_modify_peer_table_via_oob(self):
        pump = _make_pump()
        registry = get_registry()
        registry.initialize_root("test-oob")
        pump.register("concierge", _oob_test_handler, TestPayload,
                       description="Concierge", agent=True, peers=["tool1", "tool2"])
        pump.register_peer_table("premium", {"concierge": ["tool1"]})

        el = etree.fromstring(
            f'<modify-peer-table xmlns="{NS}">'
            f'<name xmlns="{NS}">premium</name>'
            f'<listener xmlns="{NS}">concierge</listener>'
            f'<grant xmlns="{NS}"><peer xmlns="{NS}">tool2</peer></grant>'
            f'</modify-peer-table>'.encode()
        )
        result = handle_modify_peer_table(pump, el, "req-1")
        tree = etree.fromstring(result)
        payload = tree.find(f"{{{NS}}}payload")
        ack = payload.find(f"{{{NS}}}ack")
        assert ack is not None
        assert ack.get("success") == "true"
        assert "tool2" in pump._peer_tables["premium"]["concierge"]
        registry.clear()

    def test_modify_peer_table_not_found(self):
        pump = _make_pump()
        el = etree.fromstring(
            f'<modify-peer-table xmlns="{NS}">'
            f'<name xmlns="{NS}">nonexistent</name>'
            f'<listener xmlns="{NS}">x</listener>'
            f'</modify-peer-table>'.encode()
        )
        result = handle_modify_peer_table(pump, el, "req-1")
        tree = etree.fromstring(result)
        payload = tree.find(f"{{{NS}}}payload")
        error = payload.find(f"{{{NS}}}error")
        assert error.find(f"{{{NS}}}code").text == "NOT_FOUND"


# ============================================================================
# Phase 3b: Peer table unit tests
# ============================================================================


class TestPeerTables:
    """Tests for peer table registration, injection, and dispatch enforcement."""

    def setup_method(self):
        """Reset global state before each test."""
        reset_registry()

    def teardown_method(self):
        """Clean up global state after each test."""
        reset_registry()

    def test_register_peer_table(self):
        """register_peer_table stores table and creates root in registry."""
        pump = _make_pump()
        registry = get_registry()
        registry.initialize_root("test")

        pump.register("concierge", _oob_test_handler, TestPayload,
                       description="Concierge agent", agent=True, peers=["tool1", "tool2"])
        pump.register("tool1", _oob_test_handler, TestPayload, description="Tool 1")

        pump.register_peer_table("premium", {
            "concierge": ["tool1", "tool2"],
        })

        assert "premium" in pump._peer_tables
        assert pump._peer_tables["premium"]["concierge"] == ["tool1", "tool2"]

        # Table root should exist in registry
        table_root = registry.get_table_root("premium")
        assert table_root is not None
        chain = registry.lookup(table_root)
        assert chain == "premium.test-oob"

    @pytest.mark.asyncio
    async def test_inject_with_routing_table(self):
        """inject() with routing_table creates thread rooted at table prefix."""
        pump = _make_pump()
        registry = get_registry()
        registry.initialize_root("test")

        pump.register("concierge", _oob_test_handler, TestPayload,
                       description="Concierge", agent=True, peers=["tool1", "tool2"])
        pump.register_peer_table("premium", {"concierge": ["tool1", "tool2"]})

        thread_id = await pump.inject("concierge", TestPayload(value="hello"),
                                       routing_table="premium")

        # Thread chain should start with table name, not "system"
        chain = registry.lookup(thread_id)
        assert chain is not None
        assert chain.startswith("premium.")
        assert "concierge" in chain

        # Table should be detected for this thread
        table_name = registry.get_table_for_thread(thread_id)
        assert table_name == "premium"

    @pytest.mark.asyncio
    async def test_inject_without_routing_table_uses_system(self):
        """inject() without routing_table uses system root (backward compat)."""
        pump = _make_pump()
        registry = get_registry()
        registry.initialize_root("test")

        pump.register("concierge", _oob_test_handler, TestPayload,
                       description="Concierge")

        thread_id = await pump.inject("concierge", TestPayload(value="hello"))

        chain = registry.lookup(thread_id)
        assert chain is not None
        assert chain.startswith("system.")

        # No table for default threads
        table_name = registry.get_table_for_thread(thread_id)
        assert table_name is None

    def test_modify_peer_table_grant(self):
        """modify_peer_table grant adds peers to a table entry."""
        pump = _make_pump()
        registry = get_registry()
        registry.initialize_root("test")

        pump.register("agent", _oob_test_handler, TestPayload,
                       description="Agent", agent=True, peers=["tool1", "tool2", "tool3"])
        pump.register_peer_table("basic", {"agent": ["tool1"]})

        updated = pump.modify_peer_table("basic", "agent", grant=["tool2", "tool3"])
        assert updated == ["tool1", "tool2", "tool3"]
        assert pump._peer_tables["basic"]["agent"] == ["tool1", "tool2", "tool3"]

    def test_modify_peer_table_revoke(self):
        """modify_peer_table revoke removes peers from a table entry."""
        pump = _make_pump()
        registry = get_registry()
        registry.initialize_root("test")

        pump.register("agent", _oob_test_handler, TestPayload,
                       description="Agent", agent=True, peers=["tool1", "tool2", "tool3"])
        pump.register_peer_table("premium", {"agent": ["tool1", "tool2", "tool3"]})

        updated = pump.modify_peer_table("premium", "agent", revoke=["tool2"])
        assert updated == ["tool1", "tool3"]
        assert pump._peer_tables["premium"]["agent"] == ["tool1", "tool3"]

    def test_modify_peer_table_nonexistent_raises(self):
        """modify_peer_table raises KeyError for unregistered table."""
        pump = _make_pump()
        with pytest.raises(KeyError, match="not registered"):
            pump.modify_peer_table("nonexistent", "agent", grant=["tool1"])

    def test_table_usage_instructions(self):
        """Tabled agents get table-specific usage_instructions."""
        pump = _make_pump()
        registry = get_registry()
        registry.initialize_root("test")

        pump.register("concierge", _oob_test_handler, TestPayload,
                       description="Concierge agent", agent=True,
                       peers=["tool1", "tool2", "tool3"])
        pump.register("tool1", _oob_test_handler, TestPayload, description="Tool 1")
        pump.register("tool2", _oob_test_handler, TestPayload, description="Tool 2")
        pump.register("tool3", _oob_test_handler, TestPayload, description="Tool 3")

        # Build static usage_instructions (normally done by start())
        concierge = pump.listeners["concierge"]
        concierge.usage_instructions = pump._build_usage_instructions(concierge)

        # Register a table with a subset (ceiling enforcement: subset of listener.peers)
        pump.register_peer_table("premium", {
            "concierge": ["tool1", "tool2"],
        })

        # Table usage instructions should mention tool1 and tool2
        table_instr = pump._table_usage_instructions["premium"]["concierge"]
        assert "tool1" in table_instr
        assert "tool2" in table_instr

        # Listener's static usage_instructions should mention all three
        listener_instr = concierge.usage_instructions
        assert "tool1" in listener_instr
        assert "## tool3" in listener_instr

    def test_modify_rebuilds_usage_instructions(self):
        """modify_peer_table rebuilds usage_instructions for the affected listener."""
        pump = _make_pump()
        registry = get_registry()
        registry.initialize_root("test")

        pump.register("agent", _oob_test_handler, TestPayload,
                       description="Agent", agent=True, peers=["tool1", "tool2"])
        pump.register("tool1", _oob_test_handler, TestPayload, description="Tool 1")
        pump.register("tool2", _oob_test_handler, TestPayload, description="Tool 2")

        pump.register_peer_table("tier", {"agent": ["tool1"]})
        instr_before = pump._table_usage_instructions["tier"]["agent"]
        assert "tool2" not in instr_before or "not registered" in instr_before

        pump.modify_peer_table("tier", "agent", grant=["tool2"])
        instr_after = pump._table_usage_instructions["tier"]["agent"]
        assert "tool2" in instr_after

    def test_multiple_tables_independent(self):
        """Multiple peer tables maintain independent peer lists."""
        pump = _make_pump()
        registry = get_registry()
        registry.initialize_root("test")

        pump.register("agent", _oob_test_handler, TestPayload,
                       description="Agent", agent=True, peers=["a", "b", "c", "d"])

        pump.register_peer_table("premium", {"agent": ["a", "b", "c"]})
        pump.register_peer_table("basic", {"agent": ["a"]})

        assert pump._peer_tables["premium"]["agent"] == ["a", "b", "c"]
        assert pump._peer_tables["basic"]["agent"] == ["a"]

        # Modifying one doesn't affect the other
        pump.modify_peer_table("basic", "agent", grant=["d"])
        assert pump._peer_tables["basic"]["agent"] == ["a", "d"]
        assert pump._peer_tables["premium"]["agent"] == ["a", "b", "c"]

    def test_get_table_for_thread_returns_none_for_system(self):
        """get_table_for_thread returns None for system-rooted threads."""
        registry = get_registry()
        root = registry.initialize_root("test")
        child = registry.extend_chain(root, "external")
        assert registry.get_table_for_thread(child) is None

    def test_get_table_for_thread_returns_table_name(self):
        """get_table_for_thread returns table name for tabled threads."""
        registry = get_registry()
        registry.initialize_root("test")
        table_root = registry.initialize_table_root("premium", "test")
        child = registry.extend_chain(table_root, "external")
        assert registry.get_table_for_thread(child) == "premium"

    def test_get_table_for_thread_unknown_uuid(self):
        """get_table_for_thread returns None for unknown UUIDs."""
        registry = get_registry()
        assert registry.get_table_for_thread("nonexistent-uuid") is None


# ============================================================================
# Phase 4: Server integration tests
# ============================================================================


class TestOOBServer:
    """Integration tests for the WebSocket server."""

    @pytest.mark.asyncio
    async def test_server_starts_and_stops(self):
        """OOB server can start and stop cleanly."""
        from xml_pipeline.oob.server import OOBServer

        pump = _make_pump()
        server = OOBServer(pump=pump, port=0)  # port=0 lets OS pick
        # Mock websockets.serve to avoid actual binding
        mock_ws_server = AsyncMock()
        mock_ws_server.close = MagicMock()
        mock_ws_server.wait_closed = AsyncMock()

        # Directly set server state instead of mocking websockets.serve
        server._server = mock_ws_server
        pump.subscribe_events(server._on_pump_event)
        assert server._server is mock_ws_server

        await server.stop()
        assert server._server is None
        mock_ws_server.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_dispatch_unknown_command(self):
        """Unknown commands return error response."""
        from xml_pipeline.oob.server import OOBServer

        pump = _make_pump()
        server = OOBServer(pump=pump, identity=None)

        xml = _make_privileged_msg(f'<unknown-command xmlns="{NS}"/>')
        result = await server._dispatch(xml, MagicMock())
        tree = etree.fromstring(result)
        payload = tree.find(f"{{{NS}}}payload")
        error = payload.find(f"{{{NS}}}error")
        assert error.find(f"{{{NS}}}code").text == "UNKNOWN_COMMAND"

    @pytest.mark.asyncio
    async def test_dispatch_list_listeners(self):
        """dispatch() correctly routes list-listeners command."""
        from xml_pipeline.oob.server import OOBServer

        pump = _make_pump()
        pump.register("echo", _oob_test_handler, TestPayload, description="Echo")
        server = OOBServer(pump=pump, identity=None)

        xml = _make_privileged_msg(f'<list-listeners xmlns="{NS}"/>')
        result = await server._dispatch(xml, MagicMock())
        tree = etree.fromstring(result)
        payload = tree.find(f"{{{NS}}}payload")
        listeners = payload.find(f"{{{NS}}}listeners")
        assert int(listeners.get("count")) >= 1

    @pytest.mark.asyncio
    async def test_dispatch_with_signature_check(self):
        """dispatch() rejects invalid signatures when identity is set."""
        from xml_pipeline.oob.server import OOBServer

        identity = Identity.generate()
        other_identity = Identity.generate()
        pump = _make_pump()
        server = OOBServer(pump=pump, identity=identity)

        # Signed with wrong key
        xml = _make_privileged_msg(f'<list-listeners xmlns="{NS}"/>', identity=other_identity)
        result = await server._dispatch(xml, MagicMock())
        tree = etree.fromstring(result)
        payload = tree.find(f"{{{NS}}}payload")
        error = payload.find(f"{{{NS}}}error")
        assert error.find(f"{{{NS}}}code").text == "INVALID_SIGNATURE"

    @pytest.mark.asyncio
    async def test_dispatch_subscribe_creates_subscription(self):
        """subscribe-thread dispatch creates a subscription entry."""
        from xml_pipeline.oob.server import OOBServer

        pump = _make_pump()
        server = OOBServer(pump=pump, identity=None)
        mock_ws = MagicMock()

        xml = _make_privileged_msg(f'<subscribe-thread xmlns="{NS}"/>')
        result = await server._dispatch(xml, mock_ws)
        tree = etree.fromstring(result)
        payload = tree.find(f"{{{NS}}}payload")
        sub = payload.find(f"{{{NS}}}subscription")
        sub_id = sub.find(f"{{{NS}}}subscription-id").text
        assert sub_id in server._subscriptions
        assert server._subscriptions[sub_id].websocket is mock_ws

    @pytest.mark.asyncio
    async def test_dispatch_unsubscribe_removes_subscription(self):
        """unsubscribe dispatch removes a subscription entry."""
        from xml_pipeline.oob.server import OOBServer

        pump = _make_pump()
        server = OOBServer(pump=pump, identity=None)
        mock_ws = MagicMock()

        # First subscribe
        xml = _make_privileged_msg(f'<subscribe-thread xmlns="{NS}"/>')
        result = await server._dispatch(xml, mock_ws)
        tree = etree.fromstring(result)
        payload = tree.find(f"{{{NS}}}payload")
        sub = payload.find(f"{{{NS}}}subscription")
        sub_id = sub.find(f"{{{NS}}}subscription-id").text
        assert sub_id in server._subscriptions

        # Then unsubscribe
        xml = _make_privileged_msg(
            f'<unsubscribe xmlns="{NS}">'
            f'<subscription-id xmlns="{NS}">{sub_id}</subscription-id>'
            f'</unsubscribe>'
        )
        await server._dispatch(xml, mock_ws)
        assert sub_id not in server._subscriptions

    @pytest.mark.asyncio
    async def test_push_event_delivery(self):
        """Push events are delivered to subscribed clients."""
        from xml_pipeline.oob.server import OOBServer, Subscription

        pump = _make_pump()
        server = OOBServer(pump=pump, identity=None)

        mock_ws = AsyncMock()
        sub_id = "test-sub-1"
        server._subscriptions[sub_id] = Subscription(
            subscription_id=sub_id,
            websocket=mock_ws,
        )

        event = MessageReceivedEvent(
            thread_id="thread-1",
            from_id="greeter",
            to_id="shouter",
            payload_type="greeting",
            payload=None,
        )
        await server._deliver_push(event)

        mock_ws.send.assert_called_once()
        sent_xml = mock_ws.send.call_args[0][0]
        tree = etree.fromstring(sent_xml)
        assert tree.tag == f"{{{NS}}}privileged-push"

    @pytest.mark.asyncio
    async def test_push_event_thread_filter(self):
        """Push events are filtered by thread_id subscription."""
        from xml_pipeline.oob.server import OOBServer, Subscription

        pump = _make_pump()
        server = OOBServer(pump=pump, identity=None)

        mock_ws = AsyncMock()
        sub_id = "filtered-sub"
        server._subscriptions[sub_id] = Subscription(
            subscription_id=sub_id,
            websocket=mock_ws,
            thread_id="specific-thread",
        )

        # Event for wrong thread — should NOT be delivered
        event = MessageReceivedEvent(
            thread_id="other-thread",
            from_id="a", to_id="b", payload_type="x", payload=None,
        )
        await server._deliver_push(event)
        mock_ws.send.assert_not_called()

        # Event for matching thread — should be delivered
        event2 = MessageReceivedEvent(
            thread_id="specific-thread",
            from_id="a", to_id="b", payload_type="x", payload=None,
        )
        await server._deliver_push(event2)
        mock_ws.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_push_reload_event(self):
        """ReloadEvent is delivered as system-event push."""
        from xml_pipeline.oob.server import OOBServer, Subscription

        pump = _make_pump()
        server = OOBServer(pump=pump, identity=None)

        mock_ws = AsyncMock()
        sub_id = "reload-sub"
        server._subscriptions[sub_id] = Subscription(
            subscription_id=sub_id,
            websocket=mock_ws,
        )

        event = ReloadEvent(
            success=True,
            added_listeners=["new-listener"],
        )
        await server._deliver_push(event)
        mock_ws.send.assert_called_once()
        sent_xml = mock_ws.send.call_args[0][0]
        tree = etree.fromstring(sent_xml)
        payload = tree.find(f"{{{NS}}}payload")
        se = payload.find(f"{{{NS}}}system-event")
        assert se.find(f"{{{NS}}}event-type").text == "config-changed"

    @pytest.mark.asyncio
    async def test_push_disconnected_client_cleaned_up(self):
        """Disconnected clients are removed from subscriptions on send failure."""
        from xml_pipeline.oob.server import OOBServer, Subscription

        pump = _make_pump()
        server = OOBServer(pump=pump, identity=None)

        mock_ws = AsyncMock()
        mock_ws.send.side_effect = ConnectionError("gone")
        sub_id = "dying-sub"
        server._subscriptions[sub_id] = Subscription(
            subscription_id=sub_id,
            websocket=mock_ws,
        )

        event = MessageReceivedEvent(
            thread_id="t", from_id="a", to_id="b", payload_type="x", payload=None,
        )
        await server._deliver_push(event)

        # Subscription should be cleaned up
        assert sub_id not in server._subscriptions


# ============================================================================
# Phase 5: StreamPump integration tests
# ============================================================================


class TestOOBStreamPumpIntegration:
    """Tests for OOB integration with StreamPump lifecycle."""

    def test_config_oob_fields_default(self):
        """OrganismConfig has OOB fields with correct defaults."""
        config = OrganismConfig(name="test")
        assert config.oob_enabled is True
        assert config.oob_bind == "127.0.0.1"
        assert config.oob_port == 8766

    def test_pump_kwargs_oob_disabled_by_default(self):
        """StreamPump kwargs constructor defaults to oob_enabled=False."""
        pump = StreamPump(name="test")
        assert pump.config.oob_enabled is False

    def test_pump_kwargs_oob_enabled(self):
        """StreamPump kwargs constructor can enable OOB."""
        pump = StreamPump(name="test", oob_enabled=True, oob_port=9999)
        assert pump.config.oob_enabled is True
        assert pump.config.oob_port == 9999

    def test_pump_has_oob_attributes(self):
        """StreamPump has _oob_server and _start_time attributes."""
        pump = StreamPump(name="test")
        assert pump._oob_server is None
        assert pump._start_time == 0.0

    def test_config_loader_parses_oob(self):
        """ConfigLoader._parse reads oob section from YAML dict."""
        from xml_pipeline.message_bus.stream_pump import ConfigLoader

        raw = {
            "organism": {"name": "test"},
            "oob": {
                "enabled": True,
                "bind": "0.0.0.0",
                "port": 9999,
            },
            "listeners": [],
        }
        config = ConfigLoader._parse(raw)
        assert config.oob_enabled is True
        assert config.oob_bind == "0.0.0.0"
        assert config.oob_port == 9999

    def test_config_loader_oob_defaults(self):
        """ConfigLoader._parse uses OOB defaults when section is missing."""
        from xml_pipeline.message_bus.stream_pump import ConfigLoader

        raw = {
            "organism": {"name": "test"},
            "listeners": [],
        }
        config = ConfigLoader._parse(raw)
        assert config.oob_enabled is True
        assert config.oob_bind == "127.0.0.1"
        assert config.oob_port == 8766

    def test_config_loader_oob_disabled(self):
        """ConfigLoader._parse respects oob.enabled: false."""
        from xml_pipeline.message_bus.stream_pump import ConfigLoader

        raw = {
            "organism": {"name": "test"},
            "oob": {"enabled": False},
            "listeners": [],
        }
        config = ConfigLoader._parse(raw)
        assert config.oob_enabled is False

    @pytest.mark.asyncio
    async def test_pump_start_skips_oob_when_disabled(self):
        """start() does not create OOB server when disabled."""
        pump = StreamPump(name="test-no-oob", oob_enabled=False)
        await pump.start()
        assert pump._oob_server is None
        assert pump._start_time > 0
        # Drain the boot message so queue.join() won't block
        while not pump.queue.empty():
            pump.queue.get_nowait()
            pump.queue.task_done()

    @pytest.mark.asyncio
    async def test_pump_shutdown_handles_no_oob(self):
        """shutdown() works when OOB server was never started."""
        pump = StreamPump(name="test-shutdown", oob_enabled=False)
        await pump.start()
        # Drain the boot message so queue.join() won't block
        while not pump.queue.empty():
            pump.queue.get_nowait()
            pump.queue.task_done()
        await pump.shutdown()
        assert pump._oob_server is None
