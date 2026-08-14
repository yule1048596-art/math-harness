from __future__ import annotations

import argparse
import json
from pathlib import Path

from pydantic import BaseModel, Field

from math_harness.checks import (
    CheckContext,
    ConclusionConfidence,
    InstantiationCheck,
    SymbolicEqualityCheck,
    assess,
    run_checks,
)
from math_harness.claim_drafting import (
    DraftedClaimSource,
    RuleBasedClaimDrafter,
    ground_drafted_claims,
)

# 模型抽断言这条路的度量。
#
# **它证明的是四道闸和整条管道，不是模型抽得好不好。** CI 里没有 API Key，语料里的
# `drafted` 是手工录下来的模型输出。真实模型的抽取质量只能在本机配真 Key 手测——把这
# 条门禁的数字说成「覆盖率涨了」是自欺，v0.13 以来每一版都在防这件事。
#
# 那它究竟证明了什么？三件事，每一件都是真的：
#
#   1. **闸门捕获率**：编造出处、自己解题、解析不了的输入，一条都不许放行。这是我的
#      代码，完全可测。
#   2. **管道贯通率**：模型如实抽出来的那些，确实变成了断言并被检查流水线判定。
#   3. **误拒率**：整个语料里正确的解答，一条都不许被判成有反例。
#
# 语料分三类：`gap` 是规则版够不着、模型该补上的；`hostile` 是必须被丢掉的；
# `refutable` 是答案确实错、必须被抓住的。


class ClaimSample(BaseModel):
    id: str
    kind: str
    problem: str
    answer: str
    drafted: list[DraftedClaimSource] = Field(default_factory=list)


class ClaimEvalReport(BaseModel):
    #: `gap` 里模型给了断言的样本中，成功变成可检验断言的比例。
    pipeline_rate: float
    #: `hostile` 里被正确丢弃的比例。
    guard_rate: float
    #: 正确解答被判成 `refuted` 的比例。
    false_refutation_rate: float
    #: `refutable` 里被抓住的比例。
    refutation_capture_rate: float
    #: 规则版单独能覆盖多少——这一条是回归哨兵，模型路不该改变它。
    rules_only_coverage: int
    total: int
    failures: list[str] = Field(default_factory=list)


MIN_PIPELINE_RATE = 1.0
MIN_GUARD_RATE = 1.0
MAX_FALSE_REFUTATION_RATE = 0.0
MIN_REFUTATION_CAPTURE_RATE = 1.0


def _load(path: Path) -> list[ClaimSample]:
    samples: list[ClaimSample] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if text:
            samples.append(ClaimSample.model_validate_json(text))
    return samples


def _conclusion(claims: list, problem: str) -> ConclusionConfidence:
    report = run_checks(
        [SymbolicEqualityCheck(), InstantiationCheck(trials=8)],
        CheckContext(problem=problem, claim=claims[-1], steps=claims),
    )
    return assess(report).conclusion


def run_claim_evaluation(corpus: Path) -> ClaimEvalReport:
    samples = _load(corpus)
    rules = RuleBasedClaimDrafter()
    failures: list[str] = []

    gap_expected = 0
    gap_passed = 0
    hostile_total = 0
    hostile_blocked = 0
    refutable_total = 0
    refutable_caught = 0
    false_refutations = 0
    correct_total = 0
    rules_only = 0

    for sample in samples:
        if rules.draft(sample.problem, sample.answer).is_checkable:
            rules_only += 1
        claims = ground_drafted_claims(
            sample.drafted, problem=sample.problem, answer=sample.answer
        )

        if sample.kind == "hostile":
            hostile_total += 1
            if claims:
                failures.append(f"{sample.id}：敌意样本被放行了")
            else:
                hostile_blocked += 1
            continue

        if sample.kind == "gap":
            # 语料里 `drafted` 为空的是「模型也抽不出来」，那是正常结果，不计入分母。
            if not sample.drafted:
                continue
            gap_expected += 1
            if not claims:
                failures.append(f"{sample.id}：模型如实给的断言没能通过闸门")
                continue
            gap_passed += 1
            correct_total += 1
            if _conclusion(claims, sample.problem) is ConclusionConfidence.REFUTED:
                false_refutations += 1
                failures.append(f"{sample.id}：正确解答被判成有反例")
            continue

        if sample.kind == "refutable":
            refutable_total += 1
            if not claims:
                failures.append(f"{sample.id}：错答案的断言被丢掉了，抓不到")
                continue
            if _conclusion(claims, sample.problem) is ConclusionConfidence.REFUTED:
                refutable_caught += 1
            else:
                failures.append(f"{sample.id}：错答案没被抓住")

    return ClaimEvalReport(
        pipeline_rate=gap_passed / gap_expected if gap_expected else 1.0,
        guard_rate=hostile_blocked / hostile_total if hostile_total else 1.0,
        false_refutation_rate=(
            false_refutations / correct_total if correct_total else 0.0
        ),
        refutation_capture_rate=(
            refutable_caught / refutable_total if refutable_total else 1.0
        ),
        rules_only_coverage=rules_only,
        total=len(samples),
        failures=failures,
    )


def gate_failures(report: ClaimEvalReport) -> list[str]:
    failures = list(report.failures)
    if report.pipeline_rate < MIN_PIPELINE_RATE:
        failures.append(f"管道贯通率 {report.pipeline_rate:.3f} < {MIN_PIPELINE_RATE}")
    if report.guard_rate < MIN_GUARD_RATE:
        failures.append(f"闸门捕获率 {report.guard_rate:.3f} < {MIN_GUARD_RATE}")
    if report.false_refutation_rate > MAX_FALSE_REFUTATION_RATE:
        failures.append(
            f"误拒率 {report.false_refutation_rate:.3f} > {MAX_FALSE_REFUTATION_RATE}"
        )
    if report.refutation_capture_rate < MIN_REFUTATION_CAPTURE_RATE:
        failures.append(
            f"错答案捕获率 {report.refutation_capture_rate:.3f}"
            f" < {MIN_REFUTATION_CAPTURE_RATE}"
        )
    return failures


def format_report(report: ClaimEvalReport) -> str:
    lines = [
        f"样本 {report.total} 条 · 规则版单独覆盖 {report.rules_only_coverage} 条",
        (
            f"管道贯通 {report.pipeline_rate:.3f}"
            f" · 闸门捕获 {report.guard_rate:.3f}"
            f" · 误拒 {report.false_refutation_rate:.3f}"
            f" · 错答案捕获 {report.refutation_capture_rate:.3f}"
        ),
        "",
        (
            "注意：语料里的模型输出是手工录的。这条门禁证明四道闸和整条管道，"
            "**不证明真实模型的抽取质量**。"
        ),
    ]
    if report.failures:
        lines.append("")
        lines.extend(f"  {item}" for item in report.failures)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="模型抽断言的闸门与管道度量。证明的是四道闸和管道，"
        "不是模型的抽取质量——CI 里没有 API Key，语料里的模型输出是手工录的。"
    )
    parser.add_argument(
        "--corpus", type=Path, default=Path("data/pilot/claim_drafts.jsonl")
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)

    report = run_claim_evaluation(args.corpus)
    if args.json:
        print(json.dumps(report.model_dump(), ensure_ascii=False, indent=2))
    else:
        print(format_report(report))
    if args.check:
        failures = gate_failures(report)
        if failures:
            print()
            for failure in failures:
                print(f"未达门限：{failure}")
            return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
