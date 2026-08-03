from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

from math_harness.tools.mcp_client import MCPClient, MCPError

# 模型自主调用工具的循环。
#
# 一条不可让步的边界：**工具结果只是让候选解更可能正确，不构成验证**。模型即使声称
# Wolfram 已经验证过答案，验证状态仍然只能由 `SolutionVerifier` 写入。这一点同时靠
# 提示词和「工具层不产出任何验证字段」两处保证。
#
# 成本纪律：工具循环天然会把一次求解放大成多次模型调用，所以轮数和总调用数都封顶。
# 这与项目一贯把 SDK 传输重试锁死 `0` 是同一条原则——不让隐式重试悄悄放大账单。

DEFAULT_MAX_ROUNDS = 4
DEFAULT_MAX_TOOL_CALLS = 8

TOOL_SYSTEM_NOTE = (
    "你可以调用 Wolfram 工具做符号计算来提高答案质量。"
    "但工具的输出只是参考：本系统的答案正确性由本地独立验证器裁定，"
    "工具计算过、或你认为工具确认过，都不等于已验证。"
    "不要在回答里声称结果已被验证。"
)


@dataclass
class ToolCallRecord:
    """一次工具调用的审计记录，进 `generation.stages` 供复核。"""

    name: str
    arguments: dict[str, Any]
    result: str
    duration_ms: int = 0
    failed: bool = False

    def as_payload(self) -> dict[str, Any]:
        return {
            "tool": self.name,
            "arguments": self.arguments,
            # 截断避免把整段计算塞进审计记录。
            "result": self.result[:2_000],
            "duration_ms": self.duration_ms,
            "failed": self.failed,
        }


@dataclass
class ToolSession:
    """一次求解或对话回合里的工具预算与记录。"""

    client: MCPClient
    max_rounds: int = DEFAULT_MAX_ROUNDS
    max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS
    records: list[ToolCallRecord] = field(default_factory=list)
    rounds_used: int = 0

    @property
    def calls_used(self) -> int:
        return len(self.records)

    @property
    def exhausted(self) -> bool:
        return (
            self.rounds_used >= self.max_rounds
            or self.calls_used >= self.max_tool_calls
        )

    def function_definitions(self) -> list[dict[str, Any]]:
        """工具清单取不到时返回空列表——降级为纯模型生成，不让整轮失败。"""

        try:
            return [tool.as_function_definition() for tool in self.client.list_tools()]
        except MCPError:
            return []

    def execute(self, name: str, raw_arguments: str | dict[str, Any]) -> str:
        """执行一次调用并记录。预算耗尽或失败都返回给模型看的文本，不抛异常。"""

        from time import perf_counter

        if self.calls_used >= self.max_tool_calls:
            return "工具调用次数已达上限，请基于现有信息作答。"

        if isinstance(raw_arguments, str):
            try:
                arguments = json.loads(raw_arguments) if raw_arguments.strip() else {}
            except json.JSONDecodeError:
                record = ToolCallRecord(
                    name=name,
                    arguments={"_raw": raw_arguments[:500]},
                    result="参数不是合法 JSON。",
                    failed=True,
                )
                self.records.append(record)
                return record.result
        else:
            arguments = dict(raw_arguments)

        started = perf_counter()
        try:
            result = self.client.call_tool(name, arguments)
            failed = False
        except MCPError as exc:
            result = f"工具调用失败：{exc}"
            failed = True
        duration = int((perf_counter() - started) * 1000)

        record = ToolCallRecord(
            name=name,
            arguments=arguments,
            result=result,
            duration_ms=duration,
            failed=failed,
        )
        self.records.append(record)
        return result

    def research(self, client: Any, model: str, question: str) -> str:
        """跑一轮自由格式的工具循环，把 Wolfram 的计算结果收集成一段文字。

        刻意做成**前置研究**而不是嵌进候选解请求：结构化输出那条路径有 json_schema 与
        json_object 两种模式和本地重试，把工具循环塞进去会让一条已经很精细的路径更难
        维护。这里先独立取证，再把结论作为上下文交给原有请求，那条路径一个字不用改。

        任何失败都返回空串——降级为纯模型生成，不让整轮求解崩掉。
        """

        definitions = self.function_definitions()
        if not definitions:
            return ""

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": TOOL_SYSTEM_NOTE},
            {"role": "user", "content": question},
        ]
        try:
            while not self.exhausted:
                self.rounds_used += 1
                response = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    tools=definitions,
                    tool_choice="auto",
                )
                choice = response.choices[0].message
                calls = getattr(choice, "tool_calls", None) or []
                if not calls:
                    return (choice.content or "").strip()

                messages.append(
                    {
                        "role": "assistant",
                        "content": choice.content or "",
                        "tool_calls": [
                            {
                                "id": call.id,
                                "type": "function",
                                "function": {
                                    "name": call.function.name,
                                    "arguments": call.function.arguments,
                                },
                            }
                            for call in calls
                        ],
                    }
                )
                for call in calls:
                    output = self.execute(call.function.name, call.function.arguments)
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.id,
                            "content": output,
                        }
                    )
        except Exception:  # noqa: BLE001
            return self.findings_text()
        return self.findings_text()

    def findings_text(self) -> str:
        """把已经拿到的工具结果压成一段可作为上下文的文字。"""

        if not self.records:
            return ""
        lines = ["以下是 Wolfram 工具的计算结果，仅供参考，不构成验证："]
        for record in self.records:
            if record.failed:
                continue
            lines.append(f"- {record.name}({record.arguments}) → {record.result[:600]}")
        return "\n".join(lines) if len(lines) > 1 else ""

    def audit_payload(self) -> dict[str, Any]:
        return {
            "tool_calls": [record.as_payload() for record in self.records],
            "rounds_used": self.rounds_used,
            "calls_used": self.calls_used,
            "budget_exhausted": self.exhausted,
        }


def _bounded_int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return min(max(value, minimum), maximum)


def build_tool_session(client: MCPClient | None) -> ToolSession | None:
    if client is None:
        return None
    return ToolSession(
        client=client,
        max_rounds=_bounded_int_env("MATH_HARNESS_TOOL_MAX_ROUNDS", 4, 1, 10),
        max_tool_calls=_bounded_int_env("MATH_HARNESS_TOOL_MAX_CALLS", 8, 1, 32),
    )
