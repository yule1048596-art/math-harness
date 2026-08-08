from __future__ import annotations

import pytest

from math_harness.attribution import (
    attribution_cases,
    measure_attribution,
)
from math_harness.methods import MethodExtractor, used_in

# 方法归属。
#
# v0.15 的门禁查数学——结论对不对、推导站不站得住——**不查方法标签**。一条数学完全
# 正确、`step_checked` 的解，照样能塞进一张写着错方法的卡，以后被检索出来当依据用。
# 这一组是那个缺口的度量与守卫。

EXTRACTOR = MethodExtractor()


def keys(problem: str, solution: str, hint: str | None = None) -> set[str]:
    result = EXTRACTOR.extract(problem, solution, hint)
    return {draft.key for draft in result.methods} - {"generic_example"}


# --- 归属只看解答 -----------------------------------------------------


def test_a_method_named_only_in_the_question_is_not_attributed():
    """实测的原始缺陷：问「用有理化」+ 求导的答案 → 方法卡写着有理化。

    方法是在解答里做出来的，不是在提问里说出来的。
    """

    assert keys("用有理化方法求这个极限。", "对幂函数逐项求导得 3*x**2。") == set()


def test_a_method_named_only_in_the_hint_is_not_attributed():
    """提示词是人给的建议，不是解答里的证据。"""

    assert keys("求这个极限。", "按定义逐项化简。", "泰勒展开") == set()


def test_a_method_actually_used_is_still_attributed():
    """收紧不能顺手把正常路径也关掉。"""

    assert "rationalization" in keys(
        "求这个极限。", "根式之差在无穷远抵消，先做共轭有理化再展开。"
    )


# --- 否定与对比 -------------------------------------------------------


@pytest.mark.parametrize(
    ("solution", "why"),
    [
        ("这题和斯特林公式无关，直接按定义算。", "方法名之后的否定"),
        ("不用泰勒展开也能做，按几何级数求和。", "方法名之前的否定"),
        ("没有必要用主导平衡，两边阶数一眼可见。", "「没有必要」"),
        ("相比泰勒展开，这里直接代入更快。", "对比性提及，实际没用"),
    ],
)
def test_a_negated_mention_is_not_attribution(solution: str, why: str):
    assert keys("求这个极限。", solution) == set(), why


def test_a_sentence_can_reject_one_method_and_use_another():
    """「这里应当取对数，而不是做有理化」——一句话里两个方法，只有一个被用了。"""

    extracted = keys("求这个极限。", "这里应当取对数，而不是做有理化。")

    assert "log_transform" in extracted
    assert "rationalization" not in extracted


def test_used_in_reports_a_later_unnegated_mention():
    """同一个方法先被否定、后又真的用了，仍然算用了。"""

    assert used_in("先说不用泰勒展开，后来还是用泰勒展开做的。", ("泰勒",))


def test_the_negation_window_does_not_reach_across_sentences():
    """窗口开太大会把上一句的否定误算到这一句头上。"""

    assert used_in("这题不难。我们用泰勒展开来做。", ("泰勒",))


# --- 误标率门禁 -------------------------------------------------------
#
# 误标比漏标严重：漏了只是没学到，标错了是学进去一个假的，而且以后会被当依据。


def test_the_mislabel_rate_is_zero_on_the_benchmark():
    """基线是 0.750（12 条错 9 条）。"""

    report = measure_attribution()

    assert report.mislabel_rate == 0.0, report.details


def test_coverage_is_well_above_the_template_only_level():
    """覆盖率是 v0.17 才补上的指标。

    v0.16 只量误标率，那个指标好看有一部分原因是提炼器对多数领域**什么都不标**——
    七个模板全是渐进方法。实测跨领域覆盖率 0.333，改由推导结构导出后到 0.632。
    """

    report = measure_attribution()

    assert report.coverage >= 0.60, report.details


def test_the_remaining_misses_are_solutions_without_a_parseable_equation():
    """剩下的漏标不是提炼失败，是**没有可解析的推导可看**。

    结构导出要有断言才能判；纯散文的解答里没有等式，抽不出断言。那是抽断言的覆盖
    上限，不是归属的问题。
    """

    from math_harness.attribution import attribution_cases
    from math_harness.claim_drafting import RuleBasedClaimDrafter

    report = measure_attribution()
    missed = {name for name, _, missing in report.details if missing}
    drafter = RuleBasedClaimDrafter()

    for case in attribution_cases():
        if case.name not in missed:
            continue
        draft = drafter.draft(case.problem, case.solution)
        key = None
        if draft.steps:
            from math_harness.method_identity import derive_method_key

            key = derive_method_key(draft.steps)
        assert key is None, f"{case.name} 抽得出断言也判得出 {key}，那它不该漏标"


def test_the_benchmark_actually_contains_traps():
    """全是正常用例的基准量不出误标率——它得先有东西可错。"""

    cases = attribution_cases()

    assert sum(1 for case in cases if not case.expected) >= 5
    assert sum(1 for case in cases if case.expected) >= 4


def test_the_measurement_can_report_a_mislabel():
    """量不出问题的度量不是度量。"""

    class OverEagerExtractor:
        name = "over-eager"
        prompt_version = "v1"

        def extract(self, problem, solution, hint=None):
            del problem, solution, hint
            from math_harness.models import (
                ExtractionStatus,
                MethodDraft,
                MethodExtractionResult,
                MethodExtractionTrace,
            )

            return MethodExtractionResult(
                methods=[
                    MethodDraft(
                        key="stirling",
                        name="乱标",
                        goal="总是声称用了斯特林公式",
                        applicable_when=["从不检查"],
                        procedure=["直接断言"],
                        failure_modes=["全错"],
                        tags=[],
                    )
                ],
                trace=MethodExtractionTrace(
                    provider=self.name,
                    prompt_version=self.prompt_version,
                    status=ExtractionStatus.SUCCESS,
                    extracted_method_keys=["stirling"],
                ),
            )

    report = measure_attribution(OverEagerExtractor())

    assert report.mislabel_rate > 0.5
