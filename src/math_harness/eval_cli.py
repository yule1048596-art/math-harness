from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel

from math_harness.methods import MethodExtractor
from math_harness.models import (
    EvaluationCase,
    EvaluationCaseResult,
    EvaluationMetrics,
    EvaluationRequest,
    EvaluationRun,
    ExampleCreate,
    SolveEvaluationCase,
    SolveEvaluationRequest,
    SolveMathTarget,
    WorkspaceCreate,
)
from math_harness.service import MathHarnessService
from math_harness.solving import OfflineSympySolutionGenerator
from math_harness.structure import extract_features


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


def _aggregate(results: Sequence[EvaluationCaseResult]) -> EvaluationMetrics:
    count = len(results)
    return EvaluationMetrics(
        case_count=count,
        hit_at_1=round(sum(result.hit_at_1 for result in results) / count, 6),
        recall_at_k=round(sum(result.recall_at_k for result in results) / count, 6),
        mean_reciprocal_rank=round(
            sum(result.reciprocal_rank for result in results) / count, 6
        ),
        zero_result_rate=round(
            sum(1 for result in results if not result.returned_method_keys) / count, 6
        ),
    )


def _metrics_by_slice(
    cases: Sequence[EvaluationCase],
    run: EvaluationRun,
) -> dict[str, dict[str, object]]:
    """按切片分开给指标。

    切片是评测报表的概念，不进存储层——这里按 case id 关联回输入即可。空切片会被
    省略，因为 `EvaluationMetrics.case_count` 要求至少 1。
    """

    slices = {case.id: case.slice for case in cases if case.slice is not None}
    grouped: dict[str, list[EvaluationCaseResult]] = {}
    for result in run.cases:
        name = slices.get(result.case_id)
        if name is not None:
            grouped.setdefault(name.value, []).append(result)
    return {
        name: _aggregate(results).model_dump(mode="json")
        for name, results in sorted(grouped.items())
    }


def _target_fingerprint(target: SolveMathTarget) -> str:
    """Identify one mathematical task while ignoring its known training answer."""

    payload = target.model_dump(mode="json", exclude_none=True)
    payload.pop("expected", None)
    payload["expression"] = "".join(target.expression.split())
    payload["point"] = "".join(target.point.split())
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


# 允许的结构同构比例上限。
#
# 不设为 0 是刻意的：留出集里保留一个小的同构对照切片，才有基线可比——对照切片掉分
# 才说明检索真的坏了。超过这个比例，指标衡量的就主要是记忆而不是泛化。
MAX_STRUCTURAL_OVERLAP = 0.40


def _structural_fingerprint(target: SolveMathTarget) -> frozenset[str] | None:
    """一道题在检索器眼里的形状：算子树上的叶到根路径集合。"""

    paths = extract_features(target).paths
    return frozenset(paths) if paths else None


def _validate_structural_isolation(
    training_examples: Sequence[ExampleCreate],
    holdout_cases: Sequence[EvaluationCase | SolveEvaluationCase],
) -> None:
    """留出集与训练集在**结构**上重叠过多时让评测失败。

    字面指纹拦不住这个：路径表示对系数不敏感，数字统一归为 `NUM`，所以
    `sqrt(x**2+3*x)-x` 和 `sqrt(x**2+5*x)-x` 能通过表达式判重，却是检索器眼中的
    同一道题。v0.12 的留出集有 78% 属于这种情况，聚合分数因此被顶到 1.0。
    """

    training_shapes = {
        shape
        for example in training_examples
        if example.math_payload is not None
        and (shape := _structural_fingerprint(example.math_payload)) is not None
    }
    if not training_shapes:
        return

    checkable = [case for case in holdout_cases if case.math_target is not None]
    if not checkable:
        return

    identical = [
        case
        for case in checkable
        if _structural_fingerprint(case.math_target) in training_shapes
    ]
    ratio = len(identical) / len(checkable)
    if ratio > MAX_STRUCTURAL_OVERLAP:
        listed = ", ".join(sorted(case.id for case in identical)[:10])
        raise ValueError(
            "dataset isolation failed; "
            f"{len(identical)}/{len(checkable)} holdout cases ({ratio:.0%}) have a "
            f"structurally identical training example, above the "
            f"{MAX_STRUCTURAL_OVERLAP:.0%} limit — the metric would measure recall of "
            f"seen shapes, not generalization: {listed}"
        )


def _validate_dataset_isolation(
    training_examples: Sequence[ExampleCreate],
    holdout_cases: Sequence[EvaluationCase | SolveEvaluationCase],
) -> None:
    """Fail before evaluation when train data is duplicated or leaks into holdouts."""

    seen_training: dict[str, str] = {}
    duplicate_training: set[str] = set()
    for example in training_examples:
        if example.math_payload is None:
            continue
        fingerprint = _target_fingerprint(example.math_payload)
        expression = "".join(example.math_payload.expression.split())
        if fingerprint in seen_training:
            duplicate_training.add(expression)
        else:
            seen_training[fingerprint] = expression

    holdout_expressions = {
        _target_fingerprint(case.math_target)
        for case in holdout_cases
        if case.math_target is not None
    }
    overlap = {
        seen_training[fingerprint]
        for fingerprint in set(seen_training) & holdout_expressions
    }

    errors: list[str] = []
    if duplicate_training:
        errors.append(
            "duplicate training expression(s): " + ", ".join(sorted(duplicate_training))
        )
    if overlap:
        errors.append(
            "training/holdout expression overlap: " + ", ".join(sorted(overlap))
        )
    if errors:
        raise ValueError("dataset isolation failed; " + "; ".join(errors))


def run_growth_evaluation(
    data_root: Path,
    train_paths: Sequence[Path],
    holdout_path: Path,
    top_k: int,
    solve_holdout_path: Path | None = None,
    retrieval_set_path: Path | None = None,
) -> dict[str, object]:
    cases = _load_jsonl(holdout_path, EvaluationCase)
    training_examples = [
        example for path in train_paths for example in _load_jsonl(path, ExampleCreate)
    ]
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
    _validate_dataset_isolation(
        training_examples,
        [*cases, *retrieval_cases, *solve_cases],
    )
    # 结构守卫只管检索口径。求解门禁跑的是确定性 SymPy，它完全不从训练集学习，
    # 结构重叠不会抬高它的分数；把它算进来只会得出误导性的比例。
    # 旧的方法卡改写句留出集没有 math_target，自然被跳过。
    _validate_structural_isolation(training_examples, retrieval_cases)

    # Evaluation is hermetic: local .env provider choices must never turn a
    # reproducibility check into a paid/networked model call.
    service = MathHarnessService(
        data_root,
        extractor=MethodExtractor(),
        generator=OfflineSympySolutionGenerator(),
    )
    workspace = service.create_workspace(
        WorkspaceCreate(
            name="渐进估计成长评测",
            description="由固定训练集和留出集创建的可复现实验空间。",
        )
    )

    baseline = service.evaluate_workspace(
        workspace.id,
        EvaluationRequest(name="before_learning", cases=cases, top_k=top_k),
    )
    retrieval_baseline = (
        service.evaluate_workspace(
            workspace.id,
            EvaluationRequest(
                name="retrieval_before", cases=retrieval_cases, top_k=top_k
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
                name="retrieval_after", cases=retrieval_cases, top_k=top_k
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
            "train_paths": [str(path.resolve()) for path in train_paths],
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
        "retrieval": (
            {
                "before": retrieval_baseline.metrics.model_dump(mode="json"),
                "after": retrieval_after.metrics.model_dump(mode="json"),
                "delta": _delta(retrieval_baseline.metrics, retrieval_after.metrics),
                # 聚合数字单独看不再可信：v0.12 的 1.0 就是被聚合掩盖的。
                "by_slice": _metrics_by_slice(retrieval_cases, retrieval_after),
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
        action="append",
        dest="train",
        help="训练语料，可重复传入以合并多份（人工语料 + 生成语料）。",
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
        default=Path("data/pilot/asymptotic_retrieval_v3.jsonl"),
        help="真实题面的检索留出集，与旧口径并跑做对照。",
    )
    parser.add_argument("--top-k", type=int, default=3, choices=range(1, 21))
    return parser


def main() -> None:
    args = build_parser().parse_args()
    report = run_growth_evaluation(
        data_root=args.data_root,
        train_paths=args.train
        or [
            Path("data/pilot/asymptotic_train.jsonl"),
            Path("data/pilot/asymptotic_train_generated.jsonl"),
        ],
        holdout_path=args.holdout,
        top_k=args.top_k,
        solve_holdout_path=args.solve_holdout,
        retrieval_set_path=args.retrieval_set,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
