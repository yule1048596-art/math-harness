from __future__ import annotations

import pytest

from math_harness.checks import (
    Binding,
    CheckContext,
    CheckOutcome,
    CheckResult,
    CheckTier,
    Claim,
    ClaimKind,
    SamplingDomain,
    SymbolicEqualityCheck,
    run_checks,
)


def _claim(lhs: str, rhs: str, *symbols: str) -> Claim:
    return Claim(
        lhs=lhs,
        rhs=rhs,
        bindings=[Binding(symbol=name) for name in symbols],
    )


class _Stub:
    """可编排的假检查，用来测流水线本身而不是某条具体检查。"""

    def __init__(self, name, tier, outcome, detail="", raises=None):
        self.name = name
        self.tier = tier
        self._outcome = outcome
        self._detail = detail
        self._raises = raises
        self.ran = False

    def applies(self, context):
        return self._outcome is not None

    def run(self, context):
        self.ran = True
        if self._raises is not None:
            raise self._raises
        return CheckResult(
            check=self.name,
            tier=self.tier,
            outcome=self._outcome,
            detail=self._detail,
        )


# --- 断言模型 ---------------------------------------------------------


def test_equality_claim_requires_rhs():
    with pytest.raises(ValueError, match="rhs"):
        Claim(kind=ClaimKind.EQUALITY, lhs="x")


def test_predicate_claim_needs_no_rhs():
    claim = Claim(kind=ClaimKind.PREDICATE, lhs="im((c-a)/(b-a)) == 0")

    assert claim.rhs is None


def test_claim_without_bindings_is_closed():
    assert _claim("1+1", "2").is_closed
    assert not _claim("x", "x", "x").is_closed


def test_binding_symbol_must_be_an_identifier():
    with pytest.raises(ValueError, match="identifier"):
        Binding(symbol="a b")


def test_discrete_domains_are_flagged_for_enumeration():
    """离散取值域该穷举而不是抽样——枚举比抽样强。"""

    assert Binding(symbol="n", domain=SamplingDomain.POSITIVE_INTEGER).is_discrete
    assert Binding(symbol="p", domain=SamplingDomain.BOOLEAN).is_discrete
    assert not Binding(symbol="x", domain=SamplingDomain.REAL).is_discrete
    assert not Binding(symbol="z", domain=SamplingDomain.COMPLEX).is_discrete


# --- 流水线 -----------------------------------------------------------


def test_inapplicable_checks_are_skipped_not_run():
    stub = _Stub("never", CheckTier.SUGGEST, None)

    report = run_checks([stub], CheckContext())

    assert not stub.ran
    assert report.results[0].outcome is CheckOutcome.SKIPPED


def test_a_raising_check_is_recorded_as_errored():
    """检查器出问题不该把用户的答案一起弄丢。"""

    stub = _Stub(
        "boom", CheckTier.ASSERT, CheckOutcome.PASSED, raises=RuntimeError("x")
    )

    report = run_checks([stub], CheckContext())

    assert report.results[0].outcome is CheckOutcome.ERRORED
    assert "RuntimeError" in report.results[0].detail


def test_errored_assert_does_not_block():
    """检查自身出错 ≠ 断言为假，不该触发重跑。"""

    stub = _Stub(
        "boom", CheckTier.ASSERT, CheckOutcome.PASSED, raises=RuntimeError("x")
    )

    report = run_checks([stub], CheckContext())

    assert report.blocking_failures == []


def test_failed_assert_blocks_but_failed_suggest_does_not():
    hard = _Stub("hard", CheckTier.ASSERT, CheckOutcome.FAILED, detail="硬失败")
    soft = _Stub("soft", CheckTier.SUGGEST, CheckOutcome.FAILED, detail="软失败")

    report = run_checks([hard, soft], CheckContext())

    assert [item.check for item in report.blocking_failures] == ["hard"]


def test_blocking_detail_is_the_first_hard_failure():
    report = run_checks(
        [
            _Stub("soft", CheckTier.SUGGEST, CheckOutcome.FAILED, detail="软"),
            _Stub(
                "hard1", CheckTier.ASSERT, CheckOutcome.FAILED, detail="第一条硬失败"
            ),
            _Stub("hard2", CheckTier.ASSERT, CheckOutcome.FAILED, detail="第二条"),
        ],
        CheckContext(),
    )

    # 只把一条细节交给模型，不要糊一堆过去。
    assert report.first_blocking_detail() == "第一条硬失败"


def test_every_check_runs_even_after_a_hard_failure():
    """硬失败决定状态，但不该妨碍收集其余证据。"""

    later = _Stub("later", CheckTier.SUGGEST, CheckOutcome.PASSED)

    run_checks(
        [_Stub("hard", CheckTier.ASSERT, CheckOutcome.FAILED), later], CheckContext()
    )

    assert later.ran


def test_audit_payload_carries_every_result():
    report = run_checks(
        [
            _Stub("a", CheckTier.ASSERT, CheckOutcome.PASSED),
            _Stub("b", CheckTier.SUGGEST, CheckOutcome.FAILED, detail="d"),
        ],
        CheckContext(),
    )

    payload = report.audit_payload()

    assert [item["check"] for item in payload["checks"]] == ["a", "b"]
    assert payload["checks"][1]["outcome"] == "failed"


# --- 符号证明 ---------------------------------------------------------


def test_true_identity_passes():
    report = run_checks(
        [SymbolicEqualityCheck()],
        CheckContext(claim=_claim("(a+b)**2 - (a-b)**2", "4*a*b", "a", "b")),
    )

    assert report.results[0].outcome is CheckOutcome.PASSED


def test_nonzero_constant_residual_is_a_definitive_refutation():
    report = run_checks(
        [SymbolicEqualityCheck()], CheckContext(claim=_claim("2+2", "5"))
    )

    assert report.results[0].outcome is CheckOutcome.FAILED
    assert report.blocking_failures


def test_unresolved_residual_is_skipped_not_failed():
    """SymPy 化简能力有限，化简不掉 ≠ 断言为假。

    误判为假会把大量正确答案拒掉。这类交给实例化检查，它能给出具体反例。
    """

    report = run_checks(
        [SymbolicEqualityCheck()],
        CheckContext(claim=_claim("(a+b)**2 - (a-b)**2", "2*a*b", "a", "b")),
    )

    assert report.results[0].outcome is CheckOutcome.SKIPPED
    assert report.blocking_failures == []


def test_unparsable_claim_is_skipped_not_failed():
    report = run_checks(
        [SymbolicEqualityCheck()],
        CheckContext(claim=_claim("__import__('os')", "0")),
    )

    assert report.results[0].outcome is CheckOutcome.SKIPPED


def test_predicate_claims_are_not_handled_by_the_equality_check():
    report = run_checks(
        [SymbolicEqualityCheck()],
        CheckContext(claim=Claim(kind=ClaimKind.PREDICATE, lhs="x > 0")),
    )

    assert report.results[0].outcome is CheckOutcome.SKIPPED


def test_symbolic_check_is_the_assert_tier():
    """只有确定性证明有资格当硬约束，其余各级都只能是软约束。"""

    assert SymbolicEqualityCheck().tier is CheckTier.ASSERT
