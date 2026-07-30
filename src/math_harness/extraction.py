from __future__ import annotations

import os
from typing import Protocol

from math_harness.methods import MethodExtractor
from math_harness.models import (
    ExtractionStatus,
    MethodExtractionResult,
)


class MethodExtractorProtocol(Protocol):
    name: str

    def extract(
        self, problem: str, solution: str, hint: str | None = None
    ) -> MethodExtractionResult: ...


class FallbackMethodExtractor:
    """Use a primary semantic extractor, then fall back to deterministic rules."""

    def __init__(
        self,
        primary: MethodExtractorProtocol,
        fallback: MethodExtractorProtocol | None = None,
    ) -> None:
        self.primary = primary
        self.fallback = fallback or MethodExtractor()
        self.name = f"{primary.name}->{self.fallback.name}"
        self.prompt_version = getattr(primary, "prompt_version", "unknown")

    def extract(
        self, problem: str, solution: str, hint: str | None = None
    ) -> MethodExtractionResult:
        try:
            return self.primary.extract(problem, solution, hint)
        except Exception as exc:  # noqa: BLE001
            result = self.fallback.extract(problem, solution, hint)
            primary_model = getattr(self.primary, "model", None)
            error = f"{exc.__class__.__name__}: {exc}"[:2_000]
            return result.model_copy(
                update={
                    "trace": result.trace.model_copy(
                        update={
                            "provider": self.name,
                            "model": primary_model,
                            "status": ExtractionStatus.FALLBACK,
                            "fallback_used": True,
                            "error": error,
                        }
                    )
                }
            )


def build_method_extractor_from_env() -> MethodExtractorProtocol:
    provider = os.getenv("MATH_HARNESS_METHOD_EXTRACTOR", "rules").strip().lower()
    if provider == "rules":
        return MethodExtractor()
    if provider == "openai":
        from math_harness.providers.openai import OpenAIStructuredMethodExtractor

        primary = OpenAIStructuredMethodExtractor(
            model=os.getenv("MATH_HARNESS_OPENAI_MODEL", "gpt-5.6-terra"),
            reasoning_effort=os.getenv("MATH_HARNESS_OPENAI_REASONING_EFFORT", "low"),
        )
        return FallbackMethodExtractor(primary)
    if provider == "mimo":
        from math_harness.providers.mimo import (
            DEFAULT_MIMO_BASE_URL,
            DEFAULT_MIMO_MODEL,
            MiMoStructuredMethodExtractor,
        )

        primary = MiMoStructuredMethodExtractor(
            api_key=os.getenv("MIMO_API_KEY"),
            base_url=os.getenv(
                "MATH_HARNESS_MIMO_BASE_URL",
                DEFAULT_MIMO_BASE_URL,
            ),
            model=os.getenv(
                "MATH_HARNESS_MIMO_MODEL",
                DEFAULT_MIMO_MODEL,
            ),
            reasoning_effort=os.getenv(
                "MATH_HARNESS_MIMO_EXTRACTION_REASONING_EFFORT",
                "none",
            ),
            max_output_tokens=_bounded_int_env(
                "MATH_HARNESS_MIMO_EXTRACTION_MAX_OUTPUT_TOKENS",
                default=3_000,
                minimum=256,
                maximum=8_000,
            ),
        )
        return FallbackMethodExtractor(primary)
    raise ValueError(
        "MATH_HARNESS_METHOD_EXTRACTOR must be 'rules', 'openai', or 'mimo'"
    )


def _bounded_int_env(
    name: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value
