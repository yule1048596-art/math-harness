from __future__ import annotations

import ast
import re
from collections.abc import Mapping

import sympy as sp

from math_harness.errors import UnsafeExpression
from math_harness.models import SymbolProperty


def build_symbol_table(
    variable: str,
    parameters: list[str],
    assumptions: dict[str, list[SymbolProperty]],
) -> dict[str, sp.Symbol]:
    """Create one consistent, real-valued symbol table for a verification target."""

    property_names = {
        SymbolProperty.REAL: "real",
        SymbolProperty.POSITIVE: "positive",
        SymbolProperty.NEGATIVE: "negative",
        SymbolProperty.NONZERO: "nonzero",
        SymbolProperty.INTEGER: "integer",
        SymbolProperty.NONNEGATIVE: "nonnegative",
        SymbolProperty.NONPOSITIVE: "nonpositive",
    }
    result: dict[str, sp.Symbol] = {}
    for name in (variable, *parameters):
        properties: dict[str, bool] = {"real": True}
        for value in assumptions.get(name, []):
            properties[property_names[value]] = True
        result[name] = sp.Symbol(name, **properties)
    return result


class SafeMathParser:
    """Parse a deliberately small mathematical expression language.

    SymPy's convenient string parsers ultimately evaluate generated Python.
    This parser instead traverses Python's AST and constructs SymPy objects
    from an explicit allowlist.
    """

    MAX_TEXT_LENGTH = 1000
    MAX_AST_NODES = 250
    MAX_INTEGER_DIGITS = 80
    MAX_LITERAL_EXPONENT = 100

    FUNCTIONS: Mapping[str, object] = {
        "sqrt": sp.sqrt,
        "exp": sp.exp,
        "log": sp.log,
        "sin": sp.sin,
        "cos": sp.cos,
        "tan": sp.tan,
        "asin": sp.asin,
        "acos": sp.acos,
        "atan": sp.atan,
        "sinh": sp.sinh,
        "cosh": sp.cosh,
        "tanh": sp.tanh,
        "gamma": sp.gamma,
        "factorial": sp.factorial,
        "Abs": sp.Abs,
        "Min": sp.Min,
        "Max": sp.Max,
        "Rational": sp.Rational,
    }

    CONSTANTS: Mapping[str, object] = {
        "pi": sp.pi,
        "E": sp.E,
        "oo": sp.oo,
    }

    _SYMBOL_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")

    def parse(
        self,
        text: str,
        symbol_names: set[str] | Mapping[str, sp.Symbol] | None = None,
    ) -> sp.Expr:
        if len(text) > self.MAX_TEXT_LENGTH:
            raise UnsafeExpression("expression is too long")

        try:
            tree = ast.parse(text.strip(), mode="eval")
        except SyntaxError as exc:
            raise UnsafeExpression(f"invalid expression syntax: {exc.msg}") from exc

        nodes = list(ast.walk(tree))
        if len(nodes) > self.MAX_AST_NODES:
            raise UnsafeExpression("expression is too complex")

        if isinstance(symbol_names, Mapping):
            allowed_symbols = dict(symbol_names)
        else:
            allowed_symbols = {
                name: sp.Symbol(name, real=True) for name in (symbol_names or set())
            }
        if any(not self._SYMBOL_PATTERN.fullmatch(name) for name in allowed_symbols):
            raise UnsafeExpression("invalid symbol name")
        if any(
            not isinstance(symbol, sp.Symbol) or symbol.name != name
            for name, symbol in allowed_symbols.items()
        ):
            raise UnsafeExpression("invalid symbol mapping")

        return self._convert(tree.body, allowed_symbols)

    def _convert(self, node: ast.AST, symbols: Mapping[str, sp.Symbol]) -> sp.Expr:
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                raise UnsafeExpression("only numeric constants are allowed")
            if isinstance(node.value, int):
                if len(str(abs(node.value))) > self.MAX_INTEGER_DIGITS:
                    raise UnsafeExpression("integer literal is too large")
                return sp.Integer(node.value)
            return sp.Rational(str(node.value))

        if isinstance(node, ast.Name):
            if node.id in self.CONSTANTS:
                return self.CONSTANTS[node.id]  # type: ignore[return-value]
            if node.id in symbols:
                return symbols[node.id]
            raise UnsafeExpression(f"unknown symbol: {node.id}")

        if isinstance(node, ast.UnaryOp):
            operand = self._convert(node.operand, symbols)
            if isinstance(node.op, ast.UAdd):
                return operand
            if isinstance(node.op, ast.USub):
                return -operand
            raise UnsafeExpression("unsupported unary operator")

        if isinstance(node, ast.BinOp):
            left = self._convert(node.left, symbols)
            right = self._convert(node.right, symbols)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                # Keep the original denominator unevaluated so exact-equivalence
                # verification can detect removable holes such as x/x at x=0.
                return sp.Mul(
                    left,
                    sp.Pow(right, -1, evaluate=False),
                    evaluate=False,
                )
            if isinstance(node.op, ast.Pow):
                if (
                    isinstance(node.right, ast.Constant)
                    and isinstance(node.right.value, int)
                    and abs(node.right.value) > self.MAX_LITERAL_EXPONENT
                ):
                    raise UnsafeExpression("literal exponent is too large")
                return left**right
            raise UnsafeExpression("unsupported binary operator")

        if isinstance(node, ast.Call):
            if (
                not isinstance(node.func, ast.Name)
                or node.func.id not in self.FUNCTIONS
            ):
                raise UnsafeExpression("function is not allowed")
            if node.keywords:
                raise UnsafeExpression("keyword arguments are not allowed")
            args = [self._convert(arg, symbols) for arg in node.args]
            if len(args) > 4:
                raise UnsafeExpression("too many function arguments")
            function = self.FUNCTIONS[node.func.id]
            try:
                return function(*args)  # type: ignore[operator, no-any-return]
            except (TypeError, ValueError) as exc:
                raise UnsafeExpression(f"invalid arguments for {node.func.id}") from exc

        raise UnsafeExpression(f"unsupported syntax: {node.__class__.__name__}")
