from __future__ import annotations

import re
from time import perf_counter
from typing import Any

from math_harness.models import ProviderTestRequest, ProviderTestResult

# 连接测试：发一次最小请求，确认 Base URL、密钥和模型名三者能一起工作。
#
# 没有这个按钮，用户填错任何一项都只能在聊天里看到一句失败，无从判断是网址错了、
# 密钥过期了，还是模型名拼错了。
#
# 密钥只用于本次请求：不落盘、不进日志、不进审计记录，也不出现在返回值里。
# `tests/test_provider_probe.py` 把这条钉死。

_PROBE_PROMPT = "Reply with the single word: ok"
_PROBE_MAX_TOKENS = 16

# sk-、tp-、Bearer 之后的长串一律视为密钥形状。
_KEY_SHAPE = re.compile(
    r"\b(?:sk|tp|key|Bearer)[-_ ][A-Za-z0-9_\-]{12,}",
    re.IGNORECASE,
)


def _redact(text: str | None, secret: str | None) -> str | None:
    """把密钥从任何回传文本里抹掉。

    两条真实泄漏路径：SDK 异常里常带上请求详情（含 Authorization 头），模型回显
    也可能把输入原样吐回来。两者都会经由这里。
    """

    if not text:
        return text
    redacted = text
    if secret and len(secret) >= 8:
        redacted = redacted.replace(secret, "***")
    # 兜底：即便密钥被转义或截断，也不放行常见的密钥形状。
    return _KEY_SHAPE.sub("***", redacted)


def _classify(exc: Exception) -> str:
    """把 SDK 异常翻译成用户能据此行动的一句话。"""

    name = exc.__class__.__name__
    text = str(exc)
    lowered = text.lower()

    if name == "ImportError" or "optional 'llm'" in text:
        return "缺少可选依赖 llm。请执行：uv sync --extra llm"
    if "authentication" in lowered or "401" in lowered or "invalid_api_key" in lowered:
        return "密钥被拒绝。请检查 API Key 是否正确、是否已过期，以及是否与该 Base URL 匹配。"
    if (
        "not found" in lowered
        or "404" in lowered
        or "model" in lowered
        and "exist" in lowered
    ):
        return "模型名或路径不存在。请检查模型名，以及 Base URL 是否需要带 /v1。"
    if "connect" in lowered or "timeout" in lowered or "timed out" in lowered:
        return "无法连接。请检查网络、Base URL 主机名，以及是否需要代理。"
    if "429" in lowered or "rate limit" in lowered:
        return "被限流。密钥有效，但当前配额或频率受限。"
    return f"{name}: {text}"[:300]


def probe_provider(
    request: ProviderTestRequest,
    client: Any | None = None,
) -> ProviderTestResult:
    started = perf_counter()
    try:
        if client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:  # pragma: no cover - 依赖缺失路径
                raise RuntimeError(
                    "Online providers require the optional 'llm' dependency"
                ) from exc
            options: dict[str, Any] = {
                "timeout": request.timeout_seconds,
                # 传输层重试保持 0，避免一次「测试」变成多次计费请求。
                "max_retries": 0,
                "base_url": request.base_url,
            }
            if request.api_key:
                options["api_key"] = request.api_key
            client = OpenAI(**options)

        response = client.responses.create(
            model=request.model,
            input=[{"role": "user", "content": _PROBE_PROMPT}],
            max_output_tokens=_PROBE_MAX_TOKENS,
            store=False,
        )
    except Exception as exc:  # noqa: BLE001
        return ProviderTestResult(
            ok=False,
            duration_ms=int((perf_counter() - started) * 1000),
            error=_redact(_classify(exc), request.api_key),
        )

    duration_ms = int((perf_counter() - started) * 1000)
    echoed_model = getattr(response, "model", None)
    output = getattr(response, "output_text", None)
    return ProviderTestResult(
        ok=True,
        duration_ms=duration_ms,
        model=echoed_model if isinstance(echoed_model, str) else None,
        # 只回显很短的一段，够确认「确实是模型在回话」即可。
        sample=_redact(
            output.strip()[:120] if isinstance(output, str) else None,
            request.api_key,
        ),
    )
