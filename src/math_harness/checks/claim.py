from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, field_validator

# 断言：检查流水线的通用单位。
#
# 这个项目原本只有 `SolveMathTarget` 一种目标形状，而它是渐进估计的形状——带趋近点、
# 方向、余项阶。表达不了「证明三点共线」「有多少种取法」「求特征值」。
#
# 断言把这些统一成一件事：**一个可实例化的命题**。领域差异只落在两处——断言怎么写，
# 以及自由符号怎么采样。检查器本身领域无关。
#
# 实测这个抽象跑通了微积分、线性代数、组合数学、平面几何（复数法）和逻辑：
#   d/dx x³ = 3x²                    随机代入实数
#   diff(∫x·eˣ) = x·eˣ               随机代入实数
#   det(A - 3I) = 0                  无自由变量
#   Σ C(n,k) = 2ⁿ                    小 n 枚举
#   im((c-a)/(b-a)) = 0              随机复数坐标
#
# 「复数法求解平面几何」是这条路的典范：它本身把几何化归成代数，一化归就可实例化。


class ClaimKind(StrEnum):
    #: `lhs` 与 `rhs` 相等。计算题、恒等式、代回验证都是这一类。
    EQUALITY = "equality"
    #: `lhs` 恒为真。几何谓词（共线、垂直）与逻辑命题是这一类。
    PREDICATE = "predicate"


class SamplingDomain(StrEnum):
    """自由符号的取值域，决定采样器怎么造实例。"""

    REAL = "real"
    POSITIVE_REAL = "positive_real"
    NONZERO_REAL = "nonzero_real"
    INTEGER = "integer"
    POSITIVE_INTEGER = "positive_integer"
    COMPLEX = "complex"
    #: 布尔取值，用于命题逻辑的真值枚举。
    BOOLEAN = "boolean"


class Binding(BaseModel):
    """一个自由符号该怎么取值。"""

    symbol: str = Field(min_length=1, max_length=64)
    domain: SamplingDomain = SamplingDomain.REAL
    #: 整数与枚举类取值域的上下界；连续域用它限制采样幅度。
    low: int = Field(default=-20, ge=-10_000, le=10_000)
    high: int = Field(default=20, ge=-10_000, le=10_000)

    @field_validator("symbol")
    @classmethod
    def validate_symbol(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped.isidentifier():
            raise ValueError("binding symbol must be a plain identifier")
        return stripped

    @property
    def is_discrete(self) -> bool:
        """离散取值域可以穷举，不必随机采样——枚举比抽样强。"""

        return self.domain in {
            SamplingDomain.INTEGER,
            SamplingDomain.POSITIVE_INTEGER,
            SamplingDomain.BOOLEAN,
        }


class Claim(BaseModel):
    """一条待检验的命题。

    `hypotheses` 是前提，实例化时必须满足，不满足的样本要丢弃重采——否则
    `sqrt(x**2) == x` 这种在 `x > 0` 下成立的断言会被随机负数误判为错。
    """

    kind: ClaimKind = ClaimKind.EQUALITY
    lhs: str = Field(min_length=1, max_length=2_000)
    rhs: str | None = Field(default=None, max_length=2_000)
    bindings: list[Binding] = Field(default_factory=list, max_length=12)
    hypotheses: list[str] = Field(default_factory=list, max_length=8)
    #: 人类可读的说明，出现在审计与失败提示里。
    description: str = Field(default="", max_length=500)

    @field_validator("rhs")
    @classmethod
    def normalize_rhs(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    def model_post_init(self, _context: object) -> None:
        if self.kind is ClaimKind.EQUALITY and self.rhs is None:
            raise ValueError("equality claim requires rhs")

    @property
    def symbols(self) -> list[str]:
        return [binding.symbol for binding in self.bindings]

    @property
    def is_closed(self) -> bool:
        """没有自由符号——直接算一次即可，不需要采样。"""

        return not self.bindings
