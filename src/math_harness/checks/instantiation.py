from __future__ import annotations

import re

import sympy as sp

from math_harness.checks.base import (
    CheckContext,
    CheckOutcome,
    CheckResult,
    CheckTier,
)
from math_harness.checks.claim import Claim, ClaimKind
from math_harness.checks.sampling import DEFAULT_SEED, iter_assignments
from math_harness.checks.sandbox import (
    ComputationTimeout,
    call_with_timeout,
)
from math_harness.errors import UnsafeExpression
from math_harness.math_parser import SafeMathParser

# 实例化检验：整套检查的主干。
#
# 本机实测同一个机制跑通了微积分、线性代数、组合数学、平面几何（复数法）和逻辑，
# 五个领域用的是同一段代码，差别只在采样器。这也是它优先于「逐个领域写符号验证模式」
# 的原因。
#
# 它给的是**强证据，不是证明**。断言在 12 个随机点上成立，极大概率成立，但不是逻辑
# 必然，所以只能是 SUGGEST 档、只能产出 `numerically_checked`，永远不能写成
# `verified`。
#
# 三个细节决定它可不可用：
#   1. **反例必须回传**——用户要据此分辨「真错」还是「缺前提」；
#   2. **未定义点要重采**——极点和分支割线不算反例，否则误拒率会很难看；
#   3. **前提参与采样**——`sqrt(x**2) == x` 在 `x > 0` 下成立，随机负数不该判它错。

DEFAULT_TRIALS = 12
#: 采样撞上未定义点时最多重采多少次，避免在无效区域里空转。
MAX_RESAMPLES = 60
_ZERO_TOLERANCE = sp.Rational(1, 10**9)
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _symbol_table(texts: list[str], declared: list[str]) -> dict[str, sp.Symbol]:
    """收集文本里出现的标识符建符号表。

    只建 `sp.Symbol`，不放行任何函数——函数仍由解析器的白名单管。哑变量（`Sum` 的
    求和指标、`Integral` 的积分变量）靠这里才认得出来。
    """

    names = set(declared)
    for text in texts:
        if not text:
            continue
        for match in _IDENTIFIER.finditer(text):
            name = match.group(0)
            if (
                name not in SafeMathParser.FUNCTIONS
                and name not in SafeMathParser.CONSTANTS
            ):
                names.add(name)
    return {name: sp.Symbol(name) for name in names}


def _evaluate(expression: sp.Expr, assignment: dict[str, sp.Expr]) -> sp.Expr | None:
    """代入取值并求数值。落在未定义点返回 None，交由调用方重采。"""

    substituted = expression.subs(assignment)
    if substituted.has(sp.zoo, sp.nan, sp.oo, -sp.oo):
        return None
    try:
        value = sp.N(substituted, 30)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    if value.has(sp.zoo, sp.nan) or not value.is_number:
        return None
    return value


def _run_trials(
    lhs_text: str,
    rhs_text: str | None,
    kind: str,
    symbol_names: list[str],
    assignments: list[dict[str, str]],
    hypothesis_texts: list[str],
) -> tuple[str, dict[str, str], int]:
    """在子进程里跑完全部实例化。

    必须是模块顶层函数：sandbox 用 spawn，子进程按限定名重新导入它。跨进程只传字符串。
    """

    parser = SafeMathParser()
    # 符号表要包含表达式里出现的**全部**标识符，不只是有 binding 的那些：
    # `Sum(f, (k, 0, n))` 里的 `k` 是由 Sum 自己绑定的哑变量，不参与采样，
    # 但解析时必须认得它，否则整条断言解析不了。
    symbols = _symbol_table([lhs_text, rhs_text or "", *hypothesis_texts], symbol_names)
    left = parser.parse(lhs_text, symbols)
    right = parser.parse(rhs_text, symbols) if rhs_text else None
    hypotheses = [parser.parse(text, symbols) for text in hypothesis_texts]

    checked = 0
    for raw in assignments:
        assignment = {
            name: sp.sympify(value, rational=True) for name, value in raw.items()
        }

        # 前提不满足的样本直接丢弃——它落在断言声明的适用范围之外。
        satisfied = True
        for hypothesis in hypotheses:
            truth = hypothesis.subs(assignment)
            if truth is not sp.true and bool(truth) is not True:
                satisfied = False
                break
        if not satisfied:
            continue

        if kind == ClaimKind.PREDICATE.value:
            truth = left.subs(assignment)
            simplified = sp.simplify(truth)
            if simplified is sp.true or simplified is sp.S.true:
                checked += 1
                continue
            if simplified is sp.false or simplified is sp.S.false:
                return "failed", {k: str(v) for k, v in raw.items()}, checked
            continue  # 判不出真假的样本不计入，也不算反例

        assert right is not None
        left_value = _evaluate(left, assignment)
        right_value = _evaluate(right, assignment)
        if left_value is None or right_value is None:
            continue  # 未定义点，重采

        difference = sp.Abs(left_value - right_value)
        scale = sp.Max(1, sp.Abs(left_value), sp.Abs(right_value))
        if difference > _ZERO_TOLERANCE * scale:
            return "failed", {k: str(v) for k, v in raw.items()}, checked
        checked += 1

    return ("passed" if checked else "inconclusive"), {}, checked


def _run_step_trials(
    steps: list[
        tuple[str, str | None, str, list[str], list[dict[str, str]], list[str]]
    ],
) -> tuple[int, str, dict[str, str], int]:
    """在**一个**子进程里查完所有步骤，返回第一个出问题的步骤。

    每步各起一个子进程的话，spawn 每次约 0.25 秒，一个十步的推导光开销就是 2.5 秒。
    步骤之间互不依赖，一趟查完即可。返回的下标从 0 起，-1 表示全部通过。
    """

    for index, (lhs, rhs, kind, names, assignments, hypotheses) in enumerate(steps):
        status, counterexample, checked = _run_trials(
            lhs, rhs, kind, names, assignments, hypotheses
        )
        if status != "passed":
            return index, status, counterexample, checked
    return -1, "passed", {}, len(steps)


class InstantiationCheck:
    """按 bindings 实例化断言并比对两边。"""

    name = "instantiation"
    tier = CheckTier.SUGGEST

    def __init__(
        self,
        trials: int = DEFAULT_TRIALS,
        timeout_seconds: float = 10.0,
        seed: int = DEFAULT_SEED,
    ) -> None:
        self.trials = trials
        self.timeout_seconds = timeout_seconds
        self.seed = seed

    def applies(self, context: CheckContext) -> bool:
        return context.claim is not None

    def run(self, context: CheckContext) -> CheckResult:
        claim = context.claim
        assert claim is not None
        return self._check_claim(claim, label="")

    def _spec(
        self, claim: Claim
    ) -> tuple[str, str | None, str, list[str], list[dict[str, str]], list[str]]:
        """把断言打包成可以跨进程传的纯字符串。"""

        assignments = [
            {name: sp.sstr(value) for name, value in assignment.items()}
            for assignment in iter_assignments(
                claim.bindings,
                trials=self.trials + MAX_RESAMPLES,
                seed=self.seed,
            )
        ]
        return (
            claim.lhs,
            claim.rhs,
            claim.kind.value,
            claim.symbols,
            assignments,
            claim.hypotheses,
        )

    def _check_claim(self, claim: Claim, label: str) -> CheckResult:
        assignments = self._spec(claim)[4]

        try:
            # 全部采样在同一个子进程里跑完：spawn 每次约 0.25 秒，按次起进程会慢到
            # 不可用。
            status, counterexample, checked = call_with_timeout(
                _run_trials,
                claim.lhs,
                claim.rhs,
                claim.kind.value,
                claim.symbols,
                assignments,
                claim.hypotheses,
                timeout_seconds=self.timeout_seconds,
            )
        except ComputationTimeout as exc:
            return self._errored(f"实例化超时：{exc}")
        except (UnsafeExpression, RuntimeError) as exc:
            return self._errored(f"实例化未能执行：{exc}")

        if status == "failed":
            detail = "、".join(f"{k}={v}" for k, v in counterexample.items())
            return CheckResult(
                check=self.name,
                tier=self.tier,
                outcome=CheckOutcome.FAILED,
                detail=(
                    f"{label}在 {detail} 处两边不相等。{claim.description}"
                ).strip(),
                counterexample=counterexample,
                evidence={"trials_before_failure": str(checked)},
            )

        if status == "inconclusive":
            # 全部样本都落在未定义点或不满足前提——没有证据，不是通过。
            return self._errored("所有样本都落在未定义点或不满足前提，无法判定。")

        return CheckResult(
            check=self.name,
            tier=self.tier,
            outcome=CheckOutcome.PASSED,
            evidence={"trials": str(checked)},
        )

    def _errored(self, detail: str) -> CheckResult:
        return CheckResult(
            check=self.name,
            tier=self.tier,
            outcome=CheckOutcome.ERRORED,
            detail=detail,
        )


class StepInstantiationCheck(InstantiationCheck):
    """逐步实例化：解答里每一步各是一个断言。

    这条检查存在的理由是本项目特有的：**方法卡是从解答过程提取的**。本机复现过
    「结论对、第 2 步符号写错」的情形——只查结论会通过，逐步查才在第 2 步给出反例
    `{a: 7, b: -4}`。答案对而推导错的解会让知识库学到一个错方法，并在以后被检索复用。
    """

    name = "step_instantiation"
    tier = CheckTier.SUGGEST

    def applies(self, context: CheckContext) -> bool:
        return bool(context.steps)

    def run(self, context: CheckContext) -> CheckResult:
        specs = [self._spec(step) for step in context.steps]

        try:
            # 整条推导共用一个子进程。步骤之间互不依赖，没有理由为每一步付一次
            # spawn 的钱。超时预算按步数放大，否则长推导会被自己的长度判成超时。
            index, status, counterexample, checked = call_with_timeout(
                _run_step_trials,
                specs,
                timeout_seconds=self.timeout_seconds * max(len(specs), 1),
            )
        except ComputationTimeout as exc:
            return self._errored(f"逐步实例化超时：{exc}")
        except (UnsafeExpression, RuntimeError) as exc:
            return self._errored(f"逐步实例化未能执行：{exc}")

        if status == "failed":
            detail = "、".join(f"{k}={v}" for k, v in counterexample.items())
            return CheckResult(
                check=self.name,
                tier=self.tier,
                outcome=CheckOutcome.FAILED,
                detail=f"第 {index + 1} 步：在 {detail} 处两边不相等。",
                counterexample=counterexample,
                evidence={"failing_step": str(index + 1)},
            )

        if status == "inconclusive":
            return self._errored(
                f"第 {index + 1} 步所有样本都落在未定义点或不满足前提，无法判定。"
            )

        return CheckResult(
            check=self.name,
            tier=self.tier,
            outcome=CheckOutcome.PASSED,
            evidence={"steps": str(checked)},
        )
