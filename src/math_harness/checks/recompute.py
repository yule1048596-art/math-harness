from __future__ import annotations

import re
from typing import Protocol

from math_harness.checks.base import (
    CheckContext,
    CheckOutcome,
    CheckResult,
    CheckTier,
)
from math_harness.checks.claim import Claim

# 独立重算：让另一个引擎（Wolfram）把同一条断言再算一遍。
#
# 它给的是 `cross_checked`，**永远不是 `verified`**。另一个程序同意不等于证明——
# 知识库靠这个标签决定以后信任谁，把它标成确定性的，污染会一路传下去。
#
# 真正的风险不在「Wolfram 算错」，而在**翻译**。把 SymPy 语法的断言译成 Wolfram
# 语言，译歪一点就变成在核对一个谁也没问过的命题，而且核对通过了还会抬高可信度——
# 比不查更糟。所以这里只放行两边写法明确一致的子集，其余一律跳过。跳过只是不加分，
# 译错是加错分。

#: 两边语义一致、翻译只需改运算符的函数。
#
# `D`、`Sum`、`Integrate`、`Matrix` 这些**故意不放**：它们的参数形状在两个系统里不
# 一样（SymPy 的 `Sum(f,(k,0,n))` 对应 Wolfram 的 `Sum[f,{k,0,n}]`），顺序或括号
# 译错都不会报错，只会安静地核对另一个命题。
_TRANSLATABLE = frozenset(
    {
        "sin", "cos", "tan", "exp", "log", "sqrt", "sinh", "cosh", "tanh",
        "asin", "acos", "atan", "Abs", "gcd", "lcm", "binomial", "factorial",
        "re", "im", "conjugate", "floor", "ceiling",
    }
)  # fmt: skip

#: SymPy 名字 → Wolfram 名字。大小写和拼写都不同，逐个列出而不是猜。
_FUNCTION_NAMES = {
    "asin": "ArcSin", "acos": "ArcCos", "atan": "ArcTan",
    "sin": "Sin", "cos": "Cos", "tan": "Tan", "exp": "Exp", "log": "Log",
    "sqrt": "Sqrt", "sinh": "Sinh", "cosh": "Cosh", "tanh": "Tanh",
    "Abs": "Abs", "gcd": "GCD", "lcm": "LCM", "binomial": "Binomial",
    "factorial": "Factorial", "re": "Re", "im": "Im",
    "conjugate": "Conjugate", "floor": "Floor", "ceiling": "Ceiling",
}  # fmt: skip

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class ToolClientProtocol(Protocol):
    def call_tool(self, name: str, arguments: dict[str, str]) -> str: ...


def is_translatable(claim: Claim) -> bool:
    """这条断言能不能安全地译成 Wolfram 语言。

    判据是保守的白名单：出现任何不在名单里的函数就判不能译。宁可跳过，不可译错。
    """

    if claim.rhs is None:
        return False
    text = f"{claim.lhs} {claim.rhs}"
    declared = {binding.symbol for binding in claim.bindings}
    for match in _IDENTIFIER.finditer(text):
        name = match.group(0)
        if name in declared or name in _TRANSLATABLE:
            continue
        # 单字母标识符当自由符号；其余认不出来的都当作不可译。
        if len(name) == 1 and name.isalpha():
            continue
        return False
    return True


def to_wolfram(expression: str) -> str:
    """把安全子集译成 Wolfram 语言。

    只改三处：幂运算符、函数名、调用括号。`is_translatable` 已经保证不会出现参数
    形状不同的构造。
    """

    translated = expression.replace("**", "^")
    for sympy_name, wolfram_name in _FUNCTION_NAMES.items():
        translated = re.sub(rf"\b{sympy_name}\s*\(", f"{wolfram_name}[", translated)
    # 函数调用的右括号要跟着换成方括号。只有被换过名字的调用才需要处理，所以按
    # 深度扫一遍，把处于「已改成 `[`」层级的 `)` 换掉。
    result: list[str] = []
    stack: list[str] = []
    for char in translated:
        if char in "([":
            stack.append(char)
            result.append(char)
        elif char == ")":
            opened = stack.pop() if stack else "("
            result.append("]" if opened == "[" else ")")
        else:
            result.append(char)
    return "".join(result)


class IndependentRecomputeCheck:
    """让 Wolfram 独立化简 `lhs - rhs`，看是不是 0。"""

    name = "independent_recompute"
    tier = CheckTier.SUGGEST

    def __init__(self, client: ToolClientProtocol | None = None) -> None:
        self.client = client

    def applies(self, context: CheckContext) -> bool:
        # 没配工具就跳过。这一层默认关闭，离线路径「不发网络请求」的承诺不能因为
        # 加了它而变。
        return (
            self.client is not None
            and context.claim is not None
            and is_translatable(context.claim)
        )

    def run(self, context: CheckContext) -> CheckResult:
        claim = context.claim
        assert claim is not None and claim.rhs is not None
        assert self.client is not None

        code = f"FullSimplify[({to_wolfram(claim.lhs)}) - ({to_wolfram(claim.rhs)})]"
        try:
            answer = self.client.call_tool("WolframLanguageEvaluator", {"code": code})
        except Exception as exc:  # noqa: BLE001
            return self._result(CheckOutcome.ERRORED, f"独立重算未能执行：{exc}", code)

        normalized = answer.strip()
        if normalized == "0":
            return self._result(CheckOutcome.PASSED, "", code)
        if normalized.startswith("工具报告错误"):
            return self._result(CheckOutcome.ERRORED, normalized, code)
        # 化简不出 0 **不等于**断言为假：另一个引擎可能只是没化开。判成 FAILED 会
        # 让一条正确的解被标成有反例，那比漏掉一次交叉验证严重得多。
        return self._result(
            CheckOutcome.SKIPPED, f"独立重算未化简为 0，结果为：{normalized}", code
        )

    def _result(self, outcome: CheckOutcome, detail: str, code: str) -> CheckResult:
        return CheckResult(
            check=self.name,
            tier=self.tier,
            outcome=outcome,
            detail=detail[:2_000],
            evidence={"wolfram_code": code[:500]},
        )
