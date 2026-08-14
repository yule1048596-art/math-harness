from __future__ import annotations

import json
from threading import Event, Thread

import pytest
from fastapi.testclient import TestClient

from math_harness.api import create_app
from math_harness.conversation import (
    ChatGeneration,
    StreamingResponderProtocol,
    stream_chat_generation,
)
from math_harness.models import (
    ConclusionConfidence,
    ConversationCreate,
    ConversationTurnRequest,
    ProcessConfidence,
    WorkspaceCreate,
)
from math_harness.service import MathHarnessService

# 流式输出。
#
# 一条硬约束贯穿全部实现：**检查在整条回复吐完之后才跑**。可信度徽章一边流一边变，
# 用户会看到「先说对、又说错」；而且逐步检查在只有半条推导时给出的判断本来就没有意义。


ANSWER_PARTS = ["先做共轭有理化：\n", "所以 diff", "(x**2, x)", " = 2*x"]
WHOLE_ANSWER = "".join(ANSWER_PARTS)


class StreamingResponder:
    name = "streaming"
    model = "m"
    prompt_version = "v1"

    def __init__(self, parts=ANSWER_PARTS, fail_after: int | None = None) -> None:
        self.parts = parts
        self.fail_after = fail_after

    def stream(self, context, message, max_output_tokens):
        del context, message, max_output_tokens
        for index, part in enumerate(self.parts):
            if self.fail_after is not None and index == self.fail_after:
                raise RuntimeError("连接断了")
            yield part

    def respond(self, context, message, max_output_tokens):
        del context, message, max_output_tokens
        return ChatGeneration(
            content=WHOLE_ANSWER, provider=self.name, model=self.model
        )


class PlainResponder:
    """不支持流式。降级要看得见地正常工作，而不是报错。"""

    name = "plain"
    model = "m"
    prompt_version = "v1"

    def respond(self, context, message, max_output_tokens):
        del context, message, max_output_tokens
        return ChatGeneration(
            content=WHOLE_ANSWER, provider=self.name, model=self.model
        )


class SelectivelySlowResponder:
    """只卡住指定消息，用来证明一个对话不会冻结别的对话。"""

    name = "selectively-slow"
    model = "m"
    prompt_version = "v1"

    def __init__(self) -> None:
        self.started = Event()
        self.release = Event()

    def stream(self, context, message, max_output_tokens):
        del context, max_output_tokens
        if message == "慢回答":
            self.started.set()
            yield "还在生成……"
            self.release.wait(timeout=3)
            yield "生成结束。"
            return
        yield "快速回答。"

    def respond(self, context, message, max_output_tokens):
        del context, max_output_tokens
        return ChatGeneration(
            content=f"回复：{message}", provider=self.name, model=self.model
        )


class SecretLeakingResponder(StreamingResponder):
    """模拟供应商把请求密钥拼进异常文本。"""

    api_key = "provider-secret-that-must-never-be-persisted"

    def stream(self, context, message, max_output_tokens):
        del context, message, max_output_tokens
        yield "已收到一部分。"
        raise RuntimeError(f"provider rejected {self.api_key}")


def drive(service, workspace_id, conversation_id, message="求导数"):
    return drive_turn(service, workspace_id, conversation_id, message=message)


def drive_turn(
    service,
    workspace_id,
    conversation_id,
    message="求导数",
    turn_id=None,
):
    stream = service.stream_conversation_turn(
        workspace_id,
        conversation_id,
        ConversationTurnRequest(message=message, turn_id=turn_id),
    )
    deltas: list[str] = []
    while True:
        try:
            deltas.append(next(stream))
        except StopIteration as stop:
            return deltas, stop.value


@pytest.fixture
def service_and_conversation(tmp_path):
    def build(responder):
        service = MathHarnessService(tmp_path, conversation_responder=responder)
        workspace = service.create_workspace(WorkspaceCreate(name="流式"))
        conversation = service.create_conversation(
            workspace.id, ConversationCreate(title="对话")
        )
        return service, workspace.id, conversation.id

    return build


# --- 逐段产出 ---------------------------------------------------------


def test_content_arrives_in_pieces(service_and_conversation):
    service, workspace_id, conversation_id = service_and_conversation(
        StreamingResponder()
    )

    deltas, result = drive(service, workspace_id, conversation_id)

    assert len(deltas) == len(ANSWER_PARTS)
    assert "".join(deltas) == WHOLE_ANSWER
    assert result.assistant_message.content == WHOLE_ANSWER


def test_a_responder_without_streaming_still_works(service_and_conversation):
    """降级不是错误路径，是正常路径的一种。离线响应器就没有流式可言。"""

    service, workspace_id, conversation_id = service_and_conversation(PlainResponder())

    deltas, result = drive(service, workspace_id, conversation_id)

    assert deltas == [WHOLE_ANSWER]
    assert result.assistant_message.content == WHOLE_ANSWER


def test_partial_content_is_kept_when_the_stream_breaks(service_and_conversation):
    """已经吐出去的字不能不算数。

    用户看着字一个个出现，最后告诉他「刚才那些作废」是最糟的处理方式。
    """

    service, workspace_id, conversation_id = service_and_conversation(
        StreamingResponder(fail_after=2)
    )

    deltas, result = drive(service, workspace_id, conversation_id)

    assert "".join(deltas) == "".join(ANSWER_PARTS[:2])
    assert result.assistant_message.content == "".join(ANSWER_PARTS[:2])
    assert result.assistant_message.generation_error is not None


def test_an_interrupted_reply_is_never_checked_or_learned(
    service_and_conversation,
):
    """半成品可以留给用户看，但绝不能伪装成完整答案进入成长闭环。"""

    service, workspace_id, conversation_id = service_and_conversation(
        StreamingResponder(
            parts=[
                "先用求导：diff(x**2, x) = 2*x\n",
                "接下来继续讨论定义域。",
            ],
            fail_after=1,
        )
    )

    deltas, result = drive(service, workspace_id, conversation_id, "求 x**2 的导数")
    message = result.assistant_message

    assert "".join(deltas) == "先用求导：diff(x**2, x) = 2*x\n"
    assert message.generation_error is not None
    assert message.conclusion_confidence is None
    assert message.process_confidence is None
    assert message.checked_claims == []
    assert result.knowledge_draft is None

    reloaded = service.list_conversation_messages(workspace_id, conversation_id)
    assert reloaded[-1].generation_error == message.generation_error
    assert service.list_examples(workspace_id) == []


def test_a_slow_stream_does_not_block_another_conversation(tmp_path):
    """网络等待不该占着全服务的知识锁；不同对话必须能并行完成。"""

    responder = SelectivelySlowResponder()
    service = MathHarnessService(tmp_path, conversation_responder=responder)
    workspace = service.create_workspace(WorkspaceCreate(name="并发"))
    slow = service.create_conversation(workspace.id, ConversationCreate(title="慢"))
    fast = service.create_conversation(workspace.id, ConversationCreate(title="快"))
    slow_done = Event()
    fast_done = Event()

    def run_slow() -> None:
        drive(service, workspace.id, slow.id, "慢回答")
        slow_done.set()

    def run_fast() -> None:
        drive(service, workspace.id, fast.id, "快回答")
        fast_done.set()

    slow_thread = Thread(target=run_slow)
    fast_thread = Thread(target=run_fast)
    slow_thread.start()
    assert responder.started.wait(timeout=1)
    fast_thread.start()
    try:
        assert fast_done.wait(timeout=0.5)
    finally:
        responder.release.set()
        slow_thread.join(timeout=3)
        fast_thread.join(timeout=3)
    assert slow_done.is_set()


def test_closing_a_stream_releases_the_conversation_lock(tmp_path):
    """客户端离开页面后生成器会被关闭；这个对话必须还能继续发送。"""

    responder = SelectivelySlowResponder()
    service = MathHarnessService(tmp_path, conversation_responder=responder)
    workspace = service.create_workspace(WorkspaceCreate(name="关闭流"))
    conversation = service.create_conversation(
        workspace.id, ConversationCreate(title="对话")
    )
    stream = service.stream_conversation_turn(
        workspace.id,
        conversation.id,
        ConversationTurnRequest(message="慢回答"),
    )

    assert next(stream) == "还在生成……"
    stream.close()

    deltas, result = drive(service, workspace.id, conversation.id, "快回答")
    assert deltas == ["快速回答。"]
    assert result.assistant_message.content == "快速回答。"


def test_a_stream_that_never_yields_still_leaves_a_message(service_and_conversation):
    service, workspace_id, conversation_id = service_and_conversation(
        StreamingResponder(fail_after=0)
    )

    deltas, result = drive(service, workspace_id, conversation_id)

    assert deltas
    assert result.assistant_message.content


# --- 与非流式路径逐字段一致 -------------------------------------------


def test_streaming_and_sync_produce_the_same_turn(tmp_path):
    """两条路共用同一段准备和同一段收尾，差别只在正文是一次拿到还是逐段拿到。

    不一致的话，用户会因为「用了哪条接口」而得到不同的可信度、不同的入库结果。
    """

    def run(streaming: bool):
        root = tmp_path / ("stream" if streaming else "sync")
        service = MathHarnessService(
            root,
            conversation_responder=(
                StreamingResponder() if streaming else PlainResponder()
            ),
        )
        workspace = service.create_workspace(WorkspaceCreate(name="一致性"))
        conversation = service.create_conversation(
            workspace.id, ConversationCreate(title="对话")
        )
        if streaming:
            _, result = drive(service, workspace.id, conversation.id)
        else:
            result = service.send_conversation_turn(
                workspace.id,
                conversation.id,
                ConversationTurnRequest(message="求导数"),
            )
        message = result.assistant_message
        return (
            message.content,
            message.conclusion_confidence,
            message.process_confidence,
            message.checked_claims,
            result.knowledge_draft is not None,
        )

    assert run(streaming=True) == run(streaming=False)


def test_confidence_is_produced_after_the_whole_reply(service_and_conversation):
    """检查要看完整的推导。半条推导上跑逐步检查，给出的判断没有意义。"""

    service, workspace_id, conversation_id = service_and_conversation(
        StreamingResponder(
            parts=[
                "(a+b)^2 = a^2 + 2ab + b^2\n",
                "(a-b)^2 = a^2 - 2ab - b^2\n",  # 错：b² 符号
                "相减得 (a+b)^2 - (a-b)^2 = 4ab",
            ]
        )
    )

    _, result = drive(service, workspace_id, conversation_id, "化简")
    message = result.assistant_message

    assert message.conclusion_confidence is ConclusionConfidence.VERIFIED
    assert message.process_confidence is ProcessConfidence.STEP_FAILED


def test_replaying_a_turn_does_not_generate_again(service_and_conversation):
    """同一个 turn_id 重发不该再花一次模型调用。"""

    service, workspace_id, conversation_id = service_and_conversation(
        StreamingResponder()
    )
    request = ConversationTurnRequest(message="求导数", turn_id="fixed-turn")
    first = service.send_conversation_turn(workspace_id, conversation_id, request)

    stream = service.stream_conversation_turn(workspace_id, conversation_id, request)
    deltas = []
    while True:
        try:
            deltas.append(next(stream))
        except StopIteration as stop:
            replayed = stop.value
            break

    assert replayed.assistant_message.id == first.assistant_message.id
    assert "".join(deltas) == first.assistant_message.content


# --- 生成器本身 -------------------------------------------------------


def test_a_plain_responder_is_not_mistaken_for_a_streaming_one():
    assert not isinstance(PlainResponder(), StreamingResponderProtocol)
    assert isinstance(StreamingResponder(), StreamingResponderProtocol)


def test_stream_chat_generation_returns_a_complete_generation():
    generator = stream_chat_generation(StreamingResponder(), None, "问", 100)
    chunks = []
    while True:
        try:
            chunks.append(next(generator))
        except StopIteration as stop:
            generation = stop.value
            break

    assert "".join(chunks) == WHOLE_ANSWER
    assert generation.content == WHOLE_ANSWER
    assert generation.error is None


def test_a_broken_stream_records_the_error_on_the_generation():
    generator = stream_chat_generation(
        StreamingResponder(fail_after=2), None, "问", 100
    )
    while True:
        try:
            next(generator)
        except StopIteration as stop:
            generation = stop.value
            break

    assert generation.error is not None
    assert generation.content == "".join(ANSWER_PARTS[:2])


def test_stream_errors_redact_provider_credentials():
    generator = stream_chat_generation(SecretLeakingResponder(), None, "问", 100)
    while True:
        try:
            next(generator)
        except StopIteration as stop:
            generation = stop.value
            break

    assert generation.error is not None
    assert SecretLeakingResponder.api_key not in generation.error
    assert "[redacted]" in generation.error


# --- SSE 接口 ---------------------------------------------------------


def read_events(response) -> list[dict]:
    events = []
    for line in response.iter_lines():
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
    return events


def test_the_endpoint_streams_deltas_then_one_result(tmp_path):
    app = create_app(tmp_path)
    app.state.service.conversation_responder = StreamingResponder()
    client = TestClient(app)
    workspace = client.post("/workspaces", json={"name": "w"}).json()
    conversation = client.post(
        f"/workspaces/{workspace['id']}/conversations", json={"title": "t"}
    ).json()

    with client.stream(
        "POST",
        f"/workspaces/{workspace['id']}/conversations/{conversation['id']}/turns/stream",
        json={"message": "求导数"},
    ) as response:
        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]
        events = read_events(response)

    kinds = [event["type"] for event in events]
    assert kinds[-1] == "result"
    assert kinds.count("result") == 1
    assert set(kinds[:-1]) == {"delta"}


def test_confidence_appears_only_in_the_result_event(tmp_path):
    """徽章不能一边流一边变。"""

    app = create_app(tmp_path)
    app.state.service.conversation_responder = StreamingResponder()
    client = TestClient(app)
    workspace = client.post("/workspaces", json={"name": "w"}).json()
    conversation = client.post(
        f"/workspaces/{workspace['id']}/conversations", json={"title": "t"}
    ).json()

    with client.stream(
        "POST",
        f"/workspaces/{workspace['id']}/conversations/{conversation['id']}/turns/stream",
        json={"message": "求导数"},
    ) as response:
        events = read_events(response)

    for event in events[:-1]:
        assert set(event) == {"type", "text"}
    message = events[-1]["result"]["assistant_message"]
    assert message["conclusion_confidence"] == "verified"


def test_an_interrupted_endpoint_result_is_visibly_untrusted(tmp_path):
    app = create_app(tmp_path)
    app.state.service.conversation_responder = StreamingResponder(
        parts=["diff(x**2, x) = 2*x", "；继续解释"],
        fail_after=1,
    )
    client = TestClient(app)
    workspace = client.post("/workspaces", json={"name": "w"}).json()
    conversation = client.post(
        f"/workspaces/{workspace['id']}/conversations", json={"title": "t"}
    ).json()

    with client.stream(
        "POST",
        f"/workspaces/{workspace['id']}/conversations/{conversation['id']}/turns/stream",
        json={"message": "求导数"},
    ) as response:
        events = read_events(response)

    result = events[-1]["result"]
    message = result["assistant_message"]
    assert message["generation_error"]
    assert message["conclusion_confidence"] is None
    assert message["process_confidence"] is None
    assert message["checked_claims"] == []
    assert result["knowledge_draft"] is None


def test_the_endpoint_reports_a_failure_as_an_event(tmp_path):
    """连回合都没跑起来时，客户端要收到一条能显示的错误，而不是一个断掉的连接。"""

    app = create_app(tmp_path)
    client = TestClient(app)
    workspace = client.post("/workspaces", json={"name": "w"}).json()

    with client.stream(
        "POST",
        f"/workspaces/{workspace['id']}/conversations/does-not-exist/turns/stream",
        json={"message": "问题"},
    ) as response:
        events = read_events(response)

    assert events[-1]["type"] == "error"


# --- 空流：吐不出东西，也不报错 ---------------------------------------
#
# 流式依赖服务端事件的**具体形状**，而各家 OpenAI 兼容服务并不一致：有的不支持在
# Responses 上开流，有的事件名不同。一条都匹配不上时既没有异常也没有正文，而同一个
# 请求走非流式完全正常。
#
# 不处理的话有两重后果：用户每一轮都看到「生成失败」，而模型其实是好的；而且那句
# 固定文案 `generation_error` 为空，会被当成正常回答去跑检查、进知识库。


class SilentStreamResponder:
    """实现了 stream()，但一段都不吐、也不抛错。非流式那条路是好的。"""

    name = "silent"
    model = "m"
    prompt_version = "v1"

    def __init__(self) -> None:
        self.respond_calls = 0

    def stream(self, context, message, max_output_tokens):
        del context, message, max_output_tokens
        return iter(())

    def respond(self, context, message, max_output_tokens):
        del context, message, max_output_tokens
        self.respond_calls += 1
        return ChatGeneration(
            content=WHOLE_ANSWER, provider=self.name, model=self.model
        )


def test_an_empty_stream_falls_back_to_a_normal_call(service_and_conversation):
    responder = SilentStreamResponder()
    service, workspace_id, conversation_id = service_and_conversation(responder)

    deltas, result = drive(service, workspace_id, conversation_id)
    message = result.assistant_message

    assert "".join(deltas) == WHOLE_ANSWER
    assert message.content == WHOLE_ANSWER
    assert responder.respond_calls == 1
    # 退回成功就是一次正常回合：该有可信度，该进知识库。
    assert message.generation_error is None
    assert message.conclusion_confidence is not None


def test_a_provider_that_refuses_to_stream_falls_back(service_and_conversation):
    """服务端直接拒绝开流，是同一种情况的另一半。"""

    class RefusingResponder(SilentStreamResponder):
        name = "refusing"

        def stream(self, context, message, max_output_tokens):
            del context, message, max_output_tokens
            raise RuntimeError("this provider does not support stream=True")
            yield ""  # pragma: no cover

    responder = RefusingResponder()
    service, workspace_id, conversation_id = service_and_conversation(responder)

    deltas, result = drive(service, workspace_id, conversation_id)

    assert "".join(deltas) == WHOLE_ANSWER
    assert result.assistant_message.content == WHOLE_ANSWER
    assert responder.respond_calls == 1


def test_partial_content_is_never_replaced_by_a_fallback_call(
    service_and_conversation,
):
    """已经吐了一半再断：保留现场，**不能**再调一次。

    再调一次要么让用户看到正文突然被换掉，要么在已显示的字后面接上另一次生成的
    后半段。中断就是中断。
    """

    class PartialThenBroken(SilentStreamResponder):
        name = "partial"

        def stream(self, context, message, max_output_tokens):
            del context, message, max_output_tokens
            yield ANSWER_PARTS[0]
            raise RuntimeError("连接断了")

    responder = PartialThenBroken()
    service, workspace_id, conversation_id = service_and_conversation(responder)

    deltas, result = drive(service, workspace_id, conversation_id)

    assert "".join(deltas) == ANSWER_PARTS[0]
    assert responder.respond_calls == 0
    assert result.assistant_message.generation_error is not None


def test_both_paths_failing_leaves_a_readable_interrupted_message(
    service_and_conversation,
):
    class HopelessResponder(SilentStreamResponder):
        name = "hopeless"

        def respond(self, context, message, max_output_tokens):
            del context, message, max_output_tokens
            self.respond_calls += 1
            raise RuntimeError("模型服务不可用")

    service, workspace_id, conversation_id = service_and_conversation(
        HopelessResponder()
    )

    _, result = drive(service, workspace_id, conversation_id)
    message = result.assistant_message

    assert "生成失败" in message.content
    # 这不是一次成功的回答：不许拿可信度，不许进知识库。
    assert message.generation_error is not None
    assert message.conclusion_confidence is None
    assert result.knowledge_draft is None


def test_a_failed_fallback_redacts_provider_credentials(service_and_conversation):
    """退回也可能带出密钥——脱敏不能只在流式那一条路上做。"""

    class LeakyResponder(SilentStreamResponder):
        name = "leaky"
        api_key = "sk-abcdefghijklmnopqrstuvwxyz123456"

        def respond(self, context, message, max_output_tokens):
            del context, message, max_output_tokens
            raise RuntimeError(f"401 with key {self.api_key}")

    service, workspace_id, conversation_id = service_and_conversation(LeakyResponder())

    _, result = drive(service, workspace_id, conversation_id)
    error = result.assistant_message.generation_error

    assert error
    assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in error
    assert "[redacted]" in error


# --- 停止生成 ---------------------------------------------------------
#
# 停止**不是取消**。已经吐出去的正文照常落库，只是带上中断标记——不检查、不给可信度、
# 不进知识库。用户主动叫停和网络断掉在这一点上没有区别，走的是同一条路。


class GatedResponder:
    """吐出第一段后等一个闸门，用来在两段之间稳定地插入停止请求。"""

    name = "gated"
    model = "m"
    prompt_version = "v1"

    def __init__(self) -> None:
        self.released = Event()
        self.first_sent = Event()
        self.respond_calls = 0

    def stream(self, context, message, max_output_tokens):
        del context, message, max_output_tokens
        yield "先求导：diff(x**2, x) = 2*x\n"
        self.first_sent.set()
        self.released.wait(timeout=3)
        yield "再讨论定义域。"

    def respond(self, context, message, max_output_tokens):
        del context, message, max_output_tokens
        self.respond_calls += 1
        return ChatGeneration(
            content=WHOLE_ANSWER, provider=self.name, model=self.model
        )


def test_stopping_keeps_what_arrived_and_marks_it_interrupted(
    service_and_conversation,
):
    responder = GatedResponder()
    service, workspace_id, conversation_id = service_and_conversation(responder)
    stream = service.stream_conversation_turn(
        workspace_id,
        conversation_id,
        ConversationTurnRequest(message="求 x**2 的导数", turn_id="turn-stop"),
    )

    first = next(stream)
    service.request_turn_stop("turn-stop")
    responder.released.set()
    with pytest.raises(StopIteration) as stop:
        next(stream)
    result = stop.value.value
    message = result.assistant_message

    assert message.content == first
    # 停下来之后不再往屏幕上接字。
    assert "定义域" not in message.content
    assert message.generation_error is not None


def test_a_stopped_reply_is_never_checked_or_learned(service_and_conversation):
    """这条正文里有一条 SymPy 验得过的等式。它照样不许拿可信度。"""

    responder = GatedResponder()
    service, workspace_id, conversation_id = service_and_conversation(responder)
    stream = service.stream_conversation_turn(
        workspace_id,
        conversation_id,
        ConversationTurnRequest(message="求 x**2 的导数", turn_id="turn-stop"),
    )

    next(stream)
    service.request_turn_stop("turn-stop")
    responder.released.set()
    with pytest.raises(StopIteration) as stop:
        next(stream)
    message = stop.value.value.assistant_message

    assert message.conclusion_confidence is None
    assert message.process_confidence is None
    assert message.checked_claims == []
    assert stop.value.value.knowledge_draft is None
    assert service.list_examples(workspace_id) == []


def test_stopping_does_not_trigger_a_full_regeneration(service_and_conversation):
    """空流会退回非流式再试一次。被叫停的那次**不能**——那是在用户说「别答了」
    之后再完整生成一遍，既费钱又违背他刚表达的意思。"""

    responder = SilentStreamResponder()
    service, workspace_id, conversation_id = service_and_conversation(responder)
    service.request_turn_stop("turn-stop")

    _, result = drive_turn(service, workspace_id, conversation_id, turn_id="turn-stop")

    assert responder.respond_calls == 0
    assert result.assistant_message.generation_error is not None


def test_a_stop_only_applies_to_the_turn_it_names(service_and_conversation):
    service, workspace_id, conversation_id = service_and_conversation(
        StreamingResponder()
    )
    service.request_turn_stop("some-other-turn")

    deltas, result = drive_turn(
        service, workspace_id, conversation_id, turn_id="turn-mine"
    )

    assert "".join(deltas) == WHOLE_ANSWER
    assert result.assistant_message.generation_error is None


def test_resending_after_a_stop_generates_normally(service_and_conversation):
    """停止之后马上重发是最常见的下一步。它必须是一次干净的生成。"""

    service, workspace_id, conversation_id = service_and_conversation(
        StreamingResponder()
    )
    service.request_turn_stop("turn-1")
    _, stopped = drive_turn(service, workspace_id, conversation_id, turn_id="turn-1")
    assert stopped.assistant_message.generation_error is not None

    deltas, result = drive_turn(
        service, workspace_id, conversation_id, turn_id="turn-2"
    )

    assert result.assistant_message.generation_error is None
    assert "".join(deltas) == WHOLE_ANSWER


def test_the_stop_endpoint_ends_the_turn_with_a_result_event(tmp_path):
    """客户端按下停止后**不断开那条流**：中断消息由服务端正常收尾后送回来。"""

    app = create_app(tmp_path)
    responder = GatedResponder()
    app.state.service.conversation_responder = responder
    client = TestClient(app)
    workspace = client.post("/workspaces", json={"name": "w"}).json()
    conversation = client.post(
        f"/workspaces/{workspace['id']}/conversations", json={"title": "t"}
    ).json()
    base = f"/workspaces/{workspace['id']}/conversations/{conversation['id']}"
    events: list[dict] = []

    def read() -> None:
        with client.stream(
            "POST",
            f"{base}/turns/stream",
            json={"message": "求 x**2 的导数", "turn_id": "turn-http"},
        ) as response:
            events.extend(read_events(response))

    reader = Thread(target=read)
    reader.start()
    try:
        assert responder.first_sent.wait(timeout=3)
        stopped = client.post(f"{base}/turns/turn-http/stop")
        assert stopped.status_code == 202
    finally:
        responder.released.set()
        reader.join(timeout=5)

    assert events[-1]["type"] == "result"
    message = events[-1]["result"]["assistant_message"]
    assert message["generation_error"]
    assert message["conclusion_confidence"] is None
    assert events[-1]["result"]["knowledge_draft"] is None


def test_stopping_an_unknown_conversation_is_a_not_found(tmp_path):
    app = create_app(tmp_path)
    client = TestClient(app)
    workspace = client.post("/workspaces", json={"name": "w"}).json()

    response = client.post(
        f"/workspaces/{workspace['id']}/conversations/nope/turns/turn-x/stop"
    )

    assert response.status_code == 404
