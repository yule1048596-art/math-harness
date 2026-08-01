from __future__ import annotations

import argparse
import json
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from math_harness.models import (
    ExampleCreate,
    GenerationStatus,
    MathPayload,
    SolveMathTarget,
    VerificationMode,
    VerificationStatus,
)
from math_harness.solving import OfflineSympySolutionGenerator
from math_harness.verifier import SolutionVerifier


def _discover_project_root() -> Path:
    """Find checked-out pilot data in editable and wheel-based CLI runs."""

    source_root = Path(__file__).resolve().parents[2]
    for candidate in (source_root, Path.cwd().resolve()):
        if (candidate / "data/pilot").is_dir():
            return candidate
    return source_root


_PROJECT_ROOT = _discover_project_root()

# 按方法家族程序化生成训练语料。
#
# 两条设计原则：
#   1. 不写新的数学代码——答案由现成的离线求解器算，正确性由现成的验证器裁决。
#      生成数据不因为「是我们自己生成的」就被信任，它走的是和人工数据完全相同的
#      那条 SymPy 裁决线。
#   2. 完全确定性——参数网格是显式整数序列，没有随机，输出按稳定键排序。同一条
#      命令必须产出逐字节相同的文件，否则评测不可复现。
#
# 注意：这里的方法归属是「由构造决定」的，不是从解答里推断出来的。这对训练语料
# 可以接受（本就是监督数据），但意味着它绝不能用来生成留出集——那会让评测变成
# 自证。留出集永远保持人工标注。


@dataclass(frozen=True)
class FamilyInstance:
    """一道待验证的候选题。

    `expected` 留空时由离线求解器补全。离线求解器覆盖面有限（Stirling、指数和、
    根式对相减都算不出来），这些形状可以直接写出候选答案——但**候选答案仍然必须
    通过验证器**才会进入语料。裁决权始终在验证器，不在这里。
    """

    problem: str
    expression: str
    expected: str | None = None
    variable: str = "x"
    parameters: tuple[str, ...] = ()
    assumptions: tuple[tuple[str, tuple[str, ...]], ...] = ()
    remainder_power: int | None = None


@dataclass(frozen=True)
class ProblemFamily:
    key: str
    method_key: str
    mode: VerificationMode
    point: str
    tags: tuple[str, ...]
    # 解答文本必须命中该方法模板的 marker，否则规则提取器抓不到这个方法。
    solution: str
    instances: tuple[FamilyInstance, ...] = field(default_factory=tuple)


def _rationalization() -> tuple[FamilyInstance, ...]:
    instances: list[FamilyInstance] = []
    for b in (1, 2, 3, 5, 7):
        for c in (0, 1, 3):
            # b^2 == 4c 时根号内是完全平方，表达式退化成含绝对值的分段形式。
            # SymPy 在这类形状上会长时间卡住，且它也不是有理化要练的结构。
            if b * b == 4 * c:
                continue
            tail = f" + {c}" if c else ""
            instances.append(
                FamilyInstance(
                    problem=(
                        f"求 x→∞ 时 sqrt(x^2 + {b}x{tail}) - x 到 O(x^-2) 的渐进展开。"
                    ),
                    expression=f"sqrt(x**2 + {b}*x{tail}) - x",
                    remainder_power=2,
                )
            )
    return tuple(instances)


def _rationalization_pairs() -> tuple[FamilyInstance, ...]:
    # sqrt(x^2+bx) - sqrt(x^2-bx) ~ b。离线求解器给不出，显式提供后仍需过验证器。
    return tuple(
        FamilyInstance(
            problem=f"求 x→∞ 时 sqrt(x^2 + {b}x) - sqrt(x^2 - {b}x) 的渐进等价式。",
            expression=f"sqrt(x**2 + {b}*x) - sqrt(x**2 - {b}*x)",
            expected=str(b),
        )
        for b in (2, 3, 4, 5, 6, 8)
    )


def _rationalization_negative() -> tuple[FamilyInstance, ...]:
    return tuple(
        FamilyInstance(
            problem=f"求 x→-∞ 时 sqrt(x^2 + {b}x) + x 到 O(x^-2) 的渐进展开。",
            expression=f"sqrt(x**2 + {b}*x) + x",
            remainder_power=2,
        )
        for b in (2, 3, 4, 5, 6, 8)
    )


def _variable_inversion() -> tuple[FamilyInstance, ...]:
    instances = [
        FamilyInstance(
            problem=f"求 x→∞ 时 1/(x + {k}) 到 O(x^-3) 的渐进展开。",
            expression=f"1/(x + {k})",
            remainder_power=3,
        )
        for k in (1, 2, 3, 4, 5, 7)
    ]
    instances += [
        FamilyInstance(
            problem=f"求 x→∞ 时 x/({k}x + 1) 到 O(x^-2) 的渐进展开。",
            expression=f"x/({k}*x + 1)",
            remainder_power=2,
        )
        for k in (1, 2, 3, 5)
    ]
    return tuple(instances)


def _variable_inversion_limit() -> tuple[FamilyInstance, ...]:
    instances = [
        FamilyInstance(
            problem=f"求 x→∞ 时 x*sin({k}/x) 的极限。",
            expression=f"x*sin({k}/x)",
        )
        for k in (1, 2, 3, 4, 5)
    ]
    instances += [
        FamilyInstance(
            problem=f"求 x→∞ 时 x*(exp({k}/x) - 1) 的极限。",
            expression=f"x*(exp({k}/x) - 1)",
        )
        for k in (1, 2, 3, 4)
    ]
    instances += [
        FamilyInstance(
            problem=f"求 x→∞ 时 x*tan({k}/x) 的极限。",
            expression=f"x*tan({k}/x)",
        )
        for k in (1, 2, 3)
    ]
    return tuple(instances)


def _taylor() -> tuple[FamilyInstance, ...]:
    instances: list[FamilyInstance] = []
    for k in (1, 2, 3, 4, 5):
        instances.append(
            FamilyInstance(
                problem=f"求 log(1 + {k}x) 在 x=0 附近到 O(x^3) 的展开。",
                expression=f"log(1 + {k}*x)",
                remainder_power=3,
            )
        )
        instances.append(
            FamilyInstance(
                problem=f"求 sin({k}x) 在 x=0 附近到 O(x^4) 的展开。",
                expression=f"sin({k}*x)",
                remainder_power=4,
            )
        )
        instances.append(
            FamilyInstance(
                problem=f"求 sqrt(1 + {k}x) 在 x=0 附近到 O(x^3) 的展开。",
                expression=f"sqrt(1 + {k}*x)",
                remainder_power=3,
            )
        )
        instances.append(
            FamilyInstance(
                problem=f"求 exp({k}x) 在 x=0 附近到 O(x^3) 的展开。",
                expression=f"exp({k}*x)",
                remainder_power=3,
            )
        )
        instances.append(
            FamilyInstance(
                problem=f"求 x→0 时 (1 - cos({k}x))/x^2 的极限。",
                expression=f"(1 - cos({k}*x))/x**2",
            )
        )
    return tuple(instances)


def _dominant_balance() -> tuple[FamilyInstance, ...]:
    instances: list[FamilyInstance] = []
    for a in (1, 4, 9, 16):
        for b in (1, 3, 5):
            instances.append(
                FamilyInstance(
                    problem=f"求 x→∞ 时 sqrt({a}x^2 + {b}x) 的渐进等价式。",
                    expression=f"sqrt({a}*x**2 + {b}*x)",
                )
            )
    for p in (2, 3, 4, 5):
        instances.append(
            FamilyInstance(
                problem=f"求 x→∞ 时 x^{p} + x^{p - 1} 的渐进等价式。",
                expression=f"x**{p} + x**{p - 1}",
            )
        )
    for m in (2, 3, 4, 5):
        # 指数和的主导项：离线求解器算不出，显式给出后仍需过验证器。
        instances.append(
            FamilyInstance(
                problem=f"求 x→∞ 时 exp({m}x) + exp(x) 的渐进等价式。",
                expression=f"exp({m}*x) + exp(x)",
                expected=f"exp({m}*x)",
            )
        )
    return tuple(instances)


def _log_transform() -> tuple[FamilyInstance, ...]:
    instances: list[FamilyInstance] = []
    for k in (1, 2, 3, 4, 5):
        for m in (1, 2, 3, 4):
            power = "x" if m == 1 else f"({m}*x)"
            shown = "x" if m == 1 else f"{m}x"
            instances.append(
                FamilyInstance(
                    problem=f"求 x→∞ 时 (1 + {k}/x)^{shown} 的极限。",
                    expression=f"(1 + {k}/x)**{power}",
                )
            )
    for k in (1, 2, 3, 4):
        instances.append(
            FamilyInstance(
                problem=f"求 x→∞ 时 (1 - {k}/x)^x 的极限。",
                expression=f"(1 - {k}/x)**x",
            )
        )
    return tuple(instances)


def _stirling() -> tuple[FamilyInstance, ...]:
    # Stirling 形式离线求解器给不出，显式提供；每条仍必须通过验证器。
    base = "sqrt(2*pi*n)*(n/E)**n"
    instances = [
        FamilyInstance(
            problem="求 n→∞ 时 Gamma(n+1) 的渐进等价式。",
            expression="gamma(n + 1)",
            expected=base,
            variable="n",
        ),
        FamilyInstance(
            problem="求 n→∞ 时 factorial(n) 的渐进等价式。",
            expression="factorial(n)",
            expected=base,
            variable="n",
        ),
    ]
    instances += [
        FamilyInstance(
            problem=f"求 n→∞ 时 Gamma(n + {k}) 的渐进等价式。",
            expression=f"gamma(n + {k})",
            expected=f"n**{k - 1}*{base}",
            variable="n",
        )
        for k in (2, 3, 4, 5, 6)
    ]
    instances += [
        FamilyInstance(
            problem=f"求 n→∞ 时 Gamma(n+1)/n^{p} 的渐进等价式。",
            expression=f"gamma(n + 1)/n**{p}",
            expected=f"{base}/n**{p}",
            variable="n",
        )
        for p in (1, 2, 3)
    ]
    instances += [
        FamilyInstance(
            problem=f"求 n→∞ 时 {k}^n * factorial(n) 的渐进等价式。",
            expression=f"{k}**n*factorial(n)",
            expected=f"{k}**n*{base}",
            variable="n",
        )
        for k in (2, 3, 4)
    ]
    return tuple(instances)


FAMILIES: tuple[ProblemFamily, ...] = (
    ProblemFamily(
        key="radical_expansion",
        method_key="rationalization",
        mode=VerificationMode.ASYMPTOTIC_EXPANSION,
        point="oo",
        tags=("渐进估计", "根式", "无穷远"),
        solution=(
            "先乘共轭式有理化，把根式之差改写为商以消除主项抵消，再按目标精度展开。"
        ),
        instances=_rationalization(),
    ),
    ProblemFamily(
        key="radical_expansion_negative",
        method_key="rationalization",
        mode=VerificationMode.ASYMPTOTIC_EXPANSION,
        point="-oo",
        tags=("渐进估计", "根式", "负无穷"),
        solution=(
            "先做共轭有理化处理无穷减无穷的抵消，注意负无穷方向上的符号，再展开。"
        ),
        instances=_rationalization_negative(),
    ),
    ProblemFamily(
        key="radical_equivalence",
        method_key="rationalization",
        mode=VerificationMode.ASYMPTOTIC_EQUIVALENCE,
        point="oo",
        tags=("渐进估计", "根式", "抵消"),
        solution="两个根式相减先做共轭有理化，再比较分子分母的主导阶。",
        instances=_rationalization_pairs(),
    ),
    ProblemFamily(
        key="reciprocal_expansion",
        method_key="variable_inversion",
        mode=VerificationMode.ASYMPTOTIC_EXPANSION,
        point="oo",
        tags=("渐进展开", "变量倒换", "无穷远"),
        solution="令 t=1/x 做变量倒换把无穷远化为零点，在零点附近展开后代回。",
        instances=_variable_inversion(),
    ),
    ProblemFamily(
        key="reciprocal_limit",
        method_key="variable_inversion",
        mode=VerificationMode.LIMIT,
        point="oo",
        tags=("极限", "变量倒换", "无穷远"),
        solution="令 t=1/x 把无穷远处的极限化为零点附近的局部问题再求解。",
        instances=_variable_inversion_limit(),
    ),
    ProblemFamily(
        key="series_at_zero",
        method_key="taylor_expansion",
        mode=VerificationMode.ASYMPTOTIC_EXPANSION,
        point="0",
        tags=("渐进展开", "零点", "泰勒"),
        solution="按目标精度直接使用泰勒展开，展开后检查主项是否抵消。",
        instances=tuple(item for item in _taylor() if item.remainder_power is not None),
    ),
    ProblemFamily(
        key="series_limit_at_zero",
        method_key="taylor_expansion",
        mode=VerificationMode.LIMIT,
        point="0",
        tags=("极限", "零点", "泰勒"),
        solution="对分子做泰勒展开，与分母同阶相消后取极限。",
        instances=tuple(item for item in _taylor() if item.remainder_power is None),
    ),
    ProblemFamily(
        key="leading_term",
        method_key="dominant_balance",
        mode=VerificationMode.ASYMPTOTIC_EQUIVALENCE,
        point="oo",
        tags=("渐进估计", "主导平衡", "主导项"),
        solution="为各项写出候选数量级做主导平衡，保留占主导的尺度。",
        instances=_dominant_balance(),
    ),
    ProblemFamily(
        key="power_limit",
        method_key="log_transform",
        mode=VerificationMode.LIMIT,
        point="oo",
        tags=("极限", "对数", "指数"),
        solution="先取对数把幂结构转化为乘积，展开后再指数还原。",
        instances=_log_transform(),
    ),
    ProblemFamily(
        key="factorial_growth",
        method_key="stirling",
        mode=VerificationMode.ASYMPTOTIC_EQUIVALENCE,
        point="oo",
        tags=("渐进估计", "阶乘", "gamma"),
        solution="使用 Stirling 斯特林公式处理阶乘与 Gamma 的大参数增长。",
        instances=_stirling(),
    ),
)


# 留出集文件。生成器必须主动排除这些表达式——不生成留出集本身是不够的，
# 参数网格会把留出题「顺手」覆盖掉，那样评测测的是记忆而不是检索。
HOLDOUT_FILES: tuple[str, ...] = (
    "data/pilot/asymptotic_retrieval_v2.jsonl",
    "data/pilot/asymptotic_solve_holdout.jsonl",
    "data/pilot/asymptotic_holdout.jsonl",
)

# The generated extension augments this hand-authored seed; repeating seed
# expressions would inflate sample counts without adding mathematical coverage.
SEED_TRAIN_FILES: tuple[str, ...] = ("data/pilot/asymptotic_train.jsonl",)


def _normalize_expression(expression: str) -> str:
    return "".join(expression.split())


def _load_expressions(paths: Sequence[Path]) -> set[str]:
    expressions: set[str] = set()
    for path in paths:
        if not path.exists():
            continue
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            record = json.loads(line)
            target = record.get("math_target") or record.get("math_payload")
            if target and target.get("expression"):
                expressions.add(_normalize_expression(target["expression"]))
    return expressions


def _default_dataset_paths(names: Sequence[str]) -> list[Path]:
    paths = [_PROJECT_ROOT / name for name in names]
    missing = [str(path) for path in paths if not path.exists()]
    if missing:
        raise FileNotFoundError(
            "required isolation dataset(s) not found: " + ", ".join(missing)
        )
    return paths


def load_holdout_expressions(
    paths: Sequence[Path] | None = None,
) -> set[str]:
    """收集所有留出集用到的表达式，供生成器排除。"""

    resolved = (
        list(paths) if paths is not None else _default_dataset_paths(HOLDOUT_FILES)
    )
    return _load_expressions(resolved)


def load_seed_training_expressions(
    paths: Sequence[Path] | None = None,
) -> set[str]:
    """Collect hand-authored seed expressions so generated data only adds coverage."""

    resolved = (
        list(paths) if paths is not None else _default_dataset_paths(SEED_TRAIN_FILES)
    )
    return _load_expressions(resolved)


@dataclass
class GenerationReport:
    emitted: int = 0
    rejected: int = 0
    excluded: int = 0
    by_method: dict[str, int] = field(default_factory=dict)
    rejected_samples: list[str] = field(default_factory=list)
    excluded_samples: list[str] = field(default_factory=list)


def _target(family: ProblemFamily, item: FamilyInstance) -> SolveMathTarget:
    return SolveMathTarget(
        expression=item.expression,
        variable=item.variable,
        parameters=list(item.parameters),
        assumptions={name: list(values) for name, values in item.assumptions},
        point=family.point,
        mode=family.mode,
        remainder_power=item.remainder_power,
    )


def generate_examples(
    families: tuple[ProblemFamily, ...] = FAMILIES,
    per_family: int | None = None,
    excluded_expressions: set[str] | None = None,
) -> tuple[list[ExampleCreate], GenerationReport]:
    """产出经验证的训练例子。

    未通过验证的候选被丢弃，绝不降级放行；命中留出集的候选被排除，否则评测测的
    是记忆而不是检索。
    """

    solver = OfflineSympySolutionGenerator()
    verifier = SolutionVerifier()
    report = GenerationReport()
    examples: list[ExampleCreate] = []
    excluded = (
        excluded_expressions
        if excluded_expressions is not None
        else load_holdout_expressions() | load_seed_training_expressions()
    )

    for family in families:
        instances = family.instances
        if per_family is not None:
            instances = instances[:per_family]
        for item in instances:
            if _normalize_expression(item.expression) in excluded:
                report.excluded += 1
                report.excluded_samples.append(f"{family.key}: {item.expression}")
                continue
            target = _target(family, item)
            answer = item.expected
            if answer is None:
                result = solver.generate(item.problem, [], target, 0)
                # 生成失败时 candidate 为 None，状态判断不能替代空值检查。
                candidate = result.candidate
                answer = candidate.answer_expression if candidate else None
                if result.trace.status is not GenerationStatus.SUCCESS or not answer:
                    report.rejected += 1
                    report.rejected_samples.append(f"{family.key}: {item.expression}")
                    continue

            payload = MathPayload(
                **target.model_dump(mode="json"),
                expected=answer,
            )
            if verifier.verify(payload).status is not VerificationStatus.VERIFIED:
                report.rejected += 1
                report.rejected_samples.append(f"{family.key}: {item.expression}")
                continue

            examples.append(
                ExampleCreate(
                    problem=item.problem,
                    solution=family.solution,
                    tags=list(family.tags),
                    reviewed=True,
                    math_payload=payload,
                )
            )
            report.emitted += 1
            report.by_method[family.method_key] = (
                report.by_method.get(family.method_key, 0) + 1
            )

    examples.sort(
        key=lambda example: (example.problem, example.math_payload.expression)
    )
    return examples, report


def _serialize(examples: list[ExampleCreate]) -> Iterator[str]:
    for example in examples:
        yield json.dumps(
            example.model_dump(mode="json", exclude_none=True),
            ensure_ascii=False,
            sort_keys=True,
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate a verified asymptotic training corpus."
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("data/pilot/asymptotic_train_generated.jsonl"),
    )
    parser.add_argument(
        "--per-family",
        type=int,
        default=None,
        help="每个家族最多取多少个实例；默认取全部。",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    examples, report = generate_examples(per_family=args.per_family)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        "".join(f"{line}\n" for line in _serialize(examples)),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "out": str(args.out),
                "emitted": report.emitted,
                "rejected": report.rejected,
                "by_method": report.by_method,
                "excluded": report.excluded,
                "rejected_samples": report.rejected_samples[:10],
                "excluded_samples": report.excluded_samples[:10],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
