from __future__ import annotations

import pytest

from math_harness.checks import Claim, ClaimKind
from math_harness.claim_drafting import RuleBasedClaimDrafter
from math_harness.conversation import ChatGeneration
from math_harness.math_parser import SafeMathParser
from math_harness.models import (
    ConversationCreate,
    ConversationTurnRequest,
    ExampleCreate,
    WorkspaceCreate,
)
from math_harness.providers.openai_relevance import (
    RELEVANCE_SYSTEM_PROMPT,
    OpenAIRelevanceJudge,
    _parse_verdict,
)
from math_harness.relevance import (
    GroundedRelevanceRule,
    RelevanceGate,
    TurnRelevance,
    question_expressions,
)
from math_harness.service import MathHarnessService


def equality(lhs: str, rhs: str) -> Claim:
    return Claim(kind=ClaimKind.EQUALITY, lhs=lhs, rhs=rhs)


class CountingJudge:
    """记调用次数的假判定器。真实调用次数是度量三元组里的第三个数。"""

    name = "fake"
    prompt_version = "fake-v1"

    def __init__(self, verdict: TurnRelevance = TurnRelevance.SOLVING) -> None:
        self.verdict = verdict
        self.calls = 0
        self.seen: list[tuple[str, list[str]]] = []

    def judge(self, question: str, recent_questions: list[str]) -> TurnRelevance:
        self.calls += 1
        self.seen.append((question, recent_questions))
        return self.verdict


class ExplodingJudge:
    name = "boom"
    prompt_version = "boom-v1"

    def judge(self, question: str, recent_questions: list[str]) -> TurnRelevance:
        raise RuntimeError("judge is down")


# --- 规则层 -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("求 x^3 的导数", ["x**3"]),
        ("计算 Integral(x*exp(x), x)", ["Integral(x*exp(x), x)"]),
        # 单符号与纯数字太容易偶然撞上，不作为 grounding 依据。
        ("a 是什么", []),
        ("等于 42 吗", []),
        ("你好", []),
        ("谢谢！", []),
        ("你能干什么", []),
    ],
)
def test_question_expressions_only_keeps_nontrivial_math(
    question: str, expected: list[str]
) -> None:
    found = question_expressions(question, SafeMathParser())
    assert [str(item) for item in found] == expected


def test_rule_grounds_when_the_claim_contains_the_question_expression() -> None:
    rule = GroundedRelevanceRule()
    verdict = rule.evaluate(
        "求 x^3 的导数", [equality("Derivative(x**3, x)", "3*x**2")]
    )
    assert verdict is not None
    assert verdict.relevance is TurnRelevance.SOLVING
    # 规则层是免费的：定下来就不该再花模型调用。
    assert verdict.model_calls == 0
    assert verdict.source == "rules"


def test_rule_stays_silent_when_the_claim_is_unrelated_to_the_question() -> None:
    """用户报的那个场景：一句问候，回答顺口带了一条正确公式。

    规则层必须**扎不住**——扎住了就是误收，而误收率的门槛是 0.0。
    """

    rule = GroundedRelevanceRule()
    assert rule.evaluate("你好", [equality("Derivative(x**3, x)", "3*x**2")]) is None


def test_rule_stays_silent_on_follow_up_questions() -> None:
    """承接式追问扎不住是对的：它交给模型判，不是由规则层否掉。"""

    rule = GroundedRelevanceRule()
    assert (
        rule.evaluate("这个怎么证明？", [equality("(a+b)**2", "a**2+2*a*b+b**2")])
        is None
    )


def test_rule_survives_unparseable_claims() -> None:
    rule = GroundedRelevanceRule()
    assert rule.evaluate("求 x^3 的导数", [equality("!!! 不是式子", "???")]) is None


def test_rule_works_on_claims_drafted_from_a_real_answer() -> None:
    drafter = RuleBasedClaimDrafter()
    answer = "Derivative(x^3, x) = 3x^2"
    draft = drafter.draft("求 x^3 的导数", answer)
    claims = ([draft.claim] if draft.claim else []) + draft.steps
    assert GroundedRelevanceRule().evaluate("求 x^3 的导数", claims) is not None


# --- 闸门 -------------------------------------------------------------------


def test_gate_does_not_call_the_model_when_the_rule_already_decided() -> None:
    judge = CountingJudge()
    gate = RelevanceGate(judge=judge)
    verdict = gate.evaluate(
        "求 x^3 的导数", [equality("Derivative(x**3, x)", "3*x**2")]
    )
    assert verdict.relevance is TurnRelevance.SOLVING
    assert judge.calls == 0
    assert verdict.model_calls == 0


def test_gate_asks_the_model_only_when_neither_rule_can_decide() -> None:
    """「你好」不会走到这里——它被免费的寒暄规则拦下了。这条得挑一句规则定不了的。"""

    judge = CountingJudge(TurnRelevance.CHITCHAT)
    gate = RelevanceGate(judge=judge)
    verdict = gate.evaluate(
        "今天天气不错啊", [equality("Derivative(x**3, x)", "3*x**2")]
    )
    assert judge.calls == 1
    assert verdict.relevance is TurnRelevance.CHITCHAT
    assert not verdict.may_enter_knowledge_base
    assert verdict.model_calls == 1


def test_an_obvious_greeting_never_costs_a_model_call() -> None:
    judge = CountingJudge(TurnRelevance.CHITCHAT)
    RelevanceGate(judge=judge).evaluate("你好", [equality("x**2", "x*x")])
    assert judge.calls == 0


def test_gate_lets_the_model_rescue_a_follow_up_question() -> None:
    """用户拍板的那一条：承接上文的追问要能进知识库。"""

    gate = RelevanceGate(judge=CountingJudge(TurnRelevance.SOLVING))
    verdict = gate.evaluate(
        "这个怎么证明？",
        [equality("(a+b)**2", "a**2+2*a*b+b**2")],
        recent_questions=["证明 (a+b)^2 = a^2+2ab+b^2"],
    )
    assert verdict.may_enter_knowledge_base


def test_the_obvious_chitchat_rule_works_without_any_model() -> None:
    """用户实际撞上的那一句。没配判定模型的用户也该挡得住。"""

    verdict = RelevanceGate().evaluate(
        "你好", [equality("Derivative(x**3, x)", "3*x**2")]
    )
    assert verdict.relevance is TurnRelevance.CHITCHAT
    assert not verdict.may_enter_knowledge_base
    assert verdict.model_calls == 0


@pytest.mark.parametrize(
    "question", ["你好！", "  谢谢  ", "Hello", "你能干什么？", "哈哈哈"]
)
def test_more_obvious_chitchat(question: str) -> None:
    gate = RelevanceGate()
    assert not gate.evaluate(
        question, [equality("x**2", "x*x")]
    ).may_enter_knowledge_base


@pytest.mark.parametrize(
    "question",
    [
        # 差一个字就不匹配——寒暄开头的正经提问必须放过去。
        "你好，帮我求 x^3 的导数",
        "请解这道题",
        "这个怎么证明？",
        "换个方法呢",
    ],
)
def test_the_chitchat_rule_never_touches_a_real_request(question: str) -> None:
    """误拦一条解题比误收一条闲聊糟得多：知识库会**悄悄**停止生长。"""

    gate = RelevanceGate()
    assert gate.evaluate(question, [equality("x**2", "x*x")]).may_enter_knowledge_base


def test_a_turn_the_gate_cannot_judge_is_let_through() -> None:
    """判不出来放行。拦住的那条错误在界面上看不见，放过的那条用户一眼就看见。"""

    verdict = RelevanceGate().evaluate("接着上面那个", [equality("x**2", "x*x")])
    assert verdict.relevance is TurnRelevance.UNKNOWN
    assert verdict.may_enter_knowledge_base
    assert verdict.model_calls == 0


def test_a_broken_judge_degrades_to_unknown() -> None:
    verdict = RelevanceGate(judge=ExplodingJudge()).evaluate(
        "接着上面那个", [equality("x**2", "x*x")]
    )
    assert verdict.relevance is TurnRelevance.UNKNOWN
    assert "RuntimeError" in verdict.reason


def test_only_an_explicit_chitchat_verdict_blocks() -> None:
    unknown = RelevanceGate(judge=CountingJudge(TurnRelevance.UNKNOWN))
    chitchat = RelevanceGate(judge=CountingJudge(TurnRelevance.CHITCHAT))
    claims = [equality("x**2", "x*x")]
    assert unknown.evaluate("接着上面那个", claims).may_enter_knowledge_base
    assert not chitchat.evaluate("接着上面那个", claims).may_enter_knowledge_base


# --- 判定模型 ---------------------------------------------------------------


def test_the_prompt_never_asks_about_correctness_or_shows_the_answer() -> None:
    """判定模型只读用户说的话。

    给它看回答等于请它复现当前这个 bug——「回答里有数学，所以算解题」正是成因。
    """

    banned = ["回答", "答案", "解答", "对不对", "正确", "验证"]
    for word in banned:
        assert word not in RELEVANCE_SYSTEM_PROMPT, word


def test_the_judge_is_never_handed_the_answer_text() -> None:
    captured: dict[str, object] = {}

    class FakeClient:
        class responses:
            @staticmethod
            def create(**kwargs: object) -> object:
                captured.update(kwargs)

                class Response:
                    output_text = "solving"

                return Response()

    judge = OpenAIRelevanceJudge(model="m", client=FakeClient())
    answer = "由幂函数求导公式：Derivative(x**3, x) = 3*x**2"
    assert judge.judge("你好", ["求 x^3 的导数"]) is TurnRelevance.SOLVING
    material = str(captured["input"])
    assert "你好" in material
    assert answer not in material
    assert "3*x**2" not in material


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("solving", TurnRelevance.SOLVING),
        ("chitchat", TurnRelevance.CHITCHAT),
        ("  SOLVING\n", TurnRelevance.SOLVING),
        ("solving.", TurnRelevance.SOLVING),
        # 没照要求回答时不去猜——猜错的那一半正好落在误收上。
        ("我觉得这是在解题", TurnRelevance.UNKNOWN),
        ("", TurnRelevance.UNKNOWN),
        ("yes", TurnRelevance.UNKNOWN),
    ],
)
def test_verdict_parsing_refuses_to_guess(output: str, expected: TurnRelevance) -> None:
    assert _parse_verdict(output) is expected


# --- 端到端 -----------------------------------------------------------------


class FixedResponder:
    name = "fixed-chat"
    model = "chat-test"
    prompt_version = "fixed-chat-v1"

    def __init__(self, answer: str) -> None:
        self.answer = answer

    def respond(self, *args, **kwargs):
        del args, kwargs
        return ChatGeneration(content=self.answer, provider=self.name, model=self.model)


def turn(tmp_path, question: str, answer: str, **service_kwargs):
    service = MathHarnessService(
        tmp_path, conversation_responder=FixedResponder(answer), **service_kwargs
    )
    workspace = service.create_workspace(WorkspaceCreate(name="相关性"))
    conversation = service.create_conversation(
        workspace.id, ConversationCreate(title="对话")
    )
    result = service.send_conversation_turn(
        workspace.id, conversation.id, ConversationTurnRequest(message=question)
    )
    return service, workspace, result


#: 用户实际收到的那条回答：一句问候，顺口带上了上一题的正确结论。
GREETING_REPLY = "你好！很高兴见到你。\n之前我们求了 diff(x**3, x) = 3*x**2"


def test_a_greeting_gets_no_badge_and_no_draft(tmp_path):
    """用户报的那个场景，原样。

    以前：绿色「符号验证」徽章 + 待复核草稿，题面是 `你好`。
    """

    _, _, result = turn(tmp_path, "你好", GREETING_REPLY)
    message = result.assistant_message

    assert message.conclusion_confidence is None
    assert message.process_confidence is None
    assert message.checked_claims == []
    assert result.knowledge_draft is None


def test_a_greeting_leaves_the_knowledge_base_empty(tmp_path):
    service, workspace, _ = turn(tmp_path, "你好", GREETING_REPLY)
    assert service.list_examples(workspace.id) == []


def test_the_skip_is_recorded_as_a_learning_event(tmp_path):
    """跳过必须留痕：度量要靠它，用户日后也要能查「为什么这轮没入库」。"""

    service, workspace, _ = turn(tmp_path, "你好", GREETING_REPLY)
    store = service.workspaces.store(workspace.id)
    events = [
        event
        for event in store.list_learning_events()
        if event.event_type == "turn_relevance_skipped"
    ]
    assert len(events) == 1
    assert events[0].payload["relevance"] == "chitchat"
    assert events[0].payload["source"] == "rules"
    assert events[0].payload["model_calls"] == 0


def test_a_real_question_still_gets_checked_and_captured(tmp_path):
    """闸门不能靠什么都不做来显得完美：正常提问必须原样走完。"""

    _, _, result = turn(
        tmp_path,
        "求 x**3 的导数",
        "diff(x**3, x) = 3*x**2",
    )
    assert result.assistant_message.conclusion_confidence is not None
    assert result.knowledge_draft is not None


def test_an_old_chitchat_draft_is_flagged_not_deleted(tmp_path):
    """v0.21 之前收进来的脏数据。

    **打标，不删。** 判据刚改过，拿新闸门去回溯删旧数据，等于把一次没验证过的判断
    直接作用在已有内容上。
    """

    service = MathHarnessService(tmp_path)
    workspace = service.create_workspace(WorkspaceCreate(name="旧数据"))
    stale = service.ingest_example(
        workspace.id,
        ExampleCreate(problem="你好", solution="diff(x**3, x) = 3*x**2", tags=[]),
    ).example
    real = service.ingest_example(
        workspace.id,
        ExampleCreate(
            problem="求 x**3 的导数", solution="diff(x**3, x) = 3*x**2", tags=[]
        ),
    ).example

    assert stale.looks_irrelevant
    assert not real.looks_irrelevant
    # 还在库里——标只是提醒。
    assert {item.id for item in service.list_examples(workspace.id)} == {
        stale.id,
        real.id,
    }
