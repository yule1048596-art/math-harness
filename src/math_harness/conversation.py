from __future__ import annotations

import os
import re
from collections.abc import Callable, Generator, Iterator
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

_INCOMPLETE_HISTORY_PREFIX = (
    "[Previous assistant response was interrupted and is incomplete.]\n"
)
_KEY_LIKE_SECRET = re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b")

#: 用户按下停止时记的错误。
#
# 它是**错误**而不是一种正常结束，因为下游按 `error` 是否为空决定要不要检查、给可信度、
# 写知识库——而半条推导无论是被网络掐断的还是被用户叫停的，都不是答案。
STOPPED_BY_USER = "StoppedByUser: 用户中止了这次生成"


def conversation_history_content(message: ConversationMessage) -> str:
    """给模型的历史内容；中断回答必须显式标出，不能伪装成完整上下文。"""

    if message.generation_error and message.role.value == "assistant":
        return _INCOMPLETE_HISTORY_PREFIX + message.content
    return message.content


def _generation_error(responder: object, exc: Exception) -> str:
    """记录可诊断错误，但绝不把 provider 密钥写进消息、备份或界面。"""

    detail = f"{exc.__class__.__name__}: {exc}"
    api_key = getattr(responder, "api_key", None)
    if isinstance(api_key, str) and api_key:
        detail = detail.replace(api_key, "[redacted]")
    return _KEY_LIKE_SECRET.sub("[redacted]", detail)[:2_000]


def _stopped_generation(
    responder: object, content: str, started: float
) -> ChatGeneration:
    """用户叫停后的收尾：正文照常保留，但这一回合是中断，不是回答。"""

    return ChatGeneration(
        content=content or "这次回答在生成出任何正文之前就被停止了。",
        provider=getattr(responder, "name", responder.__class__.__name__),
        model=getattr(responder, "model", None),
        prompt_version=getattr(responder, "prompt_version", "conversation-v1"),
        raw_output=content[:8_000] or None,
        error=STOPPED_BY_USER,
        duration_ms=max(0, round((perf_counter() - started) * 1_000)),
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
                {
                    "role": message.role.value,
                    "content": conversation_history_content(message),
                }
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
    should_stop: Callable[[], bool] | None = None,
) -> Generator[str, None, ChatGeneration]:
    """逐段产出正文，结束时返回完整的一次生成。

    不支持流式的响应器退回一次性返回——**降级要看得见地正常工作**，而不是报错。

    生成中途失败时，已经吐出去的部分不丢：用户看着字一个个出现，最后告诉他「刚才那些
    不算数」是最糟的处理方式。已收到的内容照常保存，错误记在生成结果里。

    **一个字都还没吐出去时，退回非流式再试一次。** 流式依赖服务端事件的具体形状，而各家
    OpenAI 兼容服务并不一致：有的不支持在 Responses 上开流，有的事件名不同。那种情况下
    一条事件都匹配不上，可同一个请求走非流式完全正常——不退回的话，用户每一轮都看到
    「生成失败」，而模型是好的。

    判据是**用户看到字了没有**，不是流空不空：已经显示了半句再去重新生成，用户要么看到
    正文被换掉，要么在已显示的字后面接上另一次生成的后半段。中断就是中断。

    `should_stop` 是用户按下停止的信号，每吐出一段查一次。停下来之后**不退回非流式**：
    那会在用户明确叫停之后再完整生成一次，既费钱又违背他刚表达的意思。
    """

    started = perf_counter()
    streaming = isinstance(responder, StreamingResponderProtocol) and hasattr(
        responder, "stream"
    )
    stopped = should_stop is not None and should_stop()
    if not streaming:
        if stopped:
            return _stopped_generation(responder, "", started)
        generation = responder.respond(context, message, max_output_tokens)
        if generation.content:
            yield generation.content
        return generation
    if stopped:
        return _stopped_generation(responder, "", started)

    chunks: list[str] = []
    error: str | None = None
    stream = responder.stream(context, message, max_output_tokens)
    try:
        for chunk in stream:
            # 先查停止再吐字：按下停止之后还往屏幕上接一段，用户会以为没停住。
            # 这一段是刚从 provider 收到、用户还没看见的，丢掉不会造成不一致。
            if should_stop is not None and should_stop():
                stopped = True
                break
            if not chunk:
                continue
            chunks.append(chunk)
            yield chunk
    except Exception as exc:  # noqa: BLE001
        error = _generation_error(responder, exc)
    finally:
        # 提前跳出时立刻关掉底层的 HTTP 流，不等垃圾回收——那期间 provider 还在计费。
        close = getattr(stream, "close", None)
        if callable(close):
            close()

    content = "".join(chunks)
    if stopped:
        return _stopped_generation(responder, content, started)
    if not content:
        try:
            fallback = responder.respond(context, message, max_output_tokens)
        except Exception as exc:  # noqa: BLE001
            error = error or _generation_error(responder, exc)
        else:
            if fallback.content:
                yield fallback.content
                return fallback
        content = (
            "这次对话回复生成失败，但你的消息已经保存在当前工作区。"
            "请检查模型设置或网络后重新发送。"
        )
        # 这句是占位文案，不是回答。`error` 必须非空，否则下游会把它当成一次正常
        # 生成——去跑检查、给可信度、存进知识库。静默的空流本身不抛异常，所以这里
        # 要自己补上。
        error = error or "EmptyStream: 流式与非流式都没有返回正文"
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
            if message.role.value == "user":
                label = "用户"
            elif message.generation_error:
                label = "助手（回复中断）"
            else:
                label = "助手"
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
