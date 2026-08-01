from __future__ import annotations

import sympy as sp

from math_harness.errors import UnsafeExpression
from math_harness.math_parser import SafeMathParser, build_symbol_table
from math_harness.models import (
    ApproachDirection,
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

        symbols = build_symbol_table(
            payload.variable,
            payload.parameters,
            payload.assumptions,
        )
        try:
            expression = self.parser.parse(payload.expression, symbols)
            expected = self.parser.parse(payload.expected, symbols)
            variable = symbols[payload.variable]
            point = self._parse_point(payload.point, symbols)

            if payload.mode is VerificationMode.EXACT_EQUIVALENCE:
                return self._verify_exact(expression, expected, symbols)
            if payload.mode is VerificationMode.LIMIT:
                return self._verify_limit(
                    expression,
                    expected,
                    variable,
                    point,
                    payload.direction,
                )
            if payload.mode is VerificationMode.ASYMPTOTIC_EQUIVALENCE:
                return self._verify_asymptotic_equivalence(
                    expression,
                    expected,
                    variable,
                    point,
                    payload.direction,
                )
            return self._verify_asymptotic_expansion(
                expression,
                expected,
                variable,
                point,
                payload.remainder_power,
                payload.direction,
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

    def _parse_point(
        self,
        text: str,
        symbols: dict[str, sp.Symbol],
    ) -> sp.Expr:
        normalized = text.strip()
        if normalized in {"oo", "+oo", "infinity", "+infinity"}:
            return sp.oo
        if normalized in {"-oo", "-infinity"}:
            return -sp.oo
        return self.parser.parse(normalized, symbols)

    @staticmethod
    def _verify_exact(
        expression: sp.Expr,
        expected: sp.Expr,
        symbols: dict[str, sp.Symbol],
    ) -> VerificationReport:
        difference = sp.simplify(expression - expected)
        equivalent = difference.equals(0)
        computed = {"simplified_difference": sp.sstr(difference)}

        if equivalent is False:
            return VerificationReport(
                status=VerificationStatus.REJECTED,
                summary="表达式与期望答案不等价。",
                checks=["safe_parse", "symbolic_difference"],
                computed=computed,
            )
        if equivalent is None:
            return VerificationReport(
                status=VerificationStatus.NEEDS_REVIEW,
                summary="符号后端无法判定两个表达式是否等价，需要复核。",
                checks=["safe_parse", "symbolic_difference"],
                computed=computed,
            )

        domains_equal = True
        primary_symbol_name = next(iter(symbols))
        for symbol_name, symbol in symbols.items():
            try:
                base_domain = SolutionVerifier._base_real_domain(symbol)
                expression_domain = sp.calculus.util.continuous_domain(
                    expression,
                    symbol,
                    sp.S.Reals,
                ).intersect(base_domain)
                expected_domain = sp.calculus.util.continuous_domain(
                    expected,
                    symbol,
                    sp.S.Reals,
                ).intersect(base_domain)
            except (NotImplementedError, ValueError) as exc:
                return VerificationReport(
                    status=VerificationStatus.NEEDS_REVIEW,
                    summary=(
                        f"表达式值相同，但自动验证无法可靠比较符号 {symbol_name} "
                        "的定义域，需要复核。"
                    ),
                    checks=[
                        "safe_parse",
                        "symbolic_difference",
                        "domain_check_inconclusive",
                    ],
                    computed=computed,
                    error=f"{exc.__class__.__name__}: {exc}",
                )

            computed[f"expression_domain:{symbol_name}"] = sp.sstr(expression_domain)
            computed[f"expected_domain:{symbol_name}"] = sp.sstr(expected_domain)
            # Keep the v0.5 response keys for the main variable while exposing
            # parameter-specific evidence alongside them.
            if symbol_name == primary_symbol_name:
                computed["expression_domain"] = sp.sstr(expression_domain)
                computed["expected_domain"] = sp.sstr(expected_domain)
            domains_equal = domains_equal and expression_domain == expected_domain

        if domains_equal:
            status = VerificationStatus.VERIFIED
            summary = "符号值与定义域均等价，验证通过。"
        else:
            status = VerificationStatus.REJECTED
            summary = "表达式化简后的值相同，但定义域不一致。"
        return VerificationReport(
            status=status,
            summary=summary,
            checks=["safe_parse", "symbolic_difference", "real_domain_equivalence"],
            computed=computed,
        )

    @staticmethod
    def _verify_limit(
        expression: sp.Expr,
        expected: sp.Expr,
        variable: sp.Symbol,
        point: sp.Expr,
        direction: ApproachDirection,
    ) -> VerificationReport:
        value = SolutionVerifier._limit(
            expression,
            variable,
            point,
            direction,
        )
        equivalent = (
            True if value == expected else sp.simplify(value - expected).equals(0)
        )
        computed = {
            "limit(expression)": sp.sstr(value),
            "expected": sp.sstr(expected),
            "direction": direction.value,
        }
        if equivalent is True:
            status = VerificationStatus.VERIFIED
            summary = "极限验证通过。"
        elif equivalent is False:
            status = VerificationStatus.REJECTED
            summary = "候选答案与计算得到的极限不一致。"
        else:
            status = VerificationStatus.NEEDS_REVIEW
            summary = "符号后端无法判定候选答案是否等于该极限，需要复核。"
        return VerificationReport(
            status=status,
            summary=summary,
            checks=["safe_parse", "limit_value"],
            computed=computed,
        )

    @staticmethod
    def _verify_asymptotic_equivalence(
        expression: sp.Expr,
        expected: sp.Expr,
        variable: sp.Symbol,
        point: sp.Expr,
        direction: ApproachDirection,
    ) -> VerificationReport:
        if expected == 0:
            return VerificationReport(
                status=VerificationStatus.REJECTED,
                summary=("零不能作为渐进等价式；如需验证趋于零，请使用极限模式。"),
                checks=["safe_parse", "invalid_zero_equivalent"],
            )

        value = SolutionVerifier._limit(
            expression / expected,
            variable,
            point,
            direction,
        )
        equivalent = value.equals(1)
        computed = {
            "limit(expression/expected)": sp.sstr(value),
            "direction": direction.value,
        }

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
        direction: ApproachDirection,
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

        value = SolutionVerifier._limit(
            scaled_difference,
            variable,
            point,
            direction,
        )
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
                "direction": direction.value,
            },
        )

    @staticmethod
    def _limit(
        expression: sp.Expr,
        variable: sp.Symbol,
        point: sp.Expr,
        direction: ApproachDirection,
    ) -> sp.Expr:
        if point == sp.oo:
            sympy_direction = "-"
        elif point == -sp.oo:
            sympy_direction = "+"
        else:
            sympy_direction = {
                ApproachDirection.TWO_SIDED: "+-",
                ApproachDirection.LEFT: "-",
                ApproachDirection.RIGHT: "+",
            }[direction]
        return sp.limit(
            expression,
            variable,
            point,
            dir=sympy_direction,
        )

    @staticmethod
    def _base_real_domain(variable: sp.Symbol) -> sp.Set:
        assumptions = variable.assumptions0
        if assumptions.get("positive"):
            domain: sp.Set = sp.Interval.open(0, sp.oo)
        elif assumptions.get("negative"):
            domain = sp.Interval.open(-sp.oo, 0)
        elif assumptions.get("nonnegative"):
            domain = sp.Interval(0, sp.oo)
        elif assumptions.get("nonpositive"):
            domain = sp.Interval(-sp.oo, 0)
        else:
            domain = sp.S.Reals
        if assumptions.get("nonzero"):
            domain = domain - sp.FiniteSet(0)
        if assumptions.get("integer"):
            domain = domain.intersect(sp.S.Integers)
        return domain
