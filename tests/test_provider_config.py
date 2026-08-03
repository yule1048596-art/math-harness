from __future__ import annotations

import json

import pytest

from math_harness.conversation import (
    OfflineConversationResponder,
    build_conversation_responder_from_env,
)
from math_harness.extraction import build_method_extractor_from_env
from math_harness.memory import DisabledMemoryExtractor, build_memory_extractor_from_env
from math_harness.methods import MethodExtractor
from math_harness.provider_config import (
    KEY_ENV_PREFIX,
    PROVIDERS_ENV,
    ROLE_MEMORY_EXTRACTOR,
    ROLE_SOLVER,
    ROLE_TARGET_DRAFTER,
    ROLES,
    ROLES_ENV,
    ProviderProfile,
    key_env_name,
    load_provider_config,
    provider_config_error,
    resolve_role,
)
from math_harness.solving import (
    OfflineSympySolutionGenerator,
    build_solution_generator_from_env,
)
from math_harness.target_drafting import (
    RuleBasedTargetDrafter,
    build_target_drafter_from_env,
)

PROFILE = {
    "id": "deepseek-1",
    "name": "我的 DeepSeek",
    "base_url": "https://api.deepseek.com/v1",
    "default_model": "deepseek-chat",
    "structured_output_mode": "json_object",
    "timeout_seconds": 45.0,
    "max_output_tokens": 2_048,
}


def _configure(monkeypatch, roles: dict[str, dict], key: str | None = "sk-test"):
    monkeypatch.setenv(PROVIDERS_ENV, json.dumps([PROFILE]))
    monkeypatch.setenv(ROLES_ENV, json.dumps(roles))
    if key is not None:
        monkeypatch.setenv(key_env_name(PROFILE["id"]), key)


# --- 配置解析 -----------------------------------------------------------


def test_absent_config_returns_none(monkeypatch):
    monkeypatch.delenv(PROVIDERS_ENV, raising=False)

    assert load_provider_config() is None
    assert provider_config_error() is None


def test_key_env_name_sanitizes_ids():
    # Swift 侧必须用同一规则；两边各有断言。
    assert key_env_name("deepseek-1") == f"{KEY_ENV_PREFIX}DEEPSEEK_1"
    assert key_env_name("a.b-c") == f"{KEY_ENV_PREFIX}A_B_C"


def test_profile_rejects_non_http_base_url():
    with pytest.raises(ValueError, match="http"):
        ProviderProfile(id="x", base_url="ftp://example.com", default_model="m")


def test_base_url_trailing_slash_is_normalized():
    profile = ProviderProfile(
        id="x", base_url="https://example.com/v1/", default_model="m"
    )

    assert profile.base_url == "https://example.com/v1"


def test_json_object_mode_enables_one_retry():
    schema = ProviderProfile(id="a", base_url="https://x.co", default_model="m")
    obj = ProviderProfile(
        id="b",
        base_url="https://x.co",
        default_model="m",
        structured_output_mode="json_object",
    )

    assert schema.json_object_retries == 0
    assert obj.json_object_retries == 1


def test_role_binding_overrides_profile_defaults(monkeypatch):
    _configure(
        monkeypatch,
        {ROLE_SOLVER: {"profile": "deepseek-1", "model": "deepseek-reasoner"}},
    )

    resolved = resolve_role(ROLE_SOLVER)

    assert resolved is not None
    assert resolved.model == "deepseek-reasoner"
    assert resolved.base_url == "https://api.deepseek.com/v1"
    assert resolved.api_key == "sk-test"
    assert resolved.structured_output_mode == "json_object"
    assert resolved.json_object_retries == 1
    assert resolved.timeout_seconds == 45.0


def test_role_without_model_uses_profile_default(monkeypatch):
    _configure(monkeypatch, {ROLE_SOLVER: {"profile": "deepseek-1"}})

    assert resolve_role(ROLE_SOLVER).model == "deepseek-chat"


def test_offline_binding_resolves_to_none(monkeypatch):
    _configure(monkeypatch, {ROLE_SOLVER: {"profile": "offline"}})

    assert resolve_role(ROLE_SOLVER) is None
    assert load_provider_config().is_offline(ROLE_SOLVER)


def test_unknown_profile_degrades_and_records_reason(monkeypatch):
    _configure(monkeypatch, {ROLE_SOLVER: {"profile": "nonexistent"}})

    assert load_provider_config() is None
    assert "nonexistent" in (provider_config_error() or "")


@pytest.mark.parametrize(
    "raw",
    ["{not json", '{"id": "x"}', "[]not-json", '[{"id": "no-base-url"}]'],
)
def test_malformed_config_degrades_instead_of_crashing(monkeypatch, raw):
    # 用户改错一个字符就打不开 App 是不可接受的。
    monkeypatch.setenv(PROVIDERS_ENV, raw)
    monkeypatch.setenv(ROLES_ENV, "{}")

    assert load_provider_config() is None
    if raw != "[]not-json":
        assert provider_config_error() is not None


def test_unknown_role_names_are_ignored(monkeypatch):
    _configure(monkeypatch, {"not_a_role": {"profile": "deepseek-1"}})

    config = load_provider_config()

    assert config is not None
    assert config.roles == {}


def test_all_five_roles_are_recognized(monkeypatch):
    _configure(monkeypatch, {role: {"profile": "deepseek-1"} for role in ROLES})

    config = load_provider_config()

    assert set(config.roles) == set(ROLES)


# --- 分发器接线 ---------------------------------------------------------


def test_each_role_builds_a_compat_client(monkeypatch):
    _configure(monkeypatch, {role: {"profile": "deepseek-1"} for role in ROLES})

    solver = build_solution_generator_from_env()
    responder = build_conversation_responder_from_env()
    extractor = build_method_extractor_from_env()
    drafter = build_target_drafter_from_env()
    memory = build_memory_extractor_from_env()

    assert not isinstance(solver, OfflineSympySolutionGenerator)
    assert not isinstance(responder, OfflineConversationResponder)
    assert not isinstance(extractor, MethodExtractor)
    assert not isinstance(drafter, RuleBasedTargetDrafter)
    assert not isinstance(memory, DisabledMemoryExtractor)
    # 审计记录必须能看出这次调用打给了谁。
    assert "我的 DeepSeek" in responder.name


def test_offline_bindings_keep_the_zero_cost_paths(monkeypatch):
    _configure(monkeypatch, {role: {"profile": "offline"} for role in ROLES})

    assert isinstance(
        build_solution_generator_from_env(), OfflineSympySolutionGenerator
    )
    assert isinstance(
        build_conversation_responder_from_env(), OfflineConversationResponder
    )
    assert isinstance(build_method_extractor_from_env(), MethodExtractor)
    assert isinstance(build_target_drafter_from_env(), RuleBasedTargetDrafter)
    assert isinstance(build_memory_extractor_from_env(), DisabledMemoryExtractor)


def test_memory_extractor_without_key_is_disabled_not_broken(monkeypatch):
    # 缺密钥时每轮对话后台报错比直接停用更糟。
    _configure(
        monkeypatch, {ROLE_MEMORY_EXTRACTOR: {"profile": "deepseek-1"}}, key=None
    )
    monkeypatch.delenv(key_env_name(PROFILE["id"]), raising=False)

    assert isinstance(build_memory_extractor_from_env(), DisabledMemoryExtractor)


def test_roles_can_use_different_profiles(monkeypatch):
    cheap = {**PROFILE, "id": "cheap", "default_model": "deepseek-chat"}
    strong = {**PROFILE, "id": "strong", "default_model": "deepseek-reasoner"}
    monkeypatch.setenv(PROVIDERS_ENV, json.dumps([cheap, strong]))
    monkeypatch.setenv(
        ROLES_ENV,
        json.dumps(
            {
                ROLE_SOLVER: {"profile": "strong"},
                ROLE_MEMORY_EXTRACTOR: {"profile": "cheap"},
            }
        ),
    )

    # 分角色最主要的实际价值：记忆提取用便宜模型，求解用强模型。
    assert resolve_role(ROLE_SOLVER).model == "deepseek-reasoner"
    assert resolve_role(ROLE_MEMORY_EXTRACTOR).model == "deepseek-chat"


# --- 向后兼容（本版最重要的约束）---------------------------------------


def test_legacy_env_path_is_untouched_when_new_config_absent(monkeypatch):
    """没有新配置时，逐变量路径必须逐字不变。

    math-harness-eval 强制规则提取器 + 离线 SymPy 来保证评测可复现，现有测试也
    依赖这条路径。这条断言是那份保证的守卫。
    """

    monkeypatch.delenv(PROVIDERS_ENV, raising=False)
    monkeypatch.delenv(ROLES_ENV, raising=False)
    monkeypatch.setenv("MATH_HARNESS_SOLVER", "sympy")
    monkeypatch.setenv("MATH_HARNESS_METHOD_EXTRACTOR", "rules")
    monkeypatch.setenv("MATH_HARNESS_CONVERSATION_PROVIDER", "offline")
    monkeypatch.setenv("MATH_HARNESS_TARGET_DRAFTER", "rules")
    monkeypatch.setenv("MATH_HARNESS_MEMORY_EXTRACTOR", "disabled")

    assert isinstance(
        build_solution_generator_from_env(), OfflineSympySolutionGenerator
    )
    assert isinstance(build_method_extractor_from_env(), MethodExtractor)
    assert isinstance(
        build_conversation_responder_from_env(), OfflineConversationResponder
    )
    assert isinstance(build_target_drafter_from_env(), RuleBasedTargetDrafter)
    assert isinstance(build_memory_extractor_from_env(), DisabledMemoryExtractor)


def test_legacy_invalid_provider_still_raises(monkeypatch):
    monkeypatch.delenv(PROVIDERS_ENV, raising=False)
    monkeypatch.setenv("MATH_HARNESS_METHOD_EXTRACTOR", "nonsense")

    with pytest.raises(ValueError, match="must be"):
        build_method_extractor_from_env()


def test_new_config_takes_priority_over_legacy_vars(monkeypatch):
    monkeypatch.setenv("MATH_HARNESS_SOLVER", "sympy")
    _configure(monkeypatch, {ROLE_SOLVER: {"profile": "deepseek-1"}})

    assert not isinstance(
        build_solution_generator_from_env(), OfflineSympySolutionGenerator
    )


def test_partially_configured_roles_fall_back_per_role(monkeypatch):
    # 只绑定求解时，其余角色仍走各自的旧变量。
    monkeypatch.setenv("MATH_HARNESS_METHOD_EXTRACTOR", "rules")
    _configure(monkeypatch, {ROLE_SOLVER: {"profile": "deepseek-1"}})

    assert isinstance(build_method_extractor_from_env(), MethodExtractor)
    assert resolve_role(ROLE_TARGET_DRAFTER) is None


# --- 密钥不外泄 ---------------------------------------------------------


def test_profile_serialization_never_contains_the_key(monkeypatch):
    _configure(monkeypatch, {ROLE_SOLVER: {"profile": "deepseek-1"}})
    config = load_provider_config()

    dumped = config.profiles["deepseek-1"].model_dump_json()

    assert "sk-test" not in dumped
    assert "api_key" not in dumped
