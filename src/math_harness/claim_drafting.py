from __future__ import annotations

import re
from typing import Protocol

import sympy as sp
from pydantic import BaseModel, Field

from math_harness.checks import Binding, Claim, ClaimKind, SamplingDomain
from math_harness.errors import UnsafeExpression
from math_harness.math_parser import SafeMathParser
from math_harness.models import ExtractionStatus

# 从模型的回答里把可检验的断言抽出来。
#
# 这是「一个输入框」的地基：用户用自然语言问，模型正常作答，**这一步自己判断答案里
# 有什么可以查**。抽不出来不是错误，只是这条回答没有可机检的部分，可信度记
# `unchecked` 就是了——它照样能回答，照样能入库。
#
# 主路是让模型直接产出结构化断言。这里的规则版是离线兜底，也是主路失败时的下限，
# 而它能覆盖的情况比看上去多：数学回答里本来就大量存在 `左边 = 右边` 的行，两边都能
# 过安全解析器的，直接就是一条可检验断言，一次模型调用都不用花。

#: 一行里可能的等号写法。`==` 放前面，避免把它切成两个 `=`。
_EQUALS = re.compile(r"(?<![<>=!])={1,2}(?!=)")

#: 行首的编号、项目符号和常见前缀。
_LINE_NOISE = re.compile(r"^\s*(?:[（(]?\d+[)）.、]|[-*·]|第\s*\d+\s*步[:：]?)\s*")

#: LaTeX 的行内包裹与显示包裹。
_LATEX_WRAPPERS = (
    ("\\(", ""),
    ("\\)", ""),
    ("\\[", ""),
    ("\\]", ""),
    ("$$", ""),
    ("$", ""),
)

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

#: 中日韩文字、全角标点与全角符号。
_CJK = "一-鿿　-〿＀-￯"
#: 式子两端的散文。「所以 f(x) = g(x)」这种写法极常见，不剥掉的话最重要的那一行
#: ——结论——恰恰是最容易解析失败的一行。
_LEADING_PROSE = re.compile(rf"^[{_CJK}\s]+")
_TRAILING_PROSE = re.compile(rf"[{_CJK}\s]+$")


def _strip_prose(text: str) -> str:
    return _TRAILING_PROSE.sub("", _LEADING_PROSE.sub("", text)).strip()


#: 按记号习惯猜取值域。
#
# 这是**启发式**，不是推断：数学写作里 n/k/m 惯例上是自然数，z/w 是复数。猜错的代价
# 不对称——把整数当实数采样会让组合恒等式假失败，反过来只是少查一些点。所以宁可按
# 习惯猜，也不要一律当实数。
_INTEGER_NAMES = frozenset({"n", "m", "k", "i", "j", "p", "q"})
_COMPLEX_NAMES = frozenset({"z", "w"})


class ClaimDraft(BaseModel):
    """从一次问答里抽出来的可检验内容。"""

    #: 结论断言。None 表示这条回答里没有可机检的结论。
    claim: Claim | None = None
    #: 推导步骤。方法卡门禁看的是这些。
    steps: list[Claim] = Field(default_factory=list, max_length=40)
    summary: str = Field(default="", max_length=2_000)
    status: ExtractionStatus = ExtractionStatus.SKIPPED
    provider: str = Field(default="rules", max_length=80)
    prompt_version: str = Field(default="claim-rules-v1", max_length=80)
    error: str | None = Field(default=None, max_length=2_000)

    @property
    def is_checkable(self) -> bool:
        return self.claim is not None or bool(self.steps)


class ClaimDrafterProtocol(Protocol):
    name: str
    prompt_version: str

    def draft(self, problem: str, answer: str) -> ClaimDraft: ...


def normalize_math_text(text: str) -> str:
    """把常见的书写形式收敛成安全解析器认得的写法。

    只做无歧义的替换。`^` 在数学写作里就是幂，在 Python 里是异或——留着它会把
    `x^2` 悄悄解析成完全不同的东西，所以必须换掉而不是放行。
    """

    cleaned = text
    for wrapper, replacement in _LATEX_WRAPPERS:
        cleaned = cleaned.replace(wrapper, replacement)
    cleaned = cleaned.replace("^", "**")
    cleaned = cleaned.replace("×", "*").replace("÷", "/")
    cleaned = cleaned.replace("−", "-").replace("–", "-")
    return cleaned.strip()


def infer_bindings(texts: list[str], parser: SafeMathParser) -> list[Binding]:
    """收集自由符号并按记号习惯给取值域。

    只收不在函数与常量白名单里的标识符。哑变量（`Sum` 的求和指标）会被一起收进来，
    多绑一个不影响正确性——采样出来的值代进去会被 `Sum` 自己的绑定盖掉。
    """

    names: list[str] = []
    seen: set[str] = set()
    for text in texts:
        for match in _IDENTIFIER.finditer(text):
            name = match.group(0)
            if name in parser.FUNCTIONS or name in parser.CONSTANTS:
                continue
            if name in seen:
                continue
            seen.add(name)
            names.append(name)

    bindings = []
    for name in names:
        if name in _INTEGER_NAMES:
            domain = SamplingDomain.POSITIVE_INTEGER
        elif name in _COMPLEX_NAMES:
            domain = SamplingDomain.COMPLEX
        else:
            domain = SamplingDomain.REAL
        bindings.append(Binding(symbol=name, domain=domain))
    return bindings


def _split_equation(line: str) -> tuple[str, str] | None:
    """把一行切成左右两边。等号不止一个就放弃——`a = b = c` 的意图不明确。"""

    parts = _EQUALS.split(line)
    if len(parts) != 2:
        return None
    left, right = _strip_prose(parts[0]), _strip_prose(parts[1])
    if not left or not right:
        return None
    return left, right


def extract_claims(text: str, parser: SafeMathParser | None = None) -> list[Claim]:
    """扫出所有两边都能安全解析的等式。

    解析不了的行直接跳过，不报错：一段解答里混着散文是常态，抽不出来只说明那一行不是
    可机检的内容。**能过安全解析器**是唯一的准入条件——绝不为了多抽几条而放宽它。
    """

    safe_parser = parser or SafeMathParser()
    claims: list[Claim] = []
    # 「设 m = (a+b)/2」是**定义**，不是断言。把它当等式来查会独立采样 m，必然给出反例
    # ——一条引入记号的正常解答就会被误拒，而误拒比漏抓更糟。所以定义记下来代入后面的
    # 式子，不作为断言。
    definitions: dict[sp.Symbol, sp.Expr] = {}
    for raw_line in text.splitlines():
        line = _LINE_NOISE.sub("", raw_line).strip()
        if not line or "=" not in line:
            continue
        # 去掉行尾的解释性文字：中文全角标点后面通常是说明，不是式子的一部分。
        line = re.split(r"[，。；、]", line)[0]
        split = _split_equation(normalize_math_text(line))
        if split is None:
            continue
        left, right = split

        bindings = infer_bindings([left, right], safe_parser)
        # 用不带假设的纯符号表，和实例化检查里那张一致。带 `real=True` 的符号会让
        # 复数断言在解析阶段就走样，而取值域该由 binding 说了算，不该由符号假设偷偷决定。
        table = {binding.symbol: sp.Symbol(binding.symbol) for binding in bindings}
        try:
            left_expr = safe_parser.parse(left, table)
            right_expr = safe_parser.parse(right, table)
        except (UnsafeExpression, ValueError, TypeError, SyntaxError):
            continue

        if left_expr.is_Symbol and left_expr not in definitions:
            # 左边是个光秃秃的符号：这是在给记号下定义，没有可验证的内容。
            definitions[left_expr] = right_expr.subs(definitions)
            continue

        # 只在这条断言真的用到了某个定义时才代入。否则保留用户看到的原样文本——
        # 代入要走一遍 SymPy 序列化，会顺手把 `3 + 3` 规范成 `6`，而抽出来的断言是要
        # 并排展示给用户看的，不该因为前文出现过一个无关的定义就变样。
        referenced = (left_expr.free_symbols | right_expr.free_symbols) & set(
            definitions
        )
        if referenced:
            substituted = _substitute(left_expr, right_expr, definitions, safe_parser)
            if substituted is not None:
                left, right, bindings = substituted

        claims.append(
            Claim(
                kind=ClaimKind.EQUALITY,
                lhs=left,
                rhs=right,
                bindings=bindings,
            )
        )
    return claims


def _substitute(
    left_expr: sp.Expr,
    right_expr: sp.Expr,
    definitions: dict[sp.Symbol, sp.Expr],
    parser: SafeMathParser,
) -> tuple[str, str, list[Binding]] | None:
    """把已知定义代进断言，再序列化回文本。

    在 SymPy 层代入而不是做文本替换：文本替换会踩优先级，`m = a+b` 之后把 `2*m` 换成
    `2*a+b` 就悄悄算错了。代回来的文本要**重新过一遍安全解析器**——序列化产物同样不
    享受特权。
    """

    new_left = sp.sstr(left_expr.subs(definitions))
    new_right = sp.sstr(right_expr.subs(definitions))
    new_bindings = infer_bindings([new_left, new_right], parser)
    table = {binding.symbol: sp.Symbol(binding.symbol) for binding in new_bindings}
    try:
        parser.parse(new_left, table)
        parser.parse(new_right, table)
    except (UnsafeExpression, ValueError, TypeError, SyntaxError):
        # 代入后反而解析不了：返回 None，调用方保留代入前的原样，不把这条断言丢掉。
        return None
    return new_left, new_right, new_bindings


class RuleBasedClaimDrafter:
    """不调模型，直接从回答文本里扫可检验的等式。"""

    name = "rules"
    prompt_version = "claim-rules-v1"

    def __init__(self, parser: SafeMathParser | None = None) -> None:
        self.parser = parser or SafeMathParser()

    def draft(self, problem: str, answer: str) -> ClaimDraft:
        del problem  # 规则版只看回答；题面留给模型版用。
        claims = extract_claims(answer, self.parser)
        if not claims:
            return ClaimDraft(
                status=ExtractionStatus.SKIPPED,
                summary="这条回答里没有可机检的等式。",
                provider=self.name,
                prompt_version=self.prompt_version,
            )

        # 最后一条等式当结论，全部等式当推导步骤。结论也留在步骤里：逐步检查要覆盖
        # 整条推导，漏掉最后一行等于放过最容易出错的一步。
        return ClaimDraft(
            claim=claims[-1],
            steps=claims,
            status=ExtractionStatus.SUCCESS,
            summary=f"抽出 {len(claims)} 条可检验等式。",
            provider=self.name,
            prompt_version=self.prompt_version,
        )
