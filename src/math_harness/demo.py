from __future__ import annotations

import argparse
import json
from pathlib import Path

from math_harness.models import (
    ExampleCreate,
    MathPayload,
    SolveMathTarget,
    SolveRequest,
    WorkspaceCreate,
)
from math_harness.service import MathHarnessService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the first Math Harness vertical slice."
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        default=Path("demo-data"),
        help="Directory used for the workspace registry and databases.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    service = MathHarnessService(args.data_root)
    asymptotic = service.create_workspace(
        WorkspaceCreate(name="渐进估计", description="学习渐进展开与主导项分析")
    )
    unrelated = service.create_workspace(
        WorkspaceCreate(name="线性代数", description="用于验证跨空间隔离")
    )

    result = service.ingest_example(
        asymptotic.id,
        ExampleCreate(
            problem="求 x→∞ 时 sqrt(x^2+x)-x 的渐进展开到 O(x^-2)",
            solution=(
                "先乘共轭式有理化，再令 t=1/x，并使用泰勒展开，"
                "得到 1/2-1/(8x)+O(x^-2)。"
            ),
            tags=["渐进估计", "根式", "无穷远"],
            math_payload=MathPayload(
                expression="sqrt(x**2 + x) - x",
                expected="1/2 - 1/(8*x)",
                variable="x",
                point="oo",
                remainder_power=2,
            ),
        ),
    )
    plan = service.build_solve_plan(
        asymptotic.id,
        "根式相减造成抵消时，怎样求无穷远处的渐进展开？",
        tags=["asymptotic", "radical"],
    )
    attempt = service.solve_problem(
        asymptotic.id,
        SolveRequest(
            problem="求 sqrt(x^2+x)-x 在 x→∞ 时到 O(x^-2) 的渐进展开",
            tags=["asymptotic", "radical"],
            math_target=SolveMathTarget(
                expression="sqrt(x**2 + x) - x",
                variable="x",
                point="oo",
                remainder_power=2,
            ),
        ),
    )

    output = {
        "workspace": asymptotic.model_dump(mode="json"),
        "verification": result.example.verification.model_dump(mode="json"),
        "learned_methods": [
            method.model_dump(mode="json") for method in result.learned_methods
        ],
        "solve_plan": plan.model_dump(mode="json"),
        "solution_attempt": attempt.model_dump(mode="json"),
        "isolation_check": {
            "other_workspace_id": unrelated.id,
            "other_workspace_examples": len(service.list_examples(unrelated.id)),
            "other_workspace_methods": len(service.list_methods(unrelated.id)),
        },
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
