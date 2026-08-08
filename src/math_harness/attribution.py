from __future__ import annotations

from dataclasses import dataclass, field

from math_harness.extraction import MethodExtractorProtocol
from math_harness.methods import MethodExtractor

# 方法归属的误标度量。
#
# v0.15 的门禁查的是**数学**——结论对不对、推导站不站得住。它们**不查方法标签**。
# 于是一条数学完全正确、`step_checked` 的解，照样能往知识库里塞一张写着错方法的卡，
# 并在以后被检索出来当依据用。这正是「防止污染」要防的东西，却没有任何度量在看它。
#
# 两个实测的漏洞：
#   问「用有理化方法求极限」+ 答案是求导  →  提取出 rationalization
#   答案写「这题和斯特林公式无关」        →  提取出 stirling
#
# 验收指标是**误标率**：抽出了没用过的方法的比例。它比漏标率更要紧——漏了只是没学到，
# 标错了是学进去一个假的，而且以后会被当依据。


@dataclass(frozen=True)
class AttributionCase:
    """一道题解，外加它**真正**用了哪些方法。"""

    name: str
    problem: str
    solution: str
    expected: frozenset[str]
    hint: str | None = None
    #: 这条用例在考什么。误标率掉下来时要能说清是哪一类修好了。
    trap: str = ""


@dataclass
class AttributionReport:
    total: int = 0
    #: 抽出了不该抽的方法的用例数。
    mislabelled: int = 0
    #: 该抽的方法没抽出来的用例数。
    missed: int = 0
    #: 每个用例的详情，供排查。
    details: list[tuple[str, set[str], set[str]]] = field(default_factory=list)

    @property
    def mislabel_rate(self) -> float:
        return round(self.mislabelled / self.total, 6) if self.total else 0.0

    @property
    def miss_rate(self) -> float:
        return round(self.missed / self.total, 6) if self.total else 0.0


#: 通用兜底卡，不代表任何具体方法，不计入误标。
_GENERIC = "generic_example"


def attribution_cases() -> list[AttributionCase]:
    """已知用了什么方法的题解对，含专门设计的陷阱。"""

    return [
        # --- 正常应当抽出 ---
        AttributionCase(
            name="有理化-正常",
            problem="求 x→∞ 时 sqrt(x^2+x)-x 的渐进展开。",
            solution="根式之差在无穷远处抵消，先做共轭有理化，再按 1/x 展开。",
            expected=frozenset({"rationalization"}),
            trap="解答里确实用了这个方法",
        ),
        AttributionCase(
            name="泰勒-正常",
            problem="求 log(1+x) 在 0 附近的展开。",
            solution="直接用泰勒展开写到三阶。",
            expected=frozenset({"taylor_expansion"}),
            trap="解答里确实用了这个方法",
        ),
        AttributionCase(
            name="斯特林-正常",
            problem="求 n→∞ 时 n! 的渐进等价式。",
            solution="套用 Stirling 公式给出主项。",
            expected=frozenset({"stirling"}),
            trap="解答里确实用了这个方法",
        ),
        AttributionCase(
            name="两个方法",
            problem="求 n→∞ 时 n! 的精细渐进式。",
            # 刻意避开「取对数」：那既可能是题目本身的内容，也可能是一种技巧，
            # 标注两可的用例进基准只会惩罚正确行为。
            solution="先用 Stirling 公式给出主项，再对主项做泰勒展开细化。",
            expected=frozenset({"stirling", "taylor_expansion"}),
            trap="一条解答里用了两个方法",
        ),
        # --- 陷阱一：问题文本泄漏 ---
        AttributionCase(
            name="题面提到但解答没用",
            problem="用有理化方法求这个极限。",
            solution="对幂函数逐项求导：diff(x**3, x) = 3*x**2。",
            expected=frozenset(),
            trap="题面提到有理化，解答做的是求导",
        ),
        AttributionCase(
            name="题面提到斯特林但解答没用",
            problem="这道题和斯特林公式有关吗？",
            solution="直接代入定义化简即可，两项相消。",
            expected=frozenset(),
            trap="题面提到斯特林，解答没用",
        ),
        AttributionCase(
            name="提示词泄漏",
            problem="求这个极限。",
            solution="按定义逐项化简。",
            hint="泰勒展开",
            expected=frozenset(),
            trap="提示词提到泰勒，解答没用",
        ),
        # --- 陷阱二：否定 ---
        AttributionCase(
            name="否定-无关",
            problem="求这个渐进式。",
            solution="这题和斯特林公式无关，直接按定义算即可。",
            expected=frozenset(),
            trap="解答里明说与该方法无关",
        ),
        AttributionCase(
            name="否定-不用",
            problem="求这个展开。",
            solution="不用泰勒展开也能做，按几何级数直接求和。",
            expected=frozenset(),
            trap="解答里明说不用该方法",
        ),
        AttributionCase(
            name="否定-而不是",
            problem="求这个极限。",
            solution="这里应当取对数，而不是做有理化。",
            expected=frozenset({"log_transform"}),
            trap="一句话里一个方法被否定、另一个被采用",
        ),
        AttributionCase(
            name="否定-没有必要",
            problem="求这个渐进等价式。",
            solution="没有必要用主导平衡，两边阶数一眼可见。",
            expected=frozenset(),
            trap="解答里明说没必要用该方法",
        ),
        # --- 陷阱三：只是提及，不是使用 ---
        AttributionCase(
            name="对比性提及",
            problem="求这个极限。",
            solution="相比泰勒展开，这里用变量倒换 t=1/x 更直接。",
            expected=frozenset({"variable_inversion"}),
            trap="提到一个方法只是为了对比，实际用的是另一个",
        ),
    ]


def measure_attribution(
    extractor: MethodExtractorProtocol | None = None,
) -> AttributionReport:
    """量一遍误标率与漏标率。"""

    selected = extractor or MethodExtractor()
    report = AttributionReport()
    for case in attribution_cases():
        result = selected.extract(case.problem, case.solution, case.hint)
        extracted = {draft.key for draft in result.methods} - {_GENERIC}
        report.total += 1
        wrong = extracted - case.expected
        missing = case.expected - extracted
        if wrong:
            report.mislabelled += 1
        if missing:
            report.missed += 1
        report.details.append((case.name, wrong, missing))
    return report


def format_report(report: AttributionReport) -> str:
    lines = [
        f"用例 {report.total} 条 · 误标 {report.mislabelled} · 漏标 {report.missed}",
        f"误标率 {report.mislabel_rate:.3f} · 漏标率 {report.miss_rate:.3f}",
        "",
    ]
    for name, wrong, missing in report.details:
        if not wrong and not missing:
            continue
        parts = []
        if wrong:
            parts.append(f"多抽 {sorted(wrong)}")
        if missing:
            parts.append(f"漏抽 {sorted(missing)}")
        lines.append(f"  {name:<22}{'；'.join(parts)}")
    if len(lines) == 3:
        lines.append("  全部正确。")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="方法归属的误标度量。误标比漏标严重：漏了只是没学到，"
        "标错了是学进去一个假的，以后还会被当依据。"
    )
    parser.add_argument(
        "--max-mislabel-rate",
        type=float,
        default=None,
        help="超过这个误标率就以非零码退出。",
    )
    args = parser.parse_args(argv)

    report = measure_attribution()
    print(format_report(report))
    if (
        args.max_mislabel_rate is not None
        and report.mislabel_rate > args.max_mislabel_rate
    ):
        print(
            f"\n未达门限：误标率 {report.mislabel_rate:.3f} > "
            f"{args.max_mislabel_rate:.3f}"
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
