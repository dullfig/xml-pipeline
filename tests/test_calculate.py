"""Tests for the calculator tool, handler, and payload classes."""

import math

import pytest
from lxml import etree

from third_party.xmlable import parse_string

from xml_pipeline.message_bus.message_state import HandlerMetadata, HandlerResponse
from xml_pipeline.tools.base import ToolResult, get_tool_registry
from xml_pipeline.tools.calculate import (
    MATH_CONSTANTS,
    MATH_FUNCTIONS,
    Calculate,
    CalculateResult,
    calculate,
    handle_calculate,
    safe_eval,
)


# ── Helpers ──────────────────────────────────────────────────────────────

def _metadata() -> HandlerMetadata:
    """Minimal metadata fixture for handler tests."""
    return HandlerMetadata(
        thread_id="test-thread-000",
        from_id="caller",
    )


# ═══════════════════════════════════════════════════════════════════════════
# TestSafeEval — simpleeval integration
# ═══════════════════════════════════════════════════════════════════════════

class TestSafeEval:
    """Core safe_eval() function backed by simpleeval."""

    def test_basic_addition(self) -> None:
        assert safe_eval("2 + 2") == 4

    def test_operator_precedence(self) -> None:
        assert safe_eval("(10 + 5) * 3") == 45

    def test_exponentiation(self) -> None:
        assert safe_eval("2 ** 10") == 1024

    def test_floor_division(self) -> None:
        assert safe_eval("7 // 2") == 3

    def test_modulo(self) -> None:
        assert safe_eval("10 % 3") == 1

    def test_negative_numbers(self) -> None:
        assert safe_eval("-5 + 3") == -2

    def test_float_arithmetic(self) -> None:
        assert abs(safe_eval("0.1 + 0.2") - 0.3) < 1e-9

    def test_sqrt_function(self) -> None:
        assert safe_eval("sqrt(16)") == 4.0

    def test_max_function(self) -> None:
        assert safe_eval("max(1, 2, 3)") == 3

    def test_min_function(self) -> None:
        assert safe_eval("min(5, 3, 8)") == 3

    def test_sin_zero(self) -> None:
        assert safe_eval("sin(0)") == 0.0

    def test_cos_zero(self) -> None:
        assert safe_eval("cos(0)") == 1.0

    def test_floor_function(self) -> None:
        assert safe_eval("floor(3.7)") == 3

    def test_ceil_function(self) -> None:
        assert safe_eval("ceil(3.2)") == 4

    def test_log_function(self) -> None:
        assert abs(safe_eval("log(e)") - 1.0) < 1e-9

    def test_constant_pi(self) -> None:
        assert safe_eval("pi") == math.pi

    def test_constant_e(self) -> None:
        assert safe_eval("e") == math.e

    def test_constant_tau(self) -> None:
        assert safe_eval("tau") == math.tau

    def test_comparison_gt(self) -> None:
        assert safe_eval("2 > 1") is True

    def test_comparison_lt(self) -> None:
        assert safe_eval("1 < 2") is True

    def test_division_by_zero(self) -> None:
        with pytest.raises(Exception):
            safe_eval("1 / 0")

    def test_unknown_function(self) -> None:
        with pytest.raises(Exception):
            safe_eval("unknown(42)")

    def test_invalid_syntax(self) -> None:
        with pytest.raises(Exception):
            safe_eval("2 +")

    def test_all_functions_present(self) -> None:
        """Verify expected function set is available."""
        expected = {
            "abs", "round", "min", "max", "sqrt",
            "sin", "cos", "tan", "asin", "acos", "atan",
            "log", "log10", "log2", "exp",
            "floor", "ceil", "pow",
        }
        assert set(MATH_FUNCTIONS.keys()) == expected

    def test_all_constants_present(self) -> None:
        """Verify expected constant set is available."""
        assert set(MATH_CONSTANTS.keys()) == {"pi", "e", "tau", "inf"}


# ═══════════════════════════════════════════════════════════════════════════
# TestCalculateTool — @tool decorator
# ═══════════════════════════════════════════════════════════════════════════

class TestCalculateTool:
    """The @tool-decorated calculate() async function."""

    async def test_success_returns_tool_result(self) -> None:
        result = await calculate(expression="2 + 2")
        assert isinstance(result, ToolResult)
        assert result.success is True
        assert result.data == 4

    async def test_error_returns_tool_result(self) -> None:
        result = await calculate(expression="2 +")
        assert isinstance(result, ToolResult)
        assert result.success is False
        assert result.error  # non-empty error string

    async def test_registered_in_tool_registry(self) -> None:
        registry = get_tool_registry()
        tool_obj = registry.get("calculate")
        assert tool_obj is not None
        assert tool_obj.name == "calculate"


# ═══════════════════════════════════════════════════════════════════════════
# TestHandleCalculate — message-bus handler
# ═══════════════════════════════════════════════════════════════════════════

class TestHandleCalculate:
    """The handle_calculate() async handler for the message bus."""

    async def test_success_response(self) -> None:
        payload = Calculate(expression="2 + 2")
        resp = await handle_calculate(payload, _metadata())
        assert isinstance(resp, HandlerResponse)
        assert resp.is_response is True
        assert isinstance(resp.payload, CalculateResult)
        assert resp.payload.result == "4"
        assert resp.payload.error == ""
        assert resp.payload.expression == "2 + 2"

    async def test_error_response(self) -> None:
        payload = Calculate(expression="bad syntax %%")
        resp = await handle_calculate(payload, _metadata())
        assert isinstance(resp, HandlerResponse)
        assert resp.is_response is True
        assert isinstance(resp.payload, CalculateResult)
        assert resp.payload.result == ""
        assert resp.payload.error != ""
        assert resp.payload.expression == "bad syntax %%"

    async def test_echoes_expression(self) -> None:
        expr = "sqrt(16) + pi"
        payload = Calculate(expression=expr)
        resp = await handle_calculate(payload, _metadata())
        assert resp.payload.expression == expr

    async def test_always_responds(self) -> None:
        """Handler always uses .respond(), never forwards."""
        for expr in ["2 + 2", "bad"]:
            payload = Calculate(expression=expr)
            resp = await handle_calculate(payload, _metadata())
            assert resp.is_response is True


# ═══════════════════════════════════════════════════════════════════════════
# TestPayloadRoundtrip — xmlify serialization
# ═══════════════════════════════════════════════════════════════════════════

class TestPayloadRoundtrip:
    """@xmlify dataclass → XML → back."""

    def test_calculate_round_trip(self) -> None:
        original = Calculate(expression="2 + 2")
        xml_tree = original.xml_value("Calculate")
        xml_str = etree.tostring(xml_tree, encoding="unicode")
        restored = parse_string(Calculate, xml_str)
        assert restored.expression == original.expression

    def test_calculate_result_round_trip(self) -> None:
        original = CalculateResult(expression="pi", result="3.141592653589793", error="")
        xml_tree = original.xml_value("CalculateResult")
        xml_str = etree.tostring(xml_tree, encoding="unicode")
        restored = parse_string(CalculateResult, xml_str)
        assert restored.expression == original.expression
        assert restored.result == original.result
        assert restored.error == original.error

    def test_calculate_result_with_error_round_trip(self) -> None:
        original = CalculateResult(expression="bad", result="", error="Invalid syntax")
        xml_tree = original.xml_value("CalculateResult")
        xml_str = etree.tostring(xml_tree, encoding="unicode")
        restored = parse_string(CalculateResult, xml_str)
        assert restored.error == "Invalid syntax"
        assert restored.result == ""
