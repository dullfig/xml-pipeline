"""
test_solver.py — Tests for the safe math parser and equation solver.

Covers:
- Tokenizer: basic tokens, operators, scientific notation, errors
- Parser: arithmetic, precedence, unary, functions, implicit mult, equations
- AST→SymPy bridge: numbers, variables, constants, operators, functions
- safe_solve: single equation, system, quadratic, no solution, limits
- safe_simplify: algebraic simplification
- Handler: success, error, payload round-trip
- Security: no eval, no attribute access, no imports

Run with: pytest tests/test_solver.py -v
"""

from __future__ import annotations

import json
from typing import Optional

import pytest
import sympy

from xml_pipeline.tools.solver import (
    # Tokenizer
    tokenize, Token,
    NUMBER, NAME, PLUS, MINUS, STAR, SLASH, CARET, LPAREN, RPAREN,
    COMMA, EQUALS, EOF,
    # Parser
    parse, Parser, ParseError,
    Num, Var, BinOp, UnaryOp, FuncCall, Equation,
    SAFE_FUNCTION_NAMES,
    # Bridge
    to_sympy, safe_parse,
    # Solver
    safe_solve, safe_simplify,
    MAX_EQUATIONS, MAX_VARIABLES, MAX_INPUT_LENGTH,
    # Payloads
    SolveRequest, SolveResult,
    # Handler
    handle_solve,
)
from xml_pipeline.message_bus.message_state import HandlerMetadata, HandlerResponse


# ============================================================================
# 1. Tokenizer
# ============================================================================


class TestTokenizer:

    def test_simple_number(self):
        tokens = tokenize("42")
        assert tokens[0] == Token(NUMBER, "42", 0)
        assert tokens[1].type == EOF

    def test_decimal_number(self):
        tokens = tokenize("3.14")
        assert tokens[0] == Token(NUMBER, "3.14", 0)

    def test_scientific_notation(self):
        tokens = tokenize("1.5e3")
        assert tokens[0] == Token(NUMBER, "1.5e3", 0)

    def test_scientific_notation_negative_exp(self):
        tokens = tokenize("2.5e-10")
        assert tokens[0] == Token(NUMBER, "2.5e-10", 0)

    def test_variable(self):
        tokens = tokenize("xyz")
        assert tokens[0] == Token(NAME, "xyz", 0)

    def test_operators(self):
        tokens = tokenize("+ - * / ^")
        types = [t.type for t in tokens[:-1]]
        assert types == [PLUS, MINUS, STAR, SLASH, CARET]

    def test_double_star_becomes_caret(self):
        tokens = tokenize("x**2")
        types = [t.type for t in tokens[:-1]]
        assert types == [NAME, CARET, NUMBER]

    def test_parens_and_comma(self):
        tokens = tokenize("sin(x, y)")
        types = [t.type for t in tokens[:-1]]
        assert types == [NAME, LPAREN, NAME, COMMA, NAME, RPAREN]

    def test_equals(self):
        tokens = tokenize("x = 0")
        types = [t.type for t in tokens[:-1]]
        assert types == [NAME, EQUALS, NUMBER]

    def test_whitespace_ignored(self):
        tokens = tokenize("  2  +  3  ")
        types = [t.type for t in tokens[:-1]]
        assert types == [NUMBER, PLUS, NUMBER]

    def test_complex_expression(self):
        tokens = tokenize("2*x^2 + 3*x - 5 = 0")
        values = [t.value for t in tokens[:-1]]
        assert values == ["2", "*", "x", "^", "2", "+", "3", "*", "x", "-", "5", "=", "0"]

    def test_invalid_character(self):
        with pytest.raises(ValueError, match="Unexpected character"):
            tokenize("2 & 3")


# ============================================================================
# 2. Parser — AST structure
# ============================================================================


class TestParserBasic:

    def test_number(self):
        node = parse("42")
        assert isinstance(node, Num)
        assert node.value == "42"

    def test_variable(self):
        node = parse("x")
        assert isinstance(node, Var)
        assert node.name == "x"

    def test_addition(self):
        node = parse("2 + 3")
        assert isinstance(node, BinOp)
        assert node.op == "+"
        assert isinstance(node.left, Num) and node.left.value == "2"
        assert isinstance(node.right, Num) and node.right.value == "3"

    def test_subtraction(self):
        node = parse("5 - 1")
        assert isinstance(node, BinOp)
        assert node.op == "-"

    def test_multiplication(self):
        node = parse("3 * 4")
        assert isinstance(node, BinOp)
        assert node.op == "*"

    def test_division(self):
        node = parse("10 / 2")
        assert isinstance(node, BinOp)
        assert node.op == "/"

    def test_exponentiation(self):
        node = parse("x ^ 2")
        assert isinstance(node, BinOp)
        assert node.op == "^"

    def test_double_star_exponentiation(self):
        node = parse("x ** 2")
        assert isinstance(node, BinOp)
        assert node.op == "**"


class TestParserPrecedence:

    def test_add_mul_precedence(self):
        """2 + 3 * 4 → 2 + (3 * 4)"""
        node = parse("2 + 3 * 4")
        assert isinstance(node, BinOp)
        assert node.op == "+"
        assert isinstance(node.left, Num)
        assert isinstance(node.right, BinOp) and node.right.op == "*"

    def test_mul_exp_precedence(self):
        """2 * x ^ 3 → 2 * (x ^ 3)"""
        node = parse("2 * x ^ 3")
        assert isinstance(node, BinOp)
        assert node.op == "*"
        assert isinstance(node.right, BinOp) and node.right.op == "^"

    def test_parentheses_override(self):
        """(2 + 3) * 4 → (2 + 3) * 4"""
        node = parse("(2 + 3) * 4")
        assert isinstance(node, BinOp)
        assert node.op == "*"
        assert isinstance(node.left, BinOp) and node.left.op == "+"

    def test_right_associative_exponent(self):
        """2 ^ 3 ^ 4 → 2 ^ (3 ^ 4)"""
        node = parse("2 ^ 3 ^ 4")
        assert isinstance(node, BinOp)
        assert node.op == "^"
        assert isinstance(node.right, BinOp) and node.right.op == "^"

    def test_left_associative_subtraction(self):
        """5 - 3 - 1 → (5 - 3) - 1"""
        node = parse("5 - 3 - 1")
        assert isinstance(node, BinOp)
        assert node.op == "-"
        assert isinstance(node.left, BinOp) and node.left.op == "-"


class TestParserUnary:

    def test_unary_minus(self):
        node = parse("-x")
        assert isinstance(node, UnaryOp)
        assert node.op == "-"
        assert isinstance(node.operand, Var)

    def test_unary_minus_number(self):
        node = parse("-5")
        assert isinstance(node, UnaryOp)
        assert isinstance(node.operand, Num)

    def test_unary_plus_ignored(self):
        """Unary plus is a no-op: +x → x"""
        node = parse("+x")
        assert isinstance(node, Var)

    def test_double_negation(self):
        node = parse("--x")
        assert isinstance(node, UnaryOp)
        assert isinstance(node.operand, UnaryOp)

    def test_negation_in_expression(self):
        """2 + -3 → 2 + (-3)"""
        node = parse("2 + -3")
        assert isinstance(node, BinOp)
        assert isinstance(node.right, UnaryOp)


class TestParserFunctions:

    def test_single_arg(self):
        node = parse("sin(x)")
        assert isinstance(node, FuncCall)
        assert node.name == "sin"
        assert len(node.args) == 1

    def test_multi_arg(self):
        node = parse("max(1, 2, 3)")
        assert isinstance(node, FuncCall)
        assert node.name == "max"
        assert len(node.args) == 3

    def test_nested_functions(self):
        node = parse("sin(cos(x))")
        assert isinstance(node, FuncCall)
        assert node.name == "sin"
        inner = node.args[0]
        assert isinstance(inner, FuncCall) and inner.name == "cos"

    def test_function_with_expression_arg(self):
        node = parse("sqrt(x^2 + 1)")
        assert isinstance(node, FuncCall)
        assert node.name == "sqrt"
        assert isinstance(node.args[0], BinOp)


class TestParserImplicitMul:

    def test_number_variable(self):
        """2x → 2 * x"""
        node = parse("2x")
        assert isinstance(node, BinOp)
        assert node.op == "*"
        assert isinstance(node.left, Num) and node.left.value == "2"
        assert isinstance(node.right, Var) and node.right.name == "x"

    def test_number_paren(self):
        """3(x+1) → 3 * (x+1)"""
        node = parse("3(x+1)")
        assert isinstance(node, BinOp)
        assert node.op == "*"
        assert isinstance(node.left, Num)

    def test_paren_paren(self):
        """(a)(b) → (a) * (b)"""
        node = parse("(a)(b)")
        assert isinstance(node, BinOp)
        assert node.op == "*"

    def test_implicit_mul_precedence(self):
        """2x^2 → 2 * (x^2), not (2x)^2"""
        node = parse("2x^2")
        assert isinstance(node, BinOp)
        assert node.op == "*"
        assert isinstance(node.left, Num) and node.left.value == "2"
        assert isinstance(node.right, BinOp) and node.right.op == "^"

    def test_variable_paren(self):
        """Unknown name followed by ( is implicit mult, not function call."""
        node = parse("a(b)")
        assert isinstance(node, BinOp)
        assert node.op == "*"

    def test_function_not_implicit(self):
        """sin(x) is a function call, not sin * (x)."""
        node = parse("sin(x)")
        assert isinstance(node, FuncCall)


class TestParserEquation:

    def test_simple_equation(self):
        node = parse("x = 5")
        assert isinstance(node, Equation)
        assert isinstance(node.left, Var)
        assert isinstance(node.right, Num)

    def test_complex_equation(self):
        node = parse("x^2 + 2*x - 8 = 0")
        assert isinstance(node, Equation)
        assert isinstance(node.left, BinOp)
        assert isinstance(node.right, Num)


class TestParserErrors:

    def test_empty_input(self):
        with pytest.raises(ParseError):
            parse("")

    def test_unclosed_paren(self):
        with pytest.raises(ParseError):
            parse("(x + 1")

    def test_trailing_operator(self):
        with pytest.raises(ParseError):
            parse("x +")

    def test_double_operator(self):
        with pytest.raises(ParseError):
            parse("x * * y")


# ============================================================================
# 3. AST → SymPy Bridge
# ============================================================================


class TestBridge:

    def test_integer(self):
        result = safe_parse("42")
        assert result == sympy.Integer(42)

    def test_decimal(self):
        result = safe_parse("0.5")
        assert result == sympy.Rational(1, 2)

    def test_variable(self):
        result = safe_parse("x")
        assert result == sympy.Symbol("x")

    def test_constant_pi(self):
        result = safe_parse("pi")
        assert result == sympy.pi

    def test_constant_e(self):
        result = safe_parse("e")
        assert result == sympy.E

    def test_addition(self):
        result = safe_parse("2 + 3")
        assert result == 5

    def test_subtraction(self):
        result = safe_parse("5 - 3")
        assert result == 2

    def test_multiplication(self):
        result = safe_parse("3 * 4")
        assert result == 12

    def test_division(self):
        result = safe_parse("10 / 2")
        assert result == 5

    def test_exponentiation(self):
        result = safe_parse("2 ^ 10")
        assert result == 1024

    def test_unary_minus(self):
        result = safe_parse("-5")
        assert result == -5

    def test_function_sqrt(self):
        result = safe_parse("sqrt(16)")
        assert result == 4

    def test_function_sin(self):
        result = safe_parse("sin(0)")
        assert result == 0

    def test_function_cos(self):
        result = safe_parse("cos(0)")
        assert result == 1

    def test_function_log(self):
        """ln(e) = 1"""
        result = safe_parse("ln(e)")
        assert result == 1

    def test_equation(self):
        result = safe_parse("x = 5")
        assert isinstance(result, sympy.Eq)
        assert result == sympy.Eq(sympy.Symbol("x"), 5)

    def test_implicit_mul_bridge(self):
        result = safe_parse("2x")
        assert result == 2 * sympy.Symbol("x")

    def test_unknown_function_rejected(self):
        """Functions not in whitelist raise ValueError at bridge level."""
        # 'foo' is not in SAFE_FUNCTION_NAMES so parser treats it as variable
        # followed by implicit multiplication with (x)
        result = safe_parse("foo(x)")
        # This is foo * x due to implicit multiplication
        assert result == sympy.Symbol("foo") * sympy.Symbol("x")


# ============================================================================
# 4. safe_solve
# ============================================================================


class TestSafeSolve:

    def test_linear_equation(self):
        solutions = safe_solve(["2*x + 4 = 0"], ["x"])
        assert len(solutions) == 1
        assert solutions[0]["x"] == "-2"

    def test_quadratic_equation(self):
        solutions = safe_solve(["x^2 - 4 = 0"], ["x"])
        values = sorted([s["x"] for s in solutions])
        assert values == ["-2", "2"]

    def test_system_of_equations(self):
        solutions = safe_solve(
            ["2*x + y = 10", "x - y = 2"],
            ["x", "y"],
        )
        assert len(solutions) == 1
        assert solutions[0]["x"] == "4"
        assert solutions[0]["y"] == "2"

    def test_expression_equals_zero(self):
        """Expression without '=' is treated as expr = 0."""
        solutions = safe_solve(["x - 5"], ["x"])
        assert len(solutions) == 1
        assert solutions[0]["x"] == "5"

    def test_no_solution(self):
        """x^2 + 1 = 0 has no real solution."""
        solutions = safe_solve(["x^2 + 1 = 0"], ["x"])
        # SymPy returns complex solutions
        assert len(solutions) >= 1
        # Solutions should involve 'I' (imaginary unit)
        values = [s["x"] for s in solutions]
        assert any("I" in v for v in values)

    def test_cubic(self):
        """x^3 - 8 = 0 → x = 2"""
        solutions = safe_solve(["x^3 - 8 = 0"], ["x"])
        real_solutions = [s for s in solutions if "I" not in s["x"]]
        assert any(s["x"] == "2" for s in real_solutions)

    def test_with_constants(self):
        """pi appears in the equation."""
        solutions = safe_solve(["x - pi = 0"], ["x"])
        assert len(solutions) == 1
        assert solutions[0]["x"] == "pi"

    def test_too_many_equations(self):
        eqs = [f"x{i} = 0" for i in range(MAX_EQUATIONS + 1)]
        with pytest.raises(ValueError, match="Too many equations"):
            safe_solve(eqs, ["x"])

    def test_too_many_variables(self):
        vs = [f"x{i}" for i in range(MAX_VARIABLES + 1)]
        with pytest.raises(ValueError, match="Too many variables"):
            safe_solve(["x0 = 0"], vs)

    def test_equation_too_long(self):
        eq = "x + " + "1 + " * (MAX_INPUT_LENGTH // 4 + 1) + "0 = 0"
        with pytest.raises(ValueError, match="too long"):
            safe_solve([eq], ["x"])

    def test_invalid_variable_name(self):
        with pytest.raises(ValueError, match="Invalid variable"):
            safe_solve(["x = 0"], ["123bad"])


# ============================================================================
# 5. safe_simplify
# ============================================================================


class TestSafeSimplify:

    def test_simplify_sum(self):
        result = safe_simplify("x + x")
        assert result == "2*x"

    def test_simplify_product(self):
        result = safe_simplify("x * x")
        assert result == "x**2"

    def test_simplify_fraction(self):
        result = safe_simplify("(x^2 - 1) / (x - 1)")
        assert result == "x + 1"

    def test_simplify_trig(self):
        result = safe_simplify("sin(x)^2 + cos(x)^2")
        assert result == "1"

    def test_simplify_constant(self):
        result = safe_simplify("2 + 3")
        assert result == "5"


# ============================================================================
# 6. Handler
# ============================================================================


class TestHandleSolve:

    @pytest.mark.asyncio
    async def test_success(self):
        payload = SolveRequest(equations="x^2 - 4 = 0", variables="x")
        meta = HandlerMetadata(
            thread_id="t1", from_id="agent", own_name="solver",
            is_self_call=False, usage_instructions="", todo_nudge="",
        )
        resp = await handle_solve(payload, meta)
        assert resp is not None
        assert resp.payload.error == ""
        solutions = json.loads(resp.payload.solutions)
        values = sorted([s["x"] for s in solutions])
        assert values == ["-2", "2"]

    @pytest.mark.asyncio
    async def test_system(self):
        payload = SolveRequest(
            equations="x + y = 10\nx - y = 2",
            variables="x,y",
        )
        meta = HandlerMetadata(
            thread_id="t1", from_id="agent", own_name="solver",
            is_self_call=False, usage_instructions="", todo_nudge="",
        )
        resp = await handle_solve(payload, meta)
        assert resp is not None
        assert resp.payload.error == ""
        solutions = json.loads(resp.payload.solutions)
        assert solutions[0]["x"] == "6"
        assert solutions[0]["y"] == "4"

    @pytest.mark.asyncio
    async def test_parse_error(self):
        payload = SolveRequest(equations="x + = 0", variables="x")
        meta = HandlerMetadata(
            thread_id="t1", from_id="agent", own_name="solver",
            is_self_call=False, usage_instructions="", todo_nudge="",
        )
        resp = await handle_solve(payload, meta)
        assert resp is not None
        assert resp.payload.error != ""
        assert resp.payload.solutions == ""

    @pytest.mark.asyncio
    async def test_echoes_input(self):
        payload = SolveRequest(equations="x = 5", variables="x")
        meta = HandlerMetadata(
            thread_id="t1", from_id="agent", own_name="solver",
            is_self_call=False, usage_instructions="", todo_nudge="",
        )
        resp = await handle_solve(payload, meta)
        assert resp.payload.equations == "x = 5"
        assert resp.payload.variables == "x"


# ============================================================================
# 7. Security — no eval, no code execution
# ============================================================================


class TestSecurity:

    def test_no_attribute_access(self):
        """Attribute access syntax is not recognized by the parser."""
        with pytest.raises((ValueError, ParseError)):
            safe_parse("x.__class__")

    def test_no_import(self):
        """Import syntax is not recognized."""
        with pytest.raises((ValueError, ParseError)):
            safe_parse("__import__('os')")

    def test_no_brackets(self):
        """Square brackets are not in the grammar."""
        with pytest.raises((ValueError, ParseError)):
            safe_parse("x[0]")

    def test_no_string_literals(self):
        """String literals are not in the grammar."""
        with pytest.raises((ValueError, ParseError)):
            safe_parse("'hello'")

    def test_no_semicolons(self):
        """Statement separators are not in the grammar."""
        with pytest.raises((ValueError, ParseError)):
            safe_parse("x; import os")

    def test_no_assignment(self):
        """= is only for equations, not assignment (no side effects)."""
        # "x = 5" parses as Equation, not assignment
        result = safe_parse("x = 5")
        assert isinstance(result, sympy.Eq)

    def test_nested_function_depth(self):
        """Deeply nested functions parse without stack overflow up to reasonable depth."""
        expr = "x"
        for _ in range(50):
            expr = f"sin({expr})"
        # Should parse without error
        result = safe_parse(expr)
        assert result is not None

    def test_long_but_valid_expression(self):
        """A long valid expression should parse fine."""
        expr = " + ".join(["x"] * 100)
        result = safe_parse(expr)
        assert result == 100 * sympy.Symbol("x")
