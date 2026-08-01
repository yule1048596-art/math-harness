from __future__ import annotations

import argparse
import json
from pathlib import Path

from pydantic import BaseModel

from math_harness.models import (
    EvaluationCase,
    EvaluationMetrics,
    EvaluationRequest,
    ExampleCreate,
    SolveEvaluationCase,
    SolveEvaluationRequest,
    WorkspaceCreate,
)
from math_harness.service import MathHarnessService


def _load_jsonl[ModelT: BaseModel](path: Path, model: type[ModelT]) -> list[ModelT]:
    records: list[ModelT] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                records.append(model.model_validate_json(line))
            except Exception as exc:
                raise ValueError(f"{path}:{line_number}: {exc}") from exc
    if not records:
        raise ValueError(f"dataset is empty: {path}")
    return records


def _delta(before: EvaluationMetrics, after: EvaluationMetrics) -> dict[str, float]:
    return {
        "hit_at_1": round(after.hit_at_1 - before.hit_at_1, 6),
        "recall_at_k": round(after.recall_at_k - before.recall_at_k, 6),
        "mean_reciprocal_rank": round(
            after.mean_reciprocal_rank - before.mean_reciprocal_rank, 6
        ),
        "zero_result_rate": round(after.zero_result_rate - before.zero_result_rate, 6),
    }


def run_growth_evaluation(
    data_root: Path,
    train_path: Path,
    holdout_path: Path,
    top_k: int,
    solve_holdout_path: Path | None = None,
    retrieval_set_path: Path | None = None,
) -> dict[str, object]:
    service = MathHarnessService(data_root)
    workspace = service.create_workspace(
        WorkspaceCreate(
            name="渐进估计成长评测",
            description="由固定训练集和留出集创建的可复现实验空间。",
        )
    )
    cases = _load_jsonl(holdout_path, EvaluationCase)
    training_examples = _load_jsonl(train_path, ExampleCreate)
    solve_cases = (
        _load_jsonl(solve_holdout_path, SolveEvaluationCase)
        if solve_holdout_path is not None
        else []
    )
    # 第二套口径：真实题面。旧留出集是方法卡的改写句，两者并跑才能看出差别。
    retrieval_cases = (
        _load_jsonl(retrieval_set_path, EvaluationCase)
        if retrieval_set_path is not None
        else []
    )

    baseline = service.evaluate_workspace(
        workspace.id,
        EvaluationRequest(name="before_learning", cases=cases, top_k=top_k),
    )
    retrieval_baseline = (
        service.evaluate_workspace(
            workspace.id,
            EvaluationRequest(
                name="retrieval_v2_before", cases=retrieval_cases, top_k=top_k
            ),
        )
        if retrieval_cases
        else None
    )
    ingestion_results = [
        service.ingest_example(workspace.id, example) for example in training_examples
    ]
    after = service.evaluate_workspace(
        workspace.id,
        EvaluationRequest(name="after_learning", cases=cases, top_k=top_k),
    )
    retrieval_after = (
        service.evaluate_workspace(
            workspace.id,
            EvaluationRequest(
                name="retrieval_v2_after", cases=retrieval_cases, top_k=top_k
            ),
        )
        if retrieval_cases
        else None
    )
    solve_gate = (
        service.evaluate_solver(
            workspace.id,
            SolveEvaluationRequest(
                name="heldout_solution_gate",
                cases=solve_cases,
                top_k=top_k,
            ),
        )
        if solve_cases
        else None
    )

    learned_keys = sorted(
        {
            method.key
            for result in ingestion_results
            for method in result.learned_methods
        }
    )
    verified_count = sum(
        result.example.verification.status == "verified" for result in ingestion_results
    )
    return {
        "workspace": workspace.model_dump(mode="json"),
        "dataset": {
            "train_path": str(train_path.resolve()),
            "holdout_path": str(holdout_path.resolve()),
            "training_examples": len(training_examples),
            "holdout_cases": len(cases),
            "solve_holdout_path": (
                str(solve_holdout_path.resolve()) if solve_holdout_path else None
            ),
            "solve_holdout_cases": len(solve_cases),
            "retrieval_set_path": (
                str(retrieval_set_path.resolve()) if retrieval_set_path else None
            ),
            "retrieval_set_cases": len(retrieval_cases),
        },
        "learning": {
            "verified_examples": verified_count,
            "learned_method_keys": learned_keys,
        },
        "before": baseline.metrics.model_dump(mode="json"),
        "after": after.metrics.model_dump(mode="json"),
        "solve_gate": (
            solve_gate.metrics.model_dump(mode="json") if solve_gate else None
        ),
        "delta": _delta(baseline.metrics, after.metrics),
        "retrieval_v2": (
            {
                "before": retrieval_baseline.metrics.model_dump(mode="json"),
                "after": retrieval_after.metrics.model_dump(mode="json"),
                "delta": _delta(retrieval_baseline.metrics, retrieval_after.metrics),
            }
            if retrieval_baseline and retrieval_after
            else None
        ),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Measure method-retrieval growth before and after training."
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        default=Path("eval-data"),
        help="Directory used for this evaluation's isolated workspace.",
    )
    parser.add_argument(
        "--train",
        type=Path,
        default=Path("data/pilot/asymptotic_train.jsonl"),
    )
    parser.add_argument(
        "--holdout",
        type=Path,
        default=Path("data/pilot/asymptotic_holdout.jsonl"),
    )
    parser.add_argument(
        "--solve-holdout",
        type=Path,
        default=Path("data/pilot/asymptotic_solve_holdout.jsonl"),
    )
    parser.add_argument(
        "--retrieval-set",
        type=Path,
        default=Path("data/pilot/asymptotic_retrieval_v2.jsonl"),
        help="真实题面的检索留出集，与旧口径并跑做对照。",
    )
    parser.add_argument("--top-k", type=int, default=3, choices=range(1, 21))
    return parser


def main() -> None:
    args = build_parser().parse_args()
    report = run_growth_evaluation(
        data_root=args.data_root,
        train_path=args.train,
        holdout_path=args.holdout,
        top_k=args.top_k,
        solve_holdout_path=args.solve_holdout,
        retrieval_set_path=args.retrieval_set,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
