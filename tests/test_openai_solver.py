from __future__ import annotations

from types import SimpleNamespace

from math_harness.models import (
    CandidateSolution,
    CandidateStep,
    GenerationStatus,
    SolveMathTarget,
)
from math_harness.providers.openai_solver import OpenAISolutionGenerator
from math_harness.solving import FallbackSolutionGenerator


class FakeResponses:
    def __init__(self) -> None:
        self.kwargs = None

    def parse(self, **kwargs):
        self.kwargs = kwargs
        parsed = CandidateSolution(
            answer_text="候选答案为 1/2 - 1/(8*x)。",
            answer_expression="1/2 - 1/(8*x)",
            steps=[
                CandidateStep(
                    explanation="先有理化，再在无穷远展开。",
                    expression="1/2 - 1/(8*x)",
                )
            ],
            used_method_keys=[],
            assumptions=["x → oo"],
            confidence=0.93,
        )
        return SimpleNamespace(
            output_parsed=parsed,
            output_text=parsed.model_dump_json(),
            id="resp_solver_123",
            model="gpt-5.6-sol-2026-07-01",
        )


class FakeClient:
    def __init__(self) -> None:
        self.responses = FakeResponses()


class BrokenResponses:
    def parse(self, **kwargs):
        del kwargs
        raise TimeoutError("provider timed out")


class BrokenClient:
    def __init__(self) -> None:
        self.responses = BrokenResponses()


def _target() -> SolveMathTarget:
    return SolveMathTarget(
        expression="sqrt(x**2 + x) - x",
        point="oo",
        remainder_power=2,
    )


def test_openai_solver_uses_structured_responses_contract():
    client = FakeClient()
    generator = OpenAISolutionGenerator(client=client)

    result = generator.generate(
        "求根式之差的渐进展开",
        [],
        _target(),
        2_048,
    )

    assert result.candidate is not None
    assert result.trace.status is GenerationStatus.SUCCESS
    assert result.trace.response_id == "resp_solver_123"
    assert client.responses.kwargs["model"] == "gpt-5.6-sol"
    assert client.responses.kwargs["reasoning"] == {"effort": "medium"}
    assert client.responses.kwargs["text_format"] is CandidateSolution
    assert client.responses.kwargs["max_output_tokens"] == 2_048
    assert client.responses.kwargs["store"] is False


def test_openai_failure_falls_back_to_sympy_with_audit():
    generator = FallbackSolutionGenerator(
        OpenAISolutionGenerator(client=BrokenClient())
    )

    result = generator.generate(
        "求根式之差的渐进展开",
        [],
        _target(),
        2_048,
    )

    assert result.candidate is not None
    assert result.trace.status is GenerationStatus.FALLBACK
    assert result.trace.fallback_used is True
    assert result.trace.provider == "openai->sympy"
    assert result.trace.model == "gpt-5.6-sol"
    assert "TimeoutError" in (result.trace.error or "")
