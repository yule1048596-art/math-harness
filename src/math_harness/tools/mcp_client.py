from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

# 面向 MCP 服务器的最小 JSON-RPC 客户端（Streamable HTTP）。
#
# 为什么自己写而不用 provider 侧的远程 MCP：OpenAI 的 Responses API 支持
# `tools: [{"type": "mcp", …}]`，由它自己调用 MCP 再把结果喂回模型，后端几乎不用写
# 代码——但那个工具类型只有真 OpenAI 有。MiMo 等 OpenAI 兼容端点没有它，却都支持
# 标准 function calling。自己连 MCP、把工具翻译成函数定义、自己跑循环，才能覆盖全部
# provider，包括这个项目实际默认使用的那个。
#
# 只实现 Streamable HTTP，不做 SSE 回退——Wolfram 的端点支持前者，多一条传输就多一
# 条要维护的失败路径。

DEFAULT_MCP_URL = "https://agenttools.wolfram.com/mcp"
PROTOCOL_VERSION = "2025-03-26"
_CLIENT_NAME = "math-harness"


class MCPError(RuntimeError):
    """MCP 传输或协议层失败。调用方应当降级，而不是让整条求解路径崩掉。"""


def _extract_json(body: str) -> str:
    """从响应体里取出 JSON，兼容 SSE 帧。

    请求头里声明了 `Accept: application/json, text/event-stream`，服务器据此完全
    可以回 SSE。此前只按纯 JSON 解析，等于声明了一种自己不认的格式；Wolfram 目前
    回纯 JSON，所以这条路一直没被走到。
    """

    stripped = body.strip()
    if not stripped.startswith("event:") and not stripped.startswith("data:"):
        return stripped
    payloads = [
        line[len("data:") :].strip()
        for line in stripped.splitlines()
        if line.startswith("data:")
    ]
    return payloads[-1] if payloads else stripped


@dataclass(frozen=True)
class MCPTool:
    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)

    def as_function_definition(self) -> dict[str, Any]:
        """翻译成 OpenAI 风格的函数定义。"""

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description[:1_000],
                "parameters": self.input_schema or {"type": "object", "properties": {}},
            },
        }


class MCPClient:
    """连接一个 MCP 服务器，列出工具并执行调用。

    无状态：每次调用都是独立的 HTTP 请求。Wolfram 的内核本身也是无状态的，前一次
    调用里的定义不能在后一次复用。
    """

    def __init__(
        self,
        url: str = DEFAULT_MCP_URL,
        *,
        timeout_seconds: float = 60.0,
        headers: dict[str, str] | None = None,
        opener: Any | None = None,
    ) -> None:
        self.url = url
        self.timeout_seconds = timeout_seconds
        self.headers = dict(headers or {})
        self._opener = opener
        self._request_id = 0
        self._tools: list[MCPTool] | None = None

    def _call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self._request_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": params,
        }
        request = urllib.request.Request(
            self.url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                **self.headers,
            },
            method="POST",
        )
        try:
            opener = self._opener or urllib.request.urlopen
            with opener(request, timeout=self.timeout_seconds) as response:
                body = response.read().decode("utf-8")
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise MCPError(f"{exc.__class__.__name__}: {exc}") from exc

        try:
            decoded = json.loads(_extract_json(body))
        except json.JSONDecodeError as exc:
            raise MCPError(f"MCP response was not JSON: {exc}") from exc
        if "error" in decoded:
            raise MCPError(str(decoded["error"])[:400])
        result = decoded.get("result")
        if not isinstance(result, dict):
            raise MCPError("MCP response had no result object")
        return result

    def initialize(self) -> dict[str, Any]:
        return self._call(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": _CLIENT_NAME, "version": "1"},
            },
        )

    def list_tools(self, refresh: bool = False) -> list[MCPTool]:
        if self._tools is not None and not refresh:
            return self._tools
        self.initialize()
        result = self._call("tools/list", {})
        tools = [
            MCPTool(
                name=item["name"],
                description=item.get("description", ""),
                input_schema=item.get("inputSchema", {}) or {},
            )
            for item in result.get("tools", [])
            if item.get("name")
        ]
        self._tools = tools
        return tools

    def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """执行一次工具调用，把返回内容压成一段文本。

        服务器报告 `isError` 时同样返回文本而不是抛异常——模型看到错误信息后往往能
        自己改写查询重试，这比让整轮求解失败有用。
        """

        result = self._call("tools/call", {"name": name, "arguments": arguments})
        chunks: list[str] = []
        for item in result.get("content", []):
            if item.get("type") == "text" and item.get("text"):
                chunks.append(str(item["text"]))
        text = "\n".join(chunks).strip() or "(工具没有返回文本内容)"
        if result.get("isError"):
            return f"工具报告错误：{text}"
        return text


def build_mcp_client_from_env() -> MCPClient | None:
    """按环境构造客户端；未启用时返回 None。

    默认关闭：离线 SymPy 默认路径「不发送网络请求」的承诺不能因为加了这个功能而变。
    """

    enabled = os.getenv("MATH_HARNESS_WOLFRAM_TOOLS", "").strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
        return None
    return MCPClient(
        url=os.getenv("MATH_HARNESS_WOLFRAM_MCP_URL", DEFAULT_MCP_URL),
        timeout_seconds=_positive_float_env(
            "MATH_HARNESS_WOLFRAM_TIMEOUT_SECONDS", 60.0
        ),
    )


def _positive_float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default
