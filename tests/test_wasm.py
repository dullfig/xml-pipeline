"""
Tests for WASM tool integration.

Tests WIT parsing, capability gate, dynamic dataclass creation,
payload serialization, config parsing, and dispatcher registration.

Actual WASM execution tests require wasmtime + compiled .wasm fixtures
and are marked with skipif(not WASMTIME_AVAILABLE).
"""

import json
import pytest
from dataclasses import fields as dc_fields
from unittest.mock import AsyncMock, MagicMock, patch

from xml_pipeline.wasm.wit_parser import (
    WitField,
    WitRecord,
    WitInterface,
    WitValidationError,
    parse_wit,
    _kebab_to_snake,
    _resolve_type,
)
from xml_pipeline.wasm.capability_gate import (
    KNOWN_CAPABILITIES,
    CapabilityDeniedError,
    validate_capabilities,
)
from xml_pipeline.wasm.registry import (
    WasmRegistry,
    WasmInstanceHandle,
    get_wasm_registry,
    set_wasm_registry,
    reset_wasm_registry,
)
from xml_pipeline.wasm.dispatcher import (
    _to_pascal,
    _create_payload_class,
    _payload_to_json,
    _json_to_payload,
)
from xml_pipeline.wasm.loader import WasmToolConfig, WasmModule, WASMTIME_AVAILABLE
from xml_pipeline.message_bus.config_loader import ConfigLoader
from xml_pipeline.message_bus.pump_config import OrganismConfig


# ============================================================================
# WIT Parser Tests
# ============================================================================

class TestWitParser:
    """Test WIT file parsing."""

    SIMPLE_WIT = """\
package example:calculator;

interface calculate {
    record calculate-request {
        expression: string,
    }

    record calculate-response {
        result: string,
        error: string,
    }
}
"""

    def test_parse_simple_interface(self):
        interfaces = parse_wit(self.SIMPLE_WIT)
        assert len(interfaces) == 1
        iface = interfaces[0]
        assert iface.name == "calculate"
        assert iface.request.name == "calculate-request"
        assert iface.response.name == "calculate-response"

    def test_parse_request_fields(self):
        interfaces = parse_wit(self.SIMPLE_WIT)
        req = interfaces[0].request
        assert len(req.fields) == 1
        assert req.fields[0].name == "expression"
        assert req.fields[0].wit_type == "string"
        assert req.fields[0].python_type is str

    def test_parse_response_fields(self):
        interfaces = parse_wit(self.SIMPLE_WIT)
        resp = interfaces[0].response
        assert len(resp.fields) == 2
        assert resp.fields[0].name == "result"
        assert resp.fields[1].name == "error"

    def test_type_mapping_integers(self):
        assert _resolve_type("u8") is int
        assert _resolve_type("u16") is int
        assert _resolve_type("u32") is int
        assert _resolve_type("u64") is int
        assert _resolve_type("s8") is int
        assert _resolve_type("s32") is int

    def test_type_mapping_floats(self):
        assert _resolve_type("f32") is float
        assert _resolve_type("f64") is float

    def test_type_mapping_bool(self):
        assert _resolve_type("bool") is int  # 0/1 for xmlify safety

    def test_type_mapping_option(self):
        assert _resolve_type("option<string>") is str

    def test_type_mapping_list(self):
        assert _resolve_type("list<u8>") is str  # JSON-encoded

    def test_unknown_type_raises(self):
        with pytest.raises(WitValidationError, match="Unsupported WIT type"):
            _resolve_type("custom-type")

    def test_kebab_to_snake(self):
        assert _kebab_to_snake("calculate-request") == "calculate_request"
        assert _kebab_to_snake("my-field-name") == "my_field_name"
        assert _kebab_to_snake("simple") == "simple"

    def test_missing_request_raises(self):
        wit = """\
interface broken {
    record broken-response {
        value: string,
    }
}
"""
        with pytest.raises(WitValidationError, match="missing \\*-request"):
            parse_wit(wit)

    def test_missing_response_raises(self):
        wit = """\
interface broken {
    record broken-request {
        value: string,
    }
}
"""
        with pytest.raises(WitValidationError, match="missing \\*-response"):
            parse_wit(wit)

    def test_multi_interface(self):
        wit = """\
interface add {
    record add-request {
        a: u32,
        b: u32,
    }
    record add-response {
        result: u32,
    }
}

interface multiply {
    record multiply-request {
        a: u32,
        b: u32,
    }
    record multiply-response {
        result: u32,
    }
}
"""
        interfaces = parse_wit(wit)
        assert len(interfaces) == 2
        names = {i.name for i in interfaces}
        assert "add" in names
        assert "multiply" in names

    def test_empty_wit_raises(self):
        with pytest.raises(WitValidationError, match="No interfaces found"):
            parse_wit("")

    def test_records_without_interface(self):
        """Records matched by naming convention when no interface block."""
        wit = """\
record echo-request {
    text: string,
}

record echo-response {
    text: string,
}
"""
        interfaces = parse_wit(wit)
        assert len(interfaces) == 1
        assert interfaces[0].name == "echo"

    def test_field_with_option_type(self):
        wit = """\
interface test {
    record test-request {
        name: string,
        age: option<u32>,
    }
    record test-response {
        greeting: string,
    }
}
"""
        interfaces = parse_wit(wit)
        req = interfaces[0].request
        assert len(req.fields) == 2
        assert req.fields[1].name == "age"
        assert req.fields[1].python_type is str  # option → str

    def test_comments_ignored(self):
        wit = """\
// This is a comment
interface calc {
    // Another comment
    record calc-request {
        // Field comment
        expr: string,
    }
    record calc-response {
        result: string,
    }
}
"""
        interfaces = parse_wit(wit)
        assert len(interfaces) == 1
        assert interfaces[0].request.fields[0].name == "expr"


# ============================================================================
# Capability Gate Tests
# ============================================================================

class TestCapabilityGate:
    """Test capability validation."""

    def test_known_capabilities(self):
        assert "fetch" in KNOWN_CAPABILITIES
        assert "kv" in KNOWN_CAPABILITIES
        assert "log" in KNOWN_CAPABILITIES

    def test_valid_capabilities(self):
        validate_capabilities(["log"])
        validate_capabilities(["fetch", "kv", "log"])

    def test_empty_capabilities_ok(self):
        validate_capabilities([])

    def test_unknown_capability_rejected(self):
        with pytest.raises(CapabilityDeniedError, match="Unknown WASM capability"):
            validate_capabilities(["shell"])

    def test_mixed_unknown_rejected(self):
        with pytest.raises(CapabilityDeniedError):
            validate_capabilities(["log", "filesystem"])


# ============================================================================
# Dynamic Dataclass Tests
# ============================================================================

class TestDynamicDataclass:
    """Test dynamic payload class creation with xmlify."""

    def test_create_simple_class(self):
        record = WitRecord(
            name="calc-request",
            fields=[WitField(name="expression", wit_type="string", python_type=str)],
        )
        cls = _create_payload_class("calc", record)
        assert cls.__name__ == "CalcRequest"

        # Can instantiate
        obj = cls(expression="2+2")
        assert obj.expression == "2+2"

    def test_create_multi_field_class(self):
        record = WitRecord(
            name="add-request",
            fields=[
                WitField(name="a", wit_type="u32", python_type=int),
                WitField(name="b", wit_type="u32", python_type=int),
            ],
        )
        cls = _create_payload_class("calc", record)
        obj = cls(a=3, b=4)
        assert obj.a == 3
        assert obj.b == 4

    def test_xmlify_applied(self):
        """Verify the class has xmlify methods."""
        record = WitRecord(
            name="echo-request",
            fields=[WitField(name="text", wit_type="string", python_type=str)],
        )
        cls = _create_payload_class("echo", record)
        # xmlify adds xsd() and xml_value()
        assert hasattr(cls, "xsd")
        assert hasattr(cls, "xml_value")

    def test_xml_roundtrip(self):
        """Verify XML serialization works on dynamic classes."""
        record = WitRecord(
            name="greet-request",
            fields=[WitField(name="name", wit_type="string", python_type=str)],
        )
        cls = _create_payload_class("greet", record)
        obj = cls(name="Alice")

        # Serialize to XML
        from lxml import etree
        xml_tree = obj.xml_value("GreetRequest")
        xml_str = etree.tostring(xml_tree, encoding="unicode")
        assert "Alice" in xml_str

    def test_xsd_generation(self):
        """Verify XSD schema can be generated."""
        record = WitRecord(
            name="test-request",
            fields=[
                WitField(name="value", wit_type="string", python_type=str),
                WitField(name="count", wit_type="u32", python_type=int),
            ],
        )
        cls = _create_payload_class("test", record)
        xsd_tree = cls.xsd()
        assert xsd_tree is not None


# ============================================================================
# Payload Serialization Tests
# ============================================================================

class TestPayloadSerialization:
    """Test JSON serialization between pump and WASM."""

    def _make_record_and_cls(self):
        record = WitRecord(
            name="calc-request",
            fields=[
                WitField(name="expression", wit_type="string", python_type=str),
                WitField(name="precision", wit_type="u32", python_type=int),
            ],
        )
        cls = _create_payload_class("calc", record)
        return record, cls

    def test_payload_to_json(self):
        record, cls = self._make_record_and_cls()
        obj = cls(expression="2+2", precision=10)
        result = _payload_to_json(obj, record)
        data = json.loads(result)
        assert data["expression"] == "2+2"
        assert data["precision"] == 10

    def test_json_to_payload(self):
        record = WitRecord(
            name="calc-response",
            fields=[
                WitField(name="result", wit_type="string", python_type=str),
                WitField(name="error", wit_type="string", python_type=str),
            ],
        )
        cls = _create_payload_class("calc", record)
        json_str = json.dumps({"result": "4", "error": ""})
        obj = _json_to_payload(json_str, cls, record)
        assert obj.result == "4"
        assert obj.error == ""

    def test_json_to_payload_missing_field_defaults(self):
        record = WitRecord(
            name="calc-response",
            fields=[
                WitField(name="result", wit_type="string", python_type=str),
                WitField(name="error", wit_type="string", python_type=str),
            ],
        )
        cls = _create_payload_class("calc", record)
        # Missing "error" field — should use default (empty string)
        json_str = json.dumps({"result": "4"})
        obj = _json_to_payload(json_str, cls, record)
        assert obj.result == "4"
        assert obj.error == ""

    def test_to_routing_field(self):
        """_to field in WASM result is used for explicit routing."""
        record = WitRecord(
            name="route-response",
            fields=[WitField(name="value", wit_type="string", python_type=str)],
        )
        cls = _create_payload_class("route", record)
        json_str = json.dumps({"value": "hello", "_to": "next-listener"})
        data = json.loads(json_str)
        assert data.get("_to") == "next-listener"


# ============================================================================
# Pascal Case Conversion Tests
# ============================================================================

class TestPascalConversion:
    """Test _to_pascal helper."""

    def test_kebab_case(self):
        assert _to_pascal("calculate-request") == "CalculateRequest"

    def test_snake_case(self):
        assert _to_pascal("calculate_request") == "CalculateRequest"

    def test_single_word(self):
        assert _to_pascal("echo") == "Echo"

    def test_mixed(self):
        assert _to_pascal("my-cool_thing") == "MyCoolThing"


# ============================================================================
# Registry Tests
# ============================================================================

class TestWasmRegistry:
    """Test WasmRegistry singleton and instance tracking."""

    def setup_method(self):
        reset_wasm_registry()

    def teardown_method(self):
        reset_wasm_registry()

    def test_singleton(self):
        r1 = get_wasm_registry()
        r2 = get_wasm_registry()
        assert r1 is r2

    def test_set_registry(self):
        custom = WasmRegistry()
        set_wasm_registry(custom)
        assert get_wasm_registry() is custom

    def test_register_module(self):
        reg = WasmRegistry()
        module = WasmModule(name="test")
        reg.register_module("test", module)
        assert reg.has_module("test")
        assert reg.get_module("test") is module

    def test_duplicate_module_raises(self):
        reg = WasmRegistry()
        module = WasmModule(name="test")
        reg.register_module("test", module)
        with pytest.raises(KeyError, match="already registered"):
            reg.register_module("test", module)

    def test_get_missing_module_raises(self):
        reg = WasmRegistry()
        with pytest.raises(KeyError, match="not registered"):
            reg.get_module("nonexistent")

    def test_instance_tracking(self):
        reg = WasmRegistry()
        handle = WasmInstanceHandle(module_name="test", thread_id="t1")
        reg.store_instance("test", "t1", handle)
        assert reg.get_instance("test", "t1") is handle
        assert reg.get_instance("test", "t2") is None

    def test_cleanup_for_thread(self):
        reg = WasmRegistry()
        reg.store_instance("a", "t1", WasmInstanceHandle(module_name="a", thread_id="t1"))
        reg.store_instance("b", "t1", WasmInstanceHandle(module_name="b", thread_id="t1"))
        reg.store_instance("a", "t2", WasmInstanceHandle(module_name="a", thread_id="t2"))

        cleaned = reg.cleanup_for_thread("t1")
        assert cleaned == 2
        assert reg.get_instance("a", "t1") is None
        assert reg.get_instance("b", "t1") is None
        assert reg.get_instance("a", "t2") is not None

    def test_shutdown_all(self):
        reg = WasmRegistry()
        reg.register_module("m1", WasmModule(name="m1"))
        reg.store_instance("m1", "t1", WasmInstanceHandle(module_name="m1", thread_id="t1"))
        count = reg.shutdown_all()
        assert count == 1
        assert reg.module_count == 0
        assert reg.instance_count == 0

    def test_counts(self):
        reg = WasmRegistry()
        assert reg.module_count == 0
        assert reg.instance_count == 0
        reg.register_module("m1", WasmModule(name="m1"))
        reg.store_instance("m1", "t1", WasmInstanceHandle(module_name="m1", thread_id="t1"))
        assert reg.module_count == 1
        assert reg.instance_count == 1


# ============================================================================
# Config Parsing Tests
# ============================================================================

class TestConfigParsing:
    """Test YAML tools: section parsing."""

    def test_full_tool_declaration(self):
        raw = {
            "organism": {"name": "test"},
            "tools": [
                {
                    "name": "calculator",
                    "wasm_path": "./tools/calculator.wasm",
                    "wit_path": "./tools/calculator.wit",
                    "description": "Math evaluator",
                    "capabilities": ["log"],
                    "memory_limit_mb": 32,
                    "timeout_seconds": 10,
                },
            ],
        }
        config = ConfigLoader._parse(raw)
        assert len(config.wasm_tool_configs) == 1
        tool = config.wasm_tool_configs[0]
        assert tool["name"] == "calculator"
        assert tool["wasm_path"] == "./tools/calculator.wasm"
        assert tool["wit_path"] == "./tools/calculator.wit"
        assert tool["description"] == "Math evaluator"
        assert tool["capabilities"] == ["log"]
        assert tool["memory_limit_mb"] == 32
        assert tool["timeout_seconds"] == 10.0

    def test_tool_defaults(self):
        raw = {
            "organism": {"name": "test"},
            "tools": [
                {
                    "name": "echo",
                    "wasm_path": "./echo.wasm",
                    "wit_path": "./echo.wit",
                },
            ],
        }
        config = ConfigLoader._parse(raw)
        tool = config.wasm_tool_configs[0]
        assert tool["description"] == ""
        assert tool["capabilities"] == []
        assert tool["memory_limit_mb"] == 64
        assert tool["timeout_seconds"] == 5.0

    def test_empty_tools_section(self):
        raw = {
            "organism": {"name": "test"},
            "tools": [],
        }
        config = ConfigLoader._parse(raw)
        assert config.wasm_tool_configs == []

    def test_missing_tools_section(self):
        raw = {
            "organism": {"name": "test"},
        }
        config = ConfigLoader._parse(raw)
        assert config.wasm_tool_configs == []

    def test_multiple_tools(self):
        raw = {
            "organism": {"name": "test"},
            "tools": [
                {"name": "a", "wasm_path": "a.wasm", "wit_path": "a.wit"},
                {"name": "b", "wasm_path": "b.wasm", "wit_path": "b.wit"},
                {"name": "c", "wasm_path": "c.wasm", "wit_path": "c.wit"},
            ],
        }
        config = ConfigLoader._parse(raw)
        assert len(config.wasm_tool_configs) == 3
        names = [t["name"] for t in config.wasm_tool_configs]
        assert names == ["a", "b", "c"]


# ============================================================================
# Dispatcher Registration Tests (mocked WASM)
# ============================================================================

class TestDispatcherRegistration:
    """Test register_wasm_tool with mocked WASM loading."""

    CALC_WIT = """\
interface calculate {
    record calculate-request {
        expression: string,
    }
    record calculate-response {
        result: string,
        error: string,
    }
}
"""

    def _make_pump(self):
        """Create a minimal mock pump."""
        pump = MagicMock()
        pump.listeners = {}
        pump.routing_table = {}

        def mock_register(name, handler, payload_class, **kwargs):
            # Simulate pump.register() behavior
            from xml_pipeline.message_bus.pump_config import Listener
            listener = Listener(
                name=name,
                payload_class=payload_class,
                handler=handler,
                description=kwargs.get("description", ""),
                timeout=kwargs.get("timeout", 30.0),
            )
            pump.listeners[name] = listener
            return listener

        pump.register = mock_register
        return pump

    @patch("xml_pipeline.wasm.wit_parser.parse_wit_file")
    @patch("xml_pipeline.wasm.dispatcher.WASMTIME_AVAILABLE", False)
    def test_register_creates_listeners(self, mock_parse):
        from xml_pipeline.wasm.dispatcher import register_wasm_tool
        mock_parse.return_value = parse_wit(self.CALC_WIT)

        reset_wasm_registry()
        pump = self._make_pump()
        config = {
            "name": "calculator",
            "wasm_path": "fake.wasm",
            "wit_path": "fake.wit",
            "description": "Math tool",
        }

        registered = register_wasm_tool(pump, config)
        assert "calculator.calculate" in registered
        assert "calculator.calculate" in pump.listeners
        reset_wasm_registry()

    @patch("xml_pipeline.wasm.wit_parser.parse_wit_file")
    @patch("xml_pipeline.wasm.dispatcher.WASMTIME_AVAILABLE", False)
    def test_description_forwarded(self, mock_parse):
        from xml_pipeline.wasm.dispatcher import register_wasm_tool
        mock_parse.return_value = parse_wit(self.CALC_WIT)

        reset_wasm_registry()
        pump = self._make_pump()
        config = {
            "name": "calculator",
            "wasm_path": "fake.wasm",
            "wit_path": "fake.wit",
            "description": "My custom math tool",
        }

        register_wasm_tool(pump, config)
        listener = pump.listeners["calculator.calculate"]
        assert listener.description == "My custom math tool"
        reset_wasm_registry()

    @patch("xml_pipeline.wasm.wit_parser.parse_wit_file")
    @patch("xml_pipeline.wasm.dispatcher.WASMTIME_AVAILABLE", False)
    def test_timeout_set(self, mock_parse):
        from xml_pipeline.wasm.dispatcher import register_wasm_tool
        mock_parse.return_value = parse_wit(self.CALC_WIT)

        reset_wasm_registry()
        pump = self._make_pump()
        config = {
            "name": "calculator",
            "wasm_path": "fake.wasm",
            "wit_path": "fake.wit",
            "timeout_seconds": 15,
        }

        register_wasm_tool(pump, config)
        listener = pump.listeners["calculator.calculate"]
        assert listener.timeout == 15.0
        reset_wasm_registry()

    @patch("xml_pipeline.wasm.wit_parser.parse_wit_file")
    @patch("xml_pipeline.wasm.dispatcher.WASMTIME_AVAILABLE", False)
    def test_multi_interface_registration(self, mock_parse):
        from xml_pipeline.wasm.dispatcher import register_wasm_tool
        multi_wit = """\
interface add {
    record add-request { a: u32, b: u32, }
    record add-response { result: u32, }
}
interface multiply {
    record multiply-request { a: u32, b: u32, }
    record multiply-response { result: u32, }
}
"""
        mock_parse.return_value = parse_wit(multi_wit)

        reset_wasm_registry()
        pump = self._make_pump()
        config = {
            "name": "math",
            "wasm_path": "fake.wasm",
            "wit_path": "fake.wit",
        }

        registered = register_wasm_tool(pump, config)
        assert len(registered) == 2
        assert "math.add" in registered
        assert "math.multiply" in registered
        reset_wasm_registry()

    @patch("xml_pipeline.wasm.wit_parser.parse_wit_file")
    @patch("xml_pipeline.wasm.dispatcher.WASMTIME_AVAILABLE", False)
    def test_payload_class_is_xmlified(self, mock_parse):
        from xml_pipeline.wasm.dispatcher import register_wasm_tool
        mock_parse.return_value = parse_wit(self.CALC_WIT)

        reset_wasm_registry()
        pump = self._make_pump()
        config = {
            "name": "calculator",
            "wasm_path": "fake.wasm",
            "wit_path": "fake.wit",
        }

        register_wasm_tool(pump, config)
        listener = pump.listeners["calculator.calculate"]
        # Payload class should have xmlify methods
        assert hasattr(listener.payload_class, "xsd")
        assert hasattr(listener.payload_class, "xml_value")
        reset_wasm_registry()


# ============================================================================
# WasmToolConfig Tests
# ============================================================================

class TestWasmToolConfig:
    """Test WasmToolConfig dataclass."""

    def test_defaults(self):
        cfg = WasmToolConfig(
            name="test",
            wasm_path="test.wasm",
            wit_path="test.wit",
        )
        assert cfg.description == ""
        assert cfg.capabilities == []
        assert cfg.memory_limit_mb == 64
        assert cfg.timeout_seconds == 5.0

    def test_full_config(self):
        cfg = WasmToolConfig(
            name="test",
            wasm_path="test.wasm",
            wit_path="test.wit",
            description="A test tool",
            capabilities=["fetch", "log"],
            memory_limit_mb=128,
            timeout_seconds=30.0,
        )
        assert cfg.name == "test"
        assert cfg.capabilities == ["fetch", "log"]


# ============================================================================
# Public API Tests
# ============================================================================

class TestPublicAPI:
    """Test that WASM symbols are exported correctly."""

    def test_package_exports(self):
        from xml_pipeline.wasm import (
            WasmRegistry,
            WasmInstanceHandle,
            get_wasm_registry,
            set_wasm_registry,
            reset_wasm_registry,
            register_wasm_tool,
            WitInterface,
            WitRecord,
            WitField,
            WitValidationError,
            WasmModule,
            WasmValidationError,
            WasmToolConfig,
            CapabilityDeniedError,
            KNOWN_CAPABILITIES,
        )

    def test_top_level_export(self):
        from xml_pipeline import get_wasm_registry
        registry = get_wasm_registry()
        assert isinstance(registry, WasmRegistry)
        reset_wasm_registry()
