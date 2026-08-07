from __future__ import annotations

import pytest

from math_harness.checks import (
    CheckContext,
    ConclusionConfidence,
    InstantiationCheck,
    ProcessConfidence,
    SamplingDomain,
    StepInstantiationCheck,
    SymbolicEqualityCheck,
    assess,
    run_checks,
)
from math_harness.claim_drafting import (
    ClaimDraft,
    RuleBasedClaimDrafter,
    extract_claims,
    normalize_math_text,
)
from math_harness.models import ExtractionStatus

DRAFTER = RuleBasedClaimDrafter()


def graded(answer: str):
    draft = DRAFTER.draft("", answer)
    report = run_checks(
        [
            SymbolicEqualityCheck(),
            InstantiationCheck(trials=6),
            StepInstantiationCheck(trials=6),
        ],
        CheckContext(claim=draft.claim, steps=draft.steps),
    )
    return draft, assess(report)


# --- 安全性（最重要的一组）--------------------------------------------
#
# 抽断言吃的是模型输出，是整条链路上最不可信的输入。它唯一的准入条件是**能过安全
# 解析器**，绝不为了多抽几条而放宽。


@pytest.mark.parametrize(
    "hostile",
    [
        "x = __import__('os')",
        "y = open('/etc/passwd')",
        "z = eval('1+1')",
        "a = x.__class__.__mro__",
        "b = (lambda: 1)()",
        "c = [i for i in range(3)]",
        "d = globals()",
        "e = integrate(exp(-x**2), x)",
        "f = eye(50)",
        "g = Sum(1/k**2, (k, 1, 10000000))",
    ],
)
def test_hostile_output_never_becomes_a_claim(hostile: str):
    assert extract_claims(hostile) == []


def test_a_hostile_line_does_not_poison_the_safe_ones():
    """一行有问题不该把整段作废，也不该让那一行混进来。"""

    claims = extract_claims("diff(x**3, x) = 3*x**2\ny = __import__('os')")

    assert len(claims) == 1
    assert claims[0].lhs == "diff(x**3, x)"


# --- 从中文散文里抽 ---------------------------------------------------


def test_the_conclusion_line_behind_chinese_prose_is_extracted():
    """「所以 f = g」这种写法极常见。剥不掉前缀的话，最重要的结论行恰恰抽不出来。"""

    claims = extract_claims("所以 diff(x**3 + 2*x, x) = 3*x**2 + 2")

    assert len(claims) == 1
    assert claims[0].lhs == "diff(x**3 + 2*x, x)"
    assert claims[0].rhs == "3*x**2 + 2"


def test_numbering_and_bullets_are_stripped():
    claims = extract_claims("1. diff(x**3, x) = 3*x**2\n- diff(2*x, x) = 2")

    assert len(claims) == 2


def test_trailing_explanation_is_dropped():
    claims = extract_claims("diff(x**3, x) = 3*x**2，这是幂法则。")

    assert claims[0].rhs == "3*x**2"


def test_prose_only_answer_yields_nothing_and_is_not_an_error():
    """抽不出来不是失败，只是这条回答没有可机检的部分。它照样能回答、照样能入库。"""

    draft = DRAFTER.draft("", "这道题要用洛必达法则，先对分子分母分别求导。")

    assert draft.claim is None
    assert not draft.is_checkable
    assert draft.status is ExtractionStatus.SKIPPED


def test_a_chained_equality_is_skipped():
    """`a = b = c` 的意图不明确，猜错还不如不抽。"""

    assert extract_claims("x + 1 = x + 1 = x + 1") == []


# --- 书写形式的收敛 ---------------------------------------------------


def test_caret_becomes_power_not_xor():
    """`^` 在数学写作里是幂，在 Python 里是异或。留着它会静默地解析成另一个式子。"""

    assert normalize_math_text("x^2") == "x**2"

    claims = extract_claims("(a+b)^2 = a^2 + 2*a*b + b^2")

    assert claims[0].lhs == "(a+b)**2"


def test_latex_wrappers_are_stripped():
    claims = extract_claims(r"\(diff(x**2, x) = 2*x\)")

    assert len(claims) == 1


# --- 定义不是断言 -----------------------------------------------------
#
# 「设 m = (a+b)/2」没有可验证的内容。当成等式来查会独立采样 m，必然给出反例——
# 一条引入记号的正常解答就被误拒了，而误拒比漏抓更糟。


def test_a_definition_is_substituted_not_checked():
    draft, assessment = graded("设中点 m = (a+b)/2\n则 Abs(m - a) - Abs(m - b) = 0")

    assert len(draft.steps) == 1
    assert assessment.conclusion is not ConclusionConfidence.REFUTED
    assert assessment.process is ProcessConfidence.STEP_CHECKED


def test_a_definition_chain_resolves():
    draft, assessment = graded("令 u = x + 1\n再令 v = u**2\n于是 v - x**2 - 2*x = 1")

    assert "u" not in (draft.claim.lhs + draft.claim.rhs)
    assert assessment.conclusion is ConclusionConfidence.VERIFIED


def test_substitution_happens_symbolically_not_textually():
    """文本替换会踩优先级：`m = a+b` 之后把 `2*m` 换成 `2*a+b` 就悄悄算错了。"""

    _, assessment = graded("设 m = a + b\n那么 2*m = 2*a + 2*b")

    assert assessment.conclusion is ConclusionConfidence.VERIFIED


# --- 取值域的启发式 ---------------------------------------------------


def test_conventional_integer_names_are_sampled_as_integers():
    """n/k/m 在数学写作里惯例是自然数。当实数采样会让组合恒等式假失败。"""

    claims = extract_claims("Sum(binomial(n,k),(k,0,n)) = 2**n")

    domains = {binding.symbol: binding.domain for binding in claims[0].bindings}
    assert domains["n"] is SamplingDomain.POSITIVE_INTEGER


def test_conventional_complex_names_are_sampled_as_complex():
    claims = extract_claims("z*conjugate(z) = re(z)**2 + im(z)**2")

    domains = {binding.symbol: binding.domain for binding in claims[0].bindings}
    assert domains["z"] is SamplingDomain.COMPLEX


def test_a_combinatorial_identity_survives_the_heuristic():
    _, assessment = graded("Sum(binomial(n,k),(k,0,n)) = 2**n")

    assert assessment.conclusion is not ConclusionConfidence.REFUTED


# --- 端到端：这就是「一个输入框」要的行为 ------------------------------


def test_a_plain_chinese_answer_is_graded_without_any_model_call():
    """用户自然语言提问、模型正常作答，这一步自己判断出该查什么——不花一次模型调用。"""

    draft, assessment = graded(
        "我们逐项求导：\n"
        "1. diff(x**3, x) = 3*x**2\n"
        "2. diff(2*x, x) = 2\n"
        "所以 diff(x**3 + 2*x, x) = 3*x**2 + 2"
    )

    assert len(draft.steps) == 3
    assert assessment.conclusion is ConclusionConfidence.VERIFIED
    assert assessment.process is ProcessConfidence.STEP_CHECKED
    assert assessment.may_extract_methods


def test_a_correct_answer_with_a_broken_step_is_caught_from_raw_prose():
    """旗舰场景，全程从中文散文走通：结论对、第 2 步错、方法卡必须拦住。"""

    draft, assessment = graded(
        "展开两个平方：\n"
        "(a+b)^2 = a^2 + 2*a*b + b^2\n"
        "(a-b)^2 = a^2 - 2*a*b - b^2\n"  # 错：b² 符号
        "相减得 (a+b)^2 - (a-b)^2 = 4*a*b"
    )

    assert len(draft.steps) == 3
    assert assessment.conclusion is ConclusionConfidence.VERIFIED
    assert assessment.process is ProcessConfidence.STEP_FAILED
    assert not assessment.may_extract_methods
    assert set(assessment.counterexample) == {"a", "b"}


def test_a_wrong_answer_is_refuted_with_a_counterexample():
    _, assessment = graded("对幂函数求导得 diff(x**3, x) = 2*x**2")

    assert assessment.conclusion is ConclusionConfidence.REFUTED
    assert assessment.counterexample


def test_the_conclusion_is_the_last_equation_and_steps_cover_everything():
    """结论也留在步骤里：逐步检查漏掉最后一行，等于放过最容易出错的一步。"""

    draft = DRAFTER.draft(
        "", "diff(x**2, x) = 2*x\ndiff(x**3, x) = 3*x**2\ndiff(x**4, x) = 4*x**3"
    )

    assert draft.claim.lhs == "diff(x**4, x)"
    assert len(draft.steps) == 3
    assert draft.claim == draft.steps[-1]


def test_an_unrelated_definition_does_not_rewrite_other_claims():
    """抽出来的断言是要并排展示给用户看的，不该因为前文有个无关定义就变了样子。"""

    draft = DRAFTER.draft("", "设 c = 1 + 1\ndiff(x**2, x) = 2*x")

    assert draft.claim.lhs == "diff(x**2, x)"
    assert draft.claim.rhs == "2*x"


def test_the_draft_records_where_it_came_from():
    draft = DRAFTER.draft("", "diff(x**2, x) = 2*x")

    assert draft.provider == "rules"
    assert draft.status is ExtractionStatus.SUCCESS
    assert isinstance(draft, ClaimDraft)
