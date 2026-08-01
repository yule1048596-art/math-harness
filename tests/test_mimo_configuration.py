from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest

from math_harness.config import load_local_environment
from math_harness.extraction import (
    FallbackMethodExtractor,
    build_method_extractor_from_env,
)
from math_harness.models import (
    CandidateSolution,
    CandidateStep,
    GenerationStatus,
    SolveMathTarget,
    VerificationMode,
)
from math_harness.providers.mimo import (
    DEFAULT_MIMO_BASE_URL,
    DEFAULT_MIMO_MODEL,
    MiMoSolutionGenerator,
    MiMoStructuredMethodExtractor,
    MiMoStructuredTargetDrafter,
)
from math_harness.providers.openai import (
    LLMMethodCandidate,
    LLMMethodExtractionOutput,
)
from math_harness.providers.openai_target import LLMTargetDraftOutput
from math_harness.solving import (
    FallbackSolutionGenerator,
    build_solution_generator_from_env,
)
from math_harness.target_drafting import (
    FallbackTargetDrafter,
    build_target_drafter_from_env,
)


class FakeResponses:
    def __init__(self) -> None:
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        parsed = CandidateSolution(
            answer_text="答案为 2。",
            answer_expression="2",
            steps=[
                CandidateStep(
                    explanation="化简表达式。",
                    expression="2",
                )
            ],
            used_method_keys=[],
            assumptions=[],
            confidence=0.99,
        )
        return SimpleNamespace(
            output_text=parsed.model_dump_json(),
            id="resp_mimo_test",
            model=DEFAULT_MIMO_MODEL,
        )


class FakeClient:
    def __init__(self) -> None:
        self.responses = FakeResponses()


class RetryResponses(FakeResponses):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return SimpleNamespace(
                output_text="{}",
                id="resp_mimo_invalid",
                model=DEFAULT_MIMO_MODEL,
            )
        return super().create(**kwargs)


class RetryClient:
    def __init__(self) -> None:
        self.responses = RetryResponses()


class FakeExtractionResponses:
    def __init__(self) -> None:
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        parsed = LLMMethodExtractionOutput(
            methods=[
                LLMMethodCandidate(
                    key="direct_simplification",
                    name="直接化简",
                    goal="化简基础表达式。",
                    applicable_when=["表达式可直接计算"],
                    procedure=["执行代数化简"],
                    failure_modes=["需要额外条件"],
                    tags=["algebra"],
                    evidence=["解答直接完成了化简"],
                    confidence=0.95,
                )
            ],
            summary="提取到直接化简。",
            warnings=[],
        )
        return SimpleNamespace(
            output_text=parsed.model_dump_json(),
            id="resp_mimo_extraction",
            model=DEFAULT_MIMO_MODEL,
        )


class FakeExtractionClient:
    def __init__(self) -> None:
        self.responses = FakeExtractionResponses()


class FakeTargetResponses:
    def __init__(self) -> None:
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        parsed = LLMTargetDraftOutput(
            target=SolveMathTarget(
                expression="sin(x)/x",
                point="0",
                mode=VerificationMode.LIMIT,
            ),
            confidence=0.91,
            summary="识别为极限。",
        )
        return SimpleNamespace(
            output_text=parsed.model_dump_json(),
            id="resp_mimo_target",
            model=DEFAULT_MIMO_MODEL,
        )


class FakeTargetClient:
    def __init__(self) -> None:
        self.responses = FakeTargetResponses()


def test_mimo_solver_uses_responses_contract():
    client = FakeClient()
    generator = MiMoSolutionGenerator(
        api_key="test-key",
        client=client,
    )

    result = generator.generate("计算 1+1", [], None, 1_024)

    assert result.trace.provider == "xiaomi_mimo"
    assert result.trace.status is GenerationStatus.SUCCESS
    assert client.responses.kwargs["model"] == DEFAULT_MIMO_MODEL
    assert client.responses.kwargs["reasoning"] == {"effort": "none"}
    assert client.responses.kwargs["text"] == {"format": {"type": "json_object"}}
    assert client.responses.kwargs["store"] is False


def test_mimo_client_receives_secret_and_base_url_without_global_openai_key(
    monkeypatch,
):
    captured = {}

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))
    generator = MiMoSolutionGenerator(
        api_key="test-mimo-secret",
        base_url=DEFAULT_MIMO_BASE_URL,
    )

    generator._client_or_create()

    assert captured["api_key"] == "test-mimo-secret"
    assert captured["base_url"] == DEFAULT_MIMO_BASE_URL
    assert captured["timeout"] == 60
    assert captured["max_retries"] == 0


def test_mimo_retries_once_after_local_schema_validation_failure():
    client = RetryClient()
    generator = MiMoSolutionGenerator(
        api_key="test-key",
        client=client,
    )

    result = generator.generate("计算 1+1", [], None, 1_024)

    assert result.candidate is not None
    assert result.candidate.answer_expression == "2"
    assert client.responses.calls == 2
    assert (
        "previous JSON failed local schema validation"
        in (client.responses.kwargs["input"][1]["content"])
    )


def test_mimo_method_extractor_uses_json_object_and_local_validation():
    client = FakeExtractionClient()
    extractor = MiMoStructuredMethodExtractor(
        api_key="test-key",
        client=client,
    )

    result = extractor.extract("计算 1+1", "直接化简得到 2")

    assert result.methods[0].key == "direct_simplification"
    assert result.trace.provider == "xiaomi_mimo"
    assert client.responses.kwargs["reasoning"] == {"effort": "none"}
    assert client.responses.kwargs["text"] == {"format": {"type": "json_object"}}


def test_mimo_target_drafter_uses_json_object_and_requires_confirmation():
    client = FakeTargetClient()
    drafter = MiMoStructuredTargetDrafter(api_key="test-key", client=client)

    result = drafter.draft("求 sin(x)/x 在 x→0 时的极限")

    assert result.target is not None
    assert result.requires_confirmation is True
    assert result.provider == "xiaomi_mimo"
    assert client.responses.kwargs["reasoning"] == {"effort": "none"}
    assert client.responses.kwargs["text"] == {"format": {"type": "json_object"}}


def test_environment_builders_select_mimo_with_offline_fallback(monkeypatch):
    monkeypatch.setenv("MATH_HARNESS_SOLVER", "mimo")
    monkeypatch.setenv("MATH_HARNESS_METHOD_EXTRACTOR", "mimo")
    monkeypatch.setenv("MATH_HARNESS_TARGET_DRAFTER", "mimo")
    monkeypatch.setenv("MIMO_API_KEY", "test-key")
    monkeypatch.setenv("MATH_HARNESS_MIMO_TIMEOUT_SECONDS", "12.5")

    generator = build_solution_generator_from_env()
    extractor = build_method_extractor_from_env()
    target_drafter = build_target_drafter_from_env()

    assert isinstance(generator, FallbackSolutionGenerator)
    assert isinstance(generator.primary, MiMoSolutionGenerator)
    assert generator.primary.model == DEFAULT_MIMO_MODEL
    assert generator.primary.reasoning_effort == "none"
    assert generator.verification_repair_enabled is True
    assert generator.verification_fallback_enabled is True
    assert isinstance(extractor, FallbackMethodExtractor)
    assert isinstance(extractor.primary, MiMoStructuredMethodExtractor)
    assert extractor.primary.reasoning_effort == "none"
    assert isinstance(target_drafter, FallbackTargetDrafter)
    assert isinstance(target_drafter.primary, MiMoStructuredTargetDrafter)
    assert target_drafter.primary.reasoning_effort == "none"
    assert target_drafter.primary.timeout_seconds == 12.5


def test_explicit_env_file_loads_without_overriding_exported_values(
    tmp_path,
    monkeypatch,
):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "MIMO_API_KEY=file-secret\nMATH_HARNESS_MIMO_MODEL=mimo-v2.5-pro\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MIMO_API_KEY", "exported-secret")
    monkeypatch.delenv("MATH_HARNESS_MIMO_MODEL", raising=False)

    loaded = load_local_environment(env_file)

    assert loaded is True
    assert os.environ["MIMO_API_KEY"] == "exported-secret"
    assert os.environ["MATH_HARNESS_MIMO_MODEL"] == DEFAULT_MIMO_MODEL


def test_environment_can_disable_verification_recovery(monkeypatch):
    monkeypatch.setenv("MATH_HARNESS_SOLVER", "mimo")
    monkeypatch.setenv("MIMO_API_KEY", "test-key")
    monkeypatch.setenv("MATH_HARNESS_VERIFICATION_REPAIR", "false")
    monkeypatch.setenv("MATH_HARNESS_VERIFICATION_FALLBACK", "off")

    generator = build_solution_generator_from_env()

    assert isinstance(generator, FallbackSolutionGenerator)
    assert generator.verification_repair_enabled is False
    assert generator.verification_fallback_enabled is False


def test_invalid_verification_recovery_boolean_is_rejected(monkeypatch):
    monkeypatch.setenv("MATH_HARNESS_SOLVER", "mimo")
    monkeypatch.setenv("MIMO_API_KEY", "test-key")
    monkeypatch.setenv("MATH_HARNESS_VERIFICATION_REPAIR", "sometimes")

    with pytest.raises(
        ValueError,
        match="MATH_HARNESS_VERIFICATION_REPAIR must be a boolean",
    ):
        build_solution_generator_from_env()
