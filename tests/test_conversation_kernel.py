from __future__ import annotations

import sqlite3

import pytest

from math_harness.conversation import ChatGeneration, ConversationContext
from math_harness.errors import RecordNotFound
from math_harness.models import (
    CandidateSolution,
    CandidateStep,
    ConversationCreate,
    ConversationTurnRequest,
    ExampleCreate,
    GenerationStatus,
    KnowledgeStatus,
    MathPayload,
    SolutionGenerationResult,
    SolutionGenerationTrace,
    SolveMathTarget,
    SolveRequest,
    WorkspaceCreate,
)
from math_harness.service import MathHarnessService


class RecordingResponder:
    name = "recording-chat"
    model = "chat-test"
    prompt_version = "recording-chat-v1"

    def __init__(self) -> None:
        self.calls: list[tuple[ConversationContext, str, int]] = []

    def respond(self, context, message, max_output_tokens):
        self.calls.append((context, message, max_output_tokens))
        return ChatGeneration(
            content=f"回复：{message}",
            provider=self.name,
            model=self.model,
            prompt_version=self.prompt_version,
        )


class ContextAwareGenerator:
    name = "context-generator"
    model = "solve-test"
    prompt_version = "context-generator-v1"

    def __init__(self) -> None:
        self.contexts: list[dict[str, object]] = []

    def generate(self, problem, matches, math_target, max_output_tokens):
        del problem, matches, math_target, max_output_tokens
        raise AssertionError("conversation solve should use generate_with_context")

    def generate_with_context(
        self,
        problem,
        matches,
        math_target,
        max_output_tokens,
        conversation_context,
    ):
        del problem, matches, math_target, max_output_tokens
        self.contexts.append(conversation_context)
        candidate = CandidateSolution(
            answer_text="先有理化，再展开。",
            answer_expression="1/2 - 1/(8*x)",
            steps=[
                CandidateStep(
                    explanation="有理化后在无穷远展开。",
                    expression="1/2 - 1/(8*x)",
                )
            ],
            used_method_keys=[],
            assumptions=[],
            confidence=0.9,
        )
        return SolutionGenerationResult(
            candidate=candidate,
            trace=SolutionGenerationTrace(
                provider=self.name,
                model=self.model,
                prompt_version=self.prompt_version,
                status=GenerationStatus.SUCCESS,
            ),
        )


def test_chat_turns_are_persistent_ordered_idempotent_and_workspace_isolated(tmp_path):
    responder = RecordingResponder()
    service = MathHarnessService(tmp_path, conversation_responder=responder)
    workspace_a = service.create_workspace(WorkspaceCreate(name="空间 A"))
    workspace_b = service.create_workspace(WorkspaceCreate(name="空间 B"))
    conversation = service.create_conversation(
        workspace_a.id,
        ConversationCreate(),
    )

    first = service.send_conversation_turn(
        workspace_a.id,
        conversation.id,
        ConversationTurnRequest(message="你好", turn_id="turn-1"),
    )
    repeated = service.send_conversation_turn(
        workspace_a.id,
        conversation.id,
        ConversationTurnRequest(message="你好", turn_id="turn-1"),
    )
    second = service.send_conversation_turn(
        workspace_a.id,
        conversation.id,
        ConversationTurnRequest(message="继续刚才的话题", turn_id="turn-2"),
    )

    assert first.user_message.ordinal == 1
    assert first.assistant_message.ordinal == 2
    assert repeated.user_message.id == first.user_message.id
    assert repeated.assistant_message.id == first.assistant_message.id
    assert second.user_message.ordinal == 3
    assert second.conversation.title == "你好"
    assert len(responder.calls) == 2
    assert [item.content for item in responder.calls[1][0].recent_messages] == [
        "你好",
        "回复：你好",
    ]
    assert service.list_conversations(workspace_b.id) == []
    with pytest.raises(RecordNotFound):
        service.list_conversation_messages(workspace_b.id, conversation.id)
    assert [
        message.role.value
        for message in service.list_conversation_messages(
            workspace_a.id,
            conversation.id,
        )
    ] == ["user", "assistant", "user", "assistant"]


def test_chat_context_uses_only_promoted_workspace_methods(tmp_path):
    responder = RecordingResponder()
    service = MathHarnessService(tmp_path, conversation_responder=responder)
    workspace = service.create_workspace(WorkspaceCreate(name="可信上下文"))
    service.ingest_example(
        workspace.id,
        ExampleCreate(
            problem="求根式差的渐进展开",
            solution="先有理化，再令 t=1/x 展开。",
            reviewed=True,
            math_payload=MathPayload(
                expression="sqrt(x**2+x)-x",
                expected="1/2-1/(8*x)",
                point="oo",
                remainder_power=2,
            ),
        ),
    )
    conversation = service.create_conversation(workspace.id, ConversationCreate())

    service.send_conversation_turn(
        workspace.id,
        conversation.id,
        ConversationTurnRequest(message="根式抵消通常怎么处理？"),
    )

    keys = {match.method.key for match in responder.calls[-1][0].trusted_methods}
    assert "rationalization" in keys
    assert all(
        match.method.status is KnowledgeStatus.PROMOTED
        for match in responder.calls[-1][0].trusted_methods
    )


def test_verified_conversation_solve_links_attempt_and_pending_draft(tmp_path):
    generator = ContextAwareGenerator()
    service = MathHarnessService(tmp_path, generator=generator)
    workspace = service.create_workspace(WorkspaceCreate(name="多轮求解"))
    conversation = service.create_conversation(workspace.id, ConversationCreate())
    service.send_conversation_turn(
        workspace.id,
        conversation.id,
        ConversationTurnRequest(message="我们先讨论根式抵消。"),
    )

    result = service.send_conversation_turn(
        workspace.id,
        conversation.id,
        ConversationTurnRequest(
            message="现在求 sqrt(x^2+x)-x 的展开",
            math_target=SolveMathTarget(
                expression="sqrt(x**2+x)-x",
                point="oo",
                remainder_power=2,
            ),
        ),
    )

    assert result.attempt is not None
    assert result.attempt.status.value == "verified"
    assert result.knowledge_draft is not None
    assert result.knowledge_draft.status is KnowledgeStatus.PENDING_REVIEW
    assert result.assistant_message.attempt_id == result.attempt.id
    assert result.assistant_message.knowledge_draft_id == result.knowledge_draft.id
    assert result.assistant_message.verification_status.value == "verified"
    assert generator.contexts
    recent = generator.contexts[-1]["recent_messages"]
    assert [item["content"] for item in recent] == [
        "我们先讨论根式抵消。",
        service.list_conversation_messages(workspace.id, conversation.id)[1].content,
    ]


def test_conversation_rolls_old_messages_into_bounded_summary(tmp_path):
    responder = RecordingResponder()
    service = MathHarnessService(tmp_path, conversation_responder=responder)
    workspace = service.create_workspace(WorkspaceCreate(name="滚动摘要"))
    conversation = service.create_conversation(workspace.id, ConversationCreate())

    final = None
    for index in range(9):
        final = service.send_conversation_turn(
            workspace.id,
            conversation.id,
            ConversationTurnRequest(message=f"第 {index + 1} 轮"),
        )

    assert final is not None and final.summary_updated is True
    refreshed = service.get_conversation(workspace.id, conversation.id)
    assert refreshed.message_count == 18
    assert refreshed.summary_through_ordinal == 10
    assert "第 1 轮" in refreshed.summary
    assert "回复：第 5 轮" in refreshed.summary
    assert len(responder.calls[-1][0].recent_messages) <= 12


def test_pre_v010_attempts_are_wrapped_in_a_legacy_conversation(tmp_path):
    service = MathHarnessService(tmp_path)
    workspace = service.create_workspace(WorkspaceCreate(name="旧工作区"))
    service.solve_problem(
        workspace.id,
        SolveRequest(
            problem="求 x+1 的精确化简",
            math_target=SolveMathTarget(
                expression="x+1",
                mode="exact_equivalence",
            ),
        ),
    )
    database = service.workspaces.database_path(workspace.id)
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TABLE conversation_messages")
        connection.execute("DROP TABLE conversations")
        connection.execute("PRAGMA user_version = 0")

    upgraded = MathHarnessService(tmp_path)
    conversations = upgraded.list_conversations(workspace.id)

    assert len(conversations) == 1
    messages = upgraded.list_conversation_messages(
        workspace.id,
        conversations[0].id,
    )
    assert [message.role.value for message in messages] == ["user", "assistant"]
    assert messages[-1].attempt_id is not None
