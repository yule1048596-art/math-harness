from __future__ import annotations

import random

import pytest

from math_harness.checks import (
    CheckContext,
    CheckOutcome,
    InstantiationCheck,
    StepInstantiationCheck,
    SymbolicEqualityCheck,
)
from math_harness.checks.measurement import (
    MAX_FALSE_REJECTION,
    LayerMeasurement,
    _is_equivalent,
    build_population,
    wilson_lower_bound,
)
from math_harness.checks.mutation import (
    MutationKind,
    baseline_cases,
    mutate_conclusion,
    mutate_step,
)

# 完整的统计门禁走 `math-harness-mutation --check`，一轮约 54 秒，属于发布前动作。
# 这里放的是必须每次都跑的正确性属性——尤其是「基准本身是对的」和「门禁真的拒得动」。


# --- 基准的正确性（最重要的一组）--------------------------------------
#
# 基准错了，捕获率和误拒率会一起失去意义：一条本就不成立的「正确用例」会被算成误拒，
# 而基于它造出来的变异体可能反倒是对的。这一组必须在改动基准后立刻发现问题。


@pytest.mark.parametrize("case", baseline_cases(), ids=lambda case: case.name)
def test_every_baseline_case_is_actually_correct(case):
    context = CheckContext(claim=case.claim, steps=case.steps)

    conclusion = InstantiationCheck(trials=8).run(context)
    assert conclusion.outcome is CheckOutcome.PASSED, (
        f"{case.name} 的结论不成立：{conclusion.detail}"
    )

    if case.steps:
        stepwise = StepInstantiationCheck(trials=8).run(context)
        assert stepwise.outcome is CheckOutcome.PASSED, (
            f"{case.name} 的推导不成立：{stepwise.detail}"
        )


def test_baselines_cover_the_named_domains():
    """用户点名了微积分、线代、几何、离散、组合、复变、竞赛等领域，基准要真的覆盖到。"""

    names = " ".join(case.name for case in baseline_cases())

    for domain in (
        "微积分",
        "线性代数",
        "组合",
        "离散",
        "复变",
        "几何",
        "数论",
        "竞赛",
    ):
        assert domain in names, f"基准里没有{domain}"


# --- 变异体本身的性质 -------------------------------------------------


def test_step_mutation_leaves_the_conclusion_correct():
    """这是步骤扰动的定义：结论不动，只改中间步。改了结论就变成另一类扰动了。"""

    rng = random.Random(0)
    original = next(case for case in baseline_cases() if case.steps)

    mutated = mutate_step(original, MutationKind.COEFFICIENT, rng)

    assert mutated is not None
    assert mutated.claim == original.claim
    assert mutated.steps != original.steps


def test_sign_mutation_actually_applies():
    """回归：候选判据漏了跳空格时，`3*x**2 + 2` 这种正常写法一个候选都找不到，
    符号扰动整类静默地测不到——门禁会因为「没有反例」而显得全绿。"""

    rng = random.Random(0)
    _, mutants, _ = build_population()

    assert any(m.mutation.kind is MutationKind.SIGN for m in mutants)
    assert (
        mutate_conclusion(baseline_cases()[0], MutationKind.SIGN, rng).mutation.mutated
        != baseline_cases()[0].claim.rhs
    )


def test_connective_mutation_reaches_the_logic_cases():
    """前四类扰动都是改代数式的文本，逻辑命题里一条都触发不了。"""

    _, mutants, _ = build_population()

    assert any(m.mutation.kind is MutationKind.CONNECTIVE for m in mutants)


def test_population_contains_both_mutation_classes():
    """变异器悄悄失效会让门禁空过——没有变异体时捕获率是 0/0，看着不像失败。"""

    correct, mutants, _ = build_population()

    conclusion = [m for m in mutants if m.mutation.targets_conclusion]
    steps = [m for m in mutants if not m.mutation.targets_conclusion]

    assert len(correct) >= 20
    assert len(conclusion) >= 20
    assert len(steps) >= 20


def test_population_is_reproducible():
    first = [case.name for case in build_population(seed=7)[1]]
    second = [case.name for case in build_population(seed=7)[1]]

    assert first == second


# --- 等价变异体 -------------------------------------------------------


def test_equivalence_oracle_detects_an_equivalent_mutant():
    """变异测试的经典陷阱：文本改了不代表意思改了。等价的变异体算成漏抓会低估这一层。"""

    assert _is_equivalent("4*a*b", "4*b*a")
    assert _is_equivalent("x**2 + 0", "x**2")
    assert not _is_equivalent("3*x**2 + 2", "4*x**2 + 2")
    assert not _is_equivalent("a**3 - b**3", "a**3 + b**3")


def test_unparseable_mutants_are_kept_not_dropped():
    """解析不了就当不等价：宁可多留一个变异体，也不要悄悄把用例扔掉——
    悄悄缩小样本会让捕获率虚高。"""

    assert not _is_equivalent("((((", "x")


# --- 分层能力：方法卡门禁的实证依据 -----------------------------------


def test_only_the_step_layer_catches_a_step_mutation():
    """整个方法卡门禁就建立在这条上：结论正确、某步写错的解，只查结论的层**全部放行**。

    方法卡是从推导提取的，所以这种解会让知识库学到一个错方法并在以后被检索复用。
    """

    rng = random.Random(3)
    case = next(c for c in baseline_cases() if c.name == "代数-平方差恒等式")
    mutated = mutate_step(case, MutationKind.SIGN, rng)
    assert mutated is not None
    context = CheckContext(claim=mutated.claim, steps=mutated.steps)

    assert SymbolicEqualityCheck().run(context).outcome is not CheckOutcome.FAILED
    assert InstantiationCheck(trials=8).run(context).outcome is not CheckOutcome.FAILED
    assert StepInstantiationCheck(trials=8).run(context).outcome is CheckOutcome.FAILED


def test_instantiation_catches_a_conclusion_mutation():
    rng = random.Random(3)
    case = next(c for c in baseline_cases() if c.name == "微积分-多项式求导")
    mutated = mutate_conclusion(case, MutationKind.COEFFICIENT, rng)
    assert mutated is not None

    result = InstantiationCheck(trials=8).run(CheckContext(claim=mutated.claim))

    assert result.outcome is CheckOutcome.FAILED
    assert result.counterexample


# --- 门禁判据本身 -----------------------------------------------------
#
# 拒不动的门禁不是门禁。这一组钉的是判据会在该拒的时候拒。


def test_wilson_lower_bound_penalises_small_samples():
    """3 个变异体抓到 1 个也是 0.33，但什么都说明不了。下界要把样本量算进去。"""

    assert wilson_lower_bound(1, 3) < wilson_lower_bound(40, 120)
    assert wilson_lower_bound(0, 50) == 0.0
    assert wilson_lower_bound(50, 50) > 0.9


def test_a_layer_that_catches_nothing_is_rejected():
    useless = LayerMeasurement(
        layer="useless", conclusion_total=44, capture_lower_bound=0.0
    )

    assert not useless.admitted


def test_a_layer_that_rejects_correct_answers_is_rejected():
    """误拒正确答案比漏抓错答案更糟：它会让用户不再相信可信度标注，分级就白做了。"""

    noisy = LayerMeasurement(
        layer="noisy",
        conclusion_capture=1.0,
        conclusion_total=44,
        capture_lower_bound=0.92,
        false_rejection=MAX_FALSE_REJECTION + 0.01,
    )

    assert not noisy.admitted


def test_a_layer_that_earns_its_place_is_admitted():
    good = LayerMeasurement(
        layer="good",
        conclusion_capture=1.0,
        conclusion_total=44,
        capture_lower_bound=0.92,
        false_rejection=0.0,
    )

    assert good.admitted


def test_gated_capture_uses_the_population_the_layer_targets():
    """只查结论的层抓不到步骤扰动是**正确行为**，混在一起算会把它冤枉成漏抓。"""

    conclusion_layer = LayerMeasurement(
        layer="conclusion_only",
        gated_on="conclusion",
        conclusion_capture=1.0,
        step_capture=0.0,
    )
    step_layer = LayerMeasurement(
        layer="step_only", gated_on="step", conclusion_capture=0.0, step_capture=1.0
    )

    assert conclusion_layer.gated_capture == 1.0
    assert step_layer.gated_capture == 1.0
