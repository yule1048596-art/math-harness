from __future__ import annotations

import os
import sys
from types import SimpleNamespace

from math_harness.config import load_local_environment
from math_harness.extraction import (
    FallbackMethodExtractor,
    build_method_extractor_from_env,
)
from math_harness.models import CandidateSolution, CandidateStep, GenerationStatus
from math_harness.providers.mimo import (
    DEFAULT_MIMO_BASE_URL,
    DEFAULT_MIMO_MODEL,
    MiMoSolutionGenerator,
    MiMoStructuredMethodExtractor,
)
from math_harness.providers.openai import (
    LLMMethodCandidate,
    LLMMethodExtractionOutput,
)
from math_harness.solving import (
    FallbackSolutionGenerator,
    build_solution_generator_from_env,
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


def test_environment_builders_select_mimo_with_offline_fallback(monkeypatch):
    monkeypatch.setenv("MATH_HARNESS_SOLVER", "mimo")
    monkeypatch.setenv("MATH_HARNESS_METHOD_EXTRACTOR", "mimo")
    monkeypatch.setenv("MIMO_API_KEY", "test-key")

    generator = build_solution_generator_from_env()
    extractor = build_method_extractor_from_env()

    assert isinstance(generator, FallbackSolutionGenerator)
    assert isinstance(generator.primary, MiMoSolutionGenerator)
    assert generator.primary.model == DEFAULT_MIMO_MODEL
    assert generator.primary.reasoning_effort == "none"
    assert isinstance(extractor, FallbackMethodExtractor)
    assert isinstance(extractor.primary, MiMoStructuredMethodExtractor)
    assert extractor.primary.reasoning_effort == "none"


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
