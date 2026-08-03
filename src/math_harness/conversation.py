from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Protocol

from math_harness.models import ConversationMessage, MemoryItem, MethodMatch, Workspace


@dataclass(frozen=True)
class ConversationContext:
    """Bounded, workspace-scoped context supplied to one model turn."""

    workspace: Workspace
    summary: str
    recent_messages: list[ConversationMessage]
    trusted_methods: list[MethodMatch]
    soft_memories: list[MemoryItem] = field(default_factory=list)

    def model_payload(self) -> dict[str, object]:
        return {
            "workspace": {
                "name": self.workspace.name,
                "description": self.workspace.description,
            },
            "conversation_summary": self.summary or None,
            "soft_workspace_memory": [
                {
                    "kind": memory.kind.value,
                    "content": memory.content,
                    "pinned": memory.pinned,
                }
                for memory in self.soft_memories
            ],
            "recent_messages": [
                {"role": message.role.value, "content": message.content}
                for message in self.recent_messages
            ],
            "trusted_method_cards": [
                {
                    "key": match.method.key,
                    "name": match.method.name,
                    "goal": match.method.goal,
                    "applicable_when": match.method.applicable_when,
                    "procedure": match.method.procedure,
                    "failure_modes": match.method.failure_modes,
                    "retrieval_score": match.score,
                }
                for match in self.trusted_methods
            ],
        }


@dataclass(frozen=True)
class ChatGeneration:
    content: str
    provider: str
    model: str | None = None
    response_id: str | None = None
    prompt_version: str = "conversation-v1"
    raw_output: str | None = None
    error: str | None = None
    duration_ms: int = 0


class ConversationResponderProtocol(Protocol):
    name: str

    def respond(
        self,
        context: ConversationContext,
        message: str,
        max_output_tokens: int,
    ) -> ChatGeneration: ...


class OfflineConversationResponder:
    """Honest offline fallback: persistence works, unrestricted generation does not."""

    name = "offline"
    prompt_version = "offline-conversation-v1"

    def respond(
        self,
        context: ConversationContext,
        message: str,
        max_output_tokens: int,
    ) -> ChatGeneration:
        del context, max_output_tokens
        normalized = message.strip().lower()
        greetings = ("你好", "您好", "嗨", "hi", "hello", "hey")
        if any(normalized.startswith(item) for item in greetings):
            content = (
                "你好！当前工作区正在使用离线 SymPy 模式。对话会被完整保存在"
                "这个工作区中；若要进行自由自然语言聊天，请在设置中接入小米 MiMo。"
            )
        else:
            content = (
                "这条消息已经保存在当前工作区的会话中。离线 SymPy 只能处理带有"
                "可验证数学目标的求解，不能可靠生成自由文本回答；请切换到小米 MiMo，"
                "或选择“验算求解”并补充可验证目标。"
            )
        return ChatGeneration(
            content=content,
            provider=self.name,
            prompt_version=self.prompt_version,
        )


class ExtractiveConversationSummarizer:
    """Deterministic rolling summary that never turns model output into trusted math."""

    max_characters = 6_000
    per_message_characters = 700

    def summarize(
        self,
        existing_summary: str,
        messages: list[ConversationMessage],
    ) -> str:
        lines: list[str] = []
        if existing_summary.strip():
            lines.append(existing_summary.strip())
        for message in messages:
            label = "用户" if message.role.value == "user" else "助手"
            content = " ".join(message.content.split())
            if len(content) > self.per_message_characters:
                content = content[: self.per_message_characters - 1] + "…"
            lines.append(f"{label}：{content}")
        combined = "\n".join(lines)
        if len(combined) <= self.max_characters:
            return combined
        return "较早摘要已截断。\n" + combined[-(self.max_characters - 10) :]


def build_conversation_responder_from_env() -> ConversationResponderProtocol:
    configured = os.getenv("MATH_HARNESS_CONVERSATION_PROVIDER", "auto")
    provider = configured.strip().lower()
    if provider == "auto":
        provider = os.getenv("MATH_HARNESS_SOLVER", "sympy").strip().lower()
    if provider in {"offline", "sympy"}:
        return OfflineConversationResponder()
    if provider == "openai":
        from math_harness.providers.openai_chat import OpenAIConversationResponder

        return OpenAIConversationResponder(
            model=os.getenv("MATH_HARNESS_OPENAI_CHAT_MODEL", "gpt-5.6-sol"),
            reasoning_effort=os.getenv(
                "MATH_HARNESS_OPENAI_CHAT_REASONING_EFFORT", "medium"
            ),
            timeout_seconds=_positive_float_env(
                "MATH_HARNESS_OPENAI_CHAT_TIMEOUT_SECONDS", 60.0
            ),
        )
    if provider == "mimo":
        from math_harness.providers.mimo import (
            DEFAULT_MIMO_BASE_URL,
            DEFAULT_MIMO_MODEL,
        )
        from math_harness.providers.openai_chat import OpenAIConversationResponder

        return OpenAIConversationResponder(
            model=os.getenv("MATH_HARNESS_MIMO_MODEL", DEFAULT_MIMO_MODEL),
            reasoning_effort=os.getenv(
                "MATH_HARNESS_MIMO_CHAT_REASONING_EFFORT",
                os.getenv("MATH_HARNESS_MIMO_SOLVER_REASONING_EFFORT", "none"),
            ),
            timeout_seconds=_positive_float_env(
                "MATH_HARNESS_MIMO_TIMEOUT_SECONDS", 60.0
            ),
            api_key=os.getenv("MIMO_API_KEY"),
            base_url=os.getenv("MATH_HARNESS_MIMO_BASE_URL", DEFAULT_MIMO_BASE_URL),
            provider_name="xiaomi_mimo",
        )
    raise ValueError(
        "MATH_HARNESS_CONVERSATION_PROVIDER must be auto, offline, openai, or mimo"
    )


def _positive_float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive number") from exc
    if value <= 0:
        raise ValueError(f"{name} must be a positive number")
    return value
