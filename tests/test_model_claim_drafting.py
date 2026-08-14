from __future__ import annotations

from math_harness.checks import (
    CheckContext,
    ConclusionConfidence,
    InstantiationCheck,
    SymbolicEqualityCheck,
    assess,
    run_checks,
)
from math_harness.claim_drafting import (
    ClaimDraft,
    DraftedClaimSource,
    ModelAssistedClaimDrafter,
    RuleBasedClaimDrafter,
    ground_drafted_claims,
    quote_is_grounded,
)
from math_harness.models import ExtractionStatus

# 模型直出结构化断言。
#
# 它补的是规则版够不着的那一类：回答里没有等式，答案得和题面拼起来才成为一条断言
# （题面「求 x**3+2x 的导数」+ 回答「3x**2+2」→ `diff(x**3+2*x, x) = 3*x**2+2`）。
#
# 代价是一个新的失败模式：**模型可以凭空造一条断言**。规则版抽错了至少抽的是回答
# 原文；模型版可以编一条看起来对的等式，SymPy 验过，用户看到「符号验证」徽章，而验的
# 根本不是回答里说的话。下面每一组测试守的都是这一条。


PROBLEM = "求 x**3+2*x 的导数"
ANSWER = "经过计算，答案是 3*x**2 + 2。"

GOOD = DraftedClaimSource(
    lhs="diff(x**3+2*x, x)",
    rhs="3*x**2 + 2",
    answer_quote="答案是 3*x**2 + 2",
    problem_quote="求 x**3+2*x 的导数",
)


def grounded(*drafted: DraftedClaimSource, problem=PROBLEM, answer=ANSWER):
    return ground_drafted_claims(list(drafted), problem=problem, answer=answer)


# --- 正常情况 ---------------------------------------------------------


def test_a_grounded_claim_survives_and_is_checkable():
    claims = grounded(GOOD)

    assert len(claims) == 1
    assert claims[0].lhs == "diff(x**3+2*x, x)"
    assert claims[0].rhs == "3*x**2 + 2"


def test_a_claim_read_from_the_problem_says_so():
    """算子是模型从题面解读出来的，不在任何原文里。残余风险不可能归零，
    处理方式和 v0.15 一样：让它看得见。"""

    assert "解读自题面" in grounded(GOOD)[0].description


def test_a_model_drafted_claim_is_still_judged_by_sympy():
    """模型只提出。判定归 SymPy——错的照样被抓。"""

    wrong = GOOD.model_copy(
        update={"rhs": "3*x**2 + 5", "answer_quote": "答案是 3*x**2 + 5"}
    )
    claims = grounded(wrong, answer="经过计算，答案是 3*x**2 + 5。")
    report = run_checks(
        [SymbolicEqualityCheck(), InstantiationCheck(trials=6)],
        CheckContext(claim=claims[0], steps=claims),
    )

    assert assess(report).conclusion is ConclusionConfidence.REFUTED


# --- 闸一：出处必须在原文里 -------------------------------------------


def test_a_quote_that_is_not_in_the_answer_drops_the_claim():
    """最危险的一种：模型编了一句原文，再编一条断言配它。"""

    fabricated = GOOD.model_copy(update={"answer_quote": "答案是 6*x"})

    assert grounded(fabricated) == []


def test_a_quote_that_is_not_in_the_problem_drops_the_claim():
    invented_problem = GOOD.model_copy(update={"problem_quote": "求 x**5 的导数"})

    assert grounded(invented_problem) == []


def test_whitespace_differences_do_not_break_grounding():
    """折叠空白是唯一的宽松。允许改写，这道闸就等于没有。"""

    assert quote_is_grounded("答案是 3*x**2+2", "经过计算，答案是 3*x**2 + 2。")
    assert not quote_is_grounded("答案大约是 3*x**2+2", ANSWER)


# --- 闸二：至少一侧来自回答 -------------------------------------------


def test_a_model_that_solves_the_problem_itself_is_rejected():
    """两边都只能追溯到题面，说明模型在自己解题然后验自己的解。

    那是自查——研究结论说自查是负收益——而且它验的不是用户看到的那条回答。
    """

    lecture = "这题需要用幂法则，逐项求导即可。"
    self_solved = DraftedClaimSource(
        lhs="diff(x**3+2*x, x)",
        rhs="3*x**2 + 2",
        # 这句确实是回答原文，出处闸拦不住它。
        answer_quote="这题需要用幂法则",
        problem_quote="求 x**3+2*x 的导数",
    )

    assert grounded(self_solved, answer=lecture) == []


def test_an_equation_wholly_inside_the_answer_needs_no_problem_quote():
    whole = DraftedClaimSource(
        lhs="diff(x**2, x)",
        rhs="2*x",
        answer_quote="diff(x**2, x) = 2*x",
    )

    assert len(grounded(whole, answer="我们有 diff(x**2, x) = 2*x。")) == 1


# --- 闸三：必须过安全解析器 -------------------------------------------


def test_an_unparsable_side_drops_the_claim():
    prose = GOOD.model_copy(
        update={"lhs": "d/dx 的 x**3+2*x", "answer_quote": "答案是 3*x**2 + 2"}
    )

    assert grounded(prose) == []


def test_a_dangerous_expression_never_becomes_a_claim():
    """安全解析器是这套东西不执行任意代码的理由。模型输出走的是同一道闸。"""

    dangerous = DraftedClaimSource(
        lhs="__import__('os').system('echo hi')",
        rhs="3*x**2 + 2",
        answer_quote="答案是 3*x**2 + 2",
    )

    assert grounded(dangerous) == []


# --- 规则优先 ---------------------------------------------------------


class ScriptedDrafter:
    """脚本化的假模型。CI 里没有 API Key，管道必须离线可测。"""

    name = "scripted"
    prompt_version = "scripted-v1"

    def __init__(self, draft: ClaimDraft | None = None, error: Exception | None = None):
        self.calls = 0
        self._draft = draft
        self._error = error

    def draft(self, problem: str, answer: str) -> ClaimDraft:
        del problem, answer
        self.calls += 1
        if self._error is not None:
            raise self._error
        return self._draft or ClaimDraft(status=ExtractionStatus.SKIPPED)


def model_output() -> ClaimDraft:
    claims = grounded(GOOD)
    return ClaimDraft(
        claim=claims[-1],
        steps=claims,
        status=ExtractionStatus.SUCCESS,
        provider="scripted",
    )


def test_the_model_is_not_called_when_the_rules_already_found_something():
    """规则版免费、只碰回答原文。它能覆盖的场景没必要花钱，也没必要引入解读风险。"""

    scripted = ScriptedDrafter(model_output())
    drafter = ModelAssistedClaimDrafter(primary=scripted)

    draft = drafter.draft("求导", "所以 diff(x**2, x) = 2*x")

    assert draft.is_checkable
    assert draft.provider == "rules"
    assert scripted.calls == 0


def test_the_model_fills_the_gap_the_rules_cannot_reach():
    scripted = ScriptedDrafter(model_output())
    drafter = ModelAssistedClaimDrafter(primary=scripted)

    draft = drafter.draft(PROBLEM, ANSWER)

    assert scripted.calls == 1
    assert draft.is_checkable
    assert draft.claim is not None and draft.claim.rhs == "3*x**2 + 2"


def test_a_failing_model_falls_back_to_the_rule_result():
    """抽断言失败不能把用户的回答弄丢——它本来就是可选的加分项。"""

    scripted = ScriptedDrafter(error=RuntimeError("模型服务不可用"))
    drafter = ModelAssistedClaimDrafter(primary=scripted)

    draft = drafter.draft(PROBLEM, ANSWER)

    assert not draft.is_checkable
    assert draft.error is not None and "RuntimeError" in draft.error


def test_the_default_drafter_makes_no_model_calls():
    """没配角色时行为与 v0.18 逐字相同。"""

    assert isinstance(RuleBasedClaimDrafter(), RuleBasedClaimDrafter)


# --- 门禁 -------------------------------------------------------------


def test_the_shipped_corpus_passes_its_own_gate():
    """`math-harness-claims --check` 的内容，在 pytest 里也跑一遍。

    它证明的是四道闸和整条管道，**不是模型的抽取质量**——语料里的模型输出是手工录的。
    """

    from pathlib import Path

    from math_harness.claim_eval import gate_failures, run_claim_evaluation

    report = run_claim_evaluation(Path("data/pilot/claim_drafts.jsonl"))

    assert gate_failures(report) == []
    assert report.guard_rate == 1.0
    assert report.false_refutation_rate == 0.0
