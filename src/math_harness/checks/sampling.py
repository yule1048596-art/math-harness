from __future__ import annotations

import itertools
import random
from collections.abc import Iterator

import sympy as sp

from math_harness.checks.claim import Binding, SamplingDomain

# 按取值域造实例。
#
# 领域差异在整套检查里只落在两处：断言怎么写，以及自由符号怎么采样。这里是后者——
# 几何要复数坐标、组合要小正整数、逻辑要真值。检查器本身不认识领域。
#
# 离散取值域走**穷举**而不是抽样：能枚举就没必要碰运气。

#: 采样固定种子，让同一个断言的检查结果可复现——评测和排查都需要这个。
DEFAULT_SEED = 20260803


def _rational(rng: random.Random, low: int, high: int) -> sp.Expr:
    """取有理数而不是浮点，避免浮点误差被误当成反例。"""

    return sp.Rational(rng.randint(low, high), rng.randint(1, 7))


def sample_value(binding: Binding, rng: random.Random) -> sp.Expr:
    domain = binding.domain
    if domain is SamplingDomain.REAL:
        return _rational(rng, binding.low, binding.high)
    if domain is SamplingDomain.POSITIVE_REAL:
        return _rational(rng, 1, max(binding.high, 1))
    if domain is SamplingDomain.NONZERO_REAL:
        value = _rational(rng, binding.low, binding.high)
        return value if value != 0 else sp.Integer(1)
    if domain is SamplingDomain.INTEGER:
        return sp.Integer(rng.randint(binding.low, binding.high))
    if domain is SamplingDomain.POSITIVE_INTEGER:
        return sp.Integer(rng.randint(max(binding.low, 1), max(binding.high, 1)))
    if domain is SamplingDomain.COMPLEX:
        return _rational(rng, binding.low, binding.high) + sp.I * _rational(
            rng, binding.low, binding.high
        )
    if domain is SamplingDomain.BOOLEAN:
        return sp.true if rng.random() < 0.5 else sp.false
    raise ValueError(f"unsupported sampling domain: {domain}")


def discrete_values(binding: Binding) -> list[sp.Expr]:
    """离散取值域的全部取值，用于穷举。"""

    if binding.domain is SamplingDomain.BOOLEAN:
        return [sp.true, sp.false]
    low = binding.low
    high = binding.high
    if binding.domain is SamplingDomain.POSITIVE_INTEGER:
        low = max(low, 1)
        high = max(high, 1)
    return [sp.Integer(value) for value in range(low, high + 1)]


def iter_assignments(
    bindings: list[Binding],
    trials: int,
    seed: int = DEFAULT_SEED,
    max_enumerated: int = 256,
) -> Iterator[dict[str, sp.Expr]]:
    """产出一批取值。

    全部是离散取值域且组合数不大时**穷举**——穷举是完全的，抽样不是。否则随机采样。
    """

    if not bindings:
        yield {}
        return

    if all(binding.is_discrete for binding in bindings):
        grids = [discrete_values(binding) for binding in bindings]
        total = 1
        for grid in grids:
            total *= len(grid)
        if total <= max_enumerated:
            for combination in itertools.product(*grids):
                yield {
                    binding.symbol: value
                    for binding, value in zip(bindings, combination, strict=True)
                }
            return

    rng = random.Random(seed)
    for _ in range(trials):
        yield {binding.symbol: sample_value(binding, rng) for binding in bindings}
