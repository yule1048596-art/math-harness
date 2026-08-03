from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from math_harness.api import create_app
from math_harness.conversation import OfflineConversationResponder
from math_harness.models import (
    ConversationCreate,
    ConversationTurnRequest,
    ProviderOverride,
    WorkspaceCreate,
)
from math_harness.provider_config import (
    PROVIDERS_ENV,
    ROLE_CONVERSATION,
    ROLE_SOLVER,
    ROLES_ENV,
    key_env_name,
    resolve_override,
)
from math_harness.service import MathHarnessService
from math_harness.solving import OfflineSympySolutionGenerator

PROFILE_A = {
    "id": "cheap",
    "name": "便宜档",
    "base_url": "https://api.deepseek.com/v1",
    "default_model": "deepseek-chat",
}
PROFILE_B = {
    "id": "strong",
    "name": "强模型",
    "base_url": "https://api.deepseek.com/v1",
    "default_model": "deepseek-reasoner",
}


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv(PROVIDERS_ENV, json.dumps([PROFILE_A, PROFILE_B]))
    monkeypatch.setenv(ROLES_ENV, "{}")
    monkeypatch.setenv(key_env_name("cheap"), "sk-a")
    monkeypatch.setenv(key_env_name("strong"), "sk-b")


# --- 向后兼容（本阶段最重要的约束）-------------------------------------


def test_no_override_returns_the_very_same_default_instance(tmp_path):
    """不带覆盖时必须返回默认实例**本身**，不是等价对象。

    这样「行为不变」是构造上成立的，而不是依赖两条路径碰巧算出一样的结果。
    """

    service = MathHarnessService(tmp_path)

    assert service.resolve_conversation_responder(None) is (
        service.conversation_responder
    )
    assert service.resolve_solution_generator(None) is service.generator
    assert service.resolve_target_drafter(None) is service.target_drafter


def test_no_override_holds_even_when_profiles_are_configured(tmp_path, configured):
    service = MathHarnessService(tmp_path)

    assert service.resolve_solution_generator(None) is service.generator


def test_unknown_profile_falls_back_to_the_default(tmp_path, configured):
    """用户删掉档案后，引用它的历史对话要继续可用，而不是整条路径报错。"""

    service = MathHarnessService(tmp_path)
    override = ProviderOverride(profile_id="deleted-profile")

    assert service.resolve_solution_generator(override) is service.generator


def test_override_without_any_provider_config_falls_back(tmp_path):
    service = MathHarnessService(tmp_path)

    assert (
        service.resolve_conversation_responder(ProviderOverride(profile_id="cheap"))
        is service.conversation_responder
    )


# --- 覆盖生效 ---------------------------------------------------------


def test_override_builds_a_different_client(tmp_path, configured):
    service = MathHarnessService(tmp_path)

    responder = service.resolve_conversation_responder(
        ProviderOverride(profile_id="strong")
    )

    assert not isinstance(responder, OfflineConversationResponder)
    assert responder is not service.conversation_responder
    assert responder.model == "deepseek-reasoner"


def test_override_model_beats_the_profile_default(tmp_path, configured):
    service = MathHarnessService(tmp_path)

    responder = service.resolve_conversation_responder(
        ProviderOverride(profile_id="cheap", model="deepseek-reasoner")
    )

    assert responder.model == "deepseek-reasoner"


def test_two_profiles_yield_two_clients(tmp_path, configured):
    service = MathHarnessService(tmp_path)

    cheap = service.resolve_solution_generator(ProviderOverride(profile_id="cheap"))
    strong = service.resolve_solution_generator(ProviderOverride(profile_id="strong"))

    assert cheap is not strong
    assert not isinstance(cheap, OfflineSympySolutionGenerator)


def test_offline_profile_resolves_to_the_default(tmp_path, configured):
    service = MathHarnessService(tmp_path)

    assert (
        service.resolve_solution_generator(ProviderOverride(profile_id="offline"))
        is service.generator
    )
    assert resolve_override(ROLE_SOLVER, "offline") is None


# --- 缓存 -------------------------------------------------------------


def test_same_override_reuses_one_client(tmp_path, configured):
    service = MathHarnessService(tmp_path)
    override = ProviderOverride(profile_id="cheap")

    first = service.resolve_conversation_responder(override)
    second = service.resolve_conversation_responder(override)

    assert first is second


def test_roles_do_not_share_a_cache_entry(tmp_path, configured):
    service = MathHarnessService(tmp_path)
    override = ProviderOverride(profile_id="cheap")

    responder = service.resolve_conversation_responder(override)
    generator = service.resolve_solution_generator(override)

    assert responder is not generator
    assert resolve_override(ROLE_CONVERSATION, "cheap").role == ROLE_CONVERSATION


# --- 对话记住选择 -----------------------------------------------------


def test_conversation_remembers_its_provider(tmp_path, configured):
    service = MathHarnessService(tmp_path)
    workspace = service.create_workspace(WorkspaceCreate(name="切模型"))
    conversation = service.create_conversation(workspace.id, ConversationCreate())

    updated = service.set_conversation_provider(
        workspace.id, conversation.id, ProviderOverride(profile_id="strong")
    )

    assert updated.provider is not None
    assert updated.provider.profile_id == "strong"
    assert service.get_conversation(workspace.id, conversation.id).provider == (
        updated.provider
    )


def test_conversation_provider_can_be_cleared(tmp_path, configured):
    service = MathHarnessService(tmp_path)
    workspace = service.create_workspace(WorkspaceCreate(name="清空"))
    conversation = service.create_conversation(workspace.id, ConversationCreate())
    service.set_conversation_provider(
        workspace.id, conversation.id, ProviderOverride(profile_id="strong")
    )

    cleared = service.set_conversation_provider(workspace.id, conversation.id, None)

    assert cleared.provider is None


def test_turn_records_which_model_answered(tmp_path):
    """离线时也要留下痕迹：消息上必须写清是谁生成的。"""

    service = MathHarnessService(tmp_path)
    workspace = service.create_workspace(WorkspaceCreate(name="留痕"))
    conversation = service.create_conversation(workspace.id, ConversationCreate())

    result = service.send_conversation_turn(
        workspace.id,
        conversation.id,
        ConversationTurnRequest(message="你好"),
    )

    assert result.assistant_message.provider == "offline"


def test_provider_endpoint_round_trips(tmp_path, configured):
    client = TestClient(create_app(tmp_path))
    workspace = client.post("/workspaces", json={"name": "接口"}).json()
    conversation = client.post(
        f"/workspaces/{workspace['id']}/conversations", json={"title": ""}
    ).json()

    response = client.put(
        f"/workspaces/{workspace['id']}/conversations/{conversation['id']}/provider",
        json={"profile_id": "strong", "model": "deepseek-reasoner"},
    )

    assert response.status_code == 200
    assert response.json()["provider"]["profile_id"] == "strong"


def test_conversation_provider_never_carries_a_key(tmp_path, configured):
    service = MathHarnessService(tmp_path)
    workspace = service.create_workspace(WorkspaceCreate(name="密钥"))
    conversation = service.create_conversation(workspace.id, ConversationCreate())

    updated = service.set_conversation_provider(
        workspace.id, conversation.id, ProviderOverride(profile_id="cheap")
    )

    # 备份文件不加密，落库的任何东西都要按公开数据对待。
    assert "sk-a" not in updated.model_dump_json()
