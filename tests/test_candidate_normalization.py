from __future__ import annotations

from math_harness.models import (
    CandidateSolution,
    CandidateStep,
    SolveMathTarget,
    VerificationMode,
)
from math_harness.normalization import CandidateSolutionNormalizer


def _candidate(expression: str) -> CandidateSolution:
    return CandidateSolution(
        answer_text=expression,
        answer_expression=expression,
        steps=[CandidateStep(explanation="测试表达式规范化。", expression=expression)],
        used_method_keys=[],
        assumptions=[],
        confidence=0.8,
    )


def test_normalizer_strips_nested_trailing_big_o():
    result = CandidateSolutionNormalizer().normalize(
        _candidate("2/x - 2/x**2 + O(x**(-3))"),
        SolveMathTarget(
            expression="log(1 + 2/x)",
            point="oo",
            remainder_power=3,
        ),
    )

    assert result.candidate.answer_expression == "2/x - 2/x**2"
    assert result.actions == ("strip_trailing_order_term",)


def test_normalizer_converts_supported_parser_notation():
    result = CandidateSolutionNormalizer().normalize(
        _candidate(r"$ln(1+x) + e^2 − π$"),
        SolveMathTarget(
            expression="log(1+x) + E**2 - pi",
            point="0",
            remainder_power=1,
        ),
    )

    assert result.candidate.answer_expression == "log(1+x) + E**2 - pi"
    assert result.actions == (
        "strip_math_delimiters",
        "normalize_unicode_math",
        "normalize_power_operator",
        "normalize_log_function",
        "normalize_e_constant",
    )


def test_normalizer_preserves_declared_e_symbol():
    result = CandidateSolutionNormalizer().normalize(
        _candidate("e^2"),
        SolveMathTarget(
            expression="e**2",
            variable="x",
            parameters=["e"],
            point="0",
            remainder_power=1,
        ),
    )

    assert result.candidate.answer_expression == "e**2"
    assert "normalize_e_constant" not in result.actions


def test_normalizer_does_not_remove_non_trailing_order_term():
    result = CandidateSolutionNormalizer().normalize(
        _candidate("O(x) + x"),
        None,
    )

    assert result.candidate.answer_expression == "O(x) + x"
    assert result.actions == ()


def test_normalizer_preserves_big_o_for_exact_equivalence():
    result = CandidateSolutionNormalizer().normalize(
        _candidate("1 + O(x)"),
        SolveMathTarget(
            expression="1",
            point="0",
            mode=VerificationMode.EXACT_EQUIVALENCE,
        ),
    )

    assert result.candidate.answer_expression == "1 + O(x)"
    assert "strip_trailing_order_term" not in result.actions
