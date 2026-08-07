from __future__ import annotations

from typing import Protocol

from math_harness.checks.base import (
    CheckContext,
    CheckOutcome,
    CheckResult,
    CheckTier,
)

# 异模型复核：换一个 provider 看同一份解答。
#
# 两条设计约束直接来自研究结论，都不是风格问题：
#
#   1. **只配了一个 provider 时必须跳过。** 同模型自查基本无效、常常更差；同模型给
#      自己的 12 个候选打分增益 ≈0，而三个互不可见的模型能到 100%。退化成自查不是
#      「聊胜于无」，是负收益。
#   2. **提示词把解答当外部材料呈现。** 模型改不动自己的错，却改得对以外部输入形式
#      呈现的同样错误。所以措辞里绝不能出现「你自己的答案」。
#
# 它产出 `peer_reviewed`，**永远不是 `verified`**：另一个模型同意仍然只是意见。
# 它也**没有资格证伪**——见 `confidence._MAY_REFUTE`。一条被 SymPy 验过的解不该被
# 另一个模型的一句话打成「已找到反例」。

REVIEW_PROMPT_VERSION = "peer-review-v1"

#: 复核提示词。
#
# 通篇把解答说成「提交上来的」「作者」，一次都不提这是谁写的。这不是客气话——研究
# 结论说模型对「自己的答案」和「别人的答案」表现不同，这个措辞就是那个区别本身。
REVIEW_SYSTEM_PROMPT = """你在评审一份**别人提交的**数学解答。

材料是投稿人交上来的，作者是谁与你无关。你的任务是判断它对不对。

请按下面的格式回答，第一行只写一个词：
判定：正确
或
判定：有误

第二行起写理由。如果认为有误，指出**具体哪一步**错了。
不确定就写「判定：不确定」，不要猜。"""


class ReviewerProtocol(Protocol):
    """一个能做复核的模型客户端。"""

    name: str
    profile_id: str

    def review(self, system_prompt: str, material: str) -> str: ...


class PeerReviewCheck:
    """让另一个 provider 复核这份解答。"""

    name = "peer_review"
    tier = CheckTier.SUGGEST

    def __init__(
        self,
        reviewer: ReviewerProtocol | None = None,
        answer_profile_id: str | None = None,
    ) -> None:
        self.reviewer = reviewer
        self.answer_profile_id = answer_profile_id

    def applies(self, context: CheckContext) -> bool:
        """没有第二个 provider 就不做。

        这里不是「有总比没有好」：同模型自查是负收益，退化成自查会让可信度标注变成
        噪声。宁可这一层什么都不产出。
        """

        if self.reviewer is None or not context.answer_text:
            return False
        return self.reviewer.profile_id != self.answer_profile_id

    def run(self, context: CheckContext) -> CheckResult:
        assert self.reviewer is not None

        material = self._material(context)
        try:
            verdict = self.reviewer.review(REVIEW_SYSTEM_PROMPT, material)
        except Exception as exc:  # noqa: BLE001
            return self._result(CheckOutcome.ERRORED, f"复核未能执行：{exc}")

        first_line = verdict.strip().splitlines()[0] if verdict.strip() else ""
        if "正确" in first_line:
            return self._result(CheckOutcome.PASSED, "")
        if "有误" in first_line:
            # 记为 FAILED，但它没有证伪资格：只会被记成异议，不改档位。
            return self._result(CheckOutcome.FAILED, verdict.strip())
        return self._result(CheckOutcome.SKIPPED, f"复核未给出明确判定：{first_line}")

    @staticmethod
    def _material(context: CheckContext) -> str:
        parts = []
        if context.problem:
            parts.append(f"【题目】\n{context.problem}")
        parts.append(f"【提交的解答】\n{context.answer_text}")
        if context.steps:
            lines = "\n".join(
                f"{index}. {step.lhs} = {step.rhs}"
                for index, step in enumerate(context.steps, start=1)
                if step.rhs
            )
            if lines:
                parts.append(f"【解答中的推导步骤】\n{lines}")
        return "\n\n".join(parts)

    def _result(self, outcome: CheckOutcome, detail: str) -> CheckResult:
        return CheckResult(
            check=self.name,
            tier=self.tier,
            outcome=outcome,
            detail=detail[:2_000],
            evidence={
                "reviewer": self.reviewer.name if self.reviewer else "",
                "prompt_version": REVIEW_PROMPT_VERSION,
            },
        )
