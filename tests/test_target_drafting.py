from __future__ import annotations

from types import SimpleNamespace

from math_harness.models import ExtractionStatus, SolveMathTarget, VerificationMode
from math_harness.providers.openai_target import (
    LLMTargetDraftOutput,
    OpenAIStructuredTargetDrafter,
)
from math_harness.target_drafting import (
    FallbackTargetDrafter,
    RuleBasedTargetDrafter,
)


def test_rules_draft_common_chinese_asymptotic_target():
    result = RuleBasedTargetDrafter().draft(
        "求 x→∞ 时 sqrt(x^2+x)-x 的渐进展开到 O(x^-2)"
    )

    assert result.status is ExtractionStatus.SUCCESS
    assert result.requires_confirmation is True
    assert result.target is not None
    assert result.target.expression == "sqrt(x**2+x)-x"
    assert result.target.variable == "x"
    assert result.target.point == "oo"
    assert result.target.mode is VerificationMode.ASYMPTOTIC_EXPANSION
    assert result.target.remainder_power == 2


def test_rules_draft_limit_and_infers_parameters():
    result = RuleBasedTargetDrafter().draft("求 `sin(a*x)/x` 在 x→0 时的极限")

    assert result.target is not None
    assert result.target.expression == "sin(a*x)/x"
    assert result.target.parameters == ["a"]
    assert result.target.mode is VerificationMode.LIMIT
    assert result.target.remainder_power is None


def test_rules_refuse_unsafe_or_ambiguous_expression():
    result = RuleBasedTargetDrafter().draft(
        "求 `__import__('os').system('id')` 在 x→0 时的极限"
    )

    assert result.target is None
    assert result.status is ExtractionStatus.SKIPPED
    assert result.requires_confirmation is True


class BrokenTargetDrafter:
    name = "broken"
    prompt_version = "broken-v1"
    model = "broken-model"

    def draft(self, problem: str):
        del problem
        raise TimeoutError("provider timed out")


def test_target_drafter_falls_back_to_local_rules_with_audit():
    drafter = FallbackTargetDrafter(BrokenTargetDrafter())

    result = drafter.draft("求 `sin(x)/x` 在 x→0 时的极限")

    assert result.target is not None
    assert result.status is ExtractionStatus.FALLBACK
    assert result.provider == "broken->rules"
    assert result.fallback_used is True
    assert "TimeoutError" in (result.error or "")


class FakeResponses:
    def __init__(self) -> None:
        self.kwargs = None

    def parse(self, **kwargs):
        self.kwargs = kwargs
        parsed = LLMTargetDraftOutput(
            target=SolveMathTarget(
                expression="sin(x)/x",
                point="0",
                mode=VerificationMode.LIMIT,
            ),
            confidence=0.94,
            summary="识别为两侧极限。",
        )
        return SimpleNamespace(
            output_parsed=parsed,
            output_text=parsed.model_dump_json(),
            id="resp_target_test",
            model="gpt-test",
        )


class FakeClient:
    def __init__(self) -> None:
        self.responses = FakeResponses()


def test_openai_target_drafter_uses_structured_contract():
    client = FakeClient()
    result = OpenAIStructuredTargetDrafter(client=client).draft(
        "求 sin(x)/x 在 x→0 时的极限"
    )

    assert result.target is not None
    assert result.response_id == "resp_target_test"
    assert result.confidence == 0.94
    assert client.responses.kwargs["text_format"] is LLMTargetDraftOutput
    assert client.responses.kwargs["store"] is False
