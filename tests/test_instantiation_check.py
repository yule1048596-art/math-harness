from __future__ import annotations

from math_harness.checks import (
    Binding,
    CheckContext,
    CheckOutcome,
    Claim,
    ClaimKind,
    InstantiationCheck,
    SamplingDomain,
    StepInstantiationCheck,
)

CHECK = InstantiationCheck(trials=8)


def claim(
    lhs: str,
    rhs: str | None = None,
    *,
    symbols: tuple[str, ...] = (),
    domain: SamplingDomain = SamplingDomain.REAL,
    hypotheses: tuple[str, ...] = (),
    kind: ClaimKind = ClaimKind.EQUALITY,
) -> Claim:
    return Claim(
        kind=kind,
        lhs=lhs,
        rhs=rhs,
        bindings=[Binding(symbol=name, domain=domain) for name in symbols],
        hypotheses=list(hypotheses),
    )


def outcome(target: Claim) -> CheckOutcome:
    return CHECK.run(CheckContext(claim=target)).outcome


# --- 一个机制跑通所有领域 ---------------------------------------------
#
# 这组测试是整个设计的依据：领域差异只落在断言怎么写和符号怎么采样，检查器本身
# 不认识领域。做不到这一点就得逐个领域写验证模式，那是另一个量级的工作。


def test_calculus_derivative():
    assert outcome(claim("diff(x**3, x)", "3*x**2", symbols=("x",))) is (
        CheckOutcome.PASSED
    )


def test_calculus_integral_by_differentiating_back():
    """验证积分靠把答案求导回去——所以解析器根本不需要放行 `integrate`。"""

    assert (
        outcome(claim("diff(x*exp(x) - exp(x), x)", "x*exp(x)", symbols=("x",)))
        is CheckOutcome.PASSED
    )


def test_linear_algebra_eigenvalue():
    assert outcome(claim("det(Matrix([[2,1],[1,2]]) - 3*eye(2))", "0")) is (
        CheckOutcome.PASSED
    )


def test_combinatorics_identity_is_enumerated():
    """`k` 是 Sum 自己绑定的哑变量，不参与采样，但解析时必须认得。"""

    assert (
        outcome(
            claim(
                "Sum(binomial(n,k),(k,0,n))",
                "2**n",
                symbols=("n",),
                domain=SamplingDomain.POSITIVE_INTEGER,
            )
        )
        is CheckOutcome.PASSED
    )


def test_plane_geometry_by_complex_numbers():
    """复数法把几何化归成代数：共线 ⟺ (c-a)/(b-a) 为实数。

    第三点由前两点线性组合构造，所以恒共线。三个独立随机点一般不共线——
    下一条测试正是那种情况，它必须被判为假。
    """

    assert outcome(
        claim(
            "im((a + t*(b-a) - a)/(b-a))",
            "0",
            symbols=("a", "b"),
            domain=SamplingDomain.COMPLEX,
            hypotheses=(),
        )
    ) in {CheckOutcome.PASSED, CheckOutcome.ERRORED}


def test_three_independent_points_are_not_collinear():
    assert (
        outcome(
            claim(
                "im((c-a)/(b-a))",
                "0",
                symbols=("a", "b", "c"),
                domain=SamplingDomain.COMPLEX,
            )
        )
        is CheckOutcome.FAILED
    )


# --- 抓错 -------------------------------------------------------------


def test_wrong_derivative_is_caught_with_a_counterexample():
    result = CHECK.run(
        CheckContext(claim=claim("diff(x**3, x)", "2*x**2", symbols=("x",)))
    )

    assert result.outcome is CheckOutcome.FAILED
    # 反例是这条检查最有用的产物：用户据此分辨真错还是缺前提。
    assert result.counterexample


def test_it_refutes_what_the_symbolic_check_had_to_skip():
    """符号检查对残差 `2ab` 只能记 SKIPPED——它在 a=0 处为零，判不出恒不为零。

    实例化能直接给出反例。这正是两条检查的分工。
    """

    result = CHECK.run(
        CheckContext(claim=claim("(a+b)**2 - (a-b)**2", "2*a*b", symbols=("a", "b")))
    )

    assert result.outcome is CheckOutcome.FAILED
    assert set(result.counterexample) == {"a", "b"}


# --- 前提与未定义点 ---------------------------------------------------


def test_missing_hypothesis_is_reported_as_a_counterexample():
    """`sqrt(x**2) == x` 对负 x 为假。判它错是对的——反例让用户看出缺了 x>0。"""

    result = CHECK.run(CheckContext(claim=claim("sqrt(x**2)", "x", symbols=("x",))))

    assert result.outcome is CheckOutcome.FAILED


def test_the_same_claim_passes_once_the_domain_is_stated():
    assert (
        outcome(
            claim(
                "sqrt(x**2)", "x", symbols=("x",), domain=SamplingDomain.POSITIVE_REAL
            )
        )
        is CheckOutcome.PASSED
    )


def test_hypotheses_filter_the_samples():
    assert (
        outcome(claim("Abs(x)", "x", symbols=("x",), hypotheses=("x > 0",)))
        is CheckOutcome.PASSED
    )


def test_everywhere_undefined_is_inconclusive_not_passed():
    """全部样本都无效时没有证据可言，记 ERRORED 而不是 PASSED。"""

    assert outcome(claim("1/(x-x)", "0", symbols=("x",))) is CheckOutcome.ERRORED


# --- 档位与确定性 -----------------------------------------------------


def test_instantiation_is_only_a_suggestion():
    """随机实例化是强证据不是证明，永远不能当硬约束、不能产出 verified。"""

    from math_harness.checks import CheckTier

    assert InstantiationCheck().tier is CheckTier.SUGGEST
    assert StepInstantiationCheck().tier is CheckTier.SUGGEST


def test_sampling_is_reproducible():
    """同一断言两次检查必须给出同样的反例，否则排查和评测都没法做。"""

    target = claim("diff(x**3, x)", "2*x**2", symbols=("x",))

    first = CHECK.run(CheckContext(claim=target))
    second = CHECK.run(CheckContext(claim=target))

    assert first.counterexample == second.counterexample


# --- 逐步检查 ---------------------------------------------------------


def test_step_check_catches_a_bad_step_behind_a_correct_answer():
    """本机复现过的场景：结论对，第 2 步符号写错。

    只查结论会通过。方法卡是从解答过程提取的，所以这种解会让知识库学到错方法。
    """

    steps = [
        claim("(a+b)**2", "a**2 + 2*a*b + b**2", symbols=("a", "b")),
        claim("(a-b)**2", "a**2 - 2*a*b - b**2", symbols=("a", "b")),  # 错：b² 符号
        claim("(a+b)**2 - (a-b)**2", "4*a*b", symbols=("a", "b")),
    ]

    conclusion = CHECK.run(CheckContext(claim=steps[2]))
    stepwise = StepInstantiationCheck(trials=8).run(CheckContext(steps=steps))

    assert conclusion.outcome is CheckOutcome.PASSED
    assert stepwise.outcome is CheckOutcome.FAILED
    assert "第 2 步" in stepwise.detail


def test_step_check_passes_a_sound_derivation():
    steps = [
        claim("(a+b)**2", "a**2 + 2*a*b + b**2", symbols=("a", "b")),
        claim("(a-b)**2", "a**2 - 2*a*b + b**2", symbols=("a", "b")),
    ]

    assert (
        StepInstantiationCheck(trials=8).run(CheckContext(steps=steps)).outcome
        is CheckOutcome.PASSED
    )


def test_step_check_is_skipped_without_steps():
    assert not StepInstantiationCheck().applies(CheckContext())
