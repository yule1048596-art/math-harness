from __future__ import annotations

import pytest
import sympy as sp

from math_harness.errors import UnsafeExpression
from math_harness.math_parser import SafeMathParser

PARSER = SafeMathParser()
SYMBOLS = {name: sp.Symbol(name) for name in ("x", "y", "k", "n", "a", "b", "c", "z")}


def parse(text: str):
    return PARSER.parse(text, SYMBOLS)


# --- 安全属性（本阶段最重要的部分）-------------------------------------
#
# 解析器存在的唯一理由是不执行任意 Python。扩容加了容器、比较和一批新函数，
# 每一样都可能开口子，所以这一组测试比功能测试更重要。


@pytest.mark.parametrize(
    "hostile",
    [
        "__import__('os')",
        "open('/etc/passwd')",
        "eval('1+1')",
        "exec('x=1')",
        "x.__class__",
        "x.__class__.__mro__",
        "(lambda: 1)()",
        "[i for i in range(3)]",
        "{'a': 1}",
        "x if x else x",
        "f'{x}'",
        "x;y",
        "globals()",
        "getattr(x, 'foo')",
        "type(x)",
        "sp.sympify('1')",
        "Matrix.__init__",
    ],
)
def test_hostile_input_is_rejected(hostile: str):
    with pytest.raises(UnsafeExpression):
        parse(hostile)


def test_unknown_function_is_rejected():
    with pytest.raises(UnsafeExpression, match="not allowed"):
        parse("integrate(exp(-x**2), x)")


def test_keyword_arguments_stay_rejected():
    with pytest.raises(UnsafeExpression, match="keyword"):
        parse("diff(x**2, x, evaluate=False)")


# --- 求值代价的上限 ---------------------------------------------------
#
# 节点数和字符数上限管的是表达式有多大，管不住求值有多贵。本机实测这两类
# 写出来都很短，却能跑到分钟级。


def test_huge_series_range_is_rejected():
    """`Sum(1/k**2, (k, 1, 10**7))` 字面量指数只有 7，长度很短，旧上限全拦不住。"""

    with pytest.raises(UnsafeExpression, match="series range"):
        parse("Sum(1/k**2, (k, 1, 10000000))")


def test_symbolic_series_bounds_are_allowed():
    """`Sum(f, (k, 1, n))` 正是求闭式的写法，不该被限制。"""

    assert parse("Sum(binomial(n, k), (k, 0, n))") is not None


def test_modest_numeric_series_is_allowed():
    assert parse("Sum(k, (k, 1, 100))") is not None


def test_oversized_matrix_literal_is_rejected():
    """12×12 符号行列式本机实测跑过 8 秒。"""

    row = "[" + ",".join(["1"] * 12) + "]"
    with pytest.raises(UnsafeExpression, match="too large"):
        parse(f"Matrix([{','.join([row] * 12)}])")


def test_oversized_identity_matrix_is_rejected():
    with pytest.raises(UnsafeExpression, match="too large"):
        parse("eye(20)")


def test_oversized_container_is_rejected():
    with pytest.raises(UnsafeExpression, match="container"):
        parse("Matrix([[" + ",".join(["1"] * 40) + "]])")


def test_existing_limits_still_hold():
    with pytest.raises(UnsafeExpression, match="exponent"):
        parse("x**500")
    with pytest.raises(UnsafeExpression, match="too long"):
        parse("x+" * 600 + "x")


# --- 新领域 -----------------------------------------------------------


def test_linear_algebra():
    assert parse("det(Matrix([[1,2],[3,4]]))") == -2
    assert parse("trace(Matrix([[1,2],[3,4]]))") == 5
    # 特征值验证的标准形式：det(A - λI) == 0
    assert parse("det(Matrix([[2,1],[1,2]]) - 3*eye(2))") == 0


def test_combinatorics():
    assert parse("binomial(5, 2)") == 10
    assert parse("Sum(binomial(n, k), (k, 0, n))") is not None


def test_calculus_differentiation():
    assert parse("diff(x**3, x)") == 3 * SYMBOLS["x"] ** 2


def test_integral_is_representable_but_not_evaluated():
    """只放未求值的 `Integral`；验证积分靠把答案求导回去，不需要真的算积分。"""

    result = parse("Integral(x*exp(x), x)")

    assert isinstance(result, sp.Integral)


def test_complex_numbers_for_plane_geometry():
    """复数法把几何化归成代数，一化归就可实例化检验。"""

    assert parse("im((a-b)/(c-b))") is not None
    assert parse("conjugate(z) * z") is not None
    assert parse("I**2") == -1


def test_number_theory_helpers():
    assert parse("gcd(12, 18)") == 6
    assert parse("Mod(7, 3)") == 1
    assert parse("floor(7/2)") == 3


def test_predicates_build_relations_not_python_bools():
    """SymPy 对象上的 `==` 返回 Python 布尔的结构相等，必须显式建 `Eq`。"""

    result = parse("im(z) == 0")

    assert isinstance(result, sp.Equality)


def test_inequalities_and_logic():
    assert isinstance(parse("x > 0"), sp.StrictGreaterThan)
    assert parse("And(x > 0, x < 1)") is not None
    assert parse("Implies(x > 0, x >= 0)") is not None


def test_chained_comparison_is_rejected():
    """`0 < x < 1` 在 AST 里是链式比较，语义容易走样，直接拒绝。"""

    with pytest.raises(UnsafeExpression, match="single comparison"):
        parse("0 < x < 1")


# --- 既有行为不变 -----------------------------------------------------


def test_division_stays_unevaluated():
    """这条是既有验证逻辑依赖的：保留未求值分母才能发现 x/x 在 0 处的可去间断。"""

    result = parse("x/x")

    assert result != 1
