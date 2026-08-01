from __future__ import annotations

from typing import Any

from math_harness.providers.openai import OpenAIStructuredMethodExtractor
from math_harness.providers.openai_solver import OpenAISolutionGenerator
from math_harness.providers.openai_target import OpenAIStructuredTargetDrafter

DEFAULT_MIMO_BASE_URL = "https://api.xiaomimimo.com/v1"
DEFAULT_MIMO_MODEL = "mimo-v2.5-pro"


def _validate_mimo_effort(reasoning_effort: str) -> None:
    if reasoning_effort not in {"none", "high"}:
        raise ValueError("MiMo reasoning_effort must be either 'none' or 'high'")


class MiMoStructuredMethodExtractor(OpenAIStructuredMethodExtractor):
    """Xiaomi MiMo method extraction over its OpenAI-compatible Responses API."""

    def __init__(
        self,
        *,
        api_key: str | None,
        base_url: str = DEFAULT_MIMO_BASE_URL,
        model: str = DEFAULT_MIMO_MODEL,
        reasoning_effort: str = "none",
        max_output_tokens: int = 3_000,
        client: Any | None = None,
    ) -> None:
        _validate_mimo_effort(reasoning_effort)
        super().__init__(
            model=model,
            reasoning_effort=reasoning_effort,
            max_output_tokens=max_output_tokens,
            api_key=api_key,
            base_url=base_url,
            provider_name="xiaomi_mimo",
            structured_output_mode="json_object",
            json_object_retries=1,
            client=client,
        )


class MiMoSolutionGenerator(OpenAISolutionGenerator):
    """Xiaomi MiMo candidate solving with deterministic SymPy fallback upstream."""

    def __init__(
        self,
        *,
        api_key: str | None,
        base_url: str = DEFAULT_MIMO_BASE_URL,
        model: str = DEFAULT_MIMO_MODEL,
        reasoning_effort: str = "none",
        timeout_seconds: float = 60.0,
        client: Any | None = None,
    ) -> None:
        _validate_mimo_effort(reasoning_effort)
        super().__init__(
            model=model,
            reasoning_effort=reasoning_effort,
            timeout_seconds=timeout_seconds,
            api_key=api_key,
            base_url=base_url,
            provider_name="xiaomi_mimo",
            structured_output_mode="json_object",
            json_object_retries=1,
            client=client,
        )


class MiMoStructuredTargetDrafter(OpenAIStructuredTargetDrafter):
    """Xiaomi MiMo target suggestions over its OpenAI-compatible API."""

    def __init__(
        self,
        *,
        api_key: str | None,
        base_url: str = DEFAULT_MIMO_BASE_URL,
        model: str = DEFAULT_MIMO_MODEL,
        reasoning_effort: str = "none",
        max_output_tokens: int = 1_500,
        timeout_seconds: float = 60.0,
        client: Any | None = None,
    ) -> None:
        _validate_mimo_effort(reasoning_effort)
        super().__init__(
            model=model,
            reasoning_effort=reasoning_effort,
            max_output_tokens=max_output_tokens,
            timeout_seconds=timeout_seconds,
            api_key=api_key,
            base_url=base_url,
            provider_name="xiaomi_mimo",
            structured_output_mode="json_object",
            json_object_retries=1,
            client=client,
        )
