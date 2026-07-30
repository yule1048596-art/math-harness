from __future__ import annotations

import sympy as sp

from math_harness.errors import UnsafeExpression
from math_harness.math_parser import SafeMathParser
from math_harness.models import (
    MathPayload,
    VerificationMode,
    VerificationReport,
    VerificationStatus,
)


class SolutionVerifier:
    def __init__(self, parser: SafeMathParser | None = None) -> None:
        self.parser = parser or SafeMathParser()

    def verify(self, payload: MathPayload | None) -> VerificationReport:
        if payload is None:
            return VerificationReport(
                status=VerificationStatus.NEEDS_REVIEW,
                summary="原始题目已保存；未提供结构化数学表达式，需要人工或模型复核。",
                checks=["source_captured"],
            )

        symbol_names = {payload.variable, *payload.parameters}
        try:
            expression = self.parser.parse(payload.expression, symbol_names)
            expected = self.parser.parse(payload.expected, symbol_names)
            variable = sp.Symbol(payload.variable)
            point = self._parse_point(payload.point, symbol_names)

            if payload.mode is VerificationMode.EXACT_EQUIVALENCE:
                return self._verify_exact(expression, expected)
            if payload.mode is VerificationMode.ASYMPTOTIC_EQUIVALENCE:
                return self._verify_asymptotic_equivalence(
                    expression, expected, variable, point
                )
            return self._verify_asymptotic_expansion(
                expression,
                expected,
                variable,
                point,
                payload.remainder_power,
            )
        except UnsafeExpression as exc:
            return VerificationReport(
                status=VerificationStatus.REJECTED,
                summary="结构化表达式未通过安全解析。",
                checks=["safe_parse_failed"],
                error=str(exc),
            )
        # Symbolic backends can raise many exception types on valid but difficult
        # input. The trust boundary is here: an undecidable problem is queued for
        # review rather than taking down the request.
        except Exception as exc:  # noqa: BLE001
            return VerificationReport(
                status=VerificationStatus.NEEDS_REVIEW,
                summary="自动验证无法得出可靠结论，需要复核。",
                checks=["symbolic_check_inconclusive"],
                error=f"{exc.__class__.__name__}: {exc}",
            )

    def _parse_point(self, text: str, symbol_names: set[str]) -> sp.Expr:
        normalized = text.strip()
        if normalized in {"oo", "+oo", "infinity", "+infinity"}:
            return sp.oo
        if normalized in {"-oo", "-infinity"}:
            return -sp.oo
        return self.parser.parse(normalized, symbol_names)

    @staticmethod
    def _verify_exact(expression: sp.Expr, expected: sp.Expr) -> VerificationReport:
        difference = sp.simplify(expression - expected)
        equivalent = difference.equals(0)
        if equivalent is True:
            status = VerificationStatus.VERIFIED
            summary = "符号等价验证通过。"
        elif equivalent is False:
            status = VerificationStatus.REJECTED
            summary = "表达式与期望答案不等价。"
        else:
            status = VerificationStatus.NEEDS_REVIEW
            summary = "符号后端无法判定两个表达式是否等价，需要复核。"
        return VerificationReport(
            status=status,
            summary=summary,
            checks=["safe_parse", "symbolic_difference"],
            computed={"simplified_difference": sp.sstr(difference)},
        )

    @staticmethod
    def _verify_asymptotic_equivalence(
        expression: sp.Expr,
        expected: sp.Expr,
        variable: sp.Symbol,
        point: sp.Expr,
    ) -> VerificationReport:
        if expected == 0:
            value = sp.limit(expression, variable, point)
            equivalent = value.equals(0)
            computed = {"limit(expression)": sp.sstr(value)}
        else:
            value = sp.limit(expression / expected, variable, point)
            equivalent = value.equals(1)
            computed = {"limit(expression/expected)": sp.sstr(value)}

        if equivalent is True:
            status = VerificationStatus.VERIFIED
            summary = "渐进等价验证通过。"
        elif equivalent is False:
            status = VerificationStatus.REJECTED
            summary = "渐进等价验证失败。"
        else:
            status = VerificationStatus.NEEDS_REVIEW
            summary = "渐进比值极限无法判定，需要复核。"
        return VerificationReport(
            status=status,
            summary=summary,
            checks=["safe_parse", "asymptotic_ratio_limit"],
            computed=computed,
        )

    @staticmethod
    def _verify_asymptotic_expansion(
        expression: sp.Expr,
        expected: sp.Expr,
        variable: sp.Symbol,
        point: sp.Expr,
        remainder_power: int | None,
    ) -> VerificationReport:
        if remainder_power is None:
            return VerificationReport(
                status=VerificationStatus.NEEDS_REVIEW,
                summary="渐进展开验证需要 remainder_power。",
                checks=["safe_parse", "missing_remainder_power"],
            )

        difference = sp.simplify(expression - expected)
        if point in (sp.oo, -sp.oo):
            scaled_difference = sp.simplify(difference * variable**remainder_power)
            check_name = f"limit((expression-expected)*{variable}^{remainder_power})"
        else:
            scaled_difference = sp.simplify(
                difference / (variable - point) ** remainder_power
            )
            check_name = (
                f"limit((expression-expected)/({variable}-{point})^{remainder_power})"
            )

        value = sp.limit(scaled_difference, variable, point)
        if value.is_finite is True:
            status = VerificationStatus.VERIFIED
            summary = f"展开余项为指定阶或更高阶，验证通过（缩放余项极限为 {sp.sstr(value)}）。"
        elif value.is_finite is False:
            status = VerificationStatus.REJECTED
            summary = "期望展开未达到声明的余项阶数。"
        else:
            status = VerificationStatus.NEEDS_REVIEW
            summary = "缩放余项的有限性依赖未声明的条件，需要复核。"
        return VerificationReport(
            status=status,
            summary=summary,
            checks=["safe_parse", "scaled_remainder_limit"],
            computed={
                "difference": sp.sstr(difference),
                check_name: sp.sstr(value),
            },
        )
