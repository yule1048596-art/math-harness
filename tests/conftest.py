from __future__ import annotations

import pytest

from math_harness.models import ExampleCreate, MathPayload


@pytest.fixture
def verified_asymptotic_example() -> ExampleCreate:
    return ExampleCreate(
        problem="求 x→∞ 时 sqrt(x^2+x)-x 的渐进展开到 O(x^-2)",
        solution=(
            "先乘共轭式有理化，再令 t=1/x，并使用泰勒展开，得到 1/2-1/(8x)+O(x^-2)。"
        ),
        tags=["渐进估计", "根式", "无穷远"],
        math_payload=MathPayload(
            expression="sqrt(x**2 + x) - x",
            expected="1/2 - 1/(8*x)",
            variable="x",
            point="oo",
            remainder_power=2,
        ),
    )
