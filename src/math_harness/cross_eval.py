from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel, Field

from math_harness.claim_drafting import extract_expressions
from math_harness.math_parser import SafeMathParser
from math_harness.models import (
    EvaluationCase,
    EvaluationRequest,
    EvaluationSlice,
    ExampleCreate,
    ExtractionStatus,
    MethodDraft,
    MethodExtractionResult,
    MethodExtractionTrace,
    WorkspaceCreate,
)
from math_harness.service import MathHarnessService
from math_harness.solving import OfflineSympySolutionGenerator
from math_harness.structure import operator_paths

# 跨领域检索评测。
#
# 现有的评测切片全部是渐进形状，于是 v0.15 建的通用流水线**一次检索测量都没有**。
# 没有这个数，「结构检索改由断言导出」到底有没有用就是不可证伪的。
#
# 它与渐进评测分开跑，不共用一条命令：渐进那几个数字花了两个版本才拿到，不能因为
# 加了新口径而有回退的风险。
#
# **它量的是检索，不是提炼。** 训练语料显式声明每条例题教的是哪个方法。这不是绕过
# 提炼器——两件事必须分开量，混在一起的话指标掉下来分不清是「方法卡建错了」还是
# 「建对了但找不到」。提炼质量另有它自己的度量（误标率）。

#: 留出题与训练题结构相同的比例上限。
#
# 与渐进评测同一个口径。留一个小的同构 control 切片是刻意的——对照切片掉分才说明
# 检索真的坏了；超过这个比例，指标衡量的就主要是记忆而不是泛化。
MAX_STRUCTURAL_OVERLAP = 0.40

#: 检索门禁：整体 Hit@1 的下限。
#
# v0.16 之前跨领域检索是 0.000——不是检索差，是知识永远晋级不了、库里恒空。修好晋级
# 之后纯词面拿到 0.404，接上从题面推断的结构后拿到 0.596。这个门限守的是后者：
# 掉回词面水平就说明结构那一路白接了。
MIN_OVERALL_HIT_AT_1 = 0.55
#: 被表面形状骗走的下限。结构权重给高了这一格会掉——0.12 就开始掉。
MIN_CROSS_FAMILY_HIT_AT_1 = 0.45


class CrossDomainTrainingCase(BaseModel):
    """跨领域训练语料的一条。"""

    problem: str = Field(min_length=1, max_length=20_000)
    solution: str = Field(min_length=1, max_length=40_000)
    tags: list[str] = Field(default_factory=list, max_length=30)
    #: 这条例题教的是哪个方法。显式声明，见模块开头的说明。
    method_key: str = Field(min_length=1, max_length=80)
    domain: str = Field(default="", max_length=60)

    def as_example(self) -> ExampleCreate:
        return ExampleCreate(
            problem=self.problem,
            solution=self.solution,
            tags=self.tags,
            method_hint=self.method_key,
            reviewed=True,
        )


class CrossDomainMethodCard(BaseModel):
    """一张归纳过的方法卡。

    **刻意不含来源题面。** 第一版把训练题面原样塞进 goal 和 applicable_when，
    检索于是变成拿留出题面去匹配训练题面——量到的是两段文本像不像，不是找不找得到
    对的方法。真实的方法卡是归纳出来的描述，评测里的也必须是。
    """

    key: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=120)
    goal: str = Field(min_length=1, max_length=500)
    applicable_when: list[str] = Field(default_factory=list, max_length=10)
    procedure: list[str] = Field(default_factory=list, max_length=20)
    failure_modes: list[str] = Field(default_factory=list, max_length=10)
    tags: list[str] = Field(default_factory=list, max_length=20)

    def as_draft(self) -> MethodDraft:
        return MethodDraft(
            key=self.key,
            name=self.name,
            goal=self.goal,
            applicable_when=self.applicable_when,
            procedure=self.procedure,
            failure_modes=self.failure_modes,
            tags=self.tags,
        )


class DeclaredMethodExtractor:
    """按语料声明的方法键产出方法卡。

    只在评测里用。它让检索评测独立于提炼质量——两者都要量，但要分开量。
    """

    name = "declared"
    prompt_version = "declared-v2"

    def __init__(
        self,
        cases_by_problem: dict[str, CrossDomainTrainingCase],
        cards: dict[str, CrossDomainMethodCard],
    ) -> None:
        self.cases_by_problem = cases_by_problem
        self.cards = cards

    def extract(
        self, problem: str, solution: str, hint: str | None = None
    ) -> MethodExtractionResult:
        del solution, hint
        case = self.cases_by_problem.get(problem)
        card = self.cards.get(case.method_key) if case else None
        if card is None:
            return MethodExtractionResult(
                trace=MethodExtractionTrace(
                    provider=self.name,
                    prompt_version=self.prompt_version,
                    status=ExtractionStatus.SKIPPED,
                )
            )
        return MethodExtractionResult(
            methods=[card.as_draft()],
            trace=MethodExtractionTrace(
                provider=self.name,
                prompt_version=self.prompt_version,
                status=ExtractionStatus.SUCCESS,
                extracted_method_keys=[card.key],
            ),
        )


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


def text_shape(text: str, parser: SafeMathParser | None = None) -> frozenset[str]:
    """一段题面在检索器眼里的形状：题面里所有表达式的叶到根路径并集。

    跨领域用例没有 `math_target`，而渐进评测的两个守卫都是按 `math_target` 取指纹的
    ——直接套用会**静默跳过**，等于没有守卫。v0.5.0 我做过一次只查了一侧就报告
    「不存在重叠」的审计，实际有三道留出题泄漏在训练里；这里必须自己算。
    """

    safe_parser = parser or SafeMathParser()
    paths: set[str] = set()
    for expression in extract_expressions(text, safe_parser):
        paths.update(operator_paths(expression, safe_parser))
    return frozenset(paths)


def validate_isolation(
    training: Sequence[CrossDomainTrainingCase],
    holdout: Sequence[EvaluationCase],
) -> None:
    """留出集不得泄漏在训练集里，结构同构比例也不得超过上限。"""

    training_texts = {case.problem.strip() for case in training}
    leaked = [case.id for case in holdout if case.problem.strip() in training_texts]
    if leaked:
        raise ValueError(
            "cross-domain holdout leaked into training: " + ", ".join(sorted(leaked))
        )

    parser = SafeMathParser()
    methods_by_shape: dict[frozenset[str], set[str]] = {}
    for case in training:
        shape = text_shape(case.problem, parser)
        if shape:
            methods_by_shape.setdefault(shape, set()).add(case.method_key)
    if not methods_by_shape:
        raise ValueError(
            "cross-domain isolation guard has nothing to check — no training problem "
            "yielded a parseable expression, so the guard would silently pass "
            "without guarding anything"
        )

    # 记得住的定义是**结构唯一决定答案**：训练里这个形状只对应一个方法，而且正好是
    # 要的那个。
    #
    # 两种情况刻意不算：
    #   结构相同但方法不同——照着记住的形状检索会给出**错的**方法，是难例不是漏题；
    #   一个形状对应多个方法——裸的 2x2 矩阵判不出是求行列式、求特征值还是求迹，
    #     结构只把候选缩到三个，选哪个仍要靠词面。
    memorisable = [
        case
        for case in holdout
        if methods_by_shape.get(text_shape(case.problem, parser))
        == set(case.expected_method_keys)
    ]
    ratio = len(memorisable) / len(holdout)
    if ratio > MAX_STRUCTURAL_OVERLAP:
        listed = ", ".join(sorted(case.id for case in memorisable)[:10])
        raise ValueError(
            "cross-domain isolation failed; "
            f"{len(memorisable)}/{len(holdout)} holdout cases ({ratio:.0%}) can be "
            f"answered by recalling a structurally identical training problem with the "
            f"same method, above the {MAX_STRUCTURAL_OVERLAP:.0%} limit: {listed}"
        )


def _validate_method_coverage(
    training: Sequence[CrossDomainTrainingCase],
    holdout: Sequence[EvaluationCase],
    cards: dict[str, CrossDomainMethodCard],
) -> None:
    """每个用到的方法键都必须有卡片，而且必须有训练例题教它。

    少一张卡，那道留出题就是**不可能答对**的——指标会掉，但掉的原因是语料残缺，
    不是检索变差。这种失败必须在评测开始前就喊出来，而不是混进分数里。
    """

    taught = {case.method_key for case in training}
    missing_cards = sorted({case.method_key for case in training} - set(cards))
    if missing_cards:
        raise ValueError(
            "training references methods with no card: " + ", ".join(missing_cards)
        )
    expected = {key for case in holdout for key in case.expected_method_keys}
    unteachable = sorted(expected - taught)
    if unteachable:
        raise ValueError(
            "holdout expects methods no training example teaches, so those cases can "
            "never be answered: " + ", ".join(unteachable)
        )


def _by_slice(
    cases: Sequence[EvaluationCase], results: Sequence[object]
) -> dict[str, dict[str, float]]:
    grouped: dict[str, list[object]] = {}
    for case, result in zip(cases, results, strict=True):
        key = (case.slice or EvaluationSlice.CONTROL).value
        grouped.setdefault(key, []).append(result)
    return {
        name: {
            "case_count": len(items),
            "hit_at_1": round(sum(item.hit_at_1 for item in items) / len(items), 6),
            "recall_at_k": round(
                sum(item.recall_at_k for item in items) / len(items), 6
            ),
            # 一条都没检索到的比例。学习之前它应该是 1.0——知识库是空的。
            "zero_result_rate": round(
                sum(not item.returned_method_keys for item in items) / len(items), 6
            ),
        }
        for name, items in sorted(grouped.items())
    }


def run_cross_evaluation(
    data_root: Path,
    train_path: Path,
    holdout_path: Path,
    methods_path: Path,
    top_k: int = 3,
) -> dict[str, object]:
    training = _load_jsonl(train_path, CrossDomainTrainingCase)
    holdout = _load_jsonl(holdout_path, EvaluationCase)
    cards = {
        card.key: card for card in _load_jsonl(methods_path, CrossDomainMethodCard)
    }
    validate_isolation(training, holdout)
    _validate_method_coverage(training, holdout, cards)

    extractor = DeclaredMethodExtractor(
        {case.problem: case for case in training}, cards
    )
    # 与渐进评测同样是密闭的：本地 .env 里的 provider 选择绝不能把一次可复现性检查
    # 变成一次付费的网络调用。
    service = MathHarnessService(
        data_root,
        extractor=extractor,
        generator=OfflineSympySolutionGenerator(),
    )
    workspace = service.create_workspace(
        WorkspaceCreate(
            name="跨领域检索评测",
            description="覆盖微积分、线代、几何、组合、复变、数论、逻辑的检索留出集。",
        )
    )

    # 两个口径都跑，因为两种情况在产品里都真实存在，而它们差得很远。
    #
    # **主口径是不带标签的那个**：聊天路径上用户就是打一句话，`tags` 是空的。
    # 第一版只跑了带标签的口径，报出 0.787；去掉标签只剩 0.404——留出用例的标签和
    # 方法卡的标签是一一对应写出来的，检索有一半是直接从标签上把答案读出来。
    untagged = [case.model_copy(update={"tags": []}) for case in holdout]
    tagged_request = EvaluationRequest(
        name="cross_domain_tagged", cases=holdout, top_k=top_k
    )
    untagged_request = EvaluationRequest(
        name="cross_domain", cases=untagged, top_k=top_k
    )

    before = service.evaluate_workspace(workspace.id, untagged_request)
    for case in training:
        service.ingest_example(workspace.id, case.as_example())
    after = service.evaluate_workspace(workspace.id, untagged_request)
    after_tagged = service.evaluate_workspace(workspace.id, tagged_request)

    return {
        "training_count": len(training),
        "holdout_count": len(holdout),
        "top_k": top_k,
        "before": {
            "overall": before.metrics.model_dump(mode="json"),
            "slices": _by_slice(holdout, before.cases),
        },
        "after": {
            "overall": after.metrics.model_dump(mode="json"),
            "slices": _by_slice(holdout, after.cases),
        },
        "after_with_tags": {
            "overall": after_tagged.metrics.model_dump(mode="json"),
            "slices": _by_slice(holdout, after_tagged.cases),
        },
    }


def format_report(report: dict[str, object]) -> str:
    lines = [
        (
            f"训练 {report['training_count']} 条 · 留出 {report['holdout_count']} 条 "
            f"· top_k={report['top_k']}"
        ),
        "",
        (
            f"{'切片':<16}{'题数':>6}{'学前':>8}{'学后':>8}{'Recall':>9}"
            f"{'零结果':>9}{'带标签':>9}"
        ),
    ]
    before_slices = report["before"]["slices"]
    tagged_slices = report["after_with_tags"]["slices"]
    for name, after in report["after"]["slices"].items():
        lines.append(
            f"{name:<16}{after['case_count']:>6}"
            f"{before_slices.get(name, {}).get('hit_at_1', 0.0):>8.3f}"
            f"{after['hit_at_1']:>8.3f}"
            f"{after['recall_at_k']:>9.3f}"
            f"{after['zero_result_rate']:>9.3f}"
            f"{tagged_slices.get(name, {}).get('hit_at_1', 0.0):>9.3f}"
        )
    lines.append("")
    lines.append(
        f"整体 Hit@1：学前 {report['before']['overall']['hit_at_1']:.3f} → "
        f"学后 {report['after']['overall']['hit_at_1']:.3f}"
        f"（用户额外给标签时 {report['after_with_tags']['overall']['hit_at_1']:.3f}）"
    )
    lines.append("主口径是不带标签的：聊天路径上用户就是打一句话。")
    return "\n".join(lines)


def gate_failures(report: dict[str, object]) -> list[str]:
    """检索门禁。返回未达标的项，空列表表示通过。"""

    failures: list[str] = []
    overall = report["after"]["overall"]["hit_at_1"]
    if overall < MIN_OVERALL_HIT_AT_1:
        failures.append(f"整体 Hit@1 {overall:.3f} < {MIN_OVERALL_HIT_AT_1:.3f}")
    cross_family = (
        report["after"]["slices"].get("cross_family", {}).get("hit_at_1", 0.0)
    )
    if cross_family < MIN_CROSS_FAMILY_HIT_AT_1:
        failures.append(
            f"cross_family Hit@1 {cross_family:.3f} < "
            f"{MIN_CROSS_FAMILY_HIT_AT_1:.3f}——被表面形状骗走了"
        )
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="跨领域检索评测。量的是检索，不是提炼——训练语料显式声明方法键。"
    )
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument(
        "--train", type=Path, default=Path("data/pilot/cross_domain_train.jsonl")
    )
    parser.add_argument(
        "--holdout", type=Path, default=Path("data/pilot/cross_domain_retrieval.jsonl")
    )
    parser.add_argument(
        "--methods", type=Path, default=Path("data/pilot/cross_domain_methods.jsonl")
    )
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--check",
        action="store_true",
        help="低于门限就以非零码退出。发布前跑这个。",
    )
    args = parser.parse_args(argv)

    report = run_cross_evaluation(
        args.data_root, args.train, args.holdout, args.methods, args.top_k
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
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


if __name__ == "__main__":
    raise SystemExit(main())
