from __future__ import annotations

import re
from enum import StrEnum
from typing import Protocol

import sympy as sp
from pydantic import BaseModel, Field

from math_harness.checks import Claim
from math_harness.claim_drafting import infer_bindings, normalize_math_text
from math_harness.math_parser import SafeMathParser

# 这一轮到底有没有出题。
#
# 抽断言那条路只审**回答**：回答里有一条能过 SymPy 的等式，就算「有可检验内容」。于是
# 一句「你好」——模型顺口回一句「之前我们求了 x³ 的导数是 3x²」——照样拿到符号验证徽章，
# 照样在知识库里留下一条题面是 `你好` 的例题。整条链从头到尾没有一处问过**用户这一轮
# 有没有出题**，门禁只审答案，不审问题。
#
# 这里补上那一侧。分工是拆开的，而且只交出去一种判断：
#
#   这一轮用户是不是在推进一道数学题  → 模型（这是对**用户文本**的阅读理解）
#   答案对不对                        → SymPy，且只有它（不变）
#
# **硬规则：相关性只做闸门，永远不能提升可信度。** 判 SOLVING 之后答案仍要原样跑完整条
# 检查流水线才拿得到徽章与草稿；判 CHITCHAT 则整条跳过。不存在「因为模型说是解题，所以
# 更可信」这种通路。


class TurnRelevance(StrEnum):
    #: 这一轮在推进一道数学题。
    SOLVING = "solving"
    #: 寒暄、闲聊、问系统本身。
    CHITCHAT = "chitchat"
    #: 判不出来。**放行**——理由见 `RelevanceVerdict.may_enter_knowledge_base`。
    UNKNOWN = "unknown"


class RelevanceVerdict(BaseModel):
    """一次判定的结果。`source` 要落进学习事件：跳过必须留痕。"""

    relevance: TurnRelevance = TurnRelevance.UNKNOWN
    #: `rules` 表示规则层直接定了，没花模型调用；否则是判定模型的 provider 名。
    source: str = Field(default="rules", max_length=80)
    reason: str = Field(default="", max_length=500)
    #: 这次判定发生了几次模型调用。度量三元组里的第三个数。
    model_calls: int = Field(default=0, ge=0)

    @property
    def may_enter_knowledge_base(self) -> bool:
        """**只有明确判定为闲聊才拦。** 判不出来一律放行。

        这个不对称是想清楚了才这么定的。两种错各有代价：

        - 误收一条闲聊 → 知识库里多一条噪声，用户在复核界面看得见，一键拒绝；
        - 误拦一条解题 → 知识库**悄悄停止生长**，界面上一点异常都没有。

        后者正是本项目反复踩的那个坑（v0.20 的 `feature_version` 是同一类）：闸门可以靠
        什么都不做来显得完美。而且表达式扎根天生分不开「请解这道题」和「你好」——两句都
        没有数学内容，差别只在意图。分不开的时候放行，比分不开的时候拦住要好。
        """

        return self.relevance is not TurnRelevance.CHITCHAT


#: 中日韩文字。中文散文把题面切成几段，剩下的段才是式子。
_CJK = "一-鿿　-〿＀-￯"
_PROSE = re.compile(rf"[{_CJK}]+")
#: 只抹句读，**不碰括号和 ASCII 逗号**——`Integral(x*exp(x), x)` 里它们是式子的一部分。
_PUNCTUATION = str.maketrans({c: " " for c in "?？!！；:：「」《》【】"})


def _parse_or_none(text: str, parser: SafeMathParser) -> sp.Expr | None:
    """解析一段式子。自由符号要先收出来交给解析器——白名单之外的标识符它一律拒绝。

    用不带假设的纯符号表，和实例化检查里那张一致：带 `real=True` 的符号会让同一个式子
    在两侧长成不同的对象，`has()` 就永远对不上了。
    """

    try:
        bindings = infer_bindings([text], parser)
        table = {binding.symbol: sp.Symbol(binding.symbol) for binding in bindings}
        return parser.parse(text, table)
    except Exception:  # noqa: BLE001
        return None


def question_expressions(question: str, parser: SafeMathParser) -> list[sp.Expr]:
    """把提问里能解析的数学片段抽出来。

    规范化走 `normalize_math_text`——跟抽断言那侧同一套，`^`、隐式乘法、LaTeX 包裹的
    处理必须一致，否则同一个式子在提问里和在回答里会长成两个样子，grounding 永远对不上。

    只抽**非平凡**的：单个符号和纯数字不算。`a 是什么` 里的 `a` 谁都能撞上，用它判
    grounding 会让规则层误判成解题——而误收率的门槛是 0.0，规则层必须在 SOLVING 方向
    上保守，拿不准就交给模型。
    """

    text = question.translate(_PUNCTUATION)
    expressions: list[sp.Expr] = []
    # 先整段试，再退回按空白切。`Integral(x*exp(x), x)` 里带空格，逐词切会把它拆散;
    # 而 `求 x^3 的导数` 整段就是一个式子。两条都要覆盖。
    for segment in _PROSE.split(text):
        candidates = [segment, *segment.split()]
        for candidate in candidates:
            cleaned = normalize_math_text(candidate).strip().strip("=").strip()
            if not cleaned:
                continue
            parsed = _parse_or_none(cleaned, parser)
            if parsed is None:
                continue
            # 单符号、纯数字都太容易偶然撞上，不作为 grounding 依据。
            #
            # 用 `getattr` 而不是直接取属性：解析结果不一定是 `Expr`，`Matrix` 就没有
            # 这两个属性。写成 `parsed.is_Symbol` 的话，提问里出现一个矩阵字面量就会
            # 把整个回合打崩。
            if getattr(parsed, "is_Symbol", False) or getattr(
                parsed, "is_Number", False
            ):
                continue
            expressions.append(parsed)
            break  # 整段解析成功就不必再拆词。
    return expressions


class GroundedRelevanceRule:
    """免费层：断言扎在这一轮的提问里，就判解题。

    判据是**子表达式包含**，不是字符串相似：`求 x^3 的导数` 抽出 `x**3`，而断言
    `Derivative(x**3, x) = 3*x**2` 的左边确实含有 `x**3`。这跟 v0.19 已有的「引文必须
    扎在原文里」是同一个测试，只是换到提问那一侧——那一侧现在没人看。

    **只在 SOLVING 方向上发言。** 扎不住不代表在闲聊（「这个怎么证明？」就扎不住），
    所以扎不住时返回 None，交给上层去问模型。
    """

    name = "rules"

    def __init__(self, parser: SafeMathParser | None = None) -> None:
        self.parser = parser or SafeMathParser()

    def evaluate(self, question: str, claims: list[Claim]) -> RelevanceVerdict | None:
        anchors = question_expressions(question, self.parser)
        if not anchors:
            return None
        for claim in claims:
            for side in (claim.lhs, claim.rhs):
                if not side:
                    continue
                parsed = _parse_or_none(side, self.parser)
                if parsed is None:
                    continue
                for anchor in anchors:
                    if parsed == anchor or parsed.has(anchor):
                        return RelevanceVerdict(
                            relevance=TurnRelevance.SOLVING,
                            source=self.name,
                            reason=f"断言含有提问里的 {anchor}",
                        )
        return None


#: 整条消息就是这么一句时，它不可能是在解题。
#:
#: 这张表**刻意只收最明显的**，而且必须**整条匹配**——「你好，帮我求 x^3 的导数」不算。
#: 它的作用不是判断「什么是数学」（那个判不准，也不该用关键词判），而是在没配判定模型
#: 时守住最常见的那一类。宁可漏掉九成闲聊，也不能误伤一条解题。
_OBVIOUS_CHITCHAT = frozenset(
    {
        "你好", "您好", "嗨", "哈喽", "hi", "hello", "hey",
        "早上好", "中午好", "晚上好", "晚安", "在吗", "在么",
        "谢谢", "谢谢你", "多谢", "感谢", "thanks", "thankyou", "thx",
        "再见", "拜拜", "bye", "ok", "好的", "好", "嗯", "哈哈", "哈哈哈",
        "你是谁", "你叫什么", "你能干什么", "你会什么", "你能做什么",
    }
)  # fmt: skip

#: 匹配前要剥掉的东西：空白、标点、以及表情符号那一段。
_STRIP_FOR_MATCH = re.compile(r"[\s\W_]+", re.UNICODE)


class ObviousChitchatRule:
    """整条消息就是一句寒暄时判闲聊。

    存在的理由只有一个：**没配判定模型的用户也该挡得住最常见的那一类**。用户实际撞上的
    就是一句「你好」。

    它只在**极窄**的口径上发言：整条消息完全等于表里的一句，而且提问里没有任何数学。
    差一个字就不匹配，交给判定模型或者放行。
    """

    name = "rules"

    def evaluate(
        self, question: str, anchors: list[sp.Expr]
    ) -> RelevanceVerdict | None:
        if anchors:
            return None
        normalized = _STRIP_FOR_MATCH.sub("", question).lower()
        if normalized and normalized in _OBVIOUS_CHITCHAT:
            return RelevanceVerdict(
                relevance=TurnRelevance.CHITCHAT,
                source=self.name,
                reason=f"整条消息就是一句寒暄：{question.strip()[:40]}",
            )
        return None


class RelevanceJudgeProtocol(Protocol):
    name: str
    prompt_version: str

    def judge(self, question: str, recent_questions: list[str]) -> TurnRelevance: ...


class RelevanceGate:
    """规则优先，够不着才问模型。

    这个分工不是随手定的，`ModelAssistedClaimDrafter` 已经用它跑了两个版本：规则层免费、
    只碰原文，模型只补规则够不着的那一类。副作用是个好性质——规则层能定的场景，行为与
    v0.20 逐字相同。

    三层，按代价从低到高：

    1. 断言扎在提问里 → `SOLVING`，免费；
    2. 整条消息就是一句寒暄 → `CHITCHAT`，免费，**没配模型时也生效**；
    3. 都不是 → 问判定模型；没配模型就是 `UNKNOWN`，而 `UNKNOWN` 放行。

    第 3 步放行是有意的：判不出来的时候拦住，会让知识库悄悄停止生长，而这个失败模式在
    界面上完全看不出来。详见 `RelevanceVerdict.may_enter_knowledge_base`。
    """

    def __init__(
        self,
        rule: GroundedRelevanceRule | None = None,
        judge: RelevanceJudgeProtocol | None = None,
        chitchat_rule: ObviousChitchatRule | None = None,
    ) -> None:
        self.rule = rule or GroundedRelevanceRule()
        self.chitchat_rule = chitchat_rule or ObviousChitchatRule()
        self.judge = judge

    def evaluate(
        self,
        question: str,
        claims: list[Claim],
        recent_questions: list[str] | None = None,
    ) -> RelevanceVerdict:
        grounded = self.rule.evaluate(question, claims)
        if grounded is not None:
            return grounded
        obvious = self.chitchat_rule.evaluate(
            question, question_expressions(question, self.rule.parser)
        )
        if obvious is not None:
            return obvious
        if self.judge is None:
            return RelevanceVerdict(
                relevance=TurnRelevance.UNKNOWN,
                source=self.rule.name,
                reason="扎不到这一轮的提问上，也不是一句明显的寒暄，且没配判定模型。",
            )
        try:
            verdict = self.judge.judge(question, recent_questions or [])
        except Exception as exc:  # noqa: BLE001
            # 判定层挂了不能把这一轮对话弄丢，也不该因此改变结论：记 `UNKNOWN`，
            # 按「判不出来」处理，理由留在学习事件里可查。
            return RelevanceVerdict(
                relevance=TurnRelevance.UNKNOWN,
                source=self.judge.name,
                reason=f"{exc.__class__.__name__}: {exc}"[:500],
                model_calls=1,
            )
        return RelevanceVerdict(
            relevance=verdict,
            source=self.judge.name,
            reason="由判定模型给出。",
            model_calls=1,
        )


def build_relevance_gate_from_env() -> RelevanceGate:
    """按配置构造。没绑 `relevance_judge` 角色就是纯规则版，一次模型调用都不会发生。"""

    from math_harness.provider_config import ROLE_RELEVANCE_JUDGE, role_is_configured

    if not role_is_configured(ROLE_RELEVANCE_JUDGE):
        return RelevanceGate()
    from math_harness.providers.openai_relevance import build_relevance_judge_from_env

    return RelevanceGate(judge=build_relevance_judge_from_env())
