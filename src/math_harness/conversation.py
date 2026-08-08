from __future__ import annotations

import os
from collections.abc import Generator, Iterator
from dataclasses import dataclass, field
from time import perf_counter
from typing import Protocol, runtime_checkable

from math_harness.models import ConversationMessage, MemoryItem, MethodMatch, Workspace
from math_harness.provider_config import (
    ROLE_CONVERSATION,
    ResolvedRole,
    resolve_role,
    role_is_configured,
)


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


@runtime_checkable
class StreamingResponderProtocol(Protocol):
    """能逐段吐字的响应器。

    可选：没实现它的响应器照常走 `respond`，`stream_chat_generation` 会把整条回复
    当成一段产出。离线响应器就是这种情况——它本来就是瞬间返回的固定说明。
    """

    def stream(
        self,
        context: ConversationContext,
        message: str,
        max_output_tokens: int,
    ) -> Iterator[str]: ...


def stream_chat_generation(
    responder: ConversationResponderProtocol,
    context: ConversationContext,
    message: str,
    max_output_tokens: int,
) -> Generator[str, None, ChatGeneration]:
    """逐段产出正文，结束时返回完整的一次生成。

    不支持流式的响应器退回一次性返回——**降级要看得见地正常工作**，而不是报错。

    生成中途失败时，已经吐出去的部分不丢：用户看着字一个个出现，最后告诉他「刚才那些
    不算数」是最糟的处理方式。已收到的内容照常保存，错误记在生成结果里。
    """

    streaming = isinstance(responder, StreamingResponderProtocol) and hasattr(
        responder, "stream"
    )
    if not streaming:
        generation = responder.respond(context, message, max_output_tokens)
        if generation.content:
            yield generation.content
        return generation

    started = perf_counter()
    chunks: list[str] = []
    error: str | None = None
    try:
        for chunk in responder.stream(context, message, max_output_tokens):
            if not chunk:
                continue
            chunks.append(chunk)
            yield chunk
    except Exception as exc:  # noqa: BLE001
        error = f"{exc.__class__.__name__}: {exc}"[:2_000]

    content = "".join(chunks)
    if not content:
        content = (
            "这次对话回复生成失败，但你的消息已经保存在当前工作区。"
            "请检查模型设置或网络后重新发送。"
        )
        yield content
    return ChatGeneration(
        content=content,
        provider=getattr(responder, "name", responder.__class__.__name__),
        model=getattr(responder, "model", None),
        prompt_version=getattr(responder, "prompt_version", "conversation-v1"),
        raw_output=content[:8_000],
        error=error,
        duration_ms=max(0, round((perf_counter() - started) * 1_000)),
    )


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


def build_conversation_responder_from_resolved(
    resolved: ResolvedRole | None,
) -> ConversationResponderProtocol:
    """按已解析好的角色参数构造客户端。

    env 路径与单次请求覆盖共用这一段，两条路走同样的构造逻辑。
    """

    if resolved is None:
        return OfflineConversationResponder()
    from math_harness.providers.openai_chat import OpenAIConversationResponder

    return OpenAIConversationResponder(
        model=resolved.model,
        reasoning_effort=resolved.reasoning_effort,
        timeout_seconds=resolved.timeout_seconds,
        api_key=resolved.api_key,
        base_url=resolved.base_url,
        provider_name=resolved.provider_name,
    )


def build_conversation_responder_from_env() -> ConversationResponderProtocol:
    # 新配置优先；未配置时下面的逐变量路径保持原样，一个字都不改。
    if role_is_configured(ROLE_CONVERSATION):
        return build_conversation_responder_from_resolved(
            resolve_role(ROLE_CONVERSATION)
        )

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
