from __future__ import annotations

from types import SimpleNamespace

from math_harness.extraction import FallbackMethodExtractor
from math_harness.models import (
    ExampleCreate,
    ExtractionStatus,
    MathPayload,
    MethodKind,
    VerificationMode,
    WorkspaceCreate,
)
from math_harness.providers.openai import (
    LLMMethodCandidate,
    LLMMethodExtractionOutput,
    OpenAIStructuredMethodExtractor,
)
from math_harness.service import MathHarnessService


class FakeResponses:
    def __init__(self) -> None:
        self.kwargs = None

    def parse(self, **kwargs):
        self.kwargs = kwargs
        parsed = LLMMethodExtractionOutput(
            methods=[
                LLMMethodCandidate(
                    key="custom_scale_matching",
                    name="匹配中间尺度",
                    goal="在两个局部区域之间寻找共同尺度。",
                    applicable_when=["存在相互重叠的局部近似"],
                    procedure=["选择中间变量", "匹配共同项"],
                    failure_modes=["两个有效区间没有重叠"],
                    tags=["asymptotic", "matching"],
                    evidence=["解答中明确使用了中间尺度匹配"],
                    confidence=0.91,
                )
            ],
            summary="提取到一个方法。",
            warnings=[],
        )
        return SimpleNamespace(
            output_parsed=parsed,
            output_text=parsed.model_dump_json(),
            id="resp_test_123",
            model="gpt-5.6-terra-2026-07-01",
        )


class FakeClient:
    def __init__(self) -> None:
        self.responses = FakeResponses()


class BrokenExtractor:
    name = "broken"
    model = "test-model"
    prompt_version = "broken-v1"

    def extract(self, problem: str, solution: str, hint: str | None = None):
        raise TimeoutError("provider timed out")


def test_openai_extractor_uses_structured_responses_contract():
    client = FakeClient()
    extractor = OpenAIStructuredMethodExtractor(client=client)

    result = extractor.extract("一道题", "用中间尺度匹配", None)

    assert [method.key for method in result.methods] == ["custom_scale_matching"]
    assert result.trace.status is ExtractionStatus.SUCCESS
    assert result.trace.response_id == "resp_test_123"
    assert result.trace.evidence_by_method["custom_scale_matching"]
    assert client.responses.kwargs["model"] == "gpt-5.6-terra"
    assert client.responses.kwargs["reasoning"] == {"effort": "low"}
    assert client.responses.kwargs["text_format"] is LLMMethodExtractionOutput
    assert client.responses.kwargs["store"] is False


def test_custom_llm_method_and_trace_are_persisted(tmp_path):
    client = FakeClient()
    service = MathHarnessService(
        tmp_path,
        extractor=OpenAIStructuredMethodExtractor(client=client),
    )
    workspace = service.create_workspace(WorkspaceCreate(name="匹配渐近展开"))
    request = ExampleCreate(
        problem="验证一个匹配方法案例",
        solution="解答中明确使用了中间尺度匹配。",
        math_payload=MathPayload(
            expression="x + 1",
            expected="x + 1",
            mode=VerificationMode.EXACT_EQUIVALENCE,
        ),
    )

    result = service.ingest_example(workspace.id, request)
    reloaded = service.get_example(workspace.id, result.example.id)

    assert result.learned_methods[0].key == "custom_scale_matching"
    assert reloaded.extraction is not None
    assert reloaded.extraction.provider == "openai"
    assert reloaded.extraction.response_id == "resp_test_123"
    assert reloaded.extraction.confidence_by_method["custom_scale_matching"] == 0.91


def test_provider_failure_falls_back_to_rules_with_audit():
    extractor = FallbackMethodExtractor(BrokenExtractor())

    result = extractor.extract("求根式之差", "先有理化", None)

    assert result.methods[0].key == MethodKind.RATIONALIZATION
    assert result.trace.status is ExtractionStatus.FALLBACK
    assert result.trace.fallback_used is True
    assert result.trace.provider == "broken->rules"
    assert "TimeoutError" in (result.trace.error or "")
