from __future__ import annotations

from typing import Any

from math_harness.relevance import TurnRelevance

# 相关性判定客户端。
#
# 跟复核客户端一样刻意做窄，但窄的地方不同：复核不接会话上下文是为了保住「这是别人交上
# 来的」这个前提；这里**不接回答**，是因为回答里有数学恰恰是当前这个 bug 的成因——
# 一句「你好」，模型顺口回一句正确公式，整条链就把它当成了解题。给判定模型看回答，等于
# 请它复现同一个错误。
#
# 它只读用户说的话，只回一个词。

RELEVANCE_PROMPT_VERSION = "relevance-judge-v1"

RELEVANCE_SYSTEM_PROMPT = """\
你在判断一条用户消息的**意图**，不判断任何数学内容的对错。

判据只有一条：这条消息是不是在**推进一道具体的数学题**——提出新题、追问上一题的某一步、
要求换一种解法、指出上一步哪里说不通，都算。

不算的例子：寒暄、道谢、闲聊、问这个软件本身怎么用、问你是谁、纯粹的情绪表达。

只回一个词：
- solving —— 在推进一道数学题
- chitchat —— 不在
- unknown —— 判不出来

不要解释，不要加标点，只回这一个词。"""


class OpenAIRelevanceJudge:
    """问模型：用户这一轮在不在推进一道数学题。"""

    name = "openai"
    prompt_version = RELEVANCE_PROMPT_VERSION

    def __init__(
        self,
        *,
        model: str,
        reasoning_effort: str = "low",
        timeout_seconds: float = 30.0,
        api_key: str | None = None,
        base_url: str | None = None,
        provider_name: str = "openai",
        client: Any | None = None,
    ) -> None:
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.timeout_seconds = timeout_seconds
        self.api_key = api_key
        self.base_url = base_url
        self.name = provider_name
        self._client = client

    def _client_or_create(self) -> Any:
        if self._client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:
                raise RuntimeError(
                    "Relevance judging requires the optional 'llm' dependency"
                ) from exc
            options: dict[str, Any] = {
                "timeout": self.timeout_seconds,
                "max_retries": 0,
            }
            if self.api_key:
                options["api_key"] = self.api_key
            if self.base_url:
                options["base_url"] = self.base_url
            self._client = OpenAI(**options)
        return self._client

    def judge(self, question: str, recent_questions: list[str]) -> TurnRelevance:
        """`recent_questions` 只放**用户自己说过的话**，不放任何回答。

        承接式追问（「这个怎么证明？」）离开上文就判不了，所以上文必须给；但给的是用户
        这一侧的上文——判的是他想干什么，不是答得对不对。
        """

        material = _material(question, recent_questions)
        response = self._client_or_create().responses.create(
            model=self.model,
            input=[
                {"role": "system", "content": RELEVANCE_SYSTEM_PROMPT},
                {"role": "user", "content": material},
            ],
            reasoning={"effort": self.reasoning_effort},
            max_output_tokens=1_000,
            store=False,
        )
        output = getattr(response, "output_text", None)
        if not isinstance(output, str):
            return TurnRelevance.UNKNOWN
        return _parse_verdict(output)


def _material(question: str, recent_questions: list[str]) -> str:
    lines: list[str] = []
    if recent_questions:
        lines.append("这个用户之前说过的话（从早到晚）：")
        lines.extend(f"- {item.strip()[:500]}" for item in recent_questions[-5:])
        lines.append("")
    lines.append("要判断的这一条：")
    lines.append(question.strip()[:2_000])
    return "\n".join(lines)


def _parse_verdict(output: str) -> TurnRelevance:
    """认不出来就是 `UNKNOWN`。

    模型没照要求只回一个词时不去猜它的意思——猜错的那一半正好落在误收上，而误收率的
    门槛是 0.0。
    """

    cleaned = output.strip().strip(".。!！").lower()
    for value in (
        TurnRelevance.SOLVING,
        TurnRelevance.CHITCHAT,
        TurnRelevance.UNKNOWN,
    ):
        if cleaned == value.value:
            return value
    return TurnRelevance.UNKNOWN


def build_relevance_judge_from_env() -> OpenAIRelevanceJudge | None:
    """按配置构造判定客户端；没绑 `relevance_judge` 角色时返回 None。

    没绑就退回纯规则：扎不住即不进库。保守方向——宁可漏收，不可误收，误收的那一条会被
    索引、被检索，污染会一路传下去。
    """

    from math_harness.provider_config import ROLE_RELEVANCE_JUDGE, resolve_role

    resolved = resolve_role(ROLE_RELEVANCE_JUDGE)
    if resolved is None:
        return None
    return OpenAIRelevanceJudge(
        model=resolved.model,
        reasoning_effort=resolved.reasoning_effort,
        timeout_seconds=resolved.timeout_seconds,
        api_key=resolved.api_key,
        base_url=resolved.base_url,
        provider_name=resolved.provider_name,
    )
