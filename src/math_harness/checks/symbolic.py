from __future__ import annotations

import sympy as sp

from math_harness.checks.base import (
    CheckContext,
    CheckOutcome,
    CheckResult,
    CheckTier,
)
from math_harness.checks.claim import ClaimKind
from math_harness.errors import UnsafeExpression
from math_harness.math_parser import SafeMathParser, build_symbol_table

# 符号证明：唯一的 ASSERT 档检查。
#
# 它给出的是确定性结论——`simplify(lhs - rhs) == 0` 成立就是恒等式，不是「大概率成立」。
# 这也是唯一有资格产出 `verified` 的路径；实例化、独立重算、异模型复核都只能给出更低
# 的档位。
#
# 化简不出结果不等于断言为假。SymPy 化简能力有限，很多真恒等式它也化不掉，所以那种
# 情况记 SKIPPED 交给下游的实例化检查，而不是 FAILED——否则会把大量正确答案误拒。


class SymbolicEqualityCheck:
    name = "symbolic_equality"
    tier = CheckTier.ASSERT

    def __init__(self, parser: SafeMathParser | None = None) -> None:
        self.parser = parser or SafeMathParser()

    def applies(self, context: CheckContext) -> bool:
        claim = context.claim
        return claim is not None and claim.kind is ClaimKind.EQUALITY

    def run(self, context: CheckContext) -> CheckResult:
        claim = context.claim
        assert claim is not None and claim.rhs is not None

        symbols = build_symbol_table(
            claim.symbols[0] if claim.symbols else "x",
            claim.symbols[1:],
            {},
        )
        for name in claim.symbols:
            symbols.setdefault(name, sp.Symbol(name, real=True))

        try:
            left = self.parser.parse(claim.lhs, symbols)
            right = self.parser.parse(claim.rhs, symbols)
        except UnsafeExpression as exc:
            return self._skipped(f"断言无法被安全解析：{exc}")

        try:
            difference = sp.simplify(left - right)
        except Exception as exc:  # noqa: BLE001
            return self._skipped(f"符号化简未能完成：{exc.__class__.__name__}")

        if difference == 0:
            return CheckResult(
                check=self.name,
                tier=self.tier,
                outcome=CheckOutcome.PASSED,
                evidence={"method": "simplify(lhs-rhs)==0"},
            )

        # 化简成一个非零常数，是确定性的反证。
        if difference.is_number and difference.is_zero is False:
            return CheckResult(
                check=self.name,
                tier=self.tier,
                outcome=CheckOutcome.FAILED,
                detail=(
                    f"符号化简得到非零常数 {sp.sstr(difference)}，两边不相等。"
                    f"{claim.description}"
                ).strip(),
                evidence={"difference": sp.sstr(difference)[:200]},
            )

        # 化简不掉：SymPy 化简能力有限，不能据此断定为假，交给实例化检查。
        return self._skipped(
            f"符号化简未能判定，残差 {sp.sstr(difference)[:120]}",
            difference=sp.sstr(difference)[:200],
        )

    def _skipped(self, detail: str, **evidence: str) -> CheckResult:
        return CheckResult(
            check=self.name,
            tier=self.tier,
            outcome=CheckOutcome.SKIPPED,
            detail=detail,
            evidence=evidence,
        )
