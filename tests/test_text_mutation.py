from __future__ import annotations

import pytest

from math_harness.checks.text_mutation import (
    baseline_answers,
    build_text_population,
    measure_text_mutation,
)
from math_harness.claim_drafting import RuleBasedClaimDrafter

# 整条链的变异测试。
#
# 断言层那一套跑的是手工构造的 `Claim`，完全不经过「文本→断言」这条路。v0.16 把那条路
# 加进了主链：规范化（隐式乘法、LaTeX、记号别名、字母连写）、抽等式、定义代入。
# 一个变异体可能在断言层抓得住，而生产里抽断言那一步先把它弄坏了——那种失败上一套
# 测不到。


@pytest.fixture(scope="module")
def report():
    return measure_text_mutation()


# --- 基准本身 ---------------------------------------------------------


@pytest.mark.parametrize("case", baseline_answers(), ids=lambda case: case.name)
def test_every_baseline_answer_is_draftable(case):
    """抽不出断言的基准量不出任何东西——捕获率会因为「什么都没查」而虚高或虚低。"""

    assert RuleBasedClaimDrafter().draft(case.problem, case.answer).is_checkable


def test_the_baselines_use_notation_people_actually_write():
    """隐式乘法、中文连接词、恒等号、中文数字步骤都要出现。

    这些正是 v0.16 加进主链的东西，不放进基准就等于没测。
    """

    answers = " ".join(case.answer for case in baseline_answers())

    assert "2ab" in answers or "3x^2" in answers, "缺隐式乘法"
    assert "所以" in answers or "相减得" in answers, "缺中文连接词"
    assert "≡" in answers, "缺恒等号"
    assert "第一步" in answers, "缺中文数字步骤"


def test_both_mutation_classes_are_populated():
    """变异器悄悄失效会让门禁空过——没有变异体时捕获率是 0/0，看着不像失败。"""

    _, mutants = build_text_population()

    conclusion = [case for case, is_conclusion in mutants if is_conclusion]
    steps = [case for case, is_conclusion in mutants if not is_conclusion]

    assert len(conclusion) >= 10
    assert len(steps) >= 10


def test_a_step_mutation_leaves_the_conclusion_line_untouched():
    """这是步骤扰动的定义。改了结论就变成另一类扰动了。"""

    correct, mutants = build_text_population()
    originals = {case.name: case for case in correct}

    for case, is_conclusion in mutants:
        if is_conclusion:
            continue
        original = originals[case.name.split("[")[0]]
        conclusion_line = case.answer.splitlines()[case.conclusion_line]
        assert conclusion_line == original.answer.splitlines()[case.conclusion_line]


def test_the_population_is_reproducible():
    first = [case.name for case, _ in build_text_population(seed=11)[1]]
    second = [case.name for case, _ in build_text_population(seed=11)[1]]

    assert first == second


# --- 度量结果 ---------------------------------------------------------


def test_no_correct_answer_is_falsely_rejected(report):
    """误拒是硬验收：把一条正确的解答告诉用户「找到反例了」。"""

    assert report.false_rejection_rate == 0.0


def test_every_baseline_survives_drafting(report):
    assert report.undraftable == 0


def test_conclusion_errors_are_caught_through_the_whole_chain(report):
    assert report.conclusion_capture >= 0.8


def test_step_errors_are_caught_through_the_whole_chain(report):
    """结论正确、中间某行写错——方法卡门禁就靠这个判断。"""

    assert report.step_capture >= 0.8


# --- 度量本身拒得动 ---------------------------------------------------


def test_a_broken_normaliser_shows_up_as_false_rejection(monkeypatch):
    """量不出问题的度量不是度量。

    关掉字母连写的拆分——那正是 v0.16 里真出过的回归——误拒率必须涨起来。
    """

    import math_harness.claim_drafting as drafting

    monkeypatch.setattr(drafting, "split_letter_runs", lambda text: text)

    broken = measure_text_mutation()

    assert broken.false_rejection_rate > 0.0
