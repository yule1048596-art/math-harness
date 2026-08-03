from __future__ import annotations

import io
import json

import pytest

from math_harness.tools import (
    TOOL_SYSTEM_NOTE,
    MCPClient,
    MCPError,
    MCPTool,
    ToolSession,
    build_mcp_client_from_env,
    build_tool_session,
)

TOOLS_RESULT = {
    "tools": [
        {
            "name": "WolframLanguageEvaluator",
            "description": "Evaluates Wolfram Language code.",
            "inputSchema": {
                "type": "object",
                "properties": {"code": {"type": "string"}},
                "required": ["code"],
            },
        }
    ]
}


class _FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def _opener(responses: list[dict], calls: list[dict] | None = None):
    queue = list(responses)

    def open_(request, timeout=None):
        if calls is not None:
            calls.append(json.loads(request.data.decode("utf-8")))
        payload = queue.pop(0) if queue else {}
        if isinstance(payload, Exception):
            raise payload
        body = json.dumps({"jsonrpc": "2.0", "id": 1, **payload})
        return _FakeResponse(body.encode("utf-8"))

    return open_


# --- 客户端 -----------------------------------------------------------


def test_list_tools_parses_the_server_schema():
    client = MCPClient(opener=_opener([{"result": {}}, {"result": TOOLS_RESULT}]))

    tools = client.list_tools()

    assert [tool.name for tool in tools] == ["WolframLanguageEvaluator"]
    assert tools[0].input_schema["required"] == ["code"]


def test_tool_translates_to_a_function_definition():
    tool = MCPTool(
        name="WolframAlpha",
        description="x" * 5_000,
        input_schema={"type": "object", "properties": {"query": {}}},
    )

    definition = tool.as_function_definition()

    assert definition["type"] == "function"
    assert definition["function"]["name"] == "WolframAlpha"
    assert definition["function"]["parameters"]["properties"] == {"query": {}}
    # 描述要截断，否则一个啰嗦的服务器能把整个请求撑爆。
    assert len(definition["function"]["description"]) <= 1_000


def test_tool_without_schema_still_produces_valid_parameters():
    definition = MCPTool(name="t", description="d").as_function_definition()

    assert definition["function"]["parameters"] == {"type": "object", "properties": {}}


def test_call_tool_flattens_text_content():
    client = MCPClient(
        opener=_opener([{"result": {"content": [{"type": "text", "text": "1/2"}]}}])
    )

    assert client.call_tool("WolframLanguageEvaluator", {"code": "x"}) == "1/2"


def test_server_reported_error_comes_back_as_text():
    """模型看到错误信息往往能自己改写重试，比让整轮求解失败有用。"""

    client = MCPClient(
        opener=_opener(
            [
                {
                    "result": {
                        "isError": True,
                        "content": [{"type": "text", "text": "bad"}],
                    }
                }
            ]
        )
    )

    assert "错误" in client.call_tool("t", {})


def test_sse_framed_response_is_parsed():
    """请求头声明了接受 SSE，就得认得它——此前只按纯 JSON 解析。"""

    def sse(request, timeout=None):
        body = (
            'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"tools":[]}}\n\n'
        )
        return _FakeResponse(body.encode("utf-8"))

    assert MCPClient(opener=sse).initialize() == {"tools": []}


def test_plain_json_still_parses():
    client = MCPClient(opener=_opener([{"result": {"ok": True}}]))

    assert client.initialize() == {"ok": True}


def test_transport_failure_raises_mcp_error():
    client = MCPClient(opener=_opener([OSError("boom")]))

    with pytest.raises(MCPError):
        client.list_tools()


def test_protocol_error_raises_mcp_error():
    client = MCPClient(opener=_opener([{"error": {"code": -32601}}]))

    with pytest.raises(MCPError):
        client.initialize()


def test_tools_are_cached_after_the_first_listing():
    calls: list[dict] = []
    client = MCPClient(
        opener=_opener([{"result": {}}, {"result": TOOLS_RESULT}], calls)
    )

    client.list_tools()
    client.list_tools()

    assert [item["method"] for item in calls] == ["initialize", "tools/list"]


# --- 默认关闭 ---------------------------------------------------------


def test_tools_are_disabled_by_default(monkeypatch):
    """离线默认路径「不发送网络请求」的承诺不能因为加了这个功能而变。"""

    monkeypatch.delenv("MATH_HARNESS_WOLFRAM_TOOLS", raising=False)

    assert build_mcp_client_from_env() is None
    assert build_tool_session(None) is None


def test_tools_enable_only_on_an_explicit_flag(monkeypatch):
    monkeypatch.setenv("MATH_HARNESS_WOLFRAM_TOOLS", "true")

    assert build_mcp_client_from_env() is not None

    monkeypatch.setenv("MATH_HARNESS_WOLFRAM_TOOLS", "maybe")

    assert build_mcp_client_from_env() is None


# --- 预算 -------------------------------------------------------------


def _session(**kwargs) -> ToolSession:
    # 队列顺序要和真实握手一致：initialize → tools/list → 之后全是 tools/call。
    client = MCPClient(
        opener=_opener(
            [{"result": {}}, {"result": TOOLS_RESULT}]
            + [{"result": {"content": [{"type": "text", "text": "ok"}]}}] * 40
        )
    )
    return ToolSession(client=client, **kwargs)


def test_call_budget_is_enforced():
    session = _session(max_tool_calls=2)

    session.execute("t", {})
    session.execute("t", {})
    third = session.execute("t", {})

    assert "上限" in third
    assert session.calls_used == 2


def test_round_budget_marks_the_session_exhausted():
    session = _session(max_rounds=2)
    session.rounds_used = 2

    assert session.exhausted


def test_malformed_arguments_are_recorded_not_raised():
    session = _session()

    result = session.execute("t", "{not json")

    assert "合法 JSON" in result
    assert session.records[0].failed


def test_tool_failure_degrades_instead_of_raising():
    session = ToolSession(client=MCPClient(opener=_opener([OSError("down")])))

    result = session.execute("t", {})

    assert "失败" in result
    assert session.records[0].failed


def test_missing_tool_list_degrades_to_no_tools():
    """工具清单取不到时退回纯模型生成，不让整轮失败。"""

    session = ToolSession(client=MCPClient(opener=_opener([OSError("down")])))

    assert session.function_definitions() == []


def test_budget_bounds_come_from_env(monkeypatch):
    monkeypatch.setenv("MATH_HARNESS_WOLFRAM_TOOLS", "true")
    monkeypatch.setenv("MATH_HARNESS_TOOL_MAX_ROUNDS", "999")
    monkeypatch.setenv("MATH_HARNESS_TOOL_MAX_CALLS", "0")

    session = build_tool_session(build_mcp_client_from_env())

    # 越界值被夹回合法区间，避免一次配置失误把账单放大。
    assert session.max_rounds == 10
    assert session.max_tool_calls == 1


# --- 信任边界（本阶段最重要的约束）------------------------------------


def test_audit_records_every_call():
    session = _session()
    session.execute("WolframLanguageEvaluator", {"code": "Asymptotic[x]"})

    payload = session.audit_payload()

    assert payload["calls_used"] == 1
    assert payload["tool_calls"][0]["tool"] == "WolframLanguageEvaluator"
    assert payload["tool_calls"][0]["arguments"] == {"code": "Asymptotic[x]"}


def test_audit_never_contains_verification_fields():
    """工具层不产出任何验证字段——验证状态只能由 SolutionVerifier 写入。"""

    session = _session()
    session.execute("t", {"code": "x"})

    dumped = json.dumps(session.audit_payload(), ensure_ascii=False)

    assert "verification" not in dumped
    assert "verified" not in dumped


def test_prompt_tells_the_model_tools_do_not_verify():
    assert "不等于已验证" in TOOL_SYSTEM_NOTE
    assert "不要在回答里声称结果已被验证" in TOOL_SYSTEM_NOTE


# --- 研究循环 ---------------------------------------------------------


class _FakeModel:
    """最小的 OpenAI 兼容聊天客户端替身。"""

    def __init__(self, script: list[object]) -> None:
        self._script = list(script)
        self.requests: list[dict] = []
        self.chat = type("C", (), {"completions": self})()

    def create(self, **kwargs):
        self.requests.append(kwargs)
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _message(content: str = "", tool_calls=None):
    call_objs = []
    for index, (name, args) in enumerate(tool_calls or []):
        function = type("F", (), {"name": name, "arguments": args})()
        call_objs.append(type("T", (), {"id": f"c{index}", "function": function})())
    message = type("M", (), {"content": content, "tool_calls": call_objs or None})()
    return type("R", (), {"choices": [type("C", (), {"message": message})()]})()


def test_research_runs_the_tool_then_returns_findings():
    session = _session()
    model = _FakeModel(
        [
            _message(tool_calls=[("WolframLanguageEvaluator", '{"code": "1+1"}')]),
            _message(content="结论"),
        ]
    )

    result = session.research(model, "m", "求极限")

    assert result == "结论"
    assert session.calls_used == 1
    # 工具定义必须真的挂到了请求上。
    assert model.requests[0]["tools"]
    assert model.requests[0]["tool_choice"] == "auto"


def test_research_stops_at_the_round_budget():
    session = _session(max_rounds=2)
    model = _FakeModel([_message(tool_calls=[("t", "{}")])] * 10)

    session.research(model, "m", "q")

    assert session.rounds_used == 2


def test_research_degrades_when_the_model_fails():
    """模型侧报错时保留已经拿到的工具结论，不让整轮求解崩掉。"""

    session = _session()
    model = _FakeModel(
        [
            _message(tool_calls=[("WolframLanguageEvaluator", '{"code": "1"}')]),
            RuntimeError("model down"),
        ]
    )

    result = session.research(model, "m", "q")

    assert "不构成验证" in result


def test_research_without_tools_makes_no_model_call():
    session = ToolSession(client=MCPClient(opener=_opener([OSError("down")])))
    model = _FakeModel([])

    assert session.research(model, "m", "q") == ""
    assert model.requests == []


def test_findings_text_always_disclaims_verification():
    session = _session()
    session.execute("t", {"code": "x"})

    assert "不构成验证" in session.findings_text()


def test_long_tool_output_is_truncated_in_the_audit():
    client = MCPClient(
        opener=_opener(
            [{"result": {"content": [{"type": "text", "text": "y" * 10_000}]}}]
        )
    )
    session = ToolSession(client=client)

    session.execute("t", {})

    assert len(session.audit_payload()["tool_calls"][0]["result"]) <= 2_000
