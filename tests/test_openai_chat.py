from __future__ import annotations

from types import SimpleNamespace

from math_harness.conversation import ConversationContext
from math_harness.models import (
    ConversationMessage,
    ConversationMessageKind,
    ConversationRole,
    Workspace,
    utc_now,
)
from math_harness.providers.openai_chat import OpenAIConversationResponder


class FakeResponses:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            id="response-chat-1",
            model="mimo-v2.5-pro",
            output_text="  前文讨论的是等价无穷小。  ",
        )


class FakeClient:
    def __init__(self) -> None:
        self.responses = FakeResponses()


def test_openai_chat_preserves_message_roles_and_bounds_context_payload():
    now = utc_now()
    workspace = Workspace(
        id="workspace-1",
        name="极限",
        description="只讨论极限",
        created_at=now,
    )
    messages = [
        ConversationMessage(
            id="message-1",
            workspace_id=workspace.id,
            conversation_id="conversation-1",
            turn_id="turn-1",
            ordinal=1,
            role=ConversationRole.USER,
            kind=ConversationMessageKind.CHAT,
            content="我们刚才讨论了什么？",
            created_at=now,
        ),
        ConversationMessage(
            id="message-2",
            workspace_id=workspace.id,
            conversation_id="conversation-1",
            turn_id="turn-1",
            ordinal=2,
            role=ConversationRole.ASSISTANT,
            kind=ConversationMessageKind.CHAT,
            content="讨论了等价无穷小。",
            created_at=now,
        ),
    ]
    client = FakeClient()
    responder = OpenAIConversationResponder(
        model="mimo-v2.5-pro",
        reasoning_effort="none",
        provider_name="xiaomi_mimo",
        client=client,
    )

    result = responder.respond(
        ConversationContext(
            workspace=workspace,
            summary="较早讨论过极限定义。",
            recent_messages=messages,
            trusted_methods=[],
        ),
        "请继续解释。",
        1_024,
    )

    assert result.content == "前文讨论的是等价无穷小。"
    assert result.provider == "xiaomi_mimo"
    assert result.response_id == "response-chat-1"
    call = client.responses.calls[0]
    assert call["model"] == "mimo-v2.5-pro"
    assert call["reasoning"] == {"effort": "none"}
    assert call["max_output_tokens"] == 1_024
    assert call["store"] is False
    inputs = call["input"]
    assert [item["role"] for item in inputs] == [
        "system",
        "user",
        "user",
        "assistant",
        "user",
    ]
    assert "conversation_summary" in inputs[1]["content"]
    assert "recent_messages" not in inputs[1]["content"]
    assert inputs[-1]["content"] == "请继续解释。"


def test_openai_chat_marks_interrupted_assistant_history_as_incomplete():
    now = utc_now()
    workspace = Workspace(
        id="workspace-1",
        name="极限",
        description="只讨论极限",
        created_at=now,
    )
    interrupted = ConversationMessage(
        id="message-1",
        workspace_id=workspace.id,
        conversation_id="conversation-1",
        turn_id="turn-1",
        ordinal=1,
        role=ConversationRole.ASSISTANT,
        kind=ConversationMessageKind.CHAT,
        content="先使用洛必达法则，",
        generation_error="RuntimeError: connection reset",
        created_at=now,
    )
    client = FakeClient()
    responder = OpenAIConversationResponder(
        model="mimo-v2.5-pro",
        reasoning_effort="none",
        provider_name="xiaomi_mimo",
        client=client,
    )

    responder.respond(
        ConversationContext(
            workspace=workspace,
            summary="",
            recent_messages=[interrupted],
            trusted_methods=[],
        ),
        "请重新回答。",
        1_024,
    )

    inputs = client.responses.calls[0]["input"]
    assistant = next(item for item in inputs if item["role"] == "assistant")
    assert "interrupted and is incomplete" in assistant["content"]
    assert assistant["content"].endswith(interrupted.content)
