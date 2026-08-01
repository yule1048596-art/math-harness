from __future__ import annotations

from pathlib import Path

from math_harness.eval_cli import run_growth_evaluation
from math_harness.models import (
    EvaluationCase,
    EvaluationRequest,
    KnowledgeStatus,
    MethodStatusUpdate,
    WorkspaceCreate,
)
from math_harness.service import MathHarnessService

PROJECT_ROOT = Path(__file__).resolve().parents[1]


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
        retrieval_set_path=(PROJECT_ROOT / "data/pilot/asymptotic_retrieval_v2.jsonl"),
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
        retrieval_set_path=(PROJECT_ROOT / "data/pilot/asymptotic_retrieval_v2.jsonl"),
    )

    legacy = report["after"]
    real = report["retrieval_v2"]["after"]

    assert report["retrieval_v2"]["before"]["hit_at_1"] == 0
    # v0.4.0 时这里断言 real < legacy，用来钉住旧口径的水分。v0.5.0 加入
    # leaf-root path 与生成语料后真实口径追平到 1.0，那条断言已经过时。
    # 现在守住的是两件仍然要紧的事：真实口径不得回退，且不得反超旧口径——
    # 真实题面比方法卡改写句更难，反超只可能来自泄漏。
    assert real["hit_at_1"] >= 0.888888
    assert real["hit_at_1"] <= legacy["hit_at_1"]
    assert real["zero_result_rate"] == 0


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
