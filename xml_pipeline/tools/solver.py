"""
Solver tool — symbolic equation solving with a safe math parser.

Uses a hand-written tokenizer + Pratt parser to convert math strings
into an AST, then bridges to SymPy objects *without* eval(). This is
the safe replacement for sympify(), which uses eval() internally.

The parser supports:
- Arithmetic: + - * / ^ **
- Implicit multiplication: 2x, 3(x+1), (a)(b)
- Variables: any [a-zA-Z_][a-zA-Z0-9_]* not in the function/constant list
- Functions: sin, cos, tan, asin, acos, atan, sqrt, log, ln, exp, abs, floor, ceil
- Constants: pi, e, i, inf
- Equations: x^2 + 2*x - 8 = 0
- Unary minus: -x, -(x+1)

Also provides @xmlify payload classes and a handler for use as a
message-bus listener.
"""

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union

from third_party.xmlable import xmlify

from xml_pipeline.message_bus.message_state import HandlerMetadata, HandlerResponse

from .base import ToolResult, tool

# Guarded import — sympy is optional
try:
    import sympy
    SYMPY_AVAILABLE = True
except ImportError:
    SYMPY_AVAILABLE = False


# ═══════════════════════════════════════════════════════════════════════
# Tokenizer
# ═══════════════════════════════════════════════════════════════════════

# Token types
NUMBER = "NUMBER"
NAME = "NAME"
PLUS = "PLUS"
MINUS = "MINUS"
STAR = "STAR"
SLASH = "SLASH"
CARET = "CARET"
LPAREN = "LPAREN"
RPAREN = "RPAREN"
COMMA = "COMMA"
EQUALS = "EQUALS"
EOF = "EOF"


@dataclass
class Token:
    type: str
    value: str
    pos: int = 0


# Regex patterns for tokenization
_TOKEN_RE = re.compile(r"""
    (?P<NUMBER>   \d+(?:\.\d+)?(?:[eE][+-]?\d+)? )
  | (?P<NAME>     [a-zA-Z_][a-zA-Z0-9_]* )
  | (?P<DSTAR>    \*\* )
  | (?P<STAR>     \* )
  | (?P<SLASH>    / )
  | (?P<CARET>    \^ )
  | (?P<PLUS>     \+ )
  | (?P<MINUS>    - )
  | (?P<LPAREN>   \( )
  | (?P<RPAREN>   \) )
  | (?P<COMMA>    , )
  | (?P<EQUALS>   = )
  | (?P<WS>       \s+ )
""", re.VERBOSE)


def tokenize(text: str) -> List[Token]:
    """Tokenize a math expression string into a list of Tokens."""
    tokens: List[Token] = []
    pos = 0
    for m in _TOKEN_RE.finditer(text):
        if m.start() != pos:
            bad = text[pos:m.start()]
            raise ValueError(f"Unexpected character at position {pos}: {bad!r}")
        pos = m.end()
        kind = m.lastgroup
        value = m.group()
        if kind == "WS":
            continue
        if kind == "DSTAR":
            tokens.append(Token(CARET, "**", m.start()))
        elif kind == "NUMBER":
            tokens.append(Token(NUMBER, value, m.start()))
        elif kind == "NAME":
            tokens.append(Token(NAME, value, m.start()))
        else:
            tokens.append(Token(kind, value, m.start()))
    if pos != len(text):
        raise ValueError(f"Unexpected character at position {pos}: {text[pos:]!r}")
    tokens.append(Token(EOF, "", len(text)))
    return tokens


# ═══════════════════════════════════════════════════════════════════════
# AST Nodes
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class Num:
    """Numeric literal — stores raw string for precise SymPy conversion."""
    value: str

@dataclass
class Var:
    """Variable reference."""
    name: str

@dataclass
class BinOp:
    """Binary operation."""
    op: str
    left: Any
    right: Any

@dataclass
class UnaryOp:
    """Unary operation (negation)."""
    op: str
    operand: Any

@dataclass
class FuncCall:
    """Function call with arguments."""
    name: str
    args: list

@dataclass
class Equation:
    """Equation (left = right)."""
    left: Any
    right: Any


# ═══════════════════════════════════════════════════════════════════════
# Pratt Parser
# ═══════════════════════════════════════════════════════════════════════

# Binding powers for infix operators (higher = tighter)
_INFIX_BP: Dict[str, int] = {
    PLUS: 20,
    MINUS: 20,
    STAR: 30,
    SLASH: 30,
    CARET: 40,
}

# Implicit multiplication binding power (between * and ^)
_IMPLICIT_MUL_BP = 35

# Prefix binding power for unary minus
_PREFIX_BP = 50

# Function names recognized by the parser (not treated as variables)
SAFE_FUNCTION_NAMES = frozenset({
    "sin", "cos", "tan", "asin", "acos", "atan",
    "sqrt", "log", "ln", "log10", "log2",
    "exp", "abs", "floor", "ceil",
    "min", "max",
})


class ParseError(ValueError):
    """Raised when the parser encounters invalid syntax."""


class Parser:
    """Pratt parser for mathematical expressions."""

    def __init__(self, tokens: List[Token]) -> None:
        self._tokens = tokens
        self._pos = 0

    def parse(self) -> Any:
        """Parse the full expression, optionally with '=' for equations."""
        node = self._expr(0)
        if self._peek().type == EQUALS:
            self._advance()
            right = self._expr(0)
            node = Equation(node, right)
        if self._peek().type != EOF:
            tok = self._peek()
            raise ParseError(f"Unexpected token at position {tok.pos}: {tok.value!r}")
        return node

    def _peek(self) -> Token:
        return self._tokens[self._pos]

    def _advance(self) -> Token:
        tok = self._tokens[self._pos]
        self._pos += 1
        return tok

    def _expect(self, token_type: str) -> Token:
        tok = self._advance()
        if tok.type != token_type:
            raise ParseError(
                f"Expected {token_type} at position {tok.pos}, got {tok.type}: {tok.value!r}"
            )
        return tok

    def _expr(self, min_bp: int) -> Any:
        """Parse an expression with minimum binding power."""
        left = self._prefix()

        while True:
            tok = self._peek()

            # Explicit infix operator
            bp = _INFIX_BP.get(tok.type)
            if bp is not None and bp >= min_bp:
                self._advance()
                # Right-associative for exponentiation
                rbp = bp if tok.type == CARET else bp + 1
                right = self._expr(rbp)
                left = BinOp(tok.value, left, right)
                continue

            # Implicit multiplication: number, variable, or '(' following
            # an atom without an explicit operator
            if tok.type in (NUMBER, NAME, LPAREN) and _IMPLICIT_MUL_BP >= min_bp:
                right = self._expr(_IMPLICIT_MUL_BP + 1)
                left = BinOp("*", left, right)
                continue

            break

        return left

    def _prefix(self) -> Any:
        """Parse a prefix expression (atom, unary op, function call)."""
        tok = self._advance()

        if tok.type == NUMBER:
            return Num(tok.value)

        if tok.type == NAME:
            # Function call: name followed by '('
            if tok.value in SAFE_FUNCTION_NAMES and self._peek().type == LPAREN:
                return self._func_call(tok.value)
            return Var(tok.name if hasattr(tok, 'name') else tok.value)

        if tok.type == MINUS:
            operand = self._expr(_PREFIX_BP)
            return UnaryOp("-", operand)

        if tok.type == PLUS:
            # Unary plus — just parse the operand
            return self._expr(_PREFIX_BP)

        if tok.type == LPAREN:
            inner = self._expr(0)
            self._expect(RPAREN)
            return inner

        raise ParseError(
            f"Unexpected token at position {tok.pos}: {tok.type} {tok.value!r}"
        )

    def _func_call(self, name: str) -> FuncCall:
        """Parse function arguments: name '(' expr (',' expr)* ')'."""
        self._expect(LPAREN)
        args: list = []
        if self._peek().type != RPAREN:
            args.append(self._expr(0))
            while self._peek().type == COMMA:
                self._advance()
                args.append(self._expr(0))
        self._expect(RPAREN)
        return FuncCall(name, args)


def parse(text: str) -> Any:
    """Parse a math expression string into an AST node."""
    tokens = tokenize(text)
    return Parser(tokens).parse()


# ═══════════════════════════════════════════════════════════════════════
# AST → SymPy Bridge
# ═══════════════════════════════════════════════════════════════════════

# Function whitelist — maps parser names to SymPy functions
_SYMPY_FUNCTIONS: Dict[str, Any] = {}

# Constant whitelist — maps names to SymPy constants
_SYMPY_CONSTANTS: Dict[str, Any] = {}


def _init_sympy_tables() -> None:
    """Populate function/constant tables (called on first use)."""
    global _SYMPY_FUNCTIONS, _SYMPY_CONSTANTS
    if not SYMPY_AVAILABLE or _SYMPY_FUNCTIONS:
        return
    _SYMPY_FUNCTIONS = {
        "sin": sympy.sin,
        "cos": sympy.cos,
        "tan": sympy.tan,
        "asin": sympy.asin,
        "acos": sympy.acos,
        "atan": sympy.atan,
        "sqrt": sympy.sqrt,
        "log": sympy.log,
        "ln": sympy.log,
        "log10": lambda x: sympy.log(x, 10),
        "log2": lambda x: sympy.log(x, 2),
        "exp": sympy.exp,
        "abs": sympy.Abs,
        "floor": sympy.floor,
        "ceil": sympy.ceiling,
        "min": sympy.Min,
        "max": sympy.Max,
    }
    _SYMPY_CONSTANTS = {
        "pi": sympy.pi,
        "e": sympy.E,
        "i": sympy.I,
        "E": sympy.E,
        "inf": sympy.oo,
        "tau": 2 * sympy.pi,
    }


def to_sympy(node: Any) -> Any:
    """
    Convert an AST node to a SymPy expression.

    No eval() is used — each node type maps directly to a SymPy
    constructor. Unknown node types raise ValueError.
    """
    if not SYMPY_AVAILABLE:
        raise RuntimeError(
            "sympy is required for the solver tool. "
            "Install with: pip install sympy"
        )
    _init_sympy_tables()

    if isinstance(node, Num):
        s = node.value
        if "." in s or "e" in s.lower():
            return sympy.Rational(s)
        return sympy.Integer(s)

    if isinstance(node, Var):
        const = _SYMPY_CONSTANTS.get(node.name)
        if const is not None:
            return const
        return sympy.Symbol(node.name)

    if isinstance(node, BinOp):
        left = to_sympy(node.left)
        right = to_sympy(node.right)
        if node.op == "+":
            return sympy.Add(left, right)
        if node.op == "-":
            return sympy.Add(left, sympy.Mul(-1, right))
        if node.op == "*":
            return sympy.Mul(left, right)
        if node.op == "/":
            return sympy.Mul(left, sympy.Pow(right, -1))
        if node.op in ("^", "**"):
            return sympy.Pow(left, right)
        raise ValueError(f"Unknown operator: {node.op}")

    if isinstance(node, UnaryOp):
        if node.op == "-":
            return sympy.Mul(-1, to_sympy(node.operand))
        raise ValueError(f"Unknown unary operator: {node.op}")

    if isinstance(node, FuncCall):
        fn = _SYMPY_FUNCTIONS.get(node.name)
        if fn is None:
            raise ValueError(f"Unknown function: {node.name}")
        args = [to_sympy(a) for a in node.args]
        return fn(*args)

    if isinstance(node, Equation):
        return sympy.Eq(to_sympy(node.left), to_sympy(node.right))

    raise ValueError(f"Unknown AST node: {type(node).__name__}")


def safe_parse(text: str) -> Any:
    """Parse a math string into a SymPy expression. No eval()."""
    return to_sympy(parse(text))


# ═══════════════════════════════════════════════════════════════════════
# Solver API
# ═══════════════════════════════════════════════════════════════════════

MAX_EQUATIONS = 20
MAX_VARIABLES = 20
MAX_INPUT_LENGTH = 10_000  # characters per equation string


def safe_solve(
    equations: List[str],
    variables: List[str],
) -> List[Dict[str, str]]:
    """
    Safely parse and solve a system of equations.

    Args:
        equations: Math expression strings. Strings containing '=' are
                   treated as equations; others are treated as "expr = 0".
        variables: Variable names to solve for.

    Returns:
        List of solution dicts, e.g. [{"x": "2"}, {"x": "-4"}].

    Raises:
        RuntimeError: If sympy is not installed.
        ValueError: On parse errors or limit violations.
    """
    if not SYMPY_AVAILABLE:
        raise RuntimeError("sympy is required for the solver tool")

    if len(equations) > MAX_EQUATIONS:
        raise ValueError(f"Too many equations ({len(equations)} > {MAX_EQUATIONS})")
    if len(variables) > MAX_VARIABLES:
        raise ValueError(f"Too many variables ({len(variables)} > {MAX_VARIABLES})")

    _init_sympy_tables()

    # Parse variables
    syms = []
    for v in variables:
        if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', v):
            raise ValueError(f"Invalid variable name: {v!r}")
        syms.append(sympy.Symbol(v))

    # Parse equations
    eqs = []
    for eq_str in equations:
        if len(eq_str) > MAX_INPUT_LENGTH:
            raise ValueError(f"Equation too long ({len(eq_str)} > {MAX_INPUT_LENGTH})")
        expr = safe_parse(eq_str)
        if not isinstance(expr, sympy.Eq):
            expr = sympy.Eq(expr, 0)
        eqs.append(expr)

    # Solve
    solutions = sympy.solve(eqs, syms, dict=True)

    # Format results
    result = []
    for sol in solutions:
        formatted = {}
        for sym, val in sol.items():
            formatted[str(sym)] = str(val)
        result.append(formatted)

    return result


def safe_simplify(expression: str) -> str:
    """
    Safely parse and simplify a mathematical expression.

    Returns the simplified expression as a string.
    """
    if not SYMPY_AVAILABLE:
        raise RuntimeError("sympy is required for the solver tool")
    expr = safe_parse(expression)
    simplified = sympy.simplify(expr)
    return str(simplified)


# ═══════════════════════════════════════════════════════════════════════
# @tool wrapper
# ═══════════════════════════════════════════════════════════════════════

@tool
async def solve(equations: str, variables: str) -> ToolResult:
    """
    Solve a system of equations symbolically.

    Args:
        equations: Newline-separated equation strings.
                   Use '=' for equations, or expressions are treated as "= 0".
        variables: Comma-separated variable names to solve for.

    Examples:
        equations="x^2 + 2x - 8 = 0", variables="x"
        → [{"x": "2"}, {"x": "-4"}]

        equations="2x + y = 10\\nx - y = 2", variables="x,y"
        → [{"x": "4", "y": "2"}]
    """
    try:
        eq_list = [e.strip() for e in equations.strip().split("\n") if e.strip()]
        var_list = [v.strip() for v in variables.strip().split(",") if v.strip()]
        solutions = safe_solve(eq_list, var_list)
        return ToolResult(success=True, data=solutions)
    except Exception as exc:
        return ToolResult(success=False, error=str(exc))


# ═══════════════════════════════════════════════════════════════════════
# Message-bus payload classes
# ═══════════════════════════════════════════════════════════════════════

@xmlify
@dataclass
class SolveRequest:
    """Solve a system of equations symbolically."""
    equations: str    # Newline-separated equation strings
    variables: str    # Comma-separated variable names


@xmlify
@dataclass
class SolveResult:
    """Result of symbolic equation solving."""
    equations: str    # Echo back input
    variables: str    # Echo back input
    solutions: str    # JSON-formatted solution list
    error: str        # Empty on success


# ═══════════════════════════════════════════════════════════════════════
# Message-bus handler
# ═══════════════════════════════════════════════════════════════════════

async def handle_solve(
    payload: SolveRequest, metadata: HandlerMetadata
) -> HandlerResponse:
    """Handler for the solver listener — always responds to caller."""
    try:
        import json

        eq_list = [e.strip() for e in payload.equations.strip().split("\n") if e.strip()]
        var_list = [v.strip() for v in payload.variables.strip().split(",") if v.strip()]
        solutions = safe_solve(eq_list, var_list)

        return HandlerResponse.respond(
            payload=SolveResult(
                equations=payload.equations,
                variables=payload.variables,
                solutions=json.dumps(solutions),
                error="",
            )
        )
    except Exception as exc:
        return HandlerResponse.respond(
            payload=SolveResult(
                equations=payload.equations,
                variables=payload.variables,
                solutions="",
                error=str(exc),
            )
        )
