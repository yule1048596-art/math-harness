from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

from math_harness.memory import (
    DisabledMemoryExtractor,
    MemoryExtractionCandidate,
    MemoryExtractionOutput,
    OpenAIMemoryExtractor,
    build_memory_extractor_from_env,
)
from math_harness.models import (
    ConversationMessage,
    ConversationMessageKind,
    ConversationRole,
    MemoryItem,
    MemoryKind,
    MemorySource,
    utc_now,
)


def _message(content: str = "我喜欢先讲直觉") -> ConversationMessage:
    return ConversationMessage(
        id="message-1",
        workspace_id="workspace-1",
        conversation_id="conversation-1",
        turn_id="turn-1",
        ordinal=1,
        role=ConversationRole.USER,
        kind=ConversationMessageKind.CHAT,
        content=content,
        created_at=utc_now(),
    )


def _existing_memory() -> MemoryItem:
    now = utc_now()
    return MemoryItem(
        id="memory-1",
        workspace_id="workspace-1",
        kind=MemoryKind.EXPLANATION_PREFERENCE,
        content="用户偏好精简讲解",
        tags=["偏好"],
        source=MemorySource.MANUAL,
        created_at=now,
        updated_at=now,
    )


class RetryJSONResponses:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs):
        self.calls.append(deepcopy(kwargs))
        if len(self.calls) == 1:
            output = '{"candidates":"invalid"}'
        else:
            output = MemoryExtractionOutput(
                candidates=[
                    MemoryExtractionCandidate(
                        kind=MemoryKind.EXPLANATION_PREFERENCE,
                        content="用户偏好先讲直觉",
                        tags=["偏好"],
                        evidence="我喜欢先讲直觉",
                        source_message_id="message-1",
                        replaces_memory_id="memory-1",
                    )
                ]
            ).model_dump_json()
        return SimpleNamespace(
            id=f"response-{len(self.calls)}",
            model="mimo-memory-test",
            output_text=output,
            usage=SimpleNamespace(input_tokens=42, output_tokens=18),
        )


class RetryJSONClient:
    def __init__(self) -> None:
        self.responses = RetryJSONResponses()


class ParsedResponses:
    def __init__(self) -> None:
        self.call: dict[str, object] | None = None

    def parse(self, **kwargs):
        self.call = kwargs
        return SimpleNamespace(
            id="response-parsed",
            model="openai-memory-test",
            output_parsed=MemoryExtractionOutput(candidates=[]),
            usage=SimpleNamespace(input_tokens=12, output_tokens=3),
        )


class ParsedClient:
    def __init__(self) -> None:
        self.responses = ParsedResponses()


def test_mimo_memory_extractor_repairs_json_once_and_uses_compatible_parameters():
    client = RetryJSONClient()
    extractor = OpenAIMemoryExtractor(
        model="mimo-memory-test",
        reasoning_effort="none",
        api_key="test-key",
        base_url="https://example.invalid/v1",
        provider_name="xiaomi_mimo",
        structured_output_mode="json_object",
        client=client,
    )

    result = extractor.extract([_message()], [_existing_memory()])

    assert len(client.responses.calls) == 2
    assert result.provider == "xiaomi_mimo"
    assert result.model == "mimo-memory-test"
    assert result.input_tokens == 42
    assert result.output_tokens == 18
    assert result.candidates[0].replaces_memory_id == "memory-1"
    for call in client.responses.calls:
        assert call["model"] == "mimo-memory-test"
        assert call["reasoning"] == {"effort": "none"}
        assert call["max_output_tokens"] == 1_000
        assert call["store"] is False
        assert call["text"] == {"format": {"type": "json_object"}}
        assert "temperature" not in call
    assert (
        "previous output failed validation"
        in client.responses.calls[1]["input"][1]["content"]
    )
    prompt = client.responses.calls[0]["input"][1]["content"]
    assert "message-1" in prompt
    assert "memory-1" in prompt
    assert "assistant" not in prompt


def test_openai_memory_extractor_uses_native_parsed_schema():
    client = ParsedClient()
    extractor = OpenAIMemoryExtractor(
        model="openai-memory-test",
        reasoning_effort="low",
        api_key="test-key",
        structured_output_mode="json_schema",
        client=client,
    )

    result = extractor.extract([_message()], [])

    assert result.candidates == []
    assert result.input_tokens == 12
    call = client.responses.call
    assert call is not None
    assert call["text_format"] is MemoryExtractionOutput
    assert "text" not in call
    assert "temperature" not in call


def test_memory_builder_disables_unconfigured_mimo_and_supports_separate_model(
    monkeypatch,
):
    monkeypatch.setenv("MATH_HARNESS_MEMORY_EXTRACTOR", "mimo")
    monkeypatch.delenv("MIMO_API_KEY", raising=False)

    assert isinstance(build_memory_extractor_from_env(), DisabledMemoryExtractor)

    monkeypatch.setenv("MIMO_API_KEY", "test-key")
    monkeypatch.setenv("MATH_HARNESS_MIMO_MODEL", "chat-model")
    monkeypatch.setenv("MATH_HARNESS_MIMO_MEMORY_MODEL", "memory-model")
    configured = build_memory_extractor_from_env()
    assert isinstance(configured, OpenAIMemoryExtractor)
    assert configured.model == "memory-model"
    assert configured.name == "xiaomi_mimo"
    assert configured.reasoning_effort == "none"
