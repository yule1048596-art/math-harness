from __future__ import annotations

from typing import Any

from math_harness.checks.peer_review import REVIEW_PROMPT_VERSION

# 复核客户端。
#
# 刻意做得比对话客户端窄：它**不接会话上下文、不接工作区背景、不接记忆**，只收一段
# 待评审材料。这不是省事——研究结论说模型改不动自己的错却改得对以外部输入呈现的同样
# 错误，而把原对话喂回去正好会毁掉「这是别人交上来的」这个前提。


class OpenAIReviewer:
    """用另一个 provider 复核一份解答。"""

    prompt_version = REVIEW_PROMPT_VERSION

    def __init__(
        self,
        *,
        model: str,
        profile_id: str,
        reasoning_effort: str = "medium",
        timeout_seconds: float = 60.0,
        max_output_tokens: int = 1_000,
        api_key: str | None = None,
        base_url: str | None = None,
        provider_name: str = "openai",
        client: Any | None = None,
    ) -> None:
        self.model = model
        #: 用来判断复核方与作答方是不是同一个档案。相同就跳过——同模型自查是负收益。
        self.profile_id = profile_id
        self.reasoning_effort = reasoning_effort
        self.timeout_seconds = timeout_seconds
        self.max_output_tokens = max_output_tokens
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
                    "Peer review requires the optional 'llm' dependency"
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

    def review(self, system_prompt: str, material: str) -> str:
        response = self._client_or_create().responses.create(
            model=self.model,
            input=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": material},
            ],
            reasoning={"effort": self.reasoning_effort},
            max_output_tokens=self.max_output_tokens,
            store=False,
        )
        output = getattr(response, "output_text", None)
        if not isinstance(output, str) or not output.strip():
            raise RuntimeError("review response did not contain output text")
        return output.strip()


def build_reviewer_from_env() -> OpenAIReviewer | None:
    """按配置构造复核客户端；没绑 `reviewer` 角色时返回 None。

    默认不开。这一层要额外花一次模型调用，而且只有绑到**另一个** provider 才有价值。
    """

    from math_harness.provider_config import ROLE_REVIEWER, resolve_role

    resolved = resolve_role(ROLE_REVIEWER)
    if resolved is None:
        return None
    return OpenAIReviewer(
        model=resolved.model,
        profile_id=resolved.profile_id,
        reasoning_effort=resolved.reasoning_effort,
        timeout_seconds=resolved.timeout_seconds,
        max_output_tokens=min(resolved.max_output_tokens, 2_000),
        api_key=resolved.api_key,
        base_url=resolved.base_url,
        provider_name=resolved.provider_name,
    )
