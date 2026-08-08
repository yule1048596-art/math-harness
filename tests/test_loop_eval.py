from __future__ import annotations

from pathlib import Path

import pytest

from math_harness.loop_eval import (
    Lesson,
    LoopQuery,
    LoopReport,
    _load_jsonl,
    gate_failures,
    validate_corpus,
)

LESSONS = Path("data/pilot/loop_lessons.jsonl")
QUERIES = Path("data/pilot/loop_queries.jsonl")

# 端到端闭环度量的守卫。
#
# 这条度量存在的理由是：v0.15 到 v0.16 每一版的度量都只盯着那一版在改的东西，
# 于是同一类缺陷活过了三个版本。它一环都不绕，所以它自己的语料和判据必须站得住。


@pytest.fixture(scope="module")
def lessons() -> list[Lesson]:
    return _load_jsonl(LESSONS, Lesson)


@pytest.fixture(scope="module")
def queries() -> list[LoopQuery]:
    return _load_jsonl(QUERIES, LoopQuery)


def test_the_corpus_passes_its_own_guard(lessons, queries):
    validate_corpus(lessons, queries)


def test_every_query_recalls_a_lesson_that_is_taught(lessons, queries):
    """唤起一课没教过的东西，那道题**不可能**命中——指标会掉，但掉的原因是语料残缺。"""

    taught = {lesson.id for lesson in lessons}

    assert {query.recalls for query in queries} <= taught


def test_no_query_repeats_a_taught_problem(lessons, queries):
    """提问就是原题的话，量的是记忆而不是泛化。"""

    problems = {lesson.problem.strip() for lesson in lessons}

    assert [q.id for q in queries if q.problem.strip() in problems] == []


def test_the_guard_rejects_a_query_that_repeats_a_lesson(lessons, queries):
    """拒不动的守卫不是守卫。"""

    leaky = [
        *queries,
        LoopQuery(id="leak", problem=lessons[0].problem, recalls=lessons[0].id),
    ]

    with pytest.raises(ValueError, match="repeats a taught problem"):
        validate_corpus(lessons, leaky)


def test_the_guard_rejects_an_unteachable_query(lessons, queries):
    orphan = [*queries, LoopQuery(id="orphan", problem="新题", recalls="从没教过")]

    with pytest.raises(ValueError, match="not taught"):
        validate_corpus(lessons, orphan)


def test_the_corpus_covers_the_named_domains(lessons):
    domains = {lesson.domain for lesson in lessons}

    for domain in ("微积分", "组合数学", "线性代数", "代数", "复变函数", "数论"):
        assert domain in domains, f"语料里没有{domain}"


def test_lesson_ids_are_unique(lessons):
    ids = [lesson.id for lesson in lessons]

    assert len(ids) == len(set(ids))


# --- 门禁本身 ---------------------------------------------------------


def test_the_gate_rejects_an_empty_knowledge_base():
    """v0.17 之前的实测：18 课全部入库晋级，方法卡 1 张，命中率 0.056。

    不是检索差，是离线提炼器只有 7 个渐进模板，跨领域一张卡都产不出来。
    """

    report = LoopReport(
        lesson_count=18, query_count=18, captured=17, promoted=17, recalled=1
    )

    assert gate_failures(report)


def test_the_gate_rejects_knowledge_that_never_enters_the_base():
    report = LoopReport(
        lesson_count=18, query_count=18, captured=2, promoted=2, recalled=18
    )

    assert gate_failures(report)


def test_the_gate_passes_a_working_loop():
    report = LoopReport(
        lesson_count=18, query_count=18, captured=17, promoted=17, recalled=14
    )

    assert gate_failures(report) == []


# --- 碎片化 -----------------------------------------------------------
#
# 方法键从 v0.17 起是生成式的，不再是封闭集合。计划里我把「把知识库切碎」标成了
# 这一版的最高风险：碎掉之后每张卡的样本都太少，签名和反馈统计都失去意义。
#
# 实测没有碎——18 课得到 11 张，曲线先涨后平，有七课复用了已有的卡。原因是结构性的：
# 变换词表有界于解析器的函数白名单，所以键会收敛而不是增殖。这一组守住这个结论。


def test_the_gate_rejects_a_fragmenting_knowledge_base():
    """每课都造一张新卡，就是碎了。"""

    report = LoopReport(
        lesson_count=18,
        query_count=18,
        captured=18,
        promoted=18,
        recalled=18,
        method_cards=18,
    )

    assert gate_failures(report)


def test_the_gate_accepts_the_measured_growth():
    """实测 18 课 11 张，比值 0.611。"""

    report = LoopReport(
        lesson_count=18,
        query_count=18,
        captured=17,
        promoted=17,
        recalled=15,
        method_cards=11,
    )

    assert gate_failures(report) == []


def test_structural_keys_converge_rather_than_proliferate():
    """同一个技法的不同形状要落到同一个键上——这正是它不碎的原因。"""

    from math_harness.claim_drafting import RuleBasedClaimDrafter
    from math_harness.method_identity import derive_method_key

    drafter = RuleBasedClaimDrafter()
    same_technique = [
        "所以 diff(x^2*sin(x), x) = 2x*sin(x) + x^2*cos(x)",
        "所以 diff(x^3*cos(x), x) = 3x^2*cos(x) - x^3*sin(x)",
        "所以 diff(sin(x^2), x) = 2x*cos(x^2)",
    ]

    keys = {
        derive_method_key(drafter.draft("", answer).steps) for answer in same_technique
    }

    assert keys == {"differentiate"}


def test_different_techniques_get_different_keys():
    """收敛不能收敛到一起去——那就退回通用兜底卡了。"""

    from math_harness.claim_drafting import RuleBasedClaimDrafter
    from math_harness.method_identity import derive_method_key

    drafter = RuleBasedClaimDrafter()
    distinct = [
        "所以 diff(x^2*sin(x), x) = 2x*sin(x) + x^2*cos(x)",
        "Sum(binomial(n,k),(k,0,n)) = 2**n",
        "det(Matrix([[2,1],[1,2]]) - 3*eye(2)) = 0",
        "exp(I*x) = cos(x) + I*sin(x)",
        "z*conjugate(z) = re(z)**2 + im(z)**2",
    ]

    keys = [derive_method_key(drafter.draft("", answer).steps) for answer in distinct]

    assert len(set(keys)) == len(keys), keys


def test_a_card_accumulates_evidence_across_lessons(tmp_path):
    """同一个键被教到第二次时要**并进同一张卡**，而不是新建一张。

    这是签名和反馈统计有意义的前提：一张只有一个样本的卡，结构签名就是那一道题。
    """

    from math_harness.loop_eval import ScriptedResponder
    from math_harness.models import (
        ConversationCreate,
        ConversationTurnRequest,
        ExampleReviewDecision,
        ExampleReviewRequest,
        WorkspaceCreate,
    )
    from math_harness.service import MathHarnessService

    answers = {
        "求 x^2*sin(x) 的导数。": "所以 diff(x^2*sin(x), x) = 2x*sin(x) + x^2*cos(x)",
        "求 x^3*cos(x) 的导数。": "所以 diff(x^3*cos(x), x) = 3x^2*cos(x) - x^3*sin(x)",
    }
    service = MathHarnessService(
        tmp_path, conversation_responder=ScriptedResponder(answers)
    )
    workspace = service.create_workspace(WorkspaceCreate(name="合并"))
    conversation = service.create_conversation(
        workspace.id, ConversationCreate(title="教学")
    )
    for problem in answers:
        draft = service.send_conversation_turn(
            workspace.id, conversation.id, ConversationTurnRequest(message=problem)
        ).knowledge_draft
        service.review_example(
            workspace.id,
            draft.id,
            ExampleReviewRequest(
                decision=ExampleReviewDecision.APPROVE,
                expected_revision=draft.revision,
            ),
        )

    methods = service.list_methods(workspace.id)

    assert len(methods) == 1
    assert len(methods[0].example_ids) == 2
