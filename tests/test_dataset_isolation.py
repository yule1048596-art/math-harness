from __future__ import annotations

import json
from pathlib import Path

import pytest

from math_harness.eval_cli import (
    MAX_STRUCTURAL_OVERLAP,
    _load_jsonl,
    _structural_fingerprint,
    _validate_structural_isolation,
)
from math_harness.models import EvaluationCase, EvaluationSlice, ExampleCreate

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TRAIN_PATHS = [
    PROJECT_ROOT / "data/pilot/asymptotic_train.jsonl",
    PROJECT_ROOT / "data/pilot/asymptotic_train_generated.jsonl",
]
HOLDOUT_V3 = PROJECT_ROOT / "data/pilot/asymptotic_retrieval_v3.jsonl"
HOLDOUT_V2 = PROJECT_ROOT / "data/pilot/asymptotic_retrieval_v2.jsonl"


def _training() -> list[ExampleCreate]:
    return [
        example for path in TRAIN_PATHS for example in _load_jsonl(path, ExampleCreate)
    ]


def _holdout(path: Path) -> list[EvaluationCase]:
    return _load_jsonl(path, EvaluationCase)


# --- 守卫本身 ---------------------------------------------------------


def test_guard_rejects_a_structurally_saturated_holdout():
    """v0.12 的留出集必须被拒绝。

    它 78% 的题在训练集里有结构同构样本，而字面指纹检查放行了它——这正是聚合分数
    被顶到 1.0 的原因。这条断言是那个失败模式的守卫。
    """

    with pytest.raises(ValueError, match="structurally identical"):
        _validate_structural_isolation(_training(), _holdout(HOLDOUT_V2))


def test_guard_accepts_the_current_holdout():
    _validate_structural_isolation(_training(), _holdout(HOLDOUT_V3))


def test_guard_reports_the_offending_case_ids():
    with pytest.raises(ValueError) as excinfo:
        _validate_structural_isolation(_training(), _holdout(HOLDOUT_V2))

    # 报错要能直接指出是哪几题，否则修起来只能靠猜。
    assert "radical-diff-expansion" in str(excinfo.value)


def test_guard_ignores_cases_without_a_structured_target():
    """旧的方法卡改写句留出集没有 math_target，天然豁免。"""

    legacy = _load_jsonl(
        PROJECT_ROOT / "data/pilot/asymptotic_holdout.jsonl", EvaluationCase
    )

    _validate_structural_isolation(_training(), legacy)


def test_guard_is_a_noop_without_training_data():
    _validate_structural_isolation([], _holdout(HOLDOUT_V3))


# --- 数据集构成 -------------------------------------------------------


def test_holdout_overlap_stays_under_the_limit():
    training = {
        shape
        for example in _training()
        if example.math_payload is not None
        and (shape := _structural_fingerprint(example.math_payload)) is not None
    }
    cases = _holdout(HOLDOUT_V3)
    identical = sum(
        1 for case in cases if _structural_fingerprint(case.math_target) in training
    )

    assert identical / len(cases) <= MAX_STRUCTURAL_OVERLAP


def test_every_case_carries_a_slice_and_a_rationale():
    for case in _holdout(HOLDOUT_V3):
        assert case.slice is not None, case.id
        # 方法标签是人工判断，理由要写下来才复核得动。
        assert case.rationale, case.id


def test_slice_composition_matches_the_design():
    counts: dict[str, int] = {}
    for case in _holdout(HOLDOUT_V3):
        counts[case.slice.value] = counts.get(case.slice.value, 0) + 1

    assert counts == {
        EvaluationSlice.CONTROL.value: 6,
        EvaluationSlice.NOVEL_SHAPE.value: 12,
        EvaluationSlice.CROSS_FAMILY.value: 6,
        EvaluationSlice.MULTI_METHOD.value: 6,
    }


def test_novel_and_cross_family_shapes_are_absent_from_training():
    """这两个切片的全部价值就在于形状没被见过，必须逐题钉死。"""

    training = {
        shape
        for example in _training()
        if example.math_payload is not None
        and (shape := _structural_fingerprint(example.math_payload)) is not None
    }
    novel = {EvaluationSlice.NOVEL_SHAPE, EvaluationSlice.CROSS_FAMILY}

    for case in _holdout(HOLDOUT_V3):
        if case.slice in novel:
            assert _structural_fingerprint(case.math_target) not in training, case.id


def test_control_shapes_are_present_in_training():
    training = {
        shape
        for example in _training()
        if example.math_payload is not None
        and (shape := _structural_fingerprint(example.math_payload)) is not None
    }

    for case in _holdout(HOLDOUT_V3):
        if case.slice is EvaluationSlice.CONTROL:
            assert _structural_fingerprint(case.math_target) in training, case.id


def test_multi_method_cases_really_expect_several_methods():
    """单标签下 Recall@K 恒 ≥ Hit@1，只有多标签才让它携带信息。"""

    cases = [
        case
        for case in _holdout(HOLDOUT_V3)
        if case.slice is EvaluationSlice.MULTI_METHOD
    ]

    assert cases
    for case in cases:
        assert len(case.expected_method_keys) >= 2, case.id


def test_case_ids_are_unique():
    ids = [case.id for case in _holdout(HOLDOUT_V3)]

    assert len(ids) == len(set(ids))


def test_holdout_file_is_valid_jsonl():
    for line_number, line in enumerate(
        HOLDOUT_V3.read_text(encoding="utf-8").splitlines(), start=1
    ):
        assert json.loads(line), line_number
