"""
Calculate tool - evaluate mathematical expressions safely.

Uses simpleeval for safe expression evaluation with a restricted set of
math functions and constants. Also provides @xmlify payload classes and
a handler for use as a message-bus listener.
"""

import math
from dataclasses import dataclass
from typing import Any

from simpleeval import simple_eval

from third_party.xmlable import xmlify

from xml_pipeline.message_bus.message_state import HandlerMetadata, HandlerResponse

from .base import ToolResult, tool

# ---------------------------------------------------------------------------
# Allowed functions & constants
# ---------------------------------------------------------------------------

MATH_FUNCTIONS = {
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "sqrt": math.sqrt,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "asin": math.asin,
    "acos": math.acos,
    "atan": math.atan,
    "log": math.log,
    "log10": math.log10,
    "log2": math.log2,
    "exp": math.exp,
    "floor": math.floor,
    "ceil": math.ceil,
    "pow": pow,
}

MATH_CONSTANTS = {
    "pi": math.pi,
    "e": math.e,
    "tau": math.tau,
    "inf": math.inf,
}

# ---------------------------------------------------------------------------
# Core evaluation
# ---------------------------------------------------------------------------


def safe_eval(expression: str) -> Any:
    """Safely evaluate a mathematical expression."""
    return simple_eval(
        expression,
        functions=MATH_FUNCTIONS,
        names=MATH_CONSTANTS,
    )


# ---------------------------------------------------------------------------
# @tool wrapper (existing API)
# ---------------------------------------------------------------------------


@tool
async def calculate(expression: str) -> ToolResult:
    """
    Evaluate a mathematical expression using Python syntax.

    Supported:
    - Basic ops: + - * / // % **
    - Comparisons: < > <= >= == !=
    - Functions: abs, round, min, max, sqrt, sin, cos, tan, log, log10, exp, floor, ceil
    - Constants: pi, e, tau, inf
    - Parentheses for grouping

    Examples:
    - "2 + 2" → 4
    - "(10 + 5) * 3" → 45
    - "sqrt(16) + pi" → 7.141592...
    - "max(1, 2, 3)" → 3
    """
    try:
        result = safe_eval(expression)
        return ToolResult(success=True, data=result)
    except Exception as e:
        return ToolResult(success=False, error=str(e))


# ---------------------------------------------------------------------------
# Message-bus payload classes
# ---------------------------------------------------------------------------


@xmlify
@dataclass
class Calculate:
    """Evaluate a mathematical expression using Python syntax."""
    expression: str


@xmlify
@dataclass
class CalculateResult:
    """Result of a mathematical calculation."""
    expression: str   # Echo back the input
    result: str       # String representation ("4", "3.14159...")
    error: str        # Empty on success, message on failure


# ---------------------------------------------------------------------------
# Message-bus handler
# ---------------------------------------------------------------------------


async def handle_calculate(
    payload: Calculate, metadata: HandlerMetadata
) -> HandlerResponse:
    """Handler for the calculator listener — always responds to caller."""
    try:
        value = safe_eval(payload.expression)
        return HandlerResponse.respond(
            payload=CalculateResult(
                expression=payload.expression,
                result=str(value),
                error="",
            )
        )
    except Exception as e:
        return HandlerResponse.respond(
            payload=CalculateResult(
                expression=payload.expression,
                result="",
                error=str(e),
            )
        )
