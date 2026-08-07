from __future__ import annotations

import pytest

from math_harness.checks import (
    CheckOutcome,
    CheckReport,
    CheckResult,
    CheckTier,
    ConclusionConfidence,
    ProcessConfidence,
    assess,
    conclusion_rank,
    granted_level,
    retrieval_weight,
)
from math_harness.checks.confidence import _GRANTS, DETERMINISTIC_LEVELS


def result(
    check: str,
    outcome: CheckOutcome,
    tier: CheckTier = CheckTier.SUGGEST,
    counterexample: dict[str, str] | None = None,
) -> CheckResult:
    return CheckResult(
        check=check,
        tier=tier,
        outcome=outcome,
        counterexample=counterexample or {},
    )


def report(*results: CheckResult) -> CheckReport:
    return CheckReport(results=list(results))


# --- 信任边界（这一组是整个可信度设计的要害）--------------------------
#
# 知识库靠这些标签决定以后信任谁。把概率性检查标成确定性的，污染会顺着检索一路传下去，
# 而且传出去之后无从分辨。所以「谁配得上 verified」必须是结构上说了算，不是靠约定。


def test_only_the_symbolic_check_can_grant_verified():
    """`verified` 是确定性结论，只有 SymPy 的符号判定配得上。"""

    deterministic = [
        name for name, level in _GRANTS.items() if level in DETERMINISTIC_LEVELS
    ]

    assert deterministic == ["symbolic_equality"]


def test_a_tool_saying_correct_never_reaches_verified():
    """独立引擎重算一致是很强的证据，但它仍然只是另一个程序的意见。"""

    assessment = assess(report(result("independent_recompute", CheckOutcome.PASSED)))

    assert assessment.conclusion is ConclusionConfidence.CROSS_CHECKED


def test_a_model_saying_correct_never_reaches_verified():
    assessment = assess(report(result("peer_review", CheckOutcome.PASSED)))

    assert assessment.conclusion is ConclusionConfidence.PEER_REVIEWED


def test_a_suggest_tier_check_cannot_be_promoted_to_verified():
    """第二道闸：登记表就算写错了，软约束也拿不到确定性档位。

    软约束的定义是「失败不中止」。一条失败都不算数的检查不该产出确定性结论。
    """

    assert (
        granted_level("symbolic_equality", CheckTier.SUGGEST)
        is ConclusionConfidence.NUMERICALLY_CHECKED
    )
    assert (
        granted_level("symbolic_equality", CheckTier.ASSERT)
        is ConclusionConfidence.VERIFIED
    )


def test_an_unregistered_check_grants_nothing():
    """新加一层检查不会因为忘了登记而悄悄获得信任——只会不被信任。"""

    assessment = assess(report(result("brand_new_layer", CheckOutcome.PASSED)))

    assert assessment.conclusion is ConclusionConfidence.UNCHECKED


def test_instantiation_stops_at_numerically_checked():
    """随机实例化在阶段 H 的测量里抓住了 44/44 个结论扰动，仍然只是强证据不是证明。"""

    assessment = assess(
        report(result("instantiation", CheckOutcome.PASSED, CheckTier.SUGGEST))
    )

    assert assessment.conclusion is ConclusionConfidence.NUMERICALLY_CHECKED


# --- 结论轴 -----------------------------------------------------------


def test_the_strongest_passing_check_sets_the_level():
    assessment = assess(
        report(
            result("instantiation", CheckOutcome.PASSED),
            result("symbolic_equality", CheckOutcome.PASSED, CheckTier.ASSERT),
        )
    )

    assert assessment.conclusion is ConclusionConfidence.VERIFIED
    assert assessment.conclusion_source == "symbolic_equality"


def test_one_counterexample_outweighs_every_passing_check():
    """一个反例足以证伪，多少次通过都不足以证明。别的层通过不能把它救回来。"""

    assessment = assess(
        report(
            result("symbolic_equality", CheckOutcome.PASSED, CheckTier.ASSERT),
            result("instantiation", CheckOutcome.FAILED, counterexample={"x": "-3"}),
        )
    )

    assert assessment.conclusion is ConclusionConfidence.REFUTED
    assert assessment.counterexample == {"x": "-3"}


def test_refuted_is_not_the_same_as_unchecked():
    """混为一谈会让一个已知错误的答案以「未验证」的身份进库，和没查过的题平起平坐。"""

    refuted = assess(report(result("instantiation", CheckOutcome.FAILED)))
    unchecked = assess(report(result("instantiation", CheckOutcome.SKIPPED)))

    assert refuted.conclusion is ConclusionConfidence.REFUTED
    assert unchecked.conclusion is ConclusionConfidence.UNCHECKED
    assert not refuted.may_enter_knowledge_base
    assert unchecked.may_enter_knowledge_base


def test_a_check_that_errored_grants_nothing():
    """检查自身出错不等于断言为假，也不等于断言为真。"""

    assessment = assess(report(result("symbolic_equality", CheckOutcome.ERRORED)))

    assert assessment.conclusion is ConclusionConfidence.UNCHECKED


def test_no_checks_at_all_is_unchecked_not_verified():
    assert assess(CheckReport()).conclusion is ConclusionConfidence.UNCHECKED


# --- 过程轴与方法卡门禁 -----------------------------------------------
#
# 阶段 H 实测：39 个「结论正确、某步写错」的变异体，只查结论的层一个都没抓到。
# 方法卡是从推导提取的，所以这条门禁不是保险起见，是唯一的拦截点。


def test_method_cards_only_come_from_a_checked_derivation():
    checked = assess(report(result("step_instantiation", CheckOutcome.PASSED)))

    assert checked.process is ProcessConfidence.STEP_CHECKED
    assert checked.may_extract_methods


def test_a_correct_answer_with_a_broken_step_yields_no_method_card():
    """整个门禁就是为这个场景存在的：结论查得过，推导是错的。

    从这种推导里学方法，等于把一个错方法存进知识库，以后还会被检索复用。
    """

    assessment = assess(
        report(
            result("symbolic_equality", CheckOutcome.PASSED, CheckTier.ASSERT),
            result(
                "step_instantiation",
                CheckOutcome.FAILED,
                counterexample={"a": "7", "b": "-4"},
            ),
        )
    )

    assert assessment.conclusion is ConclusionConfidence.VERIFIED
    assert assessment.process is ProcessConfidence.STEP_FAILED
    assert not assessment.may_extract_methods
    # 例题本身仍然可以入库——结论确实是对的，只是不能从中学方法。
    assert assessment.may_enter_knowledge_base


def test_an_unchecked_derivation_yields_no_method_card():
    """没有可拆分步骤的解也不给方法卡：没检查过不等于检查通过。"""

    assessment = assess(
        report(result("symbolic_equality", CheckOutcome.PASSED, CheckTier.ASSERT))
    )

    assert assessment.process is ProcessConfidence.STEP_UNCHECKED
    assert not assessment.may_extract_methods


def test_the_two_axes_are_independent():
    """结论对不对和推导站不站得住是两件事——这正是双轴而不是一条阶梯的理由。"""

    verified_but_broken = assess(
        report(
            result("symbolic_equality", CheckOutcome.PASSED, CheckTier.ASSERT),
            result("step_instantiation", CheckOutcome.FAILED),
        )
    )
    unchecked_but_sound = assess(
        report(result("step_instantiation", CheckOutcome.PASSED))
    )

    assert verified_but_broken.conclusion is ConclusionConfidence.VERIFIED
    assert verified_but_broken.process is ProcessConfidence.STEP_FAILED
    assert unchecked_but_sound.conclusion is ConclusionConfidence.UNCHECKED
    assert unchecked_but_sound.process is ProcessConfidence.STEP_CHECKED


# --- 入库与检索 -------------------------------------------------------


def test_unverified_results_still_enter_the_knowledge_base():
    """用户明确决定：未验证的结果也进库，标注清楚可信度即可。"""

    assert assess(CheckReport()).may_enter_knowledge_base


def test_lower_confidence_never_outranks_verified_in_retrieval():
    """未验证内容会被检索到，但不该盖过验证过的——「越用越强」的前提是强的排前面。"""

    verified = retrieval_weight(ConclusionConfidence.VERIFIED)

    for level in ConclusionConfidence:
        if level is ConclusionConfidence.PROOF_VERIFIED:
            continue
        if level is ConclusionConfidence.VERIFIED:
            continue
        assert retrieval_weight(level) < verified


def test_refuted_content_carries_no_retrieval_weight():
    assert retrieval_weight(ConclusionConfidence.REFUTED) == 0.0


@pytest.mark.parametrize("level", list(ConclusionConfidence))
def test_every_level_is_ranked_and_weighted(level: ConclusionConfidence):
    """漏一个档位会让比较和排序在运行时炸掉，而且是在入库那一刻。"""

    assert conclusion_rank(level) >= 0
    assert 0.0 <= retrieval_weight(level) <= 1.0


# --- 端到端 -----------------------------------------------------------


def test_a_real_broken_derivation_is_graded_correctly_end_to_end():
    """真实流水线跑一遍那道复现过的题，双轴给出的组合必须是可用的。

    结论确实成立（符号检查都通过了），推导确实是错的。例题该入库、方法卡该拦住、
    反例该给到用户——这四件事一起成立，双轴才算真的有用。
    """

    from math_harness.checks import (
        Binding,
        CheckContext,
        Claim,
        ClaimKind,
        InstantiationCheck,
        StepInstantiationCheck,
        SymbolicEqualityCheck,
        run_checks,
    )

    def claim(lhs: str, rhs: str) -> Claim:
        return Claim(
            kind=ClaimKind.EQUALITY,
            lhs=lhs,
            rhs=rhs,
            bindings=[Binding(symbol="a"), Binding(symbol="b")],
        )

    context = CheckContext(
        claim=claim("(a+b)**2 - (a-b)**2", "4*a*b"),
        steps=[
            claim("(a+b)**2", "a**2 + 2*a*b + b**2"),
            claim("(a-b)**2", "a**2 - 2*a*b - b**2"),  # 错：b² 符号
        ],
    )

    assessment = assess(
        run_checks(
            [
                SymbolicEqualityCheck(),
                InstantiationCheck(trials=8),
                StepInstantiationCheck(trials=8),
            ],
            context,
        )
    )

    assert assessment.conclusion is ConclusionConfidence.VERIFIED
    assert assessment.process is ProcessConfidence.STEP_FAILED
    assert assessment.may_enter_knowledge_base
    assert not assessment.may_extract_methods
    assert set(assessment.counterexample) == {"a", "b"}


def test_a_sound_derivation_yields_both_axes_clean():
    from math_harness.checks import (
        Binding,
        CheckContext,
        Claim,
        ClaimKind,
        InstantiationCheck,
        StepInstantiationCheck,
        SymbolicEqualityCheck,
        run_checks,
    )

    def claim(lhs: str, rhs: str) -> Claim:
        return Claim(
            kind=ClaimKind.EQUALITY,
            lhs=lhs,
            rhs=rhs,
            bindings=[Binding(symbol="a"), Binding(symbol="b")],
        )

    context = CheckContext(
        claim=claim("(a+b)**2 - (a-b)**2", "4*a*b"),
        steps=[
            claim("(a+b)**2", "a**2 + 2*a*b + b**2"),
            claim("(a-b)**2", "a**2 - 2*a*b + b**2"),
        ],
    )

    assessment = assess(
        run_checks(
            [
                SymbolicEqualityCheck(),
                InstantiationCheck(trials=8),
                StepInstantiationCheck(trials=8),
            ],
            context,
        )
    )

    assert assessment.conclusion is ConclusionConfidence.VERIFIED
    assert assessment.process is ProcessConfidence.STEP_CHECKED
    assert assessment.may_extract_methods


def test_the_ranking_matches_the_declared_ladder():
    ladder = [
        ConclusionConfidence.PROOF_VERIFIED,
        ConclusionConfidence.VERIFIED,
        ConclusionConfidence.NUMERICALLY_CHECKED,
        ConclusionConfidence.CROSS_CHECKED,
        ConclusionConfidence.PEER_REVIEWED,
        ConclusionConfidence.UNCHECKED,
        ConclusionConfidence.REFUTED,
    ]

    ranks = [conclusion_rank(level) for level in ladder]

    assert ranks == sorted(ranks, reverse=True)
