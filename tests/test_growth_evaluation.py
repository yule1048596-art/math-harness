from __future__ import annotations

from pathlib import Path

import pytest

from math_harness.eval_cli import (
    _load_jsonl,
    _validate_dataset_isolation,
    run_growth_evaluation,
)
from math_harness.models import (
    EvaluationCase,
    EvaluationRequest,
    ExampleCreate,
    KnowledgeStatus,
    MethodStatusUpdate,
    SolveEvaluationCase,
    WorkspaceCreate,
)
from math_harness.service import MathHarnessService

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_pilot_training_and_all_holdouts_are_globally_isolated():
    training = [
        example
        for path in (
            PROJECT_ROOT / "data/pilot/asymptotic_train.jsonl",
            PROJECT_ROOT / "data/pilot/asymptotic_train_generated.jsonl",
        )
        for example in _load_jsonl(path, ExampleCreate)
    ]
    holdouts = [
        *_load_jsonl(
            PROJECT_ROOT / "data/pilot/asymptotic_holdout.jsonl", EvaluationCase
        ),
        *_load_jsonl(
            PROJECT_ROOT / "data/pilot/asymptotic_retrieval_v2.jsonl",
            EvaluationCase,
        ),
        *_load_jsonl(
            PROJECT_ROOT / "data/pilot/asymptotic_solve_holdout.jsonl",
            SolveEvaluationCase,
        ),
    ]

    _validate_dataset_isolation(training, holdouts)


def test_dataset_preflight_rejects_duplicate_training_expressions():
    example = ExampleCreate(
        problem="重复题",
        solution="重复解",
        reviewed=True,
        math_payload={
            "expression": "1 / (x + 1)",
            "expected": "1/x - 1/x**2",
            "point": "oo",
            "remainder_power": 3,
        },
    )

    with pytest.raises(ValueError, match="duplicate training expression"):
        _validate_dataset_isolation([example, example.model_copy()], [])


def test_growth_evaluation_improves_on_pilot_holdout(tmp_path):
    report = run_growth_evaluation(
        data_root=tmp_path,
        train_paths=[
            PROJECT_ROOT / "data/pilot/asymptotic_train.jsonl",
            PROJECT_ROOT / "data/pilot/asymptotic_train_generated.jsonl",
        ],
        holdout_path=PROJECT_ROOT / "data/pilot/asymptotic_holdout.jsonl",
        top_k=3,
        solve_holdout_path=(PROJECT_ROOT / "data/pilot/asymptotic_solve_holdout.jsonl"),
        retrieval_set_path=(PROJECT_ROOT / "data/pilot/asymptotic_retrieval_v3.jsonl"),
    )

    # 训练集整体必须可验证：任何一条过不了 SymPy，它的方法就不会晋级，
    # 也就不会进入检索，评测结论会被静默地稀释。
    assert (
        report["learning"]["verified_examples"]
        == report["dataset"]["training_examples"]
    )
    assert report["before"]["recall_at_k"] == 0
    assert report["after"]["recall_at_k"] > report["before"]["recall_at_k"]
    assert report["after"]["mean_reciprocal_rank"] > 0
    assert report["after"]["zero_result_rate"] == 0
    assert report["solve_gate"]["case_count"] == 6
    assert report["dataset"]["solve_holdout_cases"] == 6
    assert report["solve_gate"]["verified_rate"] == 0.666667
    assert report["solve_gate"]["needs_review_rate"] == 0.333333
    assert report["solve_gate"]["generation_failure_rate"] == 0


def test_real_problem_retrieval_set_is_reported_alongside_legacy_holdout(tmp_path):
    report = run_growth_evaluation(
        data_root=tmp_path,
        train_paths=[
            PROJECT_ROOT / "data/pilot/asymptotic_train.jsonl",
            PROJECT_ROOT / "data/pilot/asymptotic_train_generated.jsonl",
        ],
        holdout_path=PROJECT_ROOT / "data/pilot/asymptotic_holdout.jsonl",
        top_k=3,
        retrieval_set_path=(PROJECT_ROOT / "data/pilot/asymptotic_retrieval_v3.jsonl"),
    )

    legacy = report["after"]
    real = report["retrieval"]["after"]
    by_slice = report["retrieval"]["by_slice"]

    assert report["retrieval"]["before"]["hit_at_1"] == 0
    assert real["zero_result_rate"] == 0
    assert real["hit_at_1"] <= legacy["hit_at_1"]

    # 这里曾经断言聚合 hit@1 >= 0.888888（v0.5.0 加的防回退）。v0.13.0 换上有区分度
    # 的留出集后它必然失败，而且**应当**失败：那个 0.889 是被同构样本顶起来的。
    #
    # 回退防护改挂在对照切片上——它与训练同构，掉分才说明检索真的坏了。其余切片只
    # 记录基线、不设下限，否则下次又会有人靠放宽留出集难度来让测试变绿。
    assert by_slice["control"]["hit_at_1"] == 1.0
    assert by_slice["control"]["zero_result_rate"] == 0

    # 泛化切片的分数明显低于对照切片，这正是旧标尺掩盖掉的差距。
    assert by_slice["novel_shape"]["hit_at_1"] < by_slice["control"]["hit_at_1"]

    # 多方法切片让 Recall@K 重新携带信息：单标签时它恒 >= Hit@1，只有多标签才可能
    # 出现「首位命中但召不全」。
    assert by_slice["multi_method"]["recall_at_k"] < 1.0

    # 四个切片都要有数，缺哪个都说明数据集或分组坏了。
    assert set(by_slice) == {
        "control",
        "novel_shape",
        "cross_family",
        "multi_method",
    }


def test_evaluations_and_manual_status_changes_are_audited(
    tmp_path, verified_asymptotic_example
):
    service = MathHarnessService(tmp_path)
    workspace = service.create_workspace(WorkspaceCreate(name="审计测试"))
    ingestion = service.ingest_example(workspace.id, verified_asymptotic_example)
    rationalization = next(
        method
        for method in ingestion.learned_methods
        if method.key == "rationalization"
    )

    run = service.evaluate_workspace(
        workspace.id,
        EvaluationRequest(
            name="one_case",
            top_k=1,
            cases=[
                EvaluationCase(
                    id="case-1",
                    problem="根式相减导致抵消",
                    tags=["radical", "cancellation"],
                    expected_method_keys=["rationalization"],
                )
            ],
        ),
    )
    updated = service.update_method_status(
        workspace.id,
        rationalization.id,
        MethodStatusUpdate(status=KnowledgeStatus.DEPRECATED),
    )
    events = service.list_learning_events(workspace.id)

    assert run.metrics.hit_at_1 == 1
    assert service.list_evaluations(workspace.id) == [run]
    assert updated.status is KnowledgeStatus.DEPRECATED
    assert updated.version == rationalization.version + 1
    assert any(event.event_type == "evaluation_completed" for event in events)
    status_event = next(
        event for event in events if event.event_type == "method_status_changed"
    )
    assert status_event.payload == {"from": "promoted", "to": "deprecated"}
    assert all(
        method.id != rationalization.id for method in service.list_methods(workspace.id)
    )
    assert any(
        method.id == rationalization.id
        for method in service.list_methods(
            workspace.id,
            include_deprecated=True,
        )
    )
