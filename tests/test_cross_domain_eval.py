from __future__ import annotations

import json
from pathlib import Path

import pytest

from math_harness.cross_eval import (
    CrossDomainMethodCard,
    CrossDomainTrainingCase,
    _load_jsonl,
    text_shape,
    validate_isolation,
)
from math_harness.math_parser import SafeMathParser
from math_harness.models import EvaluationCase, EvaluationSlice

TRAIN = Path("data/pilot/cross_domain_train.jsonl")
HOLDOUT = Path("data/pilot/cross_domain_retrieval.jsonl")
METHODS = Path("data/pilot/cross_domain_methods.jsonl")

# 跨领域评测集的守卫。
#
# 这些不是为了让评测「跑得过」，是为了让评测**说的是实话**。语料出问题时指标照样会
# 出一个数字，只是那个数字不代表检索能力——v0.12 的检索聚合分是 1.0，而其中 78% 的题
# 在训练里有结构完全相同的样本。


@pytest.fixture(scope="module")
def training() -> list[CrossDomainTrainingCase]:
    return _load_jsonl(TRAIN, CrossDomainTrainingCase)


@pytest.fixture(scope="module")
def holdout() -> list[EvaluationCase]:
    return _load_jsonl(HOLDOUT, EvaluationCase)


@pytest.fixture(scope="module")
def cards() -> dict[str, CrossDomainMethodCard]:
    return {card.key: card for card in _load_jsonl(METHODS, CrossDomainMethodCard)}


# --- 泄漏与隔离 -------------------------------------------------------


def test_the_corpus_passes_its_own_isolation_guard(training, holdout):
    validate_isolation(training, holdout)


def test_no_holdout_problem_appears_verbatim_in_training(training, holdout):
    """守卫第一次跑就抓到过一条逐字相同的——这条测试保证它不会再溜回来。"""

    training_texts = {case.problem.strip() for case in training}

    assert [case.id for case in holdout if case.problem.strip() in training_texts] == []


def test_the_isolation_guard_can_actually_fail(training, holdout):
    """拒不动的守卫不是守卫。把一道留出题原样塞进训练集，它必须报错。"""

    poisoned = [
        *training,
        CrossDomainTrainingCase(
            problem=holdout[0].problem,
            solution="任意解答",
            method_key=holdout[0].expected_method_keys[0],
        ),
    ]

    with pytest.raises(ValueError, match="leaked into training"):
        validate_isolation(poisoned, holdout)


# --- 切片标注 ---------------------------------------------------------


def test_every_slice_label_matches_its_structural_fingerprint(training, holdout):
    """切片由指纹判定，不由手工标注。

    我第一版是靠直觉标的，24 条里只有 8 条标对——把本该是 control 的题标成了
    cross_family，整个难度分布因此失真。标注一旦和指纹脱钩，切片报数就没有意义了。
    """

    parser = SafeMathParser()
    methods_by_shape: dict[frozenset[str], set[str]] = {}
    for case in training:
        shape = text_shape(case.problem, parser)
        if shape:
            methods_by_shape.setdefault(shape, set()).add(case.method_key)

    def classify(case: EvaluationCase) -> str:
        shape = text_shape(case.problem, parser)
        if not shape:
            return EvaluationSlice.TEXT_ONLY.value
        if shape not in methods_by_shape:
            return EvaluationSlice.NOVEL_SHAPE.value
        if methods_by_shape[shape] == set(case.expected_method_keys):
            return EvaluationSlice.CONTROL.value
        return EvaluationSlice.CROSS_FAMILY.value

    mislabelled = [
        (case.id, case.slice.value, classify(case))
        for case in holdout
        if case.slice is not None and case.slice.value != classify(case)
    ]

    assert mislabelled == []


def test_every_slice_has_enough_cases_to_report(holdout):
    """某一格只有一两题时，那一格的分数是噪声，不是测量。"""

    counts: dict[str, int] = {}
    for case in holdout:
        counts[case.slice.value] = counts.get(case.slice.value, 0) + 1

    for slice_name in ("control", "cross_family", "novel_shape", "text_only"):
        assert counts.get(slice_name, 0) >= 5, (
            f"{slice_name} 只有 {counts.get(slice_name, 0)} 题"
        )


# --- 语料完整性 -------------------------------------------------------


def test_every_expected_method_is_taught_by_training(training, holdout):
    """留出题要的方法如果没有训练例题教过，那道题**不可能**答对。

    指标会掉，但掉的原因是语料残缺，不是检索变差。
    """

    taught = {case.method_key for case in training}
    expected = {key for case in holdout for key in case.expected_method_keys}

    assert expected - taught == set()


def test_every_taught_method_has_a_card(training, cards):
    assert {case.method_key for case in training} - set(cards) == set()


def test_method_cards_do_not_embed_their_source_problems(training, cards):
    """方法卡是**归纳过的**描述。

    第一版把训练题面原样塞进卡片，检索于是变成拿留出题面去匹配训练题面——量到的是
    两段文本像不像，不是找不找得到对的方法。
    """

    problems = {case.problem for case in training}
    card_text = " ".join(
        " ".join([card.goal, *card.applicable_when, *card.procedure])
        for card in cards.values()
    )

    for problem in problems:
        assert problem not in card_text


def test_the_corpus_covers_the_named_domains(training):
    domains = {case.domain for case in training}

    for domain in (
        "微积分",
        "线性代数",
        "组合数学",
        "复变函数",
        "平面几何",
        "数论",
        "离散数学",
    ):
        assert domain in domains, f"语料里没有{domain}"


def test_holdout_problems_are_unique(holdout):
    problems = [case.problem for case in holdout]

    assert len(problems) == len(set(problems))


def test_the_corpus_files_are_valid_jsonl():
    for path in (TRAIN, HOLDOUT, METHODS):
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if line.strip():
                json.loads(line), f"{path}:{line_number}"
