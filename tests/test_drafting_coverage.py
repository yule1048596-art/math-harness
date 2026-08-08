from __future__ import annotations

import pytest

from math_harness.checks import (
    CheckContext,
    ConclusionConfidence,
    InstantiationCheck,
    SymbolicEqualityCheck,
    assess,
    run_checks,
)
from math_harness.claim_drafting import (
    RuleBasedClaimDrafter,
    expand_latex,
    normalize_math_text,
    split_letter_runs,
)

DRAFTER = RuleBasedClaimDrafter()

# 抽断言的覆盖率与误拒。
#
# **误拒是硬验收，覆盖率不是。** 抽不出来只是这条回答不带可信度标注，照样正常显示；
# 抽出来却判错，是把一条正确的解答告诉用户「找到反例了」。多抽几条的代价绝不能是这个。


def graded(answer: str) -> ConclusionConfidence:
    draft = DRAFTER.draft("", answer)
    if not draft.is_checkable:
        return ConclusionConfidence.UNCHECKED
    report = run_checks(
        [SymbolicEqualityCheck(), InstantiationCheck(trials=6)],
        CheckContext(claim=draft.claim, steps=draft.steps),
    )
    return assess(report).conclusion


# --- 误拒（硬验收）---------------------------------------------------


@pytest.mark.parametrize(
    ("answer", "notation"),
    [
        ("所以 (a+b)^2 - (a-b)^2 = 4ab", "字母连写的隐式乘"),
        ("(x+y)^2 = x^2 + 2xy + y^2", "同上，三项式"),
        ("所以 (a+b)^2 - (a-b)^2 = 4*a*b", "显式乘号"),
        ("diff(x**3, x) = 3*x**2", "标准写法"),
        ("所以 diff(x^3+2x, x) = 3x^2 + 2", "数字后接字母的隐式乘"),
        (r"diff(log(x), x) = \frac{1}{x}", "LaTeX 分数"),
        ("sin(x)^2+cos(x)^2 ≡ 1", "恒等号"),
        (r"$$Sum(k,(k,1,n)) = n*(n+1)/2$$", "LaTeX 显示环境"),
        ("第一步：(a+b)^2 = a^2+2ab+b^2", "中文数字步骤"),
        ("结论：exp(I*x) = cos(x) + I*sin(x)", "冒号引出"),
        ("det(Matrix([[1,2],[3,4]])) = -2", "矩阵"),
    ],
)
def test_a_correct_answer_in_human_notation_is_never_refuted(
    answer: str, notation: str
):
    """`4ab` 这一条是实测出来的回归：我在补隐式乘法时只处理了数字后接字母，
    把相邻字母留成了一个标识符，于是 `4ab` 变成 `4*ab`，一条正确答案被判成
    `refuted`，反例里还带着一个根本不存在的符号 `ab`。"""

    assert graded(answer) is not ConclusionConfidence.REFUTED, notation


def test_a_wrong_answer_in_human_notation_is_still_caught():
    """放宽书写形式不能顺手把抓错的能力也放掉。"""

    assert graded("所以 (a+b)^2 - (a-b)^2 = 5ab") is ConclusionConfidence.REFUTED


# --- 字母连写的拆分 ---------------------------------------------------
#
# 拆错比不拆更糟：那等于换了一个命题去验，而且不会报错。


def test_letters_split_only_when_each_appears_standalone():
    assert split_letter_runs("(a+b)**2 = 4*ab") == "(a+b)**2 = 4*a*b"


def test_a_genuine_multi_letter_variable_is_left_alone():
    """没有单独出现过的字母，说明 `ab` 就是一个变量名。"""

    assert split_letter_runs("sin(x) = ab") == "sin(x) = ab"


def test_function_names_are_never_split():
    for text in ("sin(x)", "exp(x)", "log(x)", "det(m)"):
        assert split_letter_runs(text) == text


def test_names_with_digits_or_underscores_are_left_alone():
    """`x2`、`a_1` 是标识符，不是连写。"""

    assert split_letter_runs("x2 + a + b") == "x2 + a + b"


# --- 书写形式的收敛 ---------------------------------------------------


def test_latex_fractions_expand():
    assert "1)/(x" in expand_latex(r"\frac{1}{x}")


def test_latex_square_roots_expand():
    assert expand_latex(r"\sqrt{x+1}") == "sqrt(x+1)"


def test_the_identity_symbol_counts_as_an_equation():
    """`≡` 原先在判断有没有等号之前还没被换掉，整行被静默丢弃。"""

    assert DRAFTER.draft("", "sin(x)^2+cos(x)^2 ≡ 1").is_checkable


def test_chinese_numbered_steps_are_stripped():
    """模型写「第一步：」比写「第 1 步：」常见得多。"""

    assert DRAFTER.draft("", "第一步：(a+b)^2 = a^2+2ab+b^2").is_checkable


def test_scientific_notation_survives_implicit_multiplication():
    assert "1e5" in normalize_math_text("1e5 + x")


# --- 覆盖率 -----------------------------------------------------------
#
# 这个数字只值它的样本所值：20 条是手造的，构成是我对真实分布的猜测。它守的是
# 「不要悄悄退回去」，不是「已经够好」。


CORPUS = [
    "diff(x**3, x) = 3*x**2",
    "所以 diff(x**3+2*x, x) = 3*x**2 + 2",
    "所以 (a+b)^2 - (a-b)^2 = 4ab",
    "第一步：(a+b)^2 = a^2+2ab+b^2\n第二步：(a-b)^2 = a^2-2ab+b^2",
    "1. diff(x**2,x) = 2*x\n2. diff(x**3,x) = 3*x**2",
    "经过计算，答案是 3*x**2 + 2。",
    "化简后得到 4*a*b。",
    "这个极限等于二分之一。",
    r"我们有 \(diff(x**2, x) = 2*x\)。",
    r"$$Sum(k,(k,1,n)) = n*(n+1)/2$$",
    r"结果为 \frac{1}{2}",
    r"diff(log(x), x) = \frac{1}{x}",
    "因为三角形两边之和大于第三边，所以该不等式成立。",
    "假设存在有理数 p/q 使其平方为 2，则 p 必为偶数，矛盾。",
    "共有 10 种取法。",
    "det(Matrix([[1,2],[3,4]])) = -2",
    "所以导数为 2*x（对全体实数成立）。",
    "首先展开：\n(x+1)^2 = x^2+2x+1\n这一步用了完全平方公式。",
    "sin(x)^2+cos(x)^2 ≡ 1",
    "结论：exp(I*x) = cos(x) + I*sin(x)",
]


def test_coverage_does_not_regress():
    covered = sum(1 for answer in CORPUS if DRAFTER.draft("", answer).is_checkable)

    assert covered >= 12, f"只覆盖 {covered}/{len(CORPUS)}"


def test_the_uncovered_answers_all_lack_an_equation():
    """剩下的缺口是同一类：回答里根本没有等式。

    要处理它们得把提问里的表达式和答案的值拼成断言，而那需要**解读题目要干什么**
    （「的导数」对应 diff）。解读错了就是在验一个谁也没问过的命题，而且不报错——
    规则版不该碰这个，它是模型直出结构化断言那条路要做的事。
    """

    from math_harness.claim_drafting import normalize_math_text

    for answer in CORPUS:
        if DRAFTER.draft("", answer).is_checkable:
            continue
        assert not any(
            "=" in normalize_math_text(line) for line in answer.splitlines()
        ), answer


def test_no_answer_in_the_corpus_is_falsely_refuted():
    """整个语料里的可检验回答都是正确的，一条都不该被判成有反例。"""

    refuted = [
        answer for answer in CORPUS if graded(answer) is ConclusionConfidence.REFUTED
    ]

    assert refuted == []
