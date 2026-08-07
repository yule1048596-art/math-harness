from __future__ import annotations

import pytest

from math_harness.conversation import ChatGeneration
from math_harness.models import (
    ConclusionConfidence,
    ConversationCreate,
    ConversationTurnRequest,
    ExtractionStatus,
    KnowledgeStatus,
    ProcessConfidence,
    VerificationStatus,
    WorkspaceCreate,
)
from math_harness.service import MathHarnessService

# 聊天路径的自查。
#
# 这条路以前**一次检查都不做**——`math_target is None` 直接返回，于是「只有渐进题
# 能被验证」。模型本来就答得了各个领域的题，卡住的从来不是模型，是这道闸。


class FixedResponder:
    name = "fixed-chat"
    model = "chat-test"
    prompt_version = "fixed-chat-v1"

    def __init__(self, answer: str) -> None:
        self.answer = answer

    def respond(self, *args, **kwargs):
        del args, kwargs
        return ChatGeneration(content=self.answer, provider=self.name, model=self.model)


def ask(tmp_path, answer: str):
    service = MathHarnessService(
        tmp_path, conversation_responder=FixedResponder(answer)
    )
    workspace = service.create_workspace(WorkspaceCreate(name="聊天自查"))
    conversation = service.create_conversation(
        workspace.id, ConversationCreate(title="对话")
    )
    result = service.send_conversation_turn(
        workspace.id, conversation.id, ConversationTurnRequest(message="请解这道题")
    )
    return result.assistant_message


def test_a_plain_chat_answer_now_gets_checked(tmp_path):
    """以前这条路 `verification_status` 恒为 None，什么都不查。"""

    message = ask(
        tmp_path,
        "1. diff(x**3, x) = 3*x**2\n"
        "2. diff(2*x, x) = 2\n"
        "所以 diff(x**3 + 2*x, x) = 3*x**2 + 2",
    )

    assert message.conclusion_confidence is ConclusionConfidence.VERIFIED
    assert message.process_confidence is ProcessConfidence.STEP_CHECKED


def test_a_wrong_chat_answer_is_refuted_with_a_counterexample(tmp_path):
    message = ask(tmp_path, "对幂函数求导得 diff(x**3, x) = 2*x**2")

    assert message.conclusion_confidence is ConclusionConfidence.REFUTED
    assert message.counterexample


def test_a_correct_answer_with_a_broken_step_is_caught_in_chat(tmp_path):
    """旗舰场景走完整的聊天回合：结论对、推导错，两轴必须分别说清楚。"""

    message = ask(
        tmp_path,
        "(a+b)^2 = a^2 + 2*a*b + b^2\n"
        "(a-b)^2 = a^2 - 2*a*b - b^2\n"  # 错：b² 符号
        "相减得 (a+b)^2 - (a-b)^2 = 4*a*b",
    )

    assert message.conclusion_confidence is ConclusionConfidence.VERIFIED
    assert message.process_confidence is ProcessConfidence.STEP_FAILED
    assert set(message.counterexample) == {"a", "b"}


def test_a_prose_only_answer_stays_unlabelled_rather_than_failing(tmp_path):
    """抽不出可检验内容不是失败。留空表示「没查」，不能显示成「查了没过」。"""

    message = ask(tmp_path, "这道题要用洛必达法则，先对分子分母分别求导。")

    assert message.conclusion_confidence is None
    assert message.process_confidence is None
    assert message.checked_claims == []
    assert message.content


@pytest.mark.parametrize(
    "answer",
    [
        "y = __import__('os')",
        "z = eval('1+1')",
        "w = Sum(1/k**2, (k, 1, 10000000))",
    ],
)
def test_hostile_model_output_never_reaches_the_checker(tmp_path, answer: str):
    """回答是整条链路上最不可信的输入。抽不出来就是抽不出来，不能崩也不能放行。"""

    message = ask(tmp_path, answer)

    assert message.conclusion_confidence is None
    assert message.content == answer


def test_the_checked_claims_are_recorded_for_display(tmp_path):
    """抽错题的风险始终存在（会验证一个你没问的命题）。

    处理方式是让它**可见**——用户看得到 AI 到底验了哪些命题，而不是事前拦着不让走。
    """

    message = ask(tmp_path, "diff(x**2, x) = 2*x")

    assert message.checked_claims == ["diff(x**2, x) = 2*x"]


def test_a_checker_failure_never_loses_the_answer(tmp_path):
    """检查是附加价值，不是前置条件。它出问题不能把用户的回答一起弄丢。"""

    class ExplodingDrafter:
        name = "exploding"
        prompt_version = "boom-v1"

        def draft(self, problem, answer):
            del problem, answer
            raise RuntimeError("抽断言炸了")

    service = MathHarnessService(
        tmp_path,
        conversation_responder=FixedResponder("diff(x**2, x) = 2*x"),
        claim_drafter=ExplodingDrafter(),
    )
    workspace = service.create_workspace(WorkspaceCreate(name="容错"))
    conversation = service.create_conversation(
        workspace.id, ConversationCreate(title="对话")
    )

    result = service.send_conversation_turn(
        workspace.id, conversation.id, ConversationTurnRequest(message="问题")
    )

    assert result.assistant_message.content == "diff(x**2, x) = 2*x"
    assert result.assistant_message.conclusion_confidence is None


def test_the_confidence_survives_a_reload(tmp_path):
    """两轴要落库——徽章不能只在刚生成那一刻存在。"""

    service = MathHarnessService(
        tmp_path, conversation_responder=FixedResponder("diff(x**2, x) = 2*x")
    )
    workspace = service.create_workspace(WorkspaceCreate(name="持久化"))
    conversation = service.create_conversation(
        workspace.id, ConversationCreate(title="对话")
    )
    service.send_conversation_turn(
        workspace.id, conversation.id, ConversationTurnRequest(message="问题")
    )

    reloaded = MathHarnessService(tmp_path)
    messages = reloaded.list_conversation_messages(workspace.id, conversation.id)
    assistant = messages[-1]

    assert assistant.conclusion_confidence is ConclusionConfidence.VERIFIED
    assert assistant.checked_claims == ["diff(x**2, x) = 2*x"]


# --- 聊天路径的知识入库 -----------------------------------------------
#
# 「不会记住无关紧要的信息防止污染」在这里落地：门禁收得很紧，只有真的抽出了可检验
# 内容、而且没被反例推翻的回合才进库。


def draft(tmp_path, answer: str, message: str = "用有理化法求极限"):
    service = MathHarnessService(
        tmp_path, conversation_responder=FixedResponder(answer)
    )
    workspace = service.create_workspace(WorkspaceCreate(name="聊天入库"))
    conversation = service.create_conversation(
        workspace.id, ConversationCreate(title="对话")
    )
    result = service.send_conversation_turn(
        workspace.id, conversation.id, ConversationTurnRequest(message=message)
    )
    return result


def test_a_checkable_chat_turn_becomes_a_knowledge_draft(tmp_path):
    """以前只有求解路径产出草稿，聊天问的题一律进不了知识库。"""

    result = draft(tmp_path, "所以 diff(x**3 + 2*x, x) = 3*x**2 + 2")

    assert result.knowledge_draft is not None
    assert result.assistant_message.knowledge_draft_id == result.knowledge_draft.id


def test_a_prose_only_turn_never_enters_the_knowledge_base(tmp_path):
    """纯讲解没有可复用的东西。每个回合都建草稿会把知识库淹掉。"""

    assert (
        draft(tmp_path, "这道题要用洛必达法则，先对分子分母求导。").knowledge_draft
        is None
    )


def test_a_refuted_turn_never_enters_the_knowledge_base(tmp_path):
    """已经查出错的答案不是「待确认」，是已知错误。"""

    assert draft(tmp_path, "diff(x**3, x) = 2*x**2").knowledge_draft is None


def test_a_chat_draft_always_needs_human_review(tmp_path):
    """这条路上的检查再怎么过也是概率性的，晋级仍然要人工确认。"""

    result = draft(tmp_path, "所以 diff(x**3 + 2*x, x) = 3*x**2 + 2")

    assert result.knowledge_draft.status is KnowledgeStatus.PENDING_REVIEW
    assert result.knowledge_draft.reviewed is False


def test_a_chat_draft_keeps_both_axes(tmp_path):
    result = draft(tmp_path, "所以 diff(x**3 + 2*x, x) = 3*x**2 + 2")
    verification = result.knowledge_draft.verification

    assert verification.conclusion_confidence is ConclusionConfidence.VERIFIED
    assert verification.process_confidence is ProcessConfidence.STEP_CHECKED


def test_a_chat_turn_with_a_broken_step_stores_the_example_but_no_method(tmp_path):
    """整条链路上最要紧的一条：例题可以留，方法**不能**从坏推导里学。"""

    result = draft(
        tmp_path,
        "(a+b)^2 = a^2 + 2*a*b + b^2\n"
        "(a-b)^2 = a^2 - 2*a*b - b^2\n"
        "相减得 (a+b)^2 - (a-b)^2 = 4*a*b",
    )

    assert result.knowledge_draft is not None
    assert result.knowledge_draft.method_drafts == []
    assert result.knowledge_draft.extraction.status is ExtractionStatus.SKIPPED


def test_a_sound_chat_derivation_does_yield_a_method_draft(tmp_path):
    """门禁只拦坏推导，不能顺手把正常路径也拦掉。"""

    result = draft(tmp_path, "所以 diff(x**3 + 2*x, x) = 3*x**2 + 2")

    assert result.knowledge_draft.method_drafts != []


def test_a_numerically_checked_turn_is_not_recorded_as_verified(tmp_path):
    """随机取值都对，不该拿到和符号证明一样的三值状态——晋级门禁看的就是它。"""

    result = draft(tmp_path, "Sum(binomial(n,k),(k,0,n)) = 2**n")
    verification = result.knowledge_draft.verification

    assert (
        verification.conclusion_confidence is ConclusionConfidence.NUMERICALLY_CHECKED
    )
    assert verification.status is VerificationStatus.NEEDS_REVIEW


def test_a_capture_failure_never_loses_the_turn(tmp_path):
    """入库是附加价值，不是前置条件。"""

    service = MathHarnessService(
        tmp_path,
        conversation_responder=FixedResponder("所以 diff(x**2, x) = 2*x"),
    )
    workspace = service.create_workspace(WorkspaceCreate(name="容错"))
    conversation = service.create_conversation(
        workspace.id, ConversationCreate(title="对话")
    )
    service._ingest_example = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("炸"))

    result = service.send_conversation_turn(
        workspace.id, conversation.id, ConversationTurnRequest(message="问题")
    )

    assert result.assistant_message.content == "所以 diff(x**2, x) = 2*x"
    assert result.knowledge_draft is None
