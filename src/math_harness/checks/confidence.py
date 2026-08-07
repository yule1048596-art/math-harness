from __future__ import annotations

from pydantic import BaseModel

from math_harness.checks.base import CheckOutcome, CheckReport, CheckTier
from math_harness.models import ConclusionConfidence, ProcessConfidence

# 可信度的**判定策略**：哪条检查配得上哪一档，一轮检查怎么折算成两轴。
#
# 两个枚举本身在 `models.py`——它们要落库，和别的持久化状态放在一起，也让这个模块可以
# 反过来依赖 models 而不成环。
#
# 这里落实的硬规则：**方法卡只从 `step_checked` 的解提取**。方法卡是从推导提取的，
# 结论对不足以说明方法对；从错推导里学方法正是知识库污染的源头。例题本身可以带任意
# 档位入库（用户已决定未验证结果也进库，标注清楚即可），方法卡不行。


#: 档位排序。StrEnum 本身没有序，而「低可信度不得压过 verified」需要能比较。
_CONCLUSION_RANK: dict[ConclusionConfidence, int] = {
    ConclusionConfidence.PROOF_VERIFIED: 6,
    ConclusionConfidence.VERIFIED: 5,
    ConclusionConfidence.NUMERICALLY_CHECKED: 4,
    ConclusionConfidence.CROSS_CHECKED: 3,
    ConclusionConfidence.PEER_REVIEWED: 2,
    ConclusionConfidence.UNCHECKED: 1,
    ConclusionConfidence.REFUTED: 0,
}


def conclusion_rank(level: ConclusionConfidence) -> int:
    return _CONCLUSION_RANK[level]


#: 每条检查通过时最高能产出的结论档位。
#
# **这里是整个信任边界的唯一入口。** 把它集中成一张表，是为了让「这一层凭什么敢说
# 自己验证了」变成一个能一眼复核的问题。不在表里的检查产出 `UNCHECKED`——新加一层
# 检查不会因为忘了登记而悄悄获得信任，只会不被信任。
_GRANTS: dict[str, ConclusionConfidence] = {
    "symbolic_equality": ConclusionConfidence.VERIFIED,
    "instantiation": ConclusionConfidence.NUMERICALLY_CHECKED,
    # 阶段 F 的两层。工具或模型说「对」只能到这两档，**绝不是 verified**：
    # 知识库靠这个标签决定以后信任谁，把概率性检查标成确定性的，污染会一路传下去。
    "independent_recompute": ConclusionConfidence.CROSS_CHECKED,
    "peer_review": ConclusionConfidence.PEER_REVIEWED,
}

#: 只有确定性判定才配得上这两档，概率性检查再准也不行。
DETERMINISTIC_LEVELS = frozenset(
    {ConclusionConfidence.VERIFIED, ConclusionConfidence.PROOF_VERIFIED}
)

#: 有资格把结论判成 `refuted` 的层。
#
# 证伪要靠**证据**，不能靠意见。这三层失败时都拿得出具体反例或恒不为零的残差；
# 复核模型说「这看着不对」拿不出任何东西。不做这个区分的话，一条被 SymPy 符号验证
# 过的解会被另一个模型的一句话打成「已找到反例」——那是让模型意见压过确定性判定，
# 恰好把信任边界反过来了。
_MAY_REFUTE = frozenset({"symbolic_equality", "instantiation", "step_instantiation"})


def granted_level(check_name: str, tier: CheckTier) -> ConclusionConfidence:
    """某条检查通过时该给的档位。

    第二道闸：**SUGGEST 档的检查永远拿不到 `verified`**，哪怕登记表写错了。软约束的
    定义就是「失败不中止」，一条失败都不算数的检查不该产出确定性结论。
    """

    level = _GRANTS.get(check_name, ConclusionConfidence.UNCHECKED)
    if tier is CheckTier.SUGGEST and level in DETERMINISTIC_LEVELS:
        return ConclusionConfidence.NUMERICALLY_CHECKED
    return level


class ConfidenceAssessment(BaseModel):
    """一次检查流水线得出的双轴结论。"""

    conclusion: ConclusionConfidence = ConclusionConfidence.UNCHECKED
    process: ProcessConfidence = ProcessConfidence.STEP_UNCHECKED
    #: 支撑结论档位的那条检查，写进审计用。
    conclusion_source: str = ""
    #: 反例。有它用户才分得清「真错」和「缺前提」。
    counterexample: dict[str, str] = {}
    #: 提出异议但没有资格证伪的层留下的说明（比如复核模型不同意）。
    #: 它不改变档位，但用户应该看得到。
    dissent: list[str] = []

    @property
    def may_extract_methods(self) -> bool:
        """能不能从这个解提取方法卡。

        **只认 `step_checked`。** 阶段 H 实测：结论正确但某步写错的解，只查结论的层
        全部放行。从这种推导里学方法，等于把一个错方法存进知识库，以后还会被检索复用。
        """

        return self.process is ProcessConfidence.STEP_CHECKED

    @property
    def may_enter_knowledge_base(self) -> bool:
        """能不能作为例题入库。

        除了被反例推翻的，都可以——用户明确决定未验证的结果也进库，只要标注清楚可信度。
        已经查出错的那种不进：那不是「待确认」，是已知错误。
        """

        return self.conclusion is not ConclusionConfidence.REFUTED


def assess(report: CheckReport) -> ConfidenceAssessment:
    """把一轮检查结果折算成双轴可信度。"""

    conclusion = ConclusionConfidence.UNCHECKED
    source = ""
    counterexample: dict[str, str] = {}
    dissent: list[str] = []
    process = ProcessConfidence.STEP_UNCHECKED
    refuted = False

    for result in report.results:
        is_step_check = result.check.startswith("step_")

        if is_step_check:
            if result.outcome is CheckOutcome.PASSED:
                process = ProcessConfidence.STEP_CHECKED
            elif result.outcome is CheckOutcome.FAILED:
                process = ProcessConfidence.STEP_FAILED
                if not counterexample:
                    counterexample = dict(result.counterexample)
            continue

        if result.outcome is CheckOutcome.FAILED:
            if result.check not in _MAY_REFUTE:
                # 有异议但拿不出证据：记下来给用户看，**不动档位**。
                if result.detail:
                    dissent.append(f"{result.check}：{result.detail}")
                continue
            # 拿得出证据的层给出反例，结论就是错的。别的层通过不能把它救回来——
            # 一个反例足以证伪，多少次通过都不足以证明。
            refuted = True
            if not counterexample:
                counterexample = dict(result.counterexample)
            continue

        if result.outcome is not CheckOutcome.PASSED:
            continue

        level = granted_level(result.check, result.tier)
        if conclusion_rank(level) > conclusion_rank(conclusion):
            conclusion = level
            source = result.check

    return ConfidenceAssessment(
        conclusion=ConclusionConfidence.REFUTED if refuted else conclusion,
        process=process,
        conclusion_source=source,
        counterexample=counterexample,
        dissent=dissent,
    )


#: 检索加权。低可信度的例题不得压过 `verified` 的。
_RETRIEVAL_WEIGHT: dict[ConclusionConfidence, float] = {
    ConclusionConfidence.PROOF_VERIFIED: 1.0,
    ConclusionConfidence.VERIFIED: 1.0,
    ConclusionConfidence.NUMERICALLY_CHECKED: 0.8,
    ConclusionConfidence.CROSS_CHECKED: 0.6,
    ConclusionConfidence.PEER_REVIEWED: 0.5,
    ConclusionConfidence.UNCHECKED: 0.3,
    ConclusionConfidence.REFUTED: 0.0,
}


def retrieval_weight(level: ConclusionConfidence) -> float:
    """检索排序的可信度权重。

    未验证的内容进了知识库就会被检索到，这是用户要的。但它不该盖过验证过的内容——
    「越用越强」的前提是强的那部分排在前面。

    **目前尚未接进方法卡打分。** 方法卡只从 `step_checked` 的解提取，而现在能产出
    方法卡的只有求解路径，它们的可信度是齐平的——没有可加权的差异。真正需要加权是在
    聊天路径也开始产出知识草稿之后，那要先有「从自然语言里抽出断言」这一步，属于阶段
    G 的管线。策略先定在这里并钉上测试，接线等有东西可加权时一起做。
    """

    return _RETRIEVAL_WEIGHT[level]
