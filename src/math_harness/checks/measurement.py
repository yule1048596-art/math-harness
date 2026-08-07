from __future__ import annotations

import random
from dataclasses import dataclass
from time import perf_counter

import sympy as sp
from pydantic import BaseModel, Field

from math_harness.checks.base import Check, CheckContext, CheckOutcome
from math_harness.checks.instantiation import InstantiationCheck, StepInstantiationCheck
from math_harness.checks.mutation import (
    MutationCase,
    MutationKind,
    baseline_cases,
    mutate_conclusion,
    mutate_step,
)
from math_harness.checks.symbolic import SymbolicEqualityCheck
from math_harness.math_parser import SafeMathParser

# 度量门禁：逐层量捕获率、误拒率、成本。
#
# 这是 v0.13 造标尺时立的规矩——**证明不了提升的层不进代码库**。检查层的直觉说服力
# 很强（「多查一遍总没坏处」），但每一层都要花时间、都可能误拒正确答案，直觉不能替代
# 数据。

#: 变异种子。同一批变异体每次都一样，否则两次测量没法比。
DEFAULT_SEED = 20260807

#: 误拒率上限。误拒正确答案比漏抓错答案更糟——它会把对的解打成可疑的，用户会因此
#: 不再相信这套标注，整个可信度分级就没意义了。
MAX_FALSE_REJECTION = 0.05


def wilson_lower_bound(successes: int, trials: int, z: float = 1.96) -> float:
    """捕获率的 95% 置信下界（Wilson 区间）。

    门禁说的是「捕获率不显著高于零就不合入」，那就得有个真判据。直接看点估计不行：
    3 个变异体里抓到 1 个是 0.33，但样本这么少什么都说明不了。Wilson 下界把样本量算
    进去，小样本会自动被压到接近零。
    """

    if trials <= 0:
        return 0.0
    proportion = successes / trials
    denominator = 1 + z**2 / trials
    center = proportion + z**2 / (2 * trials)
    spread = z * ((proportion * (1 - proportion) + z**2 / (4 * trials)) / trials) ** 0.5
    return round(max(0.0, (center - spread) / denominator), 6)


class LayerMeasurement(BaseModel):
    """单层检查在一批用例上的表现。"""

    layer: str
    #: 这一层该抓的是哪一类扰动。分开算是必要的：只查结论的层抓不到步骤扰动，
    #: 那是**正确行为**，混在一起算会把它冤枉成漏抓。
    gated_on: str = "conclusion"
    #: 结论扰动里被判 FAILED 的比例。
    conclusion_capture: float = 0.0
    conclusion_total: int = 0
    #: 步骤扰动里被判 FAILED 的比例。只有逐步检查该在这一栏拿到分。
    step_capture: float = 0.0
    step_total: int = 0
    #: 正确用例里被判 FAILED 的比例。**必须接近零**。
    false_rejection: float = 0.0
    correct_total: int = 0
    #: 每个用例的平均耗时。
    mean_ms: float = 0.0
    #: 判不出结论的比例（SKIPPED / ERRORED），用来解释捕获率为什么没到 1。
    inconclusive: float = 0.0
    #: 目标population 上捕获率的 95% 置信下界。门禁看的是这个数。
    capture_lower_bound: float = 0.0

    @property
    def gated_capture(self) -> float:
        """该层在它该抓的那类扰动上的捕获率。"""

        return (
            self.conclusion_capture
            if self.gated_on == "conclusion"
            else self.step_capture
        )

    @property
    def admitted(self) -> bool:
        """能不能合入。捕获率下界必须高于零，且误拒率不能超上限。"""

        return (
            self.capture_lower_bound > 0.0
            and self.false_rejection <= MAX_FALSE_REJECTION
        )


class MutationReport(BaseModel):
    layers: list[LayerMeasurement] = Field(default_factory=list)
    #: 构造出来但语义等价、已剔除的变异体。
    discarded_equivalent: int = 0
    mutant_count: int = 0
    correct_count: int = 0

    def layer(self, name: str) -> LayerMeasurement:
        for item in self.layers:
            if item.layer == name:
                return item
        raise KeyError(name)


@dataclass
class _Tally:
    conclusion_caught: int = 0
    conclusion_seen: int = 0
    step_caught: int = 0
    step_seen: int = 0
    false_rejections: int = 0
    correct_seen: int = 0
    inconclusive: int = 0
    total_seen: int = 0
    total_ms: float = 0.0


def _is_equivalent(original: str, mutated: str) -> bool:
    """变异体是否与原式语义等价。等价的要剔除，不能算作漏抓。

    这是变异测试里的经典陷阱：文本改了不代表意思改了。`x**2` 改成 `x**2 + 0` 仍然
    正确，某一层「没抓住」它是对的，算成漏抓会低估这一层。

    这里用完整 `simplify` 当判据。它比被测的任何一层都慢也都强——度量用的裁判本来就
    该比被测对象强，基准用例都很小，慢一点无所谓。
    """

    parser = SafeMathParser()
    names = set()
    for text in (original, mutated):
        names |= {str(symbol) for symbol in _free_names(text)}
    symbols = {name: sp.Symbol(name) for name in names}
    try:
        left = parser.parse(original, symbols)
        right = parser.parse(mutated, symbols)
        return bool(sp.simplify(left - right) == 0)
    except Exception:  # noqa: BLE001
        # 解析不了就当它不等价：宁可多留一个变异体，也不要悄悄把用例扔掉。
        return False


def _free_names(text: str) -> set[sp.Symbol]:
    import re

    identifier = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
    return {
        sp.Symbol(match.group(0))
        for match in identifier.finditer(text)
        if match.group(0) not in SafeMathParser.FUNCTIONS
        and match.group(0) not in SafeMathParser.CONSTANTS
    }


def build_population(
    seed: int = DEFAULT_SEED,
) -> tuple[list[MutationCase], list[MutationCase], int]:
    """造一批用例：正确的 + 结论扰动 + 步骤扰动。

    返回 `(正确用例, 变异体, 剔除的等价变异体数)`。
    """

    rng = random.Random(seed)
    correct = baseline_cases()
    mutants: list[MutationCase] = []
    discarded = 0

    for case in correct:
        for kind in MutationKind:
            conclusion = mutate_conclusion(case, kind, rng)
            if conclusion is not None and conclusion.mutation is not None:
                if _is_equivalent(
                    conclusion.mutation.original, conclusion.mutation.mutated
                ):
                    discarded += 1
                else:
                    mutants.append(conclusion)

            step = mutate_step(case, kind, rng)
            if step is not None and step.mutation is not None:
                if _is_equivalent(step.mutation.original, step.mutation.mutated):
                    discarded += 1
                else:
                    mutants.append(step)

    return correct, mutants, discarded


def _context(case: MutationCase) -> CheckContext:
    return CheckContext(problem=case.name, claim=case.claim, steps=case.steps)


def _run_layer(check: Check, cases: list[MutationCase], tally: _Tally) -> None:
    for case in cases:
        context = _context(case)
        started = perf_counter()
        if not check.applies(context):
            outcome = CheckOutcome.SKIPPED
        else:
            try:
                outcome = check.run(context).outcome
            except Exception:  # noqa: BLE001
                outcome = CheckOutcome.ERRORED
        tally.total_ms += (perf_counter() - started) * 1000
        tally.total_seen += 1

        caught = outcome is CheckOutcome.FAILED
        if outcome in {CheckOutcome.SKIPPED, CheckOutcome.ERRORED}:
            tally.inconclusive += 1

        if case.is_correct:
            tally.correct_seen += 1
            if caught:
                tally.false_rejections += 1
            continue

        assert case.mutation is not None
        if case.mutation.targets_conclusion:
            tally.conclusion_seen += 1
            tally.conclusion_caught += int(caught)
        else:
            tally.step_seen += 1
            tally.step_caught += int(caught)


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def measure(seed: int = DEFAULT_SEED) -> MutationReport:
    """跑完整套度量。"""

    correct, mutants, discarded = build_population(seed)
    # 每一层配上它该抓的那类扰动。只查结论的层抓不到步骤扰动是正确行为，不是漏抓。
    layers: list[tuple[Check, str]] = [
        (SymbolicEqualityCheck(), "conclusion"),
        (InstantiationCheck(trials=8), "conclusion"),
        (StepInstantiationCheck(trials=8), "step"),
    ]

    measurements: list[LayerMeasurement] = []
    for check, gated_on in layers:
        tally = _Tally()
        _run_layer(check, mutants, tally)
        _run_layer(check, correct, tally)
        if gated_on == "conclusion":
            caught, seen = tally.conclusion_caught, tally.conclusion_seen
        else:
            caught, seen = tally.step_caught, tally.step_seen
        measurements.append(
            LayerMeasurement(
                layer=check.name,
                gated_on=gated_on,
                conclusion_capture=_ratio(
                    tally.conclusion_caught, tally.conclusion_seen
                ),
                conclusion_total=tally.conclusion_seen,
                step_capture=_ratio(tally.step_caught, tally.step_seen),
                step_total=tally.step_seen,
                false_rejection=_ratio(tally.false_rejections, tally.correct_seen),
                correct_total=tally.correct_seen,
                mean_ms=round(tally.total_ms / max(tally.total_seen, 1), 2),
                inconclusive=_ratio(tally.inconclusive, tally.total_seen),
                capture_lower_bound=wilson_lower_bound(caught, seen),
            )
        )

    return MutationReport(
        layers=measurements,
        discarded_equivalent=discarded,
        mutant_count=len(mutants),
        correct_count=len(correct),
    )


def format_report(report: MutationReport) -> str:
    lines = [
        (
            f"变异体 {report.mutant_count} · 正确用例 {report.correct_count} "
            f"· 剔除等价变异体 {report.discarded_equivalent}"
        ),
        "",
        (
            f"{'层':<20}{'门禁':>6}{'结论捕获':>10}{'步骤捕获':>10}"
            f"{'下界':>8}{'误拒':>7}{'判不出':>8}{'均耗时':>10}"
        ),
    ]
    for item in report.layers:
        lines.append(
            f"{item.layer:<20}"
            f"{item.gated_on:>6}"
            f"{item.conclusion_capture:>10.3f}"
            f"{item.step_capture:>10.3f}"
            f"{item.capture_lower_bound:>8.3f}"
            f"{item.false_rejection:>7.3f}"
            f"{item.inconclusive:>8.3f}"
            f"{item.mean_ms:>9.1f}ms"
        )
    rejected = [item.layer for item in report.layers if not item.admitted]
    lines.append("")
    lines.append(
        "全部层通过门禁。"
        if not rejected
        else f"未通过门禁、不该合入的层：{'、'.join(rejected)}"
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(
        description="变异测试：逐层量捕获率、误拒率、成本。捕获率不显著高于零、"
        "或误拒率过高的层不该合入。"
    )
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--json", action="store_true", help="输出 JSON 而不是表格")
    parser.add_argument(
        "--check",
        action="store_true",
        help="任何一层没通过门禁就以非零码退出。发布前跑这个。",
    )
    args = parser.parse_args(argv)

    report = measure(args.seed)
    if args.json:
        print(json.dumps(report.model_dump(), ensure_ascii=False, indent=2))
    else:
        print(format_report(report))
    if args.check and any(not item.admitted for item in report.layers):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
