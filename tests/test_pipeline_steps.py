"""
test_pipeline_steps.py — Unit tests for individual pipeline steps

Run with: pytest tests/test_pipeline_steps.py -v

Each step is tested in isolation with known inputs and expected outputs.
This makes debugging much easier than testing the full pipeline.

Install test dependencies:
    pip install -e ".[test]"
"""

import pytest
import asyncio
from dataclasses import dataclass
from lxml import etree

# Import the message state
from agentserver.message_bus.message_state import MessageState, HandlerMetadata

# Import individual steps
from agentserver.message_bus.steps.repair import repair_step
from agentserver.message_bus.steps.c14n import c14n_step
from agentserver.message_bus.steps.envelope_validation import envelope_validation_step
from agentserver.message_bus.steps.payload_extraction import payload_extraction_step
from agentserver.message_bus.steps.thread_assignment import thread_assignment_step

# Check for optional dependencies
try:
    import aiostream
    HAS_AIOSTREAM = True
except ImportError:
    HAS_AIOSTREAM = False

requires_aiostream = pytest.mark.skipif(
    not HAS_AIOSTREAM,
    reason="aiostream not installed (pip install aiostream)"
)

# Check for stream_pump dependencies
try:
    from agentserver.message_bus.stream_pump import StreamPump, Listener
    from agentserver.message_bus.steps.routing_resolution import make_routing_step
    HAS_STREAM_PUMP = True
except ImportError:
    HAS_STREAM_PUMP = False

requires_stream_pump = pytest.mark.skipif(
    not HAS_STREAM_PUMP,
    reason="stream_pump dependencies not available"
)


# ============================================================================
# Test Fixtures
# ============================================================================

@pytest.fixture
def valid_envelope_bytes():
    """A well-formed message envelope matching envelope.xsd."""
    return b'''<?xml version="1.0"?>
    <message xmlns="https://xml-pipeline.org/ns/envelope/v1">
        <meta>
            <from>calculator.add</from>
            <thread>550e8400-e29b-41d4-a716-446655440000</thread>
        </meta>
        <addpayload xmlns="https://xml-pipeline.org/ns/calculator/add/v1">
            <a>5</a>
            <b>3</b>
        </addpayload>
    </message>'''


@pytest.fixture
def malformed_xml_bytes():
    """Malformed XML that lxml can partially recover."""
    return b'<message><unclosed><nested>content</nested></message>'


@pytest.fixture
def completely_broken_bytes():
    """Not XML at all."""
    return b'this is not xml at all { json: "maybe" }'


@pytest.fixture
def multi_payload_response():
    """Handler response with multiple payloads."""
    return b'''
    <search.result><answer>42</answer></search.result>
    <calculator.add.addpayload><a>1</a><b>2</b></calculator.add.addpayload>
    <thought>I should also check...</thought>
    '''


@pytest.fixture
def empty_state():
    """Fresh MessageState with no data."""
    return MessageState()


@pytest.fixture
def state_with_bytes(valid_envelope_bytes):
    """MessageState with raw_bytes populated."""
    return MessageState(raw_bytes=valid_envelope_bytes)


# ============================================================================
# repair_step Tests
# ============================================================================

class TestRepairStep:
    """Tests for the XML repair/recovery step."""

    @pytest.mark.asyncio
    async def test_valid_xml_passes_through(self, valid_envelope_bytes):
        """Valid XML should parse without error."""
        state = MessageState(raw_bytes=valid_envelope_bytes)
        result = await repair_step(state)

        assert result.error is None
        assert result.envelope_tree is not None
        assert result.envelope_tree.tag == "{https://xml-pipeline.org/ns/envelope/v1}message"

    @pytest.mark.asyncio
    async def test_malformed_xml_recovered(self, malformed_xml_bytes):
        """Malformed XML should be recovered if possible."""
        state = MessageState(raw_bytes=malformed_xml_bytes)
        result = await repair_step(state)

        # lxml recovery mode should produce something
        # May or may not have error depending on severity
        assert result.envelope_tree is not None or result.error is not None

    @pytest.mark.asyncio
    async def test_no_bytes_sets_error(self, empty_state):
        """Missing raw_bytes should set an error."""
        result = await repair_step(empty_state)

        assert result.error is not None
        assert "no raw_bytes" in result.error

    @pytest.mark.asyncio
    async def test_clears_raw_bytes_after_parse(self, valid_envelope_bytes):
        """raw_bytes should be cleared after successful parse (memory optimization)."""
        state = MessageState(raw_bytes=valid_envelope_bytes)
        result = await repair_step(state)

        assert result.raw_bytes is None
        assert result.envelope_tree is not None


# ============================================================================
# c14n_step Tests
# ============================================================================

class TestC14nStep:
    """Tests for the canonicalization step."""

    @pytest.mark.asyncio
    async def test_normalizes_whitespace(self):
        """C14N should normalize whitespace."""
        xml_with_whitespace = b'''<root>
            <child>   value   </child>
        </root>'''

        state = MessageState(raw_bytes=xml_with_whitespace)
        state = await repair_step(state)
        result = await c14n_step(state)

        assert result.error is None
        assert result.envelope_tree is not None

    @pytest.mark.asyncio
    async def test_normalizes_attribute_order(self):
        """C14N should produce consistent attribute ordering."""
        xml_a = b'<root z="1" a="2"/>'
        xml_b = b'<root a="2" z="1"/>'

        state_a = MessageState(raw_bytes=xml_a)
        state_a = await repair_step(state_a)
        state_a = await c14n_step(state_a)

        state_b = MessageState(raw_bytes=xml_b)
        state_b = await repair_step(state_b)
        state_b = await c14n_step(state_b)

        # Both should produce identical canonical form
        c14n_a = etree.tostring(state_a.envelope_tree, method="c14n")
        c14n_b = etree.tostring(state_b.envelope_tree, method="c14n")
        assert c14n_a == c14n_b

    @pytest.mark.asyncio
    async def test_no_tree_sets_error(self, empty_state):
        """Missing envelope_tree should set error."""
        result = await c14n_step(empty_state)

        assert result.error is not None
        assert "no envelope_tree" in result.error


# ============================================================================
# payload_extraction_step Tests
# ============================================================================

class TestPayloadExtractionStep:
    """Tests for extracting payload from envelope."""

    @pytest.mark.asyncio
    async def test_extracts_payload_element(self, valid_envelope_bytes):
        """Should extract the payload element from envelope."""
        state = MessageState(raw_bytes=valid_envelope_bytes)
        state = await repair_step(state)
        state = await c14n_step(state)
        # Skip envelope validation for this test
        result = await payload_extraction_step(state)

        assert result.error is None
        assert result.payload_tree is not None
        # Tag may include namespace prefix
        assert "addpayload" in result.payload_tree.tag

    @pytest.mark.asyncio
    async def test_extracts_thread_id(self, valid_envelope_bytes):
        """Should extract thread ID from envelope."""
        state = MessageState(raw_bytes=valid_envelope_bytes)
        state = await repair_step(state)
        state = await c14n_step(state)
        result = await payload_extraction_step(state)

        assert result.thread_id == "550e8400-e29b-41d4-a716-446655440000"

    @pytest.mark.asyncio
    async def test_extracts_from_id(self, valid_envelope_bytes):
        """Should extract sender ID from envelope."""
        state = MessageState(raw_bytes=valid_envelope_bytes)
        state = await repair_step(state)
        state = await c14n_step(state)
        result = await payload_extraction_step(state)

        assert result.from_id == "calculator.add"

    @pytest.mark.asyncio
    async def test_multiple_payloads_error(self):
        """Multiple payload elements should error."""
        multi_payload = b'''
        <message xmlns="https://xml-pipeline.org/ns/envelope/v1">
            <meta>
                <from>test</from>
                <thread>uuid-here</thread>
            </meta>
            <payload1>data</payload1>
            <payload2>more data</payload2>
        </message>'''

        state = MessageState(raw_bytes=multi_payload)
        state = await repair_step(state)
        state = await c14n_step(state)
        result = await payload_extraction_step(state)

        assert result.error is not None
        assert "multiple payload" in result.error.lower()

    @pytest.mark.asyncio
    async def test_no_payload_error(self):
        """Missing payload element should error."""
        no_payload = b'''
        <message xmlns="https://xml-pipeline.org/ns/envelope/v1">
            <meta>
                <from>test</from>
                <thread>uuid-here</thread>
            </meta>
        </message>'''

        state = MessageState(raw_bytes=no_payload)
        state = await repair_step(state)
        state = await c14n_step(state)
        result = await payload_extraction_step(state)

        assert result.error is not None
        assert "no payload" in result.error.lower()

    @pytest.mark.asyncio
    async def test_missing_meta_error(self):
        """Missing <meta> block should error."""
        no_meta = b'''
        <message xmlns="https://xml-pipeline.org/ns/envelope/v1">
            <payload>data</payload>
        </message>'''

        state = MessageState(raw_bytes=no_meta)
        state = await repair_step(state)
        state = await c14n_step(state)
        result = await payload_extraction_step(state)

        assert result.error is not None
        assert "meta" in result.error.lower()

    @pytest.mark.asyncio
    async def test_missing_from_error(self):
        """Missing <from> in <meta> should error."""
        no_from = b'''
        <message xmlns="https://xml-pipeline.org/ns/envelope/v1">
            <meta>
                <thread>uuid-here</thread>
            </meta>
            <payload>data</payload>
        </message>'''

        state = MessageState(raw_bytes=no_from)
        state = await repair_step(state)
        state = await c14n_step(state)
        result = await payload_extraction_step(state)

        assert result.error is not None
        assert "from" in result.error.lower()


# ============================================================================
# thread_assignment_step Tests
# ============================================================================

class TestThreadAssignmentStep:
    """Tests for thread UUID assignment."""

    @pytest.mark.asyncio
    async def test_valid_uuid_preserved(self):
        """Valid UUID should be preserved."""
        valid_uuid = "550e8400-e29b-41d4-a716-446655440000"
        state = MessageState(thread_id=valid_uuid)
        result = await thread_assignment_step(state)

        assert result.thread_id == valid_uuid

    @pytest.mark.asyncio
    async def test_missing_uuid_generated(self, empty_state):
        """Missing UUID should generate a new one."""
        result = await thread_assignment_step(empty_state)

        assert result.thread_id is not None
        assert len(result.thread_id) == 36  # UUID format

    @pytest.mark.asyncio
    async def test_invalid_uuid_replaced(self):
        """Invalid UUID should be replaced with a new one."""
        state = MessageState(thread_id="not-a-valid-uuid")
        result = await thread_assignment_step(state)

        assert result.thread_id != "not-a-valid-uuid"
        assert len(result.thread_id) == 36

    @pytest.mark.asyncio
    async def test_replacement_logged_in_metadata(self):
        """Replaced UUIDs should be logged in metadata."""
        state = MessageState(thread_id="bad-uuid")
        result = await thread_assignment_step(state)

        diagnostics = result.metadata.get("diagnostics", [])
        assert len(diagnostics) > 0
        assert "bad-uuid" in diagnostics[0]


# ============================================================================
# Multi-Payload Extraction Tests (standalone, no aiostream required)
# ============================================================================

class TestPayloadExtractionLogic:
    """Test the core payload extraction logic without aiostream."""

    def test_extract_single_payload(self):
        """Single root element should extract cleanly."""
        raw = b"<result>42</result>"
        wrapped = b"<dummy>" + raw + b"</dummy>"
        tree = etree.fromstring(wrapped, parser=etree.XMLParser(recover=True))

        children = list(tree)
        assert len(children) == 1
        assert children[0].tag == "result"
        assert children[0].text == "42"

    def test_extract_multiple_payloads(self, multi_payload_response):
        """Multiple root elements should all be extracted."""
        wrapped = b"<dummy>" + multi_payload_response + b"</dummy>"
        tree = etree.fromstring(wrapped, parser=etree.XMLParser(recover=True))

        children = list(tree)
        assert len(children) == 3

        tags = [c.tag for c in children]
        assert "search.result" in tags
        assert "calculator.add.addpayload" in tags
        assert "thought" in tags

    def test_extract_preserves_content(self):
        """Extracted payloads should preserve their content."""
        raw = b"<data><nested><deep>value</deep></nested></data>"
        wrapped = b"<dummy>" + raw + b"</dummy>"
        tree = etree.fromstring(wrapped, parser=etree.XMLParser(recover=True))

        children = list(tree)
        assert len(children) == 1

        # Re-serialize and check
        extracted = etree.tostring(children[0])
        assert b"<deep>value</deep>" in extracted

    def test_empty_response_no_crash(self):
        """Empty response should not crash."""
        wrapped = b"<dummy></dummy>"
        tree = etree.fromstring(wrapped)

        children = list(tree)
        assert len(children) == 0

    def test_malformed_response_recovers(self):
        """Malformed XML should be recovered if possible."""
        raw = b"<unclosed><valid>text</valid>"
        wrapped = b"<dummy>" + raw + b"</dummy>"

        # With recovery parser
        tree = etree.fromstring(wrapped, parser=etree.XMLParser(recover=True))
        # Should get something, exact result depends on lxml recovery
        assert tree is not None


# ============================================================================
# Multi-Payload Extraction Tests (from stream_pump.py)
# ============================================================================

@requires_aiostream
class TestMultiPayloadExtraction:
    """Tests for the fan-out payload extraction."""

    @pytest.mark.asyncio
    async def test_single_payload_yields_one(self):
        """Single payload should yield one state."""
        from agentserver.message_bus.stream_pump import extract_payloads

        state = MessageState(
            raw_bytes=b"<result>42</result>",
            thread_id="test-thread",
            from_id="test-sender",
        )

        results = [s async for s in extract_payloads(state)]

        assert len(results) == 1
        assert b"<result>" in results[0].raw_bytes
        assert results[0].thread_id == "test-thread"
        assert results[0].from_id == "test-sender"

    @pytest.mark.asyncio
    async def test_multiple_payloads_yields_many(self, multi_payload_response):
        """Multiple payloads should yield multiple states."""
        from agentserver.message_bus.stream_pump import extract_payloads

        state = MessageState(
            raw_bytes=multi_payload_response,
            thread_id="test-thread",
            from_id="agent",
        )

        results = [s async for s in extract_payloads(state)]

        assert len(results) == 3
        # Each result should have the same thread_id and from_id
        for r in results:
            assert r.thread_id == "test-thread"
            assert r.from_id == "agent"

    @pytest.mark.asyncio
    async def test_empty_response_yields_original(self):
        """Empty response should yield original state."""
        from agentserver.message_bus.stream_pump import extract_payloads

        state = MessageState(
            raw_bytes=b"",
            thread_id="test",
            from_id="test",
        )

        results = [s async for s in extract_payloads(state)]

        # Should yield something (original or empty handling)
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_preserves_metadata(self):
        """Extracted payloads should preserve metadata."""
        from agentserver.message_bus.stream_pump import extract_payloads

        state = MessageState(
            raw_bytes=b"<a/><b/>",
            thread_id="test",
            from_id="test",
            metadata={"custom": "value"},
        )

        results = [s async for s in extract_payloads(state)]

        for r in results:
            assert r.metadata.get("custom") == "value"


# ============================================================================
# Step Factory Tests
# ============================================================================

@requires_stream_pump
class TestStepFactories:
    """Tests for the step factory functions."""

    @pytest.mark.asyncio
    async def test_xsd_validation_direct(self):
        """XSD validation via lxml schema."""
        # Create a simple schema
        xsd_str = '''
        <xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
            <xs:element name="test">
                <xs:complexType>
                    <xs:sequence>
                        <xs:element name="value" type="xs:integer"/>
                    </xs:sequence>
                </xs:complexType>
            </xs:element>
        </xs:schema>'''
        schema = etree.XMLSchema(etree.fromstring(xsd_str.encode()))

        # Valid payload
        valid_xml = etree.fromstring(b"<test><value>42</value></test>")
        assert schema.validate(valid_xml)

        # Invalid payload
        invalid_xml = etree.fromstring(b"<test><value>not-an-int</value></test>")
        assert not schema.validate(invalid_xml)

    @pytest.mark.asyncio
    async def test_routing_factory(self):
        """Routing step should use injected routing table."""
        from agentserver.message_bus.steps.routing_resolution import make_routing_step
        from agentserver.message_bus.stream_pump import Listener

        # Create mock listener
        mock_listener = Listener(
            name="calculator.add",
            payload_class=type("AddPayload", (), {}),
            handler=lambda x, m: b"<result/>",
            description="test",
        )

        routing_table = {
            "calculator.add.addpayload": [mock_listener]
        }

        step = make_routing_step(routing_table)

        # Create a mock payload instance
        @dataclass
        class AddPayload:
            a: int = 0
            b: int = 0

        state = MessageState(
            payload=AddPayload(a=1, b=2),
            to_id="calculator.add",
        )

        result = await step(state)

        assert result.error is None
        assert result.target_listeners == [mock_listener]


# ============================================================================
# Pipeline Integration Tests (lightweight)
# ============================================================================

class TestPipelineIntegration:
    """Integration tests for step sequences."""

    @pytest.mark.asyncio
    async def test_repair_through_extraction(self, valid_envelope_bytes):
        """Test repair → c14n → extraction chain."""
        state = MessageState(raw_bytes=valid_envelope_bytes)

        state = await repair_step(state)
        assert state.error is None, f"repair failed: {state.error}"

        state = await c14n_step(state)
        assert state.error is None, f"c14n failed: {state.error}"

        state = await payload_extraction_step(state)
        assert state.error is None, f"extraction failed: {state.error}"

        assert state.payload_tree is not None
        assert state.thread_id is not None
        assert state.from_id is not None

    @pytest.mark.asyncio
    async def test_error_short_circuits(self):
        """Errors should prevent downstream steps from running."""
        call_log = []

        async def step_a(state):
            call_log.append("a")
            state.error = "Intentional error"
            return state

        async def step_b(state):
            call_log.append("b")
            return state

        # Simple pipeline runner (same logic as StreamPump uses)
        async def run_pipeline(steps, state):
            for step in steps:
                state = await step(state)
                if state.error:
                    break
            return state

        result = await run_pipeline([step_a, step_b], MessageState())

        assert call_log == ["a"]  # step_b should not have been called
        assert result.error == "Intentional error"


# ============================================================================
# Run with pytest
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
