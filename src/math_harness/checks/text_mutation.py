from __future__ import annotations

import random
import re
from dataclasses import dataclass

from pydantic import BaseModel

from math_harness.checks.base import CheckContext, run_checks
from math_harness.checks.confidence import (
    ConclusionConfidence,
    ProcessConfidence,
    assess,
)
from math_harness.checks.instantiation import InstantiationCheck, StepInstantiationCheck
from math_harness.checks.mutation import MutationKind, _mutate_text
from math_harness.checks.symbolic import SymbolicEqualityCheck

# 从**回答文本**出发的变异测试。
#
# `mutation.py` 那一套跑的是手工构造的 `Claim`，完全不经过「文本→断言」这条路。
# v0.16 把那条路加进了主链：规范化（隐式乘法、LaTeX、记号别名、字母连写）、抽等式、
# 定义代入。一个变异体可能在 Claim 层被抓得住，而生产里抽断言那一步先把它弄坏了——
# 那种失败上一套测不到。
#
# 所以这里量的是**整条链**：人写记号的回答 → 抽断言 → 检查 → 双轴可信度。

_EQUATION_LINE = re.compile(r"^(.*?)(=)(.*)$")


class TextMutationCase(BaseModel):
    """一段已知正确的回答，用的是人真正会写的记号。"""

    name: str
    problem: str
    answer: str
    #: 结论所在的行号（0 起）。步骤扰动只改这一行**之前**的行。
    conclusion_line: int


@dataclass
class TextMutationReport:
    correct_total: int = 0
    false_rejections: int = 0
    conclusion_total: int = 0
    conclusion_caught: int = 0
    step_total: int = 0
    step_caught: int = 0
    discarded_equivalent: int = 0
    #: 抽不出断言的正确用例。这不算误拒，但覆盖率低会让捕获率失去意义。
    undraftable: int = 0

    @property
    def false_rejection_rate(self) -> float:
        return (
            round(self.false_rejections / self.correct_total, 6)
            if self.correct_total
            else 0.0
        )

    @property
    def conclusion_capture(self) -> float:
        return (
            round(self.conclusion_caught / self.conclusion_total, 6)
            if self.conclusion_total
            else 0.0
        )

    @property
    def step_capture(self) -> float:
        return round(self.step_caught / self.step_total, 6) if self.step_total else 0.0


def baseline_answers() -> list[TextMutationCase]:
    """已知正确的回答，**按人真正会写的样子写**。

    隐式乘法、中文连接词、LaTeX、恒等号都要出现——这些正是 v0.16 加进主链的东西，
    不放进基准就等于没测。
    """

    return [
        TextMutationCase(
            name="代数-平方差",
            problem="化简 (a+b)^2-(a-b)^2。",
            answer=(
                "(a+b)^2 = a^2 + 2ab + b^2\n"
                "(a-b)^2 = a^2 - 2ab + b^2\n"
                "相减得 (a+b)^2 - (a-b)^2 = 4ab"
            ),
            conclusion_line=2,
        ),
        TextMutationCase(
            name="微积分-多项式求导",
            problem="求 x^3+2x 的导数。",
            answer=(
                "第一步：diff(x^3, x) = 3x^2\n"
                "第二步：diff(2x, x) = 2\n"
                "所以 diff(x^3 + 2x, x) = 3x^2 + 2"
            ),
            conclusion_line=2,
        ),
        TextMutationCase(
            name="微积分-乘积法则",
            problem="求 x^2*sin(x) 的导数。",
            answer=(
                "diff(x^2, x) = 2x\n"
                "diff(sin(x), x) = cos(x)\n"
                "于是 diff(x^2*sin(x), x) = 2x*sin(x) + x^2*cos(x)"
            ),
            conclusion_line=2,
        ),
        TextMutationCase(
            name="代数-立方差",
            problem="验证立方差分解。",
            answer=(
                "a*(a^2 + ab + b^2) = a^3 + a^2*b + a*b^2\n"
                "b*(a^2 + ab + b^2) = a^2*b + a*b^2 + b^3\n"
                "相减得 (a-b)*(a^2 + ab + b^2) = a^3 - b^3"
            ),
            conclusion_line=2,
        ),
        TextMutationCase(
            name="解析几何-圆方程",
            problem="把圆方程展开成一般式。",
            answer=(
                "(x-h)^2 = x^2 - 2hx + h^2\n"
                "(y-k)^2 = y^2 - 2ky + k^2\n"
                "所以 (x-h)^2 + (y-k)^2 = x^2 + y^2 - 2hx - 2ky + h^2 + k^2"
            ),
            conclusion_line=2,
        ),
        TextMutationCase(
            name="三角-勾股恒等式",
            problem="化简 sin(x)^2+cos(x)^2。",
            answer=("cos(2x) = 1 - 2sin(x)^2\n结论：sin(x)^2 + cos(x)^2 ≡ 1"),
            conclusion_line=1,
        ),
        TextMutationCase(
            name="组合-幂和",
            problem="求前 n 项平方和。",
            answer=(
                "Sum(k,(k,1,n)) = n*(n+1)/2\n所以 Sum(k^2,(k,1,n)) = n*(n+1)*(2n+1)/6"
            ),
            conclusion_line=1,
        ),
        TextMutationCase(
            name="线性代数-特征值",
            problem="验证 3 是该矩阵的特征值。",
            answer=(
                "det(Matrix([[2,1],[1,2]])) = 3\n"
                "所以 det(Matrix([[2,1],[1,2]]) - 3*eye(2)) = 0"
            ),
            conclusion_line=1,
        ),
    ]


def _mutate_line(line: str, kind: MutationKind, rng: random.Random) -> str | None:
    """改一行等式的右边。整行不是等式就返回 None。"""

    match = _EQUATION_LINE.match(line)
    if match is None:
        return None
    head, equals, tail = match.groups()
    mutated = _mutate_text(tail.strip(), kind, rng)
    if mutated is None or mutated == tail.strip():
        return None
    return f"{head}{equals} {mutated}"


def build_text_population(
    seed: int = 20260808,
) -> tuple[list[TextMutationCase], list[tuple[TextMutationCase, bool]]]:
    """造变异体。返回 `(正确用例, [(变异体, 是否为结论扰动)])`。"""

    from math_harness.checks.measurement import _is_equivalent

    rng = random.Random(seed)
    correct = baseline_answers()
    mutants: list[tuple[TextMutationCase, bool]] = []
    for case in correct:
        lines = case.answer.splitlines()
        for kind in MutationKind:
            # 结论扰动：改最后一行。
            mutated = _mutate_line(lines[case.conclusion_line], kind, rng)
            if mutated is not None and not _equivalent_line(
                lines[case.conclusion_line], mutated, _is_equivalent
            ):
                changed = list(lines)
                changed[case.conclusion_line] = mutated
                mutants.append(
                    (
                        case.model_copy(
                            update={
                                "name": f"{case.name}[结论-{kind.value}]",
                                "answer": "\n".join(changed),
                            }
                        ),
                        True,
                    )
                )
            # 步骤扰动：改结论之前的某一行，结论保持正确。
            for index in range(case.conclusion_line):
                mutated_step = _mutate_line(lines[index], kind, rng)
                if mutated_step is None or _equivalent_line(
                    lines[index], mutated_step, _is_equivalent
                ):
                    continue
                changed = list(lines)
                changed[index] = mutated_step
                mutants.append(
                    (
                        case.model_copy(
                            update={
                                "name": f"{case.name}[第{index + 1}行-{kind.value}]",
                                "answer": "\n".join(changed),
                            }
                        ),
                        False,
                    )
                )
                break
    return correct, mutants


def _equivalent_line(original: str, mutated: str, is_equivalent) -> bool:
    """两行的右边在数学上是不是同一个东西。"""

    from math_harness.claim_drafting import normalize_math_text

    left = _EQUATION_LINE.match(original)
    right = _EQUATION_LINE.match(mutated)
    if left is None or right is None:
        return False
    return is_equivalent(
        normalize_math_text(left.group(3)), normalize_math_text(right.group(3))
    )


def measure_text_mutation(seed: int = 20260808) -> TextMutationReport:
    """跑完整条链：人写记号的回答 → 抽断言 → 检查 → 双轴。"""

    from math_harness.claim_drafting import RuleBasedClaimDrafter

    drafter = RuleBasedClaimDrafter()
    checks = [
        SymbolicEqualityCheck(),
        InstantiationCheck(trials=8),
        StepInstantiationCheck(trials=8),
    ]
    correct, mutants = build_text_population(seed)
    report = TextMutationReport()

    def grade(case: TextMutationCase):
        draft = drafter.draft(case.problem, case.answer)
        if not draft.is_checkable:
            return None
        return assess(
            run_checks(
                checks,
                CheckContext(
                    problem=case.problem,
                    claim=draft.claim,
                    steps=draft.steps,
                    answer_text=case.answer,
                ),
            )
        )

    for case in correct:
        report.correct_total += 1
        assessment = grade(case)
        if assessment is None:
            report.undraftable += 1
            continue
        if (
            assessment.conclusion is ConclusionConfidence.REFUTED
            or assessment.process is ProcessConfidence.STEP_FAILED
        ):
            report.false_rejections += 1

    for case, is_conclusion in mutants:
        assessment = grade(case)
        caught = assessment is not None and (
            assessment.conclusion is ConclusionConfidence.REFUTED
            or assessment.process is ProcessConfidence.STEP_FAILED
        )
        if is_conclusion:
            report.conclusion_total += 1
            report.conclusion_caught += int(caught)
        else:
            report.step_total += 1
            report.step_caught += int(caught)

    return report


def format_text_report(report: TextMutationReport) -> str:
    return "\n".join(
        [
            (
                f"回答 {report.correct_total} 条 · 结论扰动 "
                f"{report.conclusion_total} · 步骤扰动 {report.step_total}"
            ),
            (
                f"结论捕获 {report.conclusion_capture:.3f} · "
                f"步骤捕获 {report.step_capture:.3f} · "
                f"误拒 {report.false_rejection_rate:.3f} · "
                f"抽不出 {report.undraftable}"
            ),
        ]
    )
