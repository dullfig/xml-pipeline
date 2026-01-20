"""
Calculate tool - evaluate mathematical expressions safely.

Uses a restricted AST evaluator for safe expression evaluation.
No external dependencies required.
"""

from __future__ import annotations

import ast
import math
import operator
from typing import Any, Union

from .base import tool, ToolResult


# Allowed operations
OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}

COMPARISONS = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
}

# Allowed functions
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

# Allowed constants
MATH_CONSTANTS = {
    "pi": math.pi,
    "e": math.e,
    "tau": math.tau,
    "inf": math.inf,
}


class SafeEvaluator(ast.NodeVisitor):
    """Safely evaluate mathematical expressions using AST."""

    def visit(self, node: ast.AST) -> Any:
        """Visit a node."""
        method = f"visit_{node.__class__.__name__}"
        visitor = getattr(self, method, self.generic_visit)
        return visitor(node)

    def generic_visit(self, node: ast.AST) -> None:
        """Reject unknown node types."""
        raise ValueError(f"Unsupported operation: {node.__class__.__name__}")

    def visit_Expression(self, node: ast.Expression) -> Any:
        return self.visit(node.body)

    def visit_Constant(self, node: ast.Constant) -> Union[int, float]:
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError(f"Unsupported constant type: {type(node.value)}")

    def visit_Num(self, node: ast.Num) -> Union[int, float]:
        # Python 3.7 compatibility
        return node.n

    def visit_Name(self, node: ast.Name) -> Any:
        if node.id in MATH_CONSTANTS:
            return MATH_CONSTANTS[node.id]
        raise ValueError(f"Unknown variable: {node.id}")

    def visit_BinOp(self, node: ast.BinOp) -> Any:
        op_type = type(node.op)
        if op_type not in OPERATORS:
            raise ValueError(f"Unsupported operator: {op_type.__name__}")
        left = self.visit(node.left)
        right = self.visit(node.right)
        return OPERATORS[op_type](left, right)

    def visit_UnaryOp(self, node: ast.UnaryOp) -> Any:
        op_type = type(node.op)
        if op_type not in OPERATORS:
            raise ValueError(f"Unsupported operator: {op_type.__name__}")
        operand = self.visit(node.operand)
        return OPERATORS[op_type](operand)

    def visit_Compare(self, node: ast.Compare) -> bool:
        left = self.visit(node.left)
        for op, comparator in zip(node.ops, node.comparators):
            op_type = type(op)
            if op_type not in COMPARISONS:
                raise ValueError(f"Unsupported comparison: {op_type.__name__}")
            right = self.visit(comparator)
            if not COMPARISONS[op_type](left, right):
                return False
            left = right
        return True

    def visit_Call(self, node: ast.Call) -> Any:
        if not isinstance(node.func, ast.Name):
            raise ValueError("Only named function calls are allowed")
        func_name = node.func.id
        if func_name not in MATH_FUNCTIONS:
            raise ValueError(f"Unknown function: {func_name}")
        args = [self.visit(arg) for arg in node.args]
        return MATH_FUNCTIONS[func_name](*args)

    def visit_IfExp(self, node: ast.IfExp) -> Any:
        # Support ternary: a if condition else b
        test = self.visit(node.test)
        if test:
            return self.visit(node.body)
        return self.visit(node.orelse)


def safe_eval(expression: str) -> Any:
    """Safely evaluate a mathematical expression."""
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as e:
        raise ValueError(f"Invalid syntax: {e}")
    evaluator = SafeEvaluator()
    return evaluator.visit(tree)


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
    - Ternary expressions: a if condition else b

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
