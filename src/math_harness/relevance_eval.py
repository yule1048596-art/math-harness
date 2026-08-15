from __future__ import annotations

import argparse
import json
from pathlib import Path

from pydantic import BaseModel, Field

from math_harness.checks import Claim, ClaimKind
from math_harness.relevance import RelevanceGate, TurnRelevance

# 「这一轮有没有出题」这道闸的度量。
#
# 这道闸有一个**在界面上完全看不见**的失败模式，而本项目已经踩过同一个坑（v0.20 的
# `feature_version`）：
#
#   闸门可以靠什么都不做来显得完美。
#
# 收过头的话，知识库悄悄停止生长，用户只会觉得「它好像不怎么学东西了」，不会收到任何
# 报错。所以这里**必须同时给两个数**，缺一个都不算通过：
#
#   误收率——不该进库的进了多少（拦不住）；
#   保留率——该进库的还剩多少（拦过头）。
#
# 跟 `claim_eval` 一样的诚实性问题：CI 里没有 API Key，判定模型跑不了。做法也一样——
# 语料里的 `judge_verdict` 是**手工录的**模型应答，回放它来度量闸门逻辑。
#
# 所以这条门禁证明的是**闸门的编排**，不是模型判得准不准。后者只能在本机配真 Key 手测。
#
# 另外单独报一份**纯规则**的数（不回放模型）。那不是模拟，那是真实可跑的配置：没绑
# `relevance_judge` 角色的用户就跑在这条路上。


class RelevanceSample(BaseModel):
    id: str
    #: `solving_grounded` 规则层就该定；`solving_ungrounded` 与 `follow_up` 只有模型
    #: 判得了；`chitchat_obvious` 规则层必须挡住；`chitchat_subtle` 只有模型挡得住。
    kind: str
    question: str
    recent_questions: list[str] = Field(default_factory=list)
    #: 这一轮从回答里抽出来的断言，`[左边, 右边]`。度量的是闸门，不是抽断言。
    claims: list[tuple[str, str]] = Field(default_factory=list)
    #: 手工录下来的判定模型应答。
    judge_verdict: str = "unknown"

    @property
    def should_enter(self) -> bool:
        return self.kind.startswith("solving") or self.kind == "follow_up"


class ReplayJudge:
    """回放语料里手工录的判定。**不发任何请求。**"""

    name = "replay"
    prompt_version = "replay-v1"

    def __init__(self, verdict: str) -> None:
        self.verdict = TurnRelevance(verdict)
        self.calls = 0

    def judge(self, question: str, recent_questions: list[str]) -> TurnRelevance:
        del question, recent_questions
        self.calls += 1
        return self.verdict


class RelevanceReport(BaseModel):
    #: 配了判定模型时：不该进库的进了多少。
    false_accept_rate: float
    #: 配了判定模型时：该进库的还剩多少。
    retention_rate: float
    #: 没配判定模型时（纯规则），最明显的那类寒暄挡住了多少。
    rules_obvious_block_rate: float
    #: 没配判定模型时：该进库的还剩多少。**这一条比误收更要紧**——纯规则路上判不出来
    #: 一律放行，保留率掉下来就说明规则层在误伤。
    rules_retention_rate: float
    #: 纯规则路上挡不住的那类（`chitchat_subtle`）。**报出来，不设门槛**：规则层本来
    #: 就判不了这一类，给它设门槛等于逼着用关键词去猜「什么是数学」。
    rules_subtle_leak_rate: float
    #: 回放模式下发生的判定调用次数。免费层定下来的回合不该计数。
    model_calls: int
    total: int
    failures: list[str] = Field(default_factory=list)


MAX_FALSE_ACCEPT_RATE = 0.0
MIN_RETENTION_RATE = 0.95
MIN_RULES_OBVIOUS_BLOCK_RATE = 1.0
MIN_RULES_RETENTION_RATE = 1.0


def _load(path: Path) -> list[RelevanceSample]:
    samples: list[RelevanceSample] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if text:
            samples.append(RelevanceSample.model_validate_json(text))
    return samples


def _claims(sample: RelevanceSample) -> list[Claim]:
    return [
        Claim(kind=ClaimKind.EQUALITY, lhs=lhs, rhs=rhs) for lhs, rhs in sample.claims
    ]


def run_relevance_evaluation(corpus: Path) -> RelevanceReport:
    samples = _load(corpus)
    failures: list[str] = []

    should_enter = [item for item in samples if item.should_enter]
    should_block = [item for item in samples if not item.should_enter]
    obvious = [item for item in samples if item.kind == "chitchat_obvious"]
    subtle = [item for item in samples if item.kind == "chitchat_subtle"]

    false_accepts = 0
    retained = 0
    model_calls = 0
    rules_obvious_blocked = 0
    rules_retained = 0
    rules_subtle_leaked = 0

    for sample in samples:
        claims = _claims(sample)
        judge = ReplayJudge(sample.judge_verdict)
        verdict = RelevanceGate(judge=judge).evaluate(
            sample.question, claims, sample.recent_questions
        )
        model_calls += judge.calls

        if sample.should_enter and not verdict.may_enter_knowledge_base:
            failures.append(f"{sample.id}：该进库的被拦了（{sample.kind}）")
        elif sample.should_enter:
            retained += 1
        elif verdict.may_enter_knowledge_base:
            false_accepts += 1
            failures.append(f"{sample.id}：不该进库的被放行了（{sample.kind}）")

        # 纯规则：没绑 `relevance_judge` 角色的用户真实跑在这条路上。
        offline = RelevanceGate().evaluate(sample.question, claims)
        if sample.should_enter:
            if offline.may_enter_knowledge_base:
                rules_retained += 1
            else:
                failures.append(
                    f"{sample.id}：纯规则路上把一条解题拦掉了（{sample.kind}）"
                )
        elif sample.kind == "chitchat_obvious":
            if offline.may_enter_knowledge_base:
                failures.append(f"{sample.id}：纯规则路上没挡住一句明显的寒暄")
            else:
                rules_obvious_blocked += 1
        elif offline.may_enter_knowledge_base:
            rules_subtle_leaked += 1

    return RelevanceReport(
        false_accept_rate=(false_accepts / len(should_block) if should_block else 0.0),
        retention_rate=retained / len(should_enter) if should_enter else 1.0,
        rules_obvious_block_rate=(
            rules_obvious_blocked / len(obvious) if obvious else 1.0
        ),
        rules_retention_rate=(
            rules_retained / len(should_enter) if should_enter else 1.0
        ),
        rules_subtle_leak_rate=(rules_subtle_leaked / len(subtle) if subtle else 0.0),
        model_calls=model_calls,
        total=len(samples),
        failures=failures,
    )


def gate_failures(report: RelevanceReport) -> list[str]:
    failures = list(report.failures)
    if report.false_accept_rate > MAX_FALSE_ACCEPT_RATE:
        failures.append(
            f"误收率 {report.false_accept_rate:.3f} > {MAX_FALSE_ACCEPT_RATE}"
        )
    if report.retention_rate < MIN_RETENTION_RATE:
        failures.append(f"保留率 {report.retention_rate:.3f} < {MIN_RETENTION_RATE}")
    if report.rules_obvious_block_rate < MIN_RULES_OBVIOUS_BLOCK_RATE:
        failures.append(
            f"纯规则挡明显寒暄 {report.rules_obvious_block_rate:.3f}"
            f" < {MIN_RULES_OBVIOUS_BLOCK_RATE}"
        )
    if report.rules_retention_rate < MIN_RULES_RETENTION_RATE:
        failures.append(
            f"纯规则保留率 {report.rules_retention_rate:.3f}"
            f" < {MIN_RULES_RETENTION_RATE}"
        )
    return failures


def format_report(report: RelevanceReport) -> str:
    return "\n".join(
        [
            f"样本 {report.total} 条 · 判定调用 {report.model_calls} 次",
            (
                f"配判定模型：误收 {report.false_accept_rate:.3f}"
                f" · 保留 {report.retention_rate:.3f}"
            ),
            (
                f"纯规则：挡住明显寒暄 {report.rules_obvious_block_rate:.3f}"
                f" · 保留 {report.rules_retention_rate:.3f}"
                f" · 漏掉不明显的 {report.rules_subtle_leak_rate:.3f}"
            ),
            "",
            (
                "注意：语料里的判定是手工录的。这条门禁证明**闸门的编排**，"
                "不证明判定模型判得准不准。"
            ),
            (
                "「漏掉不明显的」不设门槛：规则层本来就判不了那一类，"
                "给它设门槛等于逼着用关键词去猜「什么是数学」。"
            ),
        ]
        + ([""] + [f"  {item}" for item in report.failures] if report.failures else [])
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="「这一轮有没有出题」这道闸的度量。同时报误收率与保留率——"
        "闸门可以靠什么都不做来显得完美，只报一个数看不出这件事。"
    )
    parser.add_argument(
        "--corpus", type=Path, default=Path("data/pilot/turn_relevance.jsonl")
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)

    report = run_relevance_evaluation(args.corpus)
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
