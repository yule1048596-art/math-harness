from __future__ import annotations

import re

from math_harness.models import ProblemKind


def classify_problem(problem: str) -> ProblemKind:
    normalized = problem.lower().replace(" ", "")
    asymptotic_markers = (
        "渐进",
        "asymptotic",
        "展开",
        "主导项",
        "等价无穷小",
        "big-o",
        "little-o",
        "\\sim",
    )
    if any(marker in normalized for marker in asymptotic_markers):
        return ProblemKind.ASYMPTOTIC
    if any(marker in normalized for marker in ("极限", "limit", "\\lim", "→", "\\to")):
        return ProblemKind.LIMIT
    if re.search(r"[=<>]", problem) or any(
        marker in normalized for marker in ("方程", "不等式")
    ):
        return ProblemKind.ALGEBRA
    return ProblemKind.UNKNOWN


def infer_query_tags(problem: str) -> list[str]:
    normalized = problem.lower()
    tags: list[str] = []
    marker_tags = {
        "asymptotic": ("渐进", "展开", "asymptotic", "主导项"),
        "radical": ("根式", "根号", "sqrt"),
        "infinity": ("无穷", "∞", "infty", "infinity"),
        "factorial": ("阶乘", "factorial", "gamma", "Γ"),
        "integral": ("积分", "integral", "\\int"),
        "cancellation": ("抵消", "相减", "cancellation"),
        "parameter": ("参数", "parameter"),
    }
    for tag, markers in marker_tags.items():
        if any(marker in normalized for marker in markers):
            tags.append(tag)
    return tags
