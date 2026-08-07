from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import StrEnum

from math_harness.checks.claim import Binding, Claim, ClaimKind, SamplingDomain

# 变异测试：用来判断每一层检查到底有没有用。
#
# 「证明不了提升的层不进代码库」是 v0.13 造标尺时立的规矩，这里同样适用。做法不需要
# 外部基准：拿已知正确的题解对，注入受控扰动，量每一层的捕获率与误拒率。
#
# 两类扰动对应两种真实失败，缺一不可：
#
#   **结论扰动**——最终答案改错。这是常规的「答错了」。
#   **步骤扰动**——保持结论正确，只把某一中间步改错。这类只有逐步检查能抓，而它对
#     本项目尤其重要：方法卡是从推导提取的，答案对而推导错的解会让知识库学到错方法。


class MutationKind(StrEnum):
    #: 系数改动：3*x**2 → 2*x**2
    COEFFICIENT = "coefficient"
    #: 符号翻转：a - b → a + b
    SIGN = "sign"
    #: 指数错位：x**3 → x**4
    EXPONENT = "exponent"
    #: 丢项：a + b + c → a + b
    DROPPED_TERM = "dropped_term"
    #: 联结词替换：And → Or。前四类都是对代数式做文本改动，逻辑命题里没有数字、
    #: 没有 `**`、没有项间加减号，一条都触发不了——不加这一类，离散数学那一档就是
    #: 完全没测。
    CONNECTIVE = "connective"


@dataclass(frozen=True)
class Mutation:
    kind: MutationKind
    original: str
    mutated: str
    #: 被改的是第几步；None 表示改的是结论。
    step_index: int | None = None

    @property
    def targets_conclusion(self) -> bool:
        return self.step_index is None


@dataclass
class MutationCase:
    """一道已知正确的题，外加一处注入的错误。"""

    name: str
    claim: Claim
    steps: list[Claim] = field(default_factory=list)
    mutation: Mutation | None = None

    @property
    def is_correct(self) -> bool:
        """没有注入扰动——用来量误拒率。"""

        return self.mutation is None


def _mutate_text(text: str, kind: MutationKind, rng: random.Random) -> str | None:
    """对表达式文本做一处受控改动。改不动返回 None。"""

    if kind is MutationKind.COEFFICIENT:
        digits = [index for index, char in enumerate(text) if char.isdigit()]
        if not digits:
            return None
        position = rng.choice(digits)
        original = int(text[position])
        replacement = str((original + 1) % 10)
        if replacement == text[position]:
            return None
        return text[:position] + replacement + text[position + 1 :]

    if kind is MutationKind.SIGN:
        # 只翻转项之间的加减号，不碰一元负号。判据是它前面（跳过空格）有没有一个项的
        # 结尾——`a + b` 里的 `+` 是二元的，`(-b)` 里的不是。**必须跳空格**：漏了
        # 这一步，`3*x**2 + 2` 这种正常写法一个候选都找不到，这类扰动会整个测不到。
        candidates = []
        for index in range(1, len(text) - 1):
            if text[index] not in "+-":
                continue
            previous = text[:index].rstrip()
            if previous and (previous[-1].isalnum() or previous[-1] in ")_"):
                candidates.append(index)
        if not candidates:
            return None
        position = rng.choice(candidates)
        flipped = "+" if text[position] == "-" else "-"
        return text[:position] + flipped + text[position + 1 :]

    if kind is MutationKind.EXPONENT:
        position = text.find("**")
        if position < 0 or position + 2 >= len(text):
            return None
        after = text[position + 2]
        if not after.isdigit():
            return None
        return text[: position + 2] + str(int(after) + 1) + text[position + 3 :]

    if kind is MutationKind.DROPPED_TERM:
        depth = 0
        splits = []
        for index, char in enumerate(text):
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            elif char in "+-" and depth == 0 and index > 0:
                splits.append(index)
        if not splits:
            return None
        position = splits[-1]
        return text[:position].strip()

    if kind is MutationKind.CONNECTIVE:
        for original, replacement in (("And(", "Or("), ("Or(", "And(")):
            if original in text:
                return text.replace(original, replacement, 1)
        return None

    return None


def mutate_conclusion(
    case: MutationCase, kind: MutationKind, rng: random.Random
) -> MutationCase | None:
    """把最终答案改错，保持推导不动。"""

    # 等式改右边（答案），命题改左边（命题本身就是它的全部内容）。
    field = "rhs" if case.claim.kind is ClaimKind.EQUALITY else "lhs"
    target = case.claim.rhs if field == "rhs" else case.claim.lhs
    if target is None:
        return None
    mutated = _mutate_text(target, kind, rng)
    if mutated is None or mutated == target:
        return None
    return MutationCase(
        name=f"{case.name}[结论-{kind.value}]",
        claim=case.claim.model_copy(update={field: mutated}),
        steps=list(case.steps),
        mutation=Mutation(kind=kind, original=target, mutated=mutated),
    )


def mutate_step(
    case: MutationCase, kind: MutationKind, rng: random.Random
) -> MutationCase | None:
    """改错某一中间步，**保持结论正确**。

    这是本项目最该抓住的一类：只查结论完全看不出来，而方法卡正是从这段推导提取的。
    """

    if not case.steps:
        return None
    indices = list(range(len(case.steps)))
    rng.shuffle(indices)
    for index in indices:
        step = case.steps[index]
        if step.rhs is None:
            continue
        mutated = _mutate_text(step.rhs, kind, rng)
        if mutated is None or mutated == step.rhs:
            continue
        steps = list(case.steps)
        steps[index] = step.model_copy(update={"rhs": mutated})
        return MutationCase(
            name=f"{case.name}[第{index + 1}步-{kind.value}]",
            claim=case.claim,  # 结论保持正确
            steps=steps,
            mutation=Mutation(
                kind=kind,
                original=step.rhs,
                mutated=mutated,
                step_index=index,
            ),
        )
    return None


def _claim(
    lhs: str,
    rhs: str,
    symbols: tuple[str, ...] = (),
    domain: SamplingDomain = SamplingDomain.REAL,
) -> Claim:
    return Claim(
        kind=ClaimKind.EQUALITY,
        lhs=lhs,
        rhs=rhs,
        bindings=[Binding(symbol=name, domain=domain) for name in symbols],
    )


def _predicate(lhs: str, symbols: tuple[str, ...], domain: SamplingDomain) -> Claim:
    return Claim(
        kind=ClaimKind.PREDICATE,
        lhs=lhs,
        rhs=None,
        bindings=[Binding(symbol=name, domain=domain) for name in symbols],
    )


def baseline_cases() -> list[MutationCase]:
    """已知正确的题解对，覆盖用户点名的各个领域。

    **每一条都必须真的成立。** 基准错了，捕获率和误拒率一起失去意义——
    `test_every_baseline_case_is_actually_correct` 就是为这件事存在的，它跑所有检查层
    确认没有一条被判为假。

    每条都带推导步骤：真实的解答就是这个形状，而步骤扰动只有在有步骤时才测得到。
    """

    integer = SamplingDomain.POSITIVE_INTEGER
    complex_ = SamplingDomain.COMPLEX
    boolean = SamplingDomain.BOOLEAN
    return [
        # --- 微积分 ---
        MutationCase(
            name="微积分-多项式求导",
            claim=_claim("diff(x**3 + 2*x, x)", "3*x**2 + 2", ("x",)),
            steps=[
                _claim("diff(x**3, x)", "3*x**2", ("x",)),
                _claim("diff(2*x, x)", "2", ("x",)),
            ],
        ),
        MutationCase(
            name="微积分-乘积法则",
            claim=_claim("diff(x**2*sin(x), x)", "2*x*sin(x) + x**2*cos(x)", ("x",)),
            steps=[
                _claim("diff(x**2, x)", "2*x", ("x",)),
                _claim("diff(sin(x), x)", "cos(x)", ("x",)),
            ],
        ),
        MutationCase(
            name="微积分-链式法则",
            claim=_claim("diff(sin(x**2), x)", "2*x*cos(x**2)", ("x",)),
            steps=[
                _claim("diff(x**2, x)", "2*x", ("x",)),
                _claim("diff(sin(y), y)", "cos(y)", ("y",)),
            ],
        ),
        MutationCase(
            name="微积分-积分求导回去",
            claim=_claim("diff(x*exp(x) - exp(x), x)", "x*exp(x)", ("x",)),
            steps=[
                _claim("diff(x*exp(x), x)", "exp(x) + x*exp(x)", ("x",)),
                _claim("diff(exp(x), x)", "exp(x)", ("x",)),
            ],
        ),
        # --- 代数 ---
        MutationCase(
            name="代数-平方差恒等式",
            claim=_claim("(a+b)**2 - (a-b)**2", "4*a*b", ("a", "b")),
            steps=[
                _claim("(a+b)**2", "a**2 + 2*a*b + b**2", ("a", "b")),
                _claim("(a-b)**2", "a**2 - 2*a*b + b**2", ("a", "b")),
            ],
        ),
        MutationCase(
            name="代数-立方差",
            claim=_claim("(a-b)*(a**2 + a*b + b**2)", "a**3 - b**3", ("a", "b")),
            steps=[
                _claim(
                    "a*(a**2 + a*b + b**2)",
                    "a**3 + a**2*b + a*b**2",
                    ("a", "b"),
                ),
                _claim(
                    "b*(a**2 + a*b + b**2)",
                    "a**2*b + a*b**2 + b**3",
                    ("a", "b"),
                ),
            ],
        ),
        MutationCase(
            name="竞赛-韦达定理",
            claim=_claim("(x-2)*(x-3)", "x**2 - 5*x + 6", ("x",)),
            steps=[
                _claim("2 + 3", "5"),
                _claim("2 * 3", "6"),
            ],
        ),
        # --- 线性代数 ---
        MutationCase(
            name="线性代数-特征值",
            claim=_claim("det(Matrix([[2,1],[1,2]]) - 3*eye(2))", "0"),
            steps=[
                _claim("det(Matrix([[2,1],[1,2]]))", "3"),
                _claim("trace(Matrix([[2,1],[1,2]]))", "4"),
            ],
        ),
        MutationCase(
            name="线性代数-二阶行列式",
            claim=_claim("det(Matrix([[1,2],[3,4]]))", "-2"),
            steps=[
                _claim("1*4 - 2*3", "-2"),
            ],
        ),
        MutationCase(
            name="线性代数-三阶行列式",
            claim=_claim("det(Matrix([[2,0,1],[1,3,2],[0,1,4]]))", "21"),
            steps=[
                _claim("3*4 - 2*1", "10"),
                _claim("1*1 - 3*0", "1"),
            ],
        ),
        # --- 组合与离散 ---
        MutationCase(
            name="组合-二项式和",
            claim=_claim("Sum(binomial(n,k),(k,0,n))", "2**n", ("n",), integer),
            steps=[
                _claim("binomial(n, 0)", "1", ("n",), integer),
                _claim("binomial(n, n)", "1", ("n",), integer),
            ],
        ),
        MutationCase(
            name="组合-等差求和",
            claim=_claim("Sum(k,(k,1,n))", "n*(n+1)/2", ("n",), integer),
            steps=[
                _claim("Sum(1,(k,1,n))", "n", ("n",), integer),
            ],
        ),
        MutationCase(
            name="组合-平方和",
            claim=_claim("Sum(k**2,(k,1,n))", "n*(n+1)*(2*n+1)/6", ("n",), integer),
            steps=[
                _claim("Sum(k,(k,1,n))", "n*(n+1)/2", ("n",), integer),
            ],
        ),
        MutationCase(
            name="离散-逻辑蕴含",
            claim=_predicate("Implies(And(p, q), p)", ("p", "q"), boolean),
            steps=[
                _predicate("Implies(And(p, q), q)", ("p", "q"), boolean),
            ],
        ),
        MutationCase(
            name="离散-德摩根律",
            claim=_predicate(
                "Equivalent(Not(And(p, q)), Or(Not(p), Not(q)))", ("p", "q"), boolean
            ),
            steps=[
                _predicate(
                    "Equivalent(Not(Or(p, q)), And(Not(p), Not(q)))",
                    ("p", "q"),
                    boolean,
                ),
            ],
        ),
        # --- 三角与复变 ---
        MutationCase(
            name="三角-勾股恒等式",
            claim=_claim("sin(x)**2 + cos(x)**2", "1", ("x",)),
            steps=[
                _claim("cos(2*x)", "1 - 2*sin(x)**2", ("x",)),
            ],
        ),
        MutationCase(
            name="三角-倍角公式",
            claim=_claim("sin(2*x)", "2*sin(x)*cos(x)", ("x",)),
            steps=[
                _claim("cos(2*x)", "cos(x)**2 - sin(x)**2", ("x",)),
            ],
        ),
        MutationCase(
            name="复变-欧拉公式",
            claim=_claim("exp(I*x)", "cos(x) + I*sin(x)", ("x",)),
            steps=[
                _claim("exp(I*x) + exp(-I*x)", "2*cos(x)", ("x",)),
            ],
        ),
        MutationCase(
            name="复变-模长",
            claim=_claim("z*conjugate(z)", "re(z)**2 + im(z)**2", ("z",), complex_),
            steps=[
                _claim("z + conjugate(z)", "2*re(z)", ("z",), complex_),
            ],
        ),
        # --- 几何 ---
        MutationCase(
            name="平面几何-中点等距",
            claim=_claim(
                "Abs((a+b)/2 - a) - Abs((a+b)/2 - b)", "0", ("a", "b"), complex_
            ),
            steps=[
                _claim("(a+b)/2 - a", "(b-a)/2", ("a", "b"), complex_),
            ],
        ),
        MutationCase(
            name="解析几何-圆方程展开",
            claim=_claim(
                "(x-h)**2 + (y-k)**2",
                "x**2 + y**2 - 2*h*x - 2*k*y + h**2 + k**2",
                ("x", "y", "h", "k"),
            ),
            steps=[
                _claim("(x-h)**2", "x**2 - 2*h*x + h**2", ("x", "h")),
                _claim("(y-k)**2", "y**2 - 2*k*y + k**2", ("y", "k")),
            ],
        ),
        # --- 数论 ---
        MutationCase(
            name="数论-取模与最大公约数",
            claim=_claim("Mod(7, 3) + gcd(12, 18)", "7"),
            steps=[
                _claim("Mod(7, 3)", "1"),
                _claim("gcd(12, 18)", "6"),
            ],
        ),
        MutationCase(
            name="数论-gcd乘lcm",
            claim=_claim("gcd(12, 18) * lcm(12, 18)", "216"),
            steps=[
                _claim("lcm(12, 18)", "36"),
                _claim("12 * 18", "216"),
            ],
        ),
    ]
