from __future__ import annotations

from datetime import UTC, datetime

from math_harness.models import (
    ConclusionConfidence,
    ConversationCreate,
    ConversationTurnRequest,
    KnowledgeStatus,
    MethodCard,
    WorkspaceCreate,
)
from math_harness.retrieval import MethodRetriever
from math_harness.service import MathHarnessService

# 检索按来源可信度加权。
#
# 用户明确要过：未验证的结果也进知识库，标注清楚可信度即可。那它们就会被检索到——
# 但不该盖过验证过的内容。「越用越强」的前提是强的那部分排在前面。


def card(key: str, confidence: ConclusionConfidence | None) -> MethodCard:
    now = datetime.now(UTC)
    return MethodCard(
        id=key,
        workspace_id="ws",
        key=key,
        name=f"方法{key}",
        goal="求两个含变量因子之积的导数。",
        applicable_when=["被求导对象是两个因子的乘积"],
        procedure=["分别求导", "按 u'v + uv' 组合"],
        failure_modes=["因子之一不含变量时是绕远路"],
        tags=["微积分", "求导"],
        status=KnowledgeStatus.PROMOTED,
        conclusion_confidence=confidence,
        created_at=now,
        updated_at=now,
    )


def ranked(cards: list[MethodCard], query: str) -> list[str]:
    return [
        match.method.key
        for match in MethodRetriever().search(cards, query, top_k=len(cards))
    ]


# --- 排序 -------------------------------------------------------------


def test_a_weaker_source_never_outranks_a_verified_one_on_equal_content():
    """两张卡内容一模一样，只有来源可信度不同。"""

    order = ranked(
        [
            card("weak", ConclusionConfidence.PEER_REVIEWED),
            card("strong", ConclusionConfidence.VERIFIED),
        ],
        "求两个因子乘积的导数",
    )

    assert order[0] == "strong"


def test_the_ordering_follows_the_whole_ladder():
    order = ranked(
        [
            card("unchecked", ConclusionConfidence.UNCHECKED),
            card("peer", ConclusionConfidence.PEER_REVIEWED),
            card("numeric", ConclusionConfidence.NUMERICALLY_CHECKED),
            card("verified", ConclusionConfidence.VERIFIED),
            card("cross", ConclusionConfidence.CROSS_CHECKED),
        ],
        "求两个因子乘积的导数",
    )

    assert order == ["verified", "numeric", "cross", "peer", "unchecked"]


def test_a_legacy_card_is_treated_as_verified():
    """v0.16 之前建的卡没有这个字段。它们走的是「验证通过 + 人工复核」那条路，
    折算成 verified 名副其实——既有排序因此一位不动。"""

    order = ranked(
        [
            card("legacy", None),
            card("numeric", ConclusionConfidence.NUMERICALLY_CHECKED),
        ],
        "求两个因子乘积的导数",
    )

    assert order[0] == "legacy"


def test_weighting_does_not_reorder_cards_of_equal_confidence():
    """同档知识库里加权是恒等变换——两个评测的分数因此一位不变。"""

    cards = [
        card("a", ConclusionConfidence.VERIFIED),
        card("b", ConclusionConfidence.VERIFIED),
    ]

    assert ranked(cards, "求两个因子乘积的导数") == ranked(
        [c.model_copy(update={"conclusion_confidence": None}) for c in cards],
        "求两个因子乘积的导数",
    )


# --- 落库 -------------------------------------------------------------


class FixedResponder:
    name = "fixed"
    model = "m"
    prompt_version = "v1"

    def __init__(self, answer: str) -> None:
        self.answer = answer

    def respond(self, *args, **kwargs):
        from math_harness.conversation import ChatGeneration

        del args, kwargs
        return ChatGeneration(content=self.answer, provider=self.name, model=self.model)


def test_a_promoted_card_records_the_confidence_of_its_source(tmp_path):
    from math_harness.models import ExampleReviewDecision, ExampleReviewRequest

    service = MathHarnessService(
        tmp_path,
        conversation_responder=FixedResponder(
            "先做共轭有理化：\n所以 diff(x**2, x) = 2*x"
        ),
    )
    workspace = service.create_workspace(WorkspaceCreate(name="可信度落库"))
    conversation = service.create_conversation(
        workspace.id, ConversationCreate(title="对话")
    )
    draft = service.send_conversation_turn(
        workspace.id, conversation.id, ConversationTurnRequest(message="求导数")
    ).knowledge_draft
    service.review_example(
        workspace.id,
        draft.id,
        ExampleReviewRequest(
            decision=ExampleReviewDecision.APPROVE, expected_revision=draft.revision
        ),
    )

    methods = service.search_methods(workspace.id, "有理化")

    assert methods
    assert methods[0].method.conclusion_confidence is ConclusionConfidence.VERIFIED


def test_confidence_survives_a_reload(tmp_path):
    from math_harness.models import ExampleReviewDecision, ExampleReviewRequest

    service = MathHarnessService(
        tmp_path,
        conversation_responder=FixedResponder(
            "先做共轭有理化：\n所以 diff(x**2, x) = 2*x"
        ),
    )
    workspace = service.create_workspace(WorkspaceCreate(name="持久化"))
    conversation = service.create_conversation(
        workspace.id, ConversationCreate(title="对话")
    )
    draft = service.send_conversation_turn(
        workspace.id, conversation.id, ConversationTurnRequest(message="求导数")
    ).knowledge_draft
    service.review_example(
        workspace.id,
        draft.id,
        ExampleReviewRequest(
            decision=ExampleReviewDecision.APPROVE, expected_revision=draft.revision
        ),
    )

    reloaded = MathHarnessService(tmp_path)
    methods = reloaded.search_methods(workspace.id, "有理化")

    assert methods[0].method.conclusion_confidence is ConclusionConfidence.VERIFIED
