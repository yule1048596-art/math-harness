from __future__ import annotations

import os
import re
from typing import Protocol

from math_harness.math_parser import SafeMathParser, build_symbol_table
from math_harness.models import (
    ApproachDirection,
    ExtractionStatus,
    MathTargetDraftResult,
    SolveMathTarget,
    VerificationMode,
)
from math_harness.provider_config import (
    ROLE_TARGET_DRAFTER,
    resolve_role,
    role_is_configured,
)


class TargetDrafterProtocol(Protocol):
    name: str
    prompt_version: str

    def draft(self, problem: str) -> MathTargetDraftResult: ...


def validate_drafted_target(
    target: SolveMathTarget,
    parser: SafeMathParser | None = None,
) -> None:
    """Reject model output that is outside the harness's safe math language."""

    if (
        target.mode is VerificationMode.ASYMPTOTIC_EXPANSION
        and target.remainder_power is None
    ):
        raise ValueError("asymptotic target draft requires remainder_power")
    safe_parser = parser or SafeMathParser()
    symbols = build_symbol_table(
        target.variable,
        target.parameters,
        target.assumptions,
    )
    safe_parser.parse(target.expression, symbols)
    if target.point not in {"oo", "+oo", "-oo", "infinity", "+infinity", "-infinity"}:
        safe_parser.parse(target.point, symbols)


class RuleBasedTargetDrafter:
    """Conservative local extraction for common limit and expansion wording."""

    name = "rules"
    prompt_version = "math-target-rules-v1"

    _approach_pattern = re.compile(
        r"(?P<variable>[A-Za-z][A-Za-z0-9_]*)\s*"
        r"(?:→|->|\\to)\s*"
        r"(?P<point>[+-]?(?:∞|oo|infinity)|[+-]?(?:\d+(?:\.\d+)?|\.\d+))",
        re.IGNORECASE,
    )
    _ascii_run_pattern = re.compile(r"[A-Za-z0-9_+\-*/().,^ \t]+")
    _name_pattern = re.compile(r"\b[A-Za-z][A-Za-z0-9_]*\b")

    def __init__(self, parser: SafeMathParser | None = None) -> None:
        self.parser = parser or SafeMathParser()

    def draft(self, problem: str) -> MathTargetDraftResult:
        mode = self._mode(problem)
        if mode is None:
            return self._skipped("没有识别出受支持的极限、渐进展开或等价问题。")

        approach = self._approach_pattern.search(problem)
        variable = approach.group("variable") if approach else "x"
        point = self._normalize_point(approach.group("point")) if approach else "oo"
        direction = self._direction(problem)
        candidates = self._expression_candidates(problem)
        warnings: list[str] = []
        for expression in candidates:
            parameters = self._parameters(expression, variable)
            if len(parameters) > 12:
                continue
            remainder_power = None
            if mode is VerificationMode.ASYMPTOTIC_EXPANSION:
                remainder_power = self._remainder_power(problem, variable)
                if remainder_power is None:
                    remainder_power = 2
                    warnings.append("题目没有明确余项阶数，建议稿暂按 2 阶；请确认。")
            try:
                target = SolveMathTarget(
                    expression=expression,
                    variable=variable,
                    parameters=parameters,
                    point=point,
                    direction=direction,
                    mode=mode,
                    remainder_power=remainder_power,
                )
                validate_drafted_target(target, self.parser)
            except Exception:  # noqa: BLE001, S112
                continue

            confidence = 0.82 if approach else 0.68
            if "`" not in problem:
                confidence -= 0.05
            return MathTargetDraftResult(
                target=target,
                status=ExtractionStatus.SUCCESS,
                provider=self.name,
                prompt_version=self.prompt_version,
                confidence=max(0, confidence),
                summary="已从题目整理出一个可验证数学目标。",
                warnings=warnings,
            )

        return self._skipped("识别到了问题类型，但没有找到能通过安全解析的数学表达式。")

    def _skipped(self, summary: str) -> MathTargetDraftResult:
        return MathTargetDraftResult(
            status=ExtractionStatus.SKIPPED,
            provider=self.name,
            prompt_version=self.prompt_version,
            summary=summary,
            warnings=["请手动填写数学目标，或继续进行非结构化对话。"],
        )

    @staticmethod
    def _mode(problem: str) -> VerificationMode | None:
        lowered = problem.lower()
        if any(
            token in lowered
            for token in ("渐进等价", "asymptotic equivalent", "等价无穷小")
        ):
            return VerificationMode.ASYMPTOTIC_EQUIVALENCE
        if any(
            token in lowered
            for token in ("渐进展开", "泰勒展开", "taylor", "asymptotic expansion")
        ):
            return VerificationMode.ASYMPTOTIC_EXPANSION
        if any(token in lowered for token in ("极限", "limit")):
            return VerificationMode.LIMIT
        if any(
            token in lowered
            for token in ("化简", "恒等", "精确等价", "simplify", "exact")
        ):
            return VerificationMode.EXACT_EQUIVALENCE
        return None

    @staticmethod
    def _direction(problem: str) -> ApproachDirection:
        lowered = problem.lower()
        if any(token in lowered for token in ("左极限", "左侧", "from the left")):
            return ApproachDirection.LEFT
        if any(token in lowered for token in ("右极限", "右侧", "from the right")):
            return ApproachDirection.RIGHT
        return ApproachDirection.TWO_SIDED

    @staticmethod
    def _normalize_point(point: str) -> str:
        normalized = point.strip().lower().replace("∞", "oo")
        if normalized in {"+oo", "+infinity"}:
            return "oo"
        if normalized == "infinity":
            return "oo"
        if normalized == "-infinity":
            return "-oo"
        return normalized

    def _expression_candidates(self, problem: str) -> list[str]:
        raw_candidates = re.findall(r"`([^`]+)`", problem)
        raw_candidates.extend(self._ascii_run_pattern.findall(problem))
        normalized: list[str] = []
        seen: set[str] = set()
        for raw in raw_candidates:
            candidate = raw.strip(" ,.;:：，。；")
            candidate = candidate.replace("−", "-").replace("×", "*").replace("÷", "/")
            candidate = re.sub(r"\bln\s*\(", "log(", candidate)
            candidate = candidate.replace("^", "**")
            if not candidate or re.match(r"^O\s*\(", candidate):
                continue
            if not any(char in candidate for char in "+-*/()"):
                continue
            if candidate not in seen:
                normalized.append(candidate)
                seen.add(candidate)
        return sorted(normalized, key=self._candidate_score, reverse=True)

    @staticmethod
    def _candidate_score(candidate: str) -> float:
        functions = sum(
            candidate.count(name + "(")
            for name in ("sqrt", "exp", "log", "sin", "cos", "tan", "gamma")
        )
        operators = sum(candidate.count(item) for item in ("+", "-", "*", "/"))
        return (
            functions * 8 + operators * 2 + candidate.count("(") + len(candidate) / 20
        )

    def _parameters(self, expression: str, variable: str) -> list[str]:
        reserved = {
            *self.parser.FUNCTIONS,
            *self.parser.CONSTANTS,
            variable,
        }
        return sorted(
            {
                name
                for name in self._name_pattern.findall(expression)
                if name not in reserved
            }
        )

    @staticmethod
    def _remainder_power(problem: str, variable: str) -> int | None:
        escaped = re.escape(variable)
        patterns = (
            rf"O\(\s*{escaped}\s*(?:\^|\*\*)\s*(-?\d+)\s*\)",
            rf"O\(\s*1\s*/\s*{escaped}\s*(?:\^|\*\*)?\s*(\d+)?\s*\)",
            r"(?:展开到|保留到|to)\s*(\d+)\s*(?:阶|order)?",
        )
        for pattern in patterns:
            match = re.search(pattern, problem, re.IGNORECASE)
            if not match:
                continue
            raw = match.group(1)
            value = abs(int(raw)) if raw else 1
            if 1 <= value <= 50:
                return value
        return None


class FallbackTargetDrafter:
    def __init__(
        self,
        primary: TargetDrafterProtocol,
        fallback: TargetDrafterProtocol | None = None,
    ) -> None:
        self.primary = primary
        self.fallback = fallback or RuleBasedTargetDrafter()
        self.name = f"{primary.name}->{self.fallback.name}"
        self.prompt_version = getattr(primary, "prompt_version", "unknown")

    def draft(self, problem: str) -> MathTargetDraftResult:
        primary_error: str | None = None
        try:
            primary_result = self.primary.draft(problem)
            if primary_result.target is not None:
                return primary_result
            primary_error = primary_result.error or primary_result.summary
        except Exception as exc:  # noqa: BLE001
            primary_error = f"{exc.__class__.__name__}: {exc}"[:2_000]

        fallback_result = self.fallback.draft(problem)
        return fallback_result.model_copy(
            update={
                "status": (
                    ExtractionStatus.FALLBACK
                    if fallback_result.target is not None
                    else fallback_result.status
                ),
                "provider": self.name,
                "model": getattr(self.primary, "model", None),
                "fallback_used": True,
                "error": primary_error,
            }
        )


def build_target_drafter_from_env() -> TargetDrafterProtocol:
    # 新配置优先；未配置时下面的逐变量路径保持原样，一个字都不改。
    if role_is_configured(ROLE_TARGET_DRAFTER):
        resolved = resolve_role(ROLE_TARGET_DRAFTER)
        if resolved is None:
            return RuleBasedTargetDrafter()
        from math_harness.providers.openai_target import OpenAIStructuredTargetDrafter

        return FallbackTargetDrafter(
            OpenAIStructuredTargetDrafter(
                model=resolved.model,
                reasoning_effort=resolved.reasoning_effort,
                max_output_tokens=min(resolved.max_output_tokens, 4_000),
                timeout_seconds=resolved.timeout_seconds,
                api_key=resolved.api_key,
                base_url=resolved.base_url,
                provider_name=resolved.provider_name,
                structured_output_mode=resolved.structured_output_mode,
                json_object_retries=resolved.json_object_retries,
            )
        )

    configured_provider = os.getenv("MATH_HARNESS_TARGET_DRAFTER")
    if configured_provider is None:
        solver_provider = os.getenv("MATH_HARNESS_SOLVER", "sympy").strip().lower()
        provider = solver_provider if solver_provider in {"openai", "mimo"} else "rules"
    else:
        provider = configured_provider.strip().lower()
    if provider == "rules":
        return RuleBasedTargetDrafter()
    if provider == "openai":
        from math_harness.providers.openai_target import OpenAIStructuredTargetDrafter

        return FallbackTargetDrafter(
            OpenAIStructuredTargetDrafter(
                model=os.getenv("MATH_HARNESS_OPENAI_MODEL", "gpt-5.6-terra"),
                reasoning_effort=os.getenv(
                    "MATH_HARNESS_OPENAI_REASONING_EFFORT",
                    "low",
                ),
            )
        )
    if provider == "mimo":
        from math_harness.providers.mimo import (
            DEFAULT_MIMO_BASE_URL,
            DEFAULT_MIMO_MODEL,
            MiMoStructuredTargetDrafter,
        )

        return FallbackTargetDrafter(
            MiMoStructuredTargetDrafter(
                api_key=os.getenv("MIMO_API_KEY"),
                base_url=os.getenv("MATH_HARNESS_MIMO_BASE_URL", DEFAULT_MIMO_BASE_URL),
                model=os.getenv("MATH_HARNESS_MIMO_MODEL", DEFAULT_MIMO_MODEL),
                reasoning_effort=os.getenv(
                    "MATH_HARNESS_MIMO_TARGET_REASONING_EFFORT",
                    "none",
                ),
                max_output_tokens=_bounded_int_env(
                    "MATH_HARNESS_MIMO_TARGET_MAX_OUTPUT_TOKENS",
                    default=1_500,
                    minimum=256,
                    maximum=4_000,
                ),
                timeout_seconds=_positive_float_env(
                    "MATH_HARNESS_MIMO_TIMEOUT_SECONDS",
                    60.0,
                ),
            )
        )
    raise ValueError("MATH_HARNESS_TARGET_DRAFTER must be 'rules', 'openai', or 'mimo'")


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


def _positive_float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive number") from exc
    if value <= 0:
        raise ValueError(f"{name} must be a positive number")
    return value
