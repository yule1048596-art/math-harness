from __future__ import annotations

import sympy as sp
from pydantic import BaseModel, Field

from math_harness.math_parser import SafeMathParser, build_symbol_table
from math_harness.models import MathPayload, SolveMathTarget

# 数学题的判别信息大多在结构里——根式之差、趋近点、余项阶——而不在词面。
# 这里把结构抽成一组确定性标签，供检索使用；不调用模型，零额外成本。

_TRIG = (sp.sin, sp.cos, sp.tan, sp.asin, sp.acos, sp.atan)
_HYPERBOLIC = (sp.sinh, sp.cosh, sp.tanh)

_PARSER = SafeMathParser()


class StructuralFeatures(BaseModel):
    """一道题的数学结构指纹。解析失败时所有字段为空，检索会自然退回词面路径。"""

    mode: str | None = None
    point_kind: str | None = None
    direction: str | None = None
    has_remainder: bool = False
    operators: list[str] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (self.mode or self.point_kind or self.operators or self.flags)


class MethodSignature(BaseModel):
    """一张方法卡「通常用在什么结构上」，由它关联例子的结构特征累积而来。"""

    modes: dict[str, int] = Field(default_factory=dict)
    point_kinds: dict[str, int] = Field(default_factory=dict)
    operators: dict[str, int] = Field(default_factory=dict)
    flags: dict[str, int] = Field(default_factory=dict)
    sample_count: int = 0

    def accumulate(self, features: StructuralFeatures) -> MethodSignature:
        if features.is_empty:
            return self

        modes = dict(self.modes)
        point_kinds = dict(self.point_kinds)
        operators = dict(self.operators)
        flags = dict(self.flags)
        if features.mode:
            modes[features.mode] = modes.get(features.mode, 0) + 1
        if features.point_kind:
            point_kinds[features.point_kind] = (
                point_kinds.get(features.point_kind, 0) + 1
            )
        for operator in features.operators:
            operators[operator] = operators.get(operator, 0) + 1
        for flag in features.flags:
            flags[flag] = flags.get(flag, 0) + 1

        return MethodSignature(
            modes=modes,
            point_kinds=point_kinds,
            operators=operators,
            flags=flags,
            sample_count=self.sample_count + 1,
        )


def _classify_point(point: str) -> str:
    normalized = point.strip().lower().replace(" ", "")
    if normalized in {"oo", "+oo", "inf", "infinity"}:
        return "pos_infinity"
    if normalized in {"-oo", "-inf", "-infinity"}:
        return "neg_infinity"
    if normalized in {"0", "0.0", "-0"}:
        return "zero"
    return "finite"


def _collect_operators(expression: sp.Expr, variable: sp.Symbol) -> set[str]:
    operators: set[str] = set()
    for node in sp.preorder_traversal(expression):
        if isinstance(node, sp.Pow):
            exponent = node.exp
            if exponent.is_Rational and exponent.q == 2:
                operators.add("radical")
            if exponent.is_number and exponent.is_negative:
                operators.add("division")
            if variable in exponent.free_symbols:
                operators.add("symbolic_pow")
            continue
        if isinstance(node, sp.exp):
            operators.add("exp")
        elif isinstance(node, sp.log):
            operators.add("log")
        elif isinstance(node, _TRIG):
            operators.add("trig")
        elif isinstance(node, _HYPERBOLIC):
            operators.add("hyperbolic")
        elif isinstance(node, sp.gamma):
            operators.add("gamma")
        elif isinstance(node, sp.factorial):
            operators.add("factorial")
    return operators


def _collect_flags(
    expression: sp.Expr,
    variable: sp.Symbol,
    operators: set[str],
    point_kind: str,
    parameters: list[str],
) -> set[str]:
    flags: set[str] = set()
    if parameters:
        flags.add("has_parameters")
    if {"gamma", "factorial"} & operators:
        flags.add("factorial_or_gamma")
    if "symbolic_pow" in operators:
        flags.add("exponential_power")
    if "trig" in operators and point_kind in {"pos_infinity", "neg_infinity"}:
        flags.add("oscillatory_at_infinity")

    transcendental = {"exp", "log", "trig", "hyperbolic", "gamma", "factorial"}
    if not (operators & transcendental) and "radical" not in operators:
        flags.add("rational_function")
        if expression.is_polynomial(variable):
            flags.add("polynomial")

    top_level = sp.Add.make_args(expression)
    if len(top_level) >= 2:
        if any(term.could_extract_minus_sign() for term in top_level):
            flags.add("cancellation_risk")
        # 根式作为多个加项之一出现，就是有理化的触发形状。这里刻意不看符号：
        # sqrt(x^2+2x)+x 在 x→-∞ 同样是 ∞-∞ 抵消，按符号判断会漏掉。
        if any("radical" in _collect_operators(term, variable) for term in top_level):
            flags.add("radical_difference")
    return flags


def extract_features(
    target: SolveMathTarget | MathPayload | None,
) -> StructuralFeatures:
    """把结构化目标转成结构特征。

    任何解析或遍历失败都降级为空特征——检索不能因为某个表达式形状古怪就整体失败。
    """

    if target is None:
        return StructuralFeatures()

    point_kind = _classify_point(target.point)
    features = StructuralFeatures(
        mode=target.mode.value,
        point_kind=point_kind,
        direction=target.direction.value,
        has_remainder=target.remainder_power is not None,
    )
    try:
        symbols = build_symbol_table(
            target.variable,
            target.parameters,
            target.assumptions,
        )
        expression = _PARSER.parse(target.expression, symbols)
        variable = symbols[target.variable]
        operators = _collect_operators(expression, variable)
        flags = _collect_flags(
            expression,
            variable,
            operators,
            point_kind,
            target.parameters,
        )
    except Exception:  # noqa: BLE001
        return features

    return features.model_copy(
        update={
            "operators": sorted(operators),
            "flags": sorted(flags),
        }
    )


def _conditional_hit_rate(
    counts: dict[str, int],
    items: list[str],
    sample_count: int,
) -> float:
    """查询中的结构项在该方法卡历史里出现得有多普遍。"""

    if not items or sample_count <= 0:
        return 0.0
    total = sum(min(counts.get(item, 0) / sample_count, 1.0) for item in items)
    return total / len(items)


def signature_score(
    signature: MethodSignature,
    features: StructuralFeatures,
) -> float:
    """方法卡的结构签名与当前题目结构的契合度，取值 [0, 1]。"""

    if signature.sample_count <= 0 or features.is_empty:
        return 0.0

    count = signature.sample_count
    mode_score = (
        min(signature.modes.get(features.mode, 0) / count, 1.0)
        if features.mode
        else 0.0
    )
    point_score = (
        min(signature.point_kinds.get(features.point_kind, 0) / count, 1.0)
        if features.point_kind
        else 0.0
    )
    operator_score = _conditional_hit_rate(
        signature.operators, features.operators, count
    )
    flag_score = _conditional_hit_rate(signature.flags, features.flags, count)

    return round(
        mode_score * 0.30
        + point_score * 0.20
        + operator_score * 0.25
        + flag_score * 0.25,
        6,
    )
