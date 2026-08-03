from __future__ import annotations

import json
import os
import re
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

# 通用 OpenAI 兼容 provider 配置。
#
# 现有的 provider 类（OpenAIStructuredMethodExtractor 等）本来就接受 base_url、
# provider_name 和 structured_output_mode——`providers/mimo.py` 就只是它们的薄子类。
# 所以「接入任意运营商」不需要新写 provider 类，只需要把配置传进去。
#
# 三条设计约束：
#   1. 结构与密钥分离。档案 JSON 不含密钥，可以安全落 UserDefaults；密钥各走一个
#      环境变量，只在子进程环境中存在。
#   2. 向后兼容是硬要求。没有 MATH_HARNESS_PROVIDERS 时，所有分发器逐字节走原来的
#      逐变量路径——math-harness-eval 强制规则提取器 + 离线 SymPy 来保证可复现，
#      现有测试也依赖那条路径。
#   3. 配置坏了要安全降级，不能让整个后端起不来。用户改错一个字符就打不开 App 是
#      不可接受的；降级到离线并把原因留给 /health 暴露。

PROVIDERS_ENV = "MATH_HARNESS_PROVIDERS"
ROLES_ENV = "MATH_HARNESS_ROLES"
KEY_ENV_PREFIX = "MATH_HARNESS_PROVIDER_KEY__"

ROLE_CONVERSATION = "conversation"
ROLE_SOLVER = "solver"
ROLE_TARGET_DRAFTER = "target_drafter"
ROLE_METHOD_EXTRACTOR = "method_extractor"
ROLE_MEMORY_EXTRACTOR = "memory_extractor"

ROLES: tuple[str, ...] = (
    ROLE_CONVERSATION,
    ROLE_SOLVER,
    ROLE_TARGET_DRAFTER,
    ROLE_METHOD_EXTRACTOR,
    ROLE_MEMORY_EXTRACTOR,
)

# 绑定到这个值表示该角色走零成本的离线路径（求解用 SymPy，提炼用规则）。
OFFLINE_PROFILE = "offline"

StructuredOutputMode = Literal["json_schema", "json_object"]

_ALLOWED_EFFORTS = {"none", "low", "medium", "high", "xhigh"}
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def key_env_name(profile_id: str) -> str:
    """档案 ID 对应的密钥环境变量名。

    Swift 侧必须用完全相同的规则生成，两边各有断言钉住。
    """

    sanitized = re.sub(r"[^A-Za-z0-9]", "_", profile_id).upper()
    return f"{KEY_ENV_PREFIX}{sanitized}"


class ProviderProfile(BaseModel):
    """一个 OpenAI 兼容服务端点。不含密钥。"""

    id: str = Field(min_length=1, max_length=64)
    name: str = Field(default="", max_length=120)
    base_url: str = Field(min_length=1, max_length=500)
    default_model: str = Field(min_length=1, max_length=200)
    # MiMo 只支持 JSON Object 模式，OpenAI 支持严格 Schema。选错会让结构化输出
    # 在运行时才失败，所以这是必须暴露的配置，不是内部细节。
    structured_output_mode: StructuredOutputMode = "json_schema"
    timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    max_output_tokens: int = Field(default=3_000, ge=256, le=32_000)

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if not _ID_PATTERN.fullmatch(value):
            raise ValueError("profile id must be alphanumeric with . _ -")
        return value

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped.startswith(("http://", "https://")):
            raise ValueError("base_url must start with http:// or https://")
        return stripped.rstrip("/")

    @property
    def json_object_retries(self) -> int:
        """JSON Object 模式不保证符合业务 Schema，本地校验失败时重试一次。"""

        return 1 if self.structured_output_mode == "json_object" else 0


class RoleBinding(BaseModel):
    """某个角色使用哪个档案、哪个模型。"""

    profile: str = Field(min_length=1, max_length=64)
    model: str | None = Field(default=None, max_length=200)
    reasoning_effort: str = "medium"
    timeout_seconds: float | None = Field(default=None, gt=0, le=600)
    max_output_tokens: int | None = Field(default=None, ge=256, le=32_000)

    @field_validator("reasoning_effort")
    @classmethod
    def validate_effort(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in _ALLOWED_EFFORTS:
            raise ValueError(
                f"reasoning_effort must be one of {sorted(_ALLOWED_EFFORTS)}"
            )
        return normalized

    @property
    def is_offline(self) -> bool:
        return self.profile.strip().lower() == OFFLINE_PROFILE


class ResolvedRole(BaseModel):
    """某角色最终生效的完整参数，可直接展开给 provider 类。"""

    role: str
    profile_id: str
    provider_name: str
    api_key: str | None
    base_url: str
    model: str
    reasoning_effort: str
    timeout_seconds: float
    max_output_tokens: int
    structured_output_mode: StructuredOutputMode
    json_object_retries: int


class ProviderRuntimeConfig(BaseModel):
    profiles: dict[str, ProviderProfile]
    roles: dict[str, RoleBinding]

    def resolve(self, role: str) -> ResolvedRole | None:
        """返回该角色的生效参数；离线或未绑定返回 None。"""

        binding = self.roles.get(role)
        if binding is None or binding.is_offline:
            return None
        profile = self.profiles.get(binding.profile)
        if profile is None:
            return None
        return ResolvedRole(
            role=role,
            profile_id=profile.id,
            # 审计记录里要能看出这次调用打给了谁。
            provider_name=f"compat:{profile.name or profile.id}",
            api_key=os.getenv(key_env_name(profile.id)),
            base_url=profile.base_url,
            model=binding.model or profile.default_model,
            reasoning_effort=binding.reasoning_effort,
            timeout_seconds=binding.timeout_seconds or profile.timeout_seconds,
            max_output_tokens=binding.max_output_tokens or profile.max_output_tokens,
            structured_output_mode=profile.structured_output_mode,
            json_object_retries=profile.json_object_retries,
        )

    def is_offline(self, role: str) -> bool:
        binding = self.roles.get(role)
        return binding is not None and binding.is_offline


_last_error: str | None = None


def provider_config_error() -> str | None:
    """最近一次配置解析失败的原因，供 /health 暴露给设置界面。"""

    return _last_error


def load_provider_config() -> ProviderRuntimeConfig | None:
    """从环境读取档案与角色绑定。

    未配置时返回 None，调用方回退到既有的逐变量行为。配置损坏时同样返回 None 并
    记录原因——宁可退回离线也不要让后端起不来。
    """

    global _last_error

    raw_profiles = os.getenv(PROVIDERS_ENV)
    if raw_profiles is None or not raw_profiles.strip():
        _last_error = None
        return None

    try:
        profile_data = json.loads(raw_profiles)
        if not isinstance(profile_data, list):
            raise TypeError(f"{PROVIDERS_ENV} must be a JSON array")
        profiles = {item["id"]: ProviderProfile(**item) for item in profile_data}

        raw_roles = os.getenv(ROLES_ENV, "{}")
        role_data = json.loads(raw_roles) if raw_roles.strip() else {}
        if not isinstance(role_data, dict):
            raise TypeError(f"{ROLES_ENV} must be a JSON object")
        roles = {
            name: RoleBinding(**value)
            for name, value in role_data.items()
            if name in ROLES
        }
    except (ValueError, TypeError, KeyError, ValidationError) as exc:
        _last_error = f"{exc.__class__.__name__}: {exc}"[:500]
        return None

    unknown = {
        binding.profile
        for binding in roles.values()
        if not binding.is_offline and binding.profile not in profiles
    }
    if unknown:
        _last_error = f"unknown provider profile(s): {', '.join(sorted(unknown))}"
        return None

    _last_error = None
    return ProviderRuntimeConfig(profiles=profiles, roles=roles)


def resolve_role(role: str) -> ResolvedRole | None:
    """便捷入口：加载配置并解析单个角色。"""

    config = load_provider_config()
    if config is None:
        return None
    return config.resolve(role)


def resolve_override(
    role: str,
    profile_id: str,
    model: str | None = None,
) -> ResolvedRole | None:
    """按显式指定的档案与模型解析角色，用于单次请求的临时覆盖。

    与 `resolve_role` 的区别是不看角色绑定：调用方已经明确说了要用哪个档案。
    档案不存在时返回 None，调用方回退到默认 provider——用户删掉一个档案后，引用它的
    历史对话应当继续可用，而不是报错。
    """

    if profile_id.strip().lower() == OFFLINE_PROFILE:
        return None
    config = load_provider_config()
    if config is None:
        return None
    profile = config.profiles.get(profile_id)
    if profile is None:
        return None
    binding = config.roles.get(role)
    return config.model_copy(
        update={
            "roles": {
                **config.roles,
                role: RoleBinding(
                    profile=profile_id,
                    model=model,
                    reasoning_effort=(
                        binding.reasoning_effort if binding else "medium"
                    ),
                ),
            }
        }
    ).resolve(role)


def role_is_configured(role: str) -> bool:
    """该角色是否由新配置接管（含显式绑定到离线）。"""

    config = load_provider_config()
    return config is not None and role in config.roles
