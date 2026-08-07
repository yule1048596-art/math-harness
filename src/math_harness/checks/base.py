from __future__ import annotations

from enum import StrEnum
from time import perf_counter
from typing import Protocol

from pydantic import BaseModel, Field

from math_harness.checks.claim import Claim

# 检查流水线的骨架。
#
# 两档语义取自 DSPy Assertions：`Assert` 是硬约束，失败要回退重跑，重试耗尽就中止；
# `Suggest` 是软约束，失败只记录并继续，best-effort。这个项目里对应的是：确定性验证
# 决定「已验证」这个状态（硬），概率性检查只影响可信度档位（软）。
#
# 现有的 `repair_after_verification` 是这套东西的单约束特例。本阶段先把流水线建起来
# 并用在当前**完全没有检查**的路径上（`math_target is None` 时直接返回的那条），
# 既有渐进路径保持原样——它被 301 项测试覆盖着，为了统一而重写它换不来用户价值。


class CheckTier(StrEnum):
    #: 硬约束：失败触发有界回退重跑，最终失败决定结论状态。
    ASSERT = "assert"
    #: 软约束：失败只降可信度，不中止流程。
    SUGGEST = "suggest"


class CheckOutcome(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    #: 这条检查不适用于当前断言（比如断言里没有可代回的方程）。
    SKIPPED = "skipped"
    #: 检查本身出错——解析失败、超时。**不等于断言为假**，两者必须分开。
    ERRORED = "errored"


class CheckResult(BaseModel):
    check: str
    tier: CheckTier
    outcome: CheckOutcome
    #: 失败时注入重跑提示的具体信息。空字符串表示无可用细节。
    detail: str = Field(default="", max_length=2_000)
    #: 反例。有它用户才分得清「真错」和「缺前提」。
    counterexample: dict[str, str] = Field(default_factory=dict)
    #: 进审计的证据，例如实例化次数、对比用的另一路结果。
    evidence: dict[str, str] = Field(default_factory=dict)
    duration_ms: int = 0

    @property
    def passed(self) -> bool:
        return self.outcome is CheckOutcome.PASSED

    @property
    def blocking(self) -> bool:
        """硬约束失败才该触发重跑。检查自身出错不算断言为假，不触发。"""

        return self.tier is CheckTier.ASSERT and self.outcome is CheckOutcome.FAILED


class CheckContext(BaseModel):
    """一次检查所需的全部输入。"""

    problem: str = Field(default="", max_length=20_000)
    #: 结论断言。为 None 表示这道题没能被整理成可检验的命题。
    claim: Claim | None = None
    #: 解答过程拆出的逐步断言。方法卡门禁就看这些。
    steps: list[Claim] = Field(default_factory=list, max_length=40)
    #: 模型给出的答案原文，供需要文本比对的检查使用。
    answer_text: str = Field(default="", max_length=40_000)


class CheckReport(BaseModel):
    """一轮流水线的全部结果。"""

    results: list[CheckResult] = Field(default_factory=list)

    @property
    def blocking_failures(self) -> list[CheckResult]:
        return [item for item in self.results if item.blocking]

    @property
    def passed_checks(self) -> list[str]:
        return [item.check for item in self.results if item.passed]

    def first_blocking_detail(self) -> str:
        """给重跑用的失败说明。取第一条硬失败，避免把一堆信息糊给模型。"""

        for item in self.results:
            if item.blocking and item.detail:
                return item.detail
        return ""

    def audit_payload(self) -> dict[str, object]:
        return {
            "checks": [
                {
                    "check": item.check,
                    "tier": item.tier.value,
                    "outcome": item.outcome.value,
                    "detail": item.detail[:500],
                    "counterexample": item.counterexample,
                    "duration_ms": item.duration_ms,
                }
                for item in self.results
            ]
        }


class Check(Protocol):
    name: str
    tier: CheckTier

    def applies(self, context: CheckContext) -> bool: ...

    def run(self, context: CheckContext) -> CheckResult: ...


def run_checks(checks: list[Check], context: CheckContext) -> CheckReport:
    """按顺序跑全部适用的检查。

    单条检查抛异常记为 `ERRORED` 而不是让整轮崩掉——检查器出问题不该把用户的答案
    也一起弄丢。这与工具调用失败降级是同一条原则。
    """

    results: list[CheckResult] = []
    for check in checks:
        if not check.applies(context):
            results.append(
                CheckResult(
                    check=check.name,
                    tier=check.tier,
                    outcome=CheckOutcome.SKIPPED,
                )
            )
            continue
        started = perf_counter()
        try:
            result = check.run(context)
        except Exception as exc:  # noqa: BLE001
            result = CheckResult(
                check=check.name,
                tier=check.tier,
                outcome=CheckOutcome.ERRORED,
                detail=f"{exc.__class__.__name__}: {exc}"[:2_000],
            )
        if not result.duration_ms:
            result = result.model_copy(
                update={"duration_ms": int((perf_counter() - started) * 1000)}
            )
        results.append(result)
    return CheckReport(results=results)
