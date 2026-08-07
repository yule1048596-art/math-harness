from __future__ import annotations

import pytest

from math_harness.checks import (
    REVIEW_SYSTEM_PROMPT,
    Binding,
    CheckContext,
    CheckOutcome,
    CheckTier,
    Claim,
    ClaimKind,
    ConclusionConfidence,
    IndependentRecomputeCheck,
    PeerReviewCheck,
    assess,
    is_translatable,
    run_checks,
    to_wolfram,
)
from math_harness.checks.base import CheckReport, CheckResult


def claim(lhs: str, rhs: str, *symbols: str) -> Claim:
    return Claim(
        kind=ClaimKind.EQUALITY,
        lhs=lhs,
        rhs=rhs,
        bindings=[Binding(symbol=name) for name in symbols],
    )


class FakeWolfram:
    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.codes: list[str] = []

    def call_tool(self, name: str, arguments: dict[str, str]) -> str:
        del name
        self.codes.append(arguments["code"])
        return self.answer


class FakeReviewer:
    def __init__(self, verdict: str, profile_id: str = "reviewer-profile") -> None:
        self.verdict = verdict
        self.profile_id = profile_id
        self.name = f"fake-{profile_id}"
        self.seen: list[tuple[str, str]] = []

    def review(self, system_prompt: str, material: str) -> str:
        self.seen.append((system_prompt, material))
        return self.verdict


# --- 独立重算：翻译是真正的风险 ---------------------------------------
#
# 危险的不是 Wolfram 算错，而是把断言译歪——译歪之后核对的是另一个命题，通过了还会
# 抬高可信度，比不查更糟。所以只放行两边写法明确一致的子集。


def test_the_safe_subset_translates():
    assert to_wolfram("sin(x)**2 + cos(x)**2") == "Sin[x]^2 + Cos[x]^2"
    assert to_wolfram("sqrt(x**2 + a)") == "Sqrt[x^2 + a]"
    assert to_wolfram("(a+b)**2") == "(a+b)^2"


@pytest.mark.parametrize(
    ("lhs", "rhs", "why"),
    [
        ("diff(x**3, x)", "3*x**2", "diff 的参数形状两边不同"),
        ("Sum(binomial(n,k),(k,0,n))", "2**n", "Sum 的区间写法两边不同"),
        ("det(Matrix([[1,2],[3,4]]))", "-2", "矩阵字面量两边不同"),
        ("Integral(x, x)", "x**2/2", "积分的参数形状两边不同"),
    ],
)
def test_constructs_whose_argument_shape_differs_are_refused(lhs, rhs, why):
    """这些译错了不会报错，只会安静地核对另一个命题。宁可跳过。"""

    assert not is_translatable(claim(lhs, rhs, "x", "n", "k", "a")), why


def test_a_translatable_claim_that_simplifies_to_zero_passes():
    wolfram = FakeWolfram("0")
    check = IndependentRecomputeCheck(client=wolfram)
    context = CheckContext(claim=claim("(a+b)**2 - (a-b)**2", "4*a*b", "a", "b"))

    result = check.run(context)

    assert result.outcome is CheckOutcome.PASSED
    assert "FullSimplify" in wolfram.codes[0]


def test_a_non_zero_result_is_skipped_not_failed():
    """化简不出 0 **不等于**断言为假：另一个引擎可能只是没化开。

    判成失败会让一条正确的解被标成有反例，比漏掉一次交叉验证严重得多。
    """

    check = IndependentRecomputeCheck(client=FakeWolfram("a - b"))

    result = check.run(CheckContext(claim=claim("a", "b", "a", "b")))

    assert result.outcome is CheckOutcome.SKIPPED


def test_the_layer_is_off_without_a_client():
    """默认关闭。离线路径「不发网络请求」的承诺不能因为加了这一层而变。"""

    assert not IndependentRecomputeCheck().applies(
        CheckContext(claim=claim("x", "x", "x"))
    )


def test_a_tool_error_never_becomes_a_verdict():
    check = IndependentRecomputeCheck(client=FakeWolfram("工具报告错误：超时"))

    result = check.run(CheckContext(claim=claim("x", "x", "x")))

    assert result.outcome is CheckOutcome.ERRORED


def test_independent_recompute_stops_at_cross_checked():
    """另一个程序同意不等于证明。"""

    check = IndependentRecomputeCheck(client=FakeWolfram("0"))
    context = CheckContext(claim=claim("(a+b)**2 - (a-b)**2", "4*a*b", "a", "b"))

    assessment = assess(run_checks([check], context))

    assert assessment.conclusion is ConclusionConfidence.CROSS_CHECKED


# --- 异模型复核：两条硬要求 -------------------------------------------


def test_the_layer_skips_when_only_one_provider_is_configured():
    """同模型自查是**负收益**，不是「聊胜于无」。退化成自查会让标注变成噪声。"""

    check = PeerReviewCheck(
        reviewer=FakeReviewer("判定：正确", profile_id="same"),
        answer_profile_id="same",
    )

    assert not check.applies(CheckContext(answer_text="答案"))


def test_the_layer_runs_when_the_reviewer_is_a_different_provider():
    check = PeerReviewCheck(
        reviewer=FakeReviewer("判定：正确", profile_id="other"),
        answer_profile_id="same",
    )

    assert check.applies(CheckContext(answer_text="答案"))


def test_the_layer_skips_without_a_reviewer():
    assert not PeerReviewCheck().applies(CheckContext(answer_text="答案"))


def test_the_prompt_presents_the_answer_as_someone_elses_work():
    """模型改不动自己的错，却改得对以外部输入呈现的同样错误。

    所以措辞是设计的一部分，不是文风：提示词里绝不能出现「你自己的答案」。
    """

    assert "你自己" not in REVIEW_SYSTEM_PROMPT
    assert "你的答案" not in REVIEW_SYSTEM_PROMPT
    assert "别人提交" in REVIEW_SYSTEM_PROMPT

    reviewer = FakeReviewer("判定：正确", profile_id="other")
    PeerReviewCheck(reviewer=reviewer, answer_profile_id="same").run(
        CheckContext(problem="题目", answer_text="解答")
    )

    _, material = reviewer.seen[0]
    assert "提交的解答" in material


def test_an_approving_review_stops_at_peer_reviewed():
    check = PeerReviewCheck(
        reviewer=FakeReviewer("判定：正确", profile_id="other"),
        answer_profile_id="same",
    )

    assessment = assess(run_checks([check], CheckContext(answer_text="解答")))

    assert assessment.conclusion is ConclusionConfidence.PEER_REVIEWED


def test_an_ambiguous_review_grants_nothing():
    check = PeerReviewCheck(
        reviewer=FakeReviewer("我不太确定", profile_id="other"),
        answer_profile_id="same",
    )

    result = check.run(CheckContext(answer_text="解答"))

    assert result.outcome is CheckOutcome.SKIPPED


def test_a_reviewer_failure_never_becomes_a_verdict():
    class ExplodingReviewer:
        name = "boom"
        profile_id = "other"

        def review(self, system_prompt, material):
            del system_prompt, material
            raise RuntimeError("复核炸了")

    check = PeerReviewCheck(reviewer=ExplodingReviewer(), answer_profile_id="same")

    assert check.run(CheckContext(answer_text="解答")).outcome is CheckOutcome.ERRORED


def test_both_layers_are_suggestions_only():
    assert IndependentRecomputeCheck().tier is CheckTier.SUGGEST
    assert PeerReviewCheck().tier is CheckTier.SUGGEST


# --- 证伪要靠证据，不能靠意见 -----------------------------------------


def test_a_dissenting_reviewer_cannot_refute_a_verified_result():
    """要害。一条被 SymPy 符号验证过的解，不该被另一个模型的一句话打成「已找到反例」。

    那是让模型意见压过确定性判定，把信任边界整个反过来了。
    """

    report = CheckReport(
        results=[
            CheckResult(
                check="symbolic_equality",
                tier=CheckTier.ASSERT,
                outcome=CheckOutcome.PASSED,
            ),
            CheckResult(
                check="peer_review",
                tier=CheckTier.SUGGEST,
                outcome=CheckOutcome.FAILED,
                detail="判定：有误，第二步不对",
            ),
        ]
    )

    assessment = assess(report)

    assert assessment.conclusion is ConclusionConfidence.VERIFIED
    assert assessment.dissent


def test_a_layer_with_a_counterexample_still_refutes():
    """有证据的层照旧能证伪——这条区分的是「有反例」和「有意见」，不是放宽。"""

    report = CheckReport(
        results=[
            CheckResult(
                check="symbolic_equality",
                tier=CheckTier.ASSERT,
                outcome=CheckOutcome.PASSED,
            ),
            CheckResult(
                check="instantiation",
                tier=CheckTier.SUGGEST,
                outcome=CheckOutcome.FAILED,
                counterexample={"x": "3"},
            ),
        ]
    )

    assert assess(report).conclusion is ConclusionConfidence.REFUTED


def test_the_dissent_is_recorded_rather_than_discarded():
    """复核的反对意见不改档位，但用户应该看得到。"""

    check = PeerReviewCheck(
        reviewer=FakeReviewer("判定：有误\n第二步展开错了", profile_id="other"),
        answer_profile_id="same",
    )

    assessment = assess(run_checks([check], CheckContext(answer_text="解答")))

    assert assessment.conclusion is ConclusionConfidence.UNCHECKED
    assert any("第二步" in item for item in assessment.dissent)


# --- 接进服务后仍然成立 -----------------------------------------------


def test_the_service_skips_review_when_the_reviewer_is_the_answering_provider(tmp_path):
    """两条硬要求得在真实调用链上成立，不能只在检查层里成立。"""

    from math_harness.conversation import ChatGeneration
    from math_harness.models import (
        ConversationCreate,
        ConversationTurnRequest,
        WorkspaceCreate,
    )
    from math_harness.service import MathHarnessService

    class SelfResponder:
        name = "same-profile"
        model = "m"
        prompt_version = "v1"

        def respond(self, *args, **kwargs):
            del args, kwargs
            return ChatGeneration(
                content="diff(x**2, x) = 2*x", provider=self.name, model=self.model
            )

    reviewer = FakeReviewer("判定：正确", profile_id="same-profile")
    service = MathHarnessService(
        tmp_path, conversation_responder=SelfResponder(), reviewer=reviewer
    )
    workspace = service.create_workspace(WorkspaceCreate(name="自查防护"))
    conversation = service.create_conversation(
        workspace.id, ConversationCreate(title="对话")
    )

    service.send_conversation_turn(
        workspace.id, conversation.id, ConversationTurnRequest(message="问题")
    )

    assert reviewer.seen == []


def test_the_service_runs_review_against_a_different_provider(tmp_path):
    from math_harness.conversation import ChatGeneration
    from math_harness.models import (
        ConversationCreate,
        ConversationTurnRequest,
        WorkspaceCreate,
    )
    from math_harness.service import MathHarnessService

    class Responder:
        name = "answering-profile"
        model = "m"
        prompt_version = "v1"

        def respond(self, *args, **kwargs):
            del args, kwargs
            return ChatGeneration(
                content="diff(x**2, x) = 2*x", provider=self.name, model=self.model
            )

    reviewer = FakeReviewer("判定：正确", profile_id="reviewing-profile")
    service = MathHarnessService(
        tmp_path, conversation_responder=Responder(), reviewer=reviewer
    )
    workspace = service.create_workspace(WorkspaceCreate(name="异模型复核"))
    conversation = service.create_conversation(
        workspace.id, ConversationCreate(title="对话")
    )

    service.send_conversation_turn(
        workspace.id, conversation.id, ConversationTurnRequest(message="求导数")
    )

    assert len(reviewer.seen) == 1
    system_prompt, material = reviewer.seen[0]
    assert "别人提交" in system_prompt
    # 题目要带上，但**不带会话历史和记忆**：把原对话喂回去会毁掉「这是别人交上来的」
    # 这个前提。
    assert "求导数" in material
