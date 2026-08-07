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
    # 矩阵与求和的字面量本身就要占不少节点，250 装不下现实输入。仍然有界。
    MAX_AST_NODES = 600
    MAX_INTEGER_DIGITS = 80
    MAX_LITERAL_EXPONENT = 100
    #: 列表/元组的长度上限，挡住用容器堆出巨大表达式。
    MAX_CONTAINER_ITEMS = 32
    #: 矩阵元素总数上限。本机实测 12×12 符号行列式就能跑过 8 秒。
    MAX_MATRIX_ELEMENTS = 64
    #: `Sum` / `Product` 数值上下界的跨度上限。
    #:
    #: 这条是真正挡住挂死的那一条：`Sum(1/k**2, (k, 1, 10**7))` 的字面量指数只有 7，
    #: 长度也很短，既有的节点数与指数上限全都拦不住它，但求值会跑到分钟级。
    #: 符号上下界不受限——`Sum(f, (k, 1, n))` 正是要求闭式的常见写法。
    MAX_SERIES_TERMS = 10_000

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
        # 组合数学
        "binomial": sp.binomial,
        # 复变与复数法平面几何：把几何化归成代数之后就可实例化检验
        "re": sp.re,
        "im": sp.im,
        "conjugate": sp.conjugate,
        "arg": sp.arg,
        "sign": sp.sign,
        # 数论与取整
        "gcd": sp.gcd,
        "lcm": sp.lcm,
        "floor": sp.floor,
        "ceiling": sp.ceiling,
        "Mod": sp.Mod,
        # 线性代数
        "Matrix": sp.Matrix,
        "eye": sp.eye,
        "zeros": sp.zeros,
        "det": lambda m: m.det(),
        "trace": lambda m: m.trace(),
        "transpose": lambda m: m.T,
        # 微积分。
        #
        # 只放 `diff`，**不放 `integrate`**：验证积分的方式是把答案求导回去比对，
        # 从来不需要真的算积分，而 `integrate` 恰恰是最容易挂死的那个函数。
        # `Integral` 是未求值类，只用于把命题写出来，不触发求值。
        "diff": sp.diff,
        "Integral": sp.Integral,
        "Derivative": sp.Derivative,
        "Sum": sp.Sum,
        "Product": sp.Product,
        "limit": sp.limit,
        # 逻辑
        "And": sp.And,
        "Or": sp.Or,
        "Not": sp.Not,
        "Implies": sp.Implies,
        "Equivalent": sp.Equivalent,
        "Eq": sp.Eq,
    }

    CONSTANTS: Mapping[str, object] = {
        "pi": sp.pi,
        "E": sp.E,
        "oo": sp.oo,
        "I": sp.I,
        "true": sp.true,
        "false": sp.false,
    }

    #: 关系运算符 → SymPy 关系类。谓词断言（`im(z) == 0`、`x > 0`）要用它们。
    #:
    #: 必须显式建 `Eq`：SymPy 对象上的 `==` 返回 Python 布尔的结构相等，不是命题。
    COMPARISONS: Mapping[type[ast.cmpop], object] = {
        ast.Eq: sp.Eq,
        ast.NotEq: sp.Ne,
        ast.Lt: sp.Lt,
        ast.LtE: sp.Le,
        ast.Gt: sp.Gt,
        ast.GtE: sp.Ge,
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

        # 列表与元组：矩阵的行、`Sum` 的 (变量, 下界, 上界) 都要用。
        # 它们只作为容器存在，不引入任何求值能力。
        if isinstance(node, (ast.List, ast.Tuple)):
            if len(node.elts) > self.MAX_CONTAINER_ITEMS:
                raise UnsafeExpression("container is too large")
            return [self._convert(item, symbols) for item in node.elts]

        if isinstance(node, ast.Compare):
            if len(node.ops) != 1 or len(node.comparators) != 1:
                raise UnsafeExpression("only a single comparison is allowed")
            operator = self.COMPARISONS.get(type(node.ops[0]))
            if operator is None:
                raise UnsafeExpression("unsupported comparison operator")
            left = self._convert(node.left, symbols)
            right = self._convert(node.comparators[0], symbols)
            return operator(left, right)  # type: ignore[operator, no-any-return]

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
            self._enforce_call_limits(node.func.id, args)
            function = self.FUNCTIONS[node.func.id]
            try:
                return function(*args)  # type: ignore[operator, no-any-return]
            except (TypeError, ValueError, AttributeError, IndexError) as exc:
                raise UnsafeExpression(f"invalid arguments for {node.func.id}") from exc

        raise UnsafeExpression(f"unsupported syntax: {node.__class__.__name__}")

    def _enforce_call_limits(self, name: str, args: list[object]) -> None:
        """对能引爆求值代价的调用单独限规模。

        节点数和字符数上限管的是**表达式有多大**，管不住**求值有多贵**。这两类的代价
        全在求值：一个 12×12 符号矩阵和一个上界 1e7 的求和，写出来都很短。
        """

        if name in {"Matrix", "eye", "zeros"}:
            self._enforce_matrix_limit(name, args)
        elif name in {"Sum", "Product"}:
            self._enforce_series_limit(args)

    def _enforce_matrix_limit(self, name: str, args: list[object]) -> None:
        if name in {"eye", "zeros"}:
            for arg in args:
                if isinstance(arg, sp.Integer) and int(arg) ** 2 > (
                    self.MAX_MATRIX_ELEMENTS
                ):
                    raise UnsafeExpression("matrix is too large")
            return
        if not args or not isinstance(args[0], list):
            return
        rows = args[0]
        columns = max(
            (len(row) if isinstance(row, list) else 1 for row in rows),
            default=0,
        )
        if len(rows) * max(columns, 1) > self.MAX_MATRIX_ELEMENTS:
            raise UnsafeExpression("matrix is too large")

    def _enforce_series_limit(self, args: list[object]) -> None:
        """求和/连乘的数值上下界跨度必须有界。

        符号上下界放行——`Sum(f, (k, 1, n))` 正是求闭式的写法，本身不触发求值。
        """

        for arg in args[1:]:
            if not isinstance(arg, list) or len(arg) != 3:
                continue
            low, high = arg[1], arg[2]
            if (
                isinstance(low, sp.Integer)
                and isinstance(high, sp.Integer)
                and int(high) - int(low) > self.MAX_SERIES_TERMS
            ):
                raise UnsafeExpression("series range is too large")
