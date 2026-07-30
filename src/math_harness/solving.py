from __future__ import annotations

import os
from time import perf_counter
from typing import Protocol

import sympy as sp

from math_harness.math_parser import SafeMathParser
from math_harness.models import (
    CandidateSolution,
    CandidateStep,
    GenerationStatus,
    MethodMatch,
    SolutionGenerationResult,
    SolutionGenerationTrace,
    SolveMathTarget,
    VerificationMode,
    VerificationReport,
)


class SolutionGeneratorProtocol(Protocol):
    name: str

    def generate(
        self,
        problem: str,
        methods: list[MethodMatch],
        math_target: SolveMathTarget | None,
        max_output_tokens: int,
    ) -> SolutionGenerationResult: ...


class OfflineSympySolutionGenerator:
    """Deterministic first-pass generator for the supported verification modes."""

    name = "sympy"
    prompt_version = "offline-sympy-v1"

    def __init__(self, parser: SafeMathParser | None = None) -> None:
        self.parser = parser or SafeMathParser()

    def generate(
        self,
        problem: str,
        methods: list[MethodMatch],
        math_target: SolveMathTarget | None,
        max_output_tokens: int,
    ) -> SolutionGenerationResult:
        del problem, max_output_tokens
        started = perf_counter()
        if math_target is None:
            return self._failure(
                "离线求解器需要 math_target 才能生成可验证候选解。",
                started,
            )

        try:
            symbol_names = {math_target.variable, *math_target.parameters}
            expression = self.parser.parse(math_target.expression, symbol_names)
            variable = sp.Symbol(math_target.variable)
            point = self._parse_point(math_target.point, symbol_names)
            answer = self._solve(expression, variable, point, math_target)
            answer_expression = sp.sstr(answer)
        except Exception as exc:  # noqa: BLE001
            return self._failure(
                f"{exc.__class__.__name__}: {exc}",
                started,
            )

        used_method_keys = [match.method.key for match in methods[:3]]
        method_step = (
            "参考已检索的方法卡：" + "、".join(used_method_keys) + "。"
            if used_method_keys
            else "当前工作区没有可用的方法卡，使用安全符号计算生成候选式。"
        )
        candidate = CandidateSolution(
            answer_text=f"候选答案：{answer_expression}",
            answer_expression=answer_expression,
            steps=[
                CandidateStep(
                    explanation=method_step,
                    expression=None,
                ),
                CandidateStep(
                    explanation=(
                        "在声明的变量、趋近点和余项阶数下进行符号化简或级数展开。"
                    ),
                    expression=answer_expression,
                ),
            ],
            used_method_keys=used_method_keys,
            assumptions=[
                f"{math_target.variable} → {math_target.point}",
                *(
                    ["参数：" + ", ".join(math_target.parameters)]
                    if math_target.parameters
                    else []
                ),
            ],
            confidence=0.95,
        )
        return SolutionGenerationResult(
            candidate=candidate,
            trace=SolutionGenerationTrace(
                provider=self.name,
                prompt_version=self.prompt_version,
                status=GenerationStatus.SUCCESS,
                raw_output=candidate.model_dump_json()[:8_000],
                duration_ms=self._duration_ms(started),
            ),
        )

    def _solve(
        self,
        expression: sp.Expr,
        variable: sp.Symbol,
        point: sp.Expr,
        target: SolveMathTarget,
    ) -> sp.Expr:
        if target.mode is VerificationMode.EXACT_EQUIVALENCE:
            return sp.simplify(expression)
        if target.mode is VerificationMode.ASYMPTOTIC_EQUIVALENCE:
            return sp.simplify(self._leading_term(expression, variable, point))
        if target.remainder_power is None:
            raise ValueError("渐进展开需要 remainder_power")
        return sp.simplify(
            sp.series(
                expression,
                variable,
                point,
                target.remainder_power,
            ).removeO()
        )

    def _leading_term(
        self,
        expression: sp.Expr,
        variable: sp.Symbol,
        point: sp.Expr,
    ) -> sp.Expr:
        local = sp.Dummy("h", positive=True)
        if point == sp.oo:
            transformed = expression.subs(variable, 1 / local)
            leading = transformed.as_leading_term(local)
            return leading.subs(local, 1 / variable)
        if point == -sp.oo:
            transformed = expression.subs(variable, -1 / local)
            leading = transformed.as_leading_term(local)
            return leading.subs(local, -1 / variable)
        transformed = expression.subs(variable, point + local)
        leading = transformed.as_leading_term(local)
        return leading.subs(local, variable - point)

    def _parse_point(self, text: str, symbol_names: set[str]) -> sp.Expr:
        normalized = text.strip()
        if normalized in {"oo", "+oo", "infinity", "+infinity"}:
            return sp.oo
        if normalized in {"-oo", "-infinity"}:
            return -sp.oo
        return self.parser.parse(normalized, symbol_names)

    def _failure(self, error: str, started: float) -> SolutionGenerationResult:
        return SolutionGenerationResult(
            candidate=None,
            trace=SolutionGenerationTrace(
                provider=self.name,
                prompt_version=self.prompt_version,
                status=GenerationStatus.ERROR,
                error=error[:2_000],
                duration_ms=self._duration_ms(started),
            ),
        )

    @staticmethod
    def _duration_ms(started: float) -> int:
        return max(0, round((perf_counter() - started) * 1_000))


class FallbackSolutionGenerator:
    """Recover from generation or verification failures with deterministic SymPy."""

    def __init__(
        self,
        primary: SolutionGeneratorProtocol,
        fallback: SolutionGeneratorProtocol | None = None,
        *,
        verification_repair_enabled: bool = True,
        verification_fallback_enabled: bool = True,
    ) -> None:
        self.primary = primary
        self.fallback = fallback or OfflineSympySolutionGenerator()
        self.name = f"{primary.name}->{self.fallback.name}"
        self.prompt_version = getattr(primary, "prompt_version", "unknown")
        self.verification_repair_enabled = verification_repair_enabled
        self.verification_fallback_enabled = verification_fallback_enabled

    def generate(
        self,
        problem: str,
        methods: list[MethodMatch],
        math_target: SolveMathTarget | None,
        max_output_tokens: int,
    ) -> SolutionGenerationResult:
        started = perf_counter()
        try:
            primary_result = self.primary.generate(
                problem,
                methods,
                math_target,
                max_output_tokens,
            )
            if primary_result.candidate is not None:
                return primary_result
            primary_error = (
                primary_result.trace.error or "primary returned no candidate"
            )
        except Exception as exc:  # noqa: BLE001
            primary_error = f"{exc.__class__.__name__}: {exc}"

        fallback_result = self.fallback.generate(
            problem,
            methods,
            math_target,
            max_output_tokens,
        )
        if fallback_result.candidate is None:
            return fallback_result.model_copy(
                update={
                    "trace": fallback_result.trace.model_copy(
                        update={
                            "provider": self.name,
                            "model": getattr(self.primary, "model", None),
                            "fallback_used": True,
                            "error": (
                                f"primary: {primary_error}; "
                                f"fallback: {fallback_result.trace.error}"
                            )[:2_000],
                            "duration_ms": self._duration_ms(started),
                        }
                    )
                }
            )
        return fallback_result.model_copy(
            update={
                "trace": fallback_result.trace.model_copy(
                    update={
                        "provider": self.name,
                        "model": getattr(self.primary, "model", None),
                        "prompt_version": self.prompt_version,
                        "status": GenerationStatus.FALLBACK,
                        "fallback_used": True,
                        "error": primary_error[:2_000],
                        "duration_ms": self._duration_ms(started),
                    }
                )
            }
        )

    def repair_after_verification(
        self,
        problem: str,
        methods: list[MethodMatch],
        math_target: SolveMathTarget,
        previous_candidate: CandidateSolution,
        verification: VerificationReport,
        max_output_tokens: int,
    ) -> SolutionGenerationResult | None:
        repair = getattr(self.primary, "repair", None)
        if not self.verification_repair_enabled or not callable(repair):
            return None

        started = perf_counter()
        try:
            result = repair(
                problem,
                methods,
                math_target,
                previous_candidate,
                verification,
                max_output_tokens,
            )
        except Exception as exc:  # noqa: BLE001
            return SolutionGenerationResult(
                candidate=None,
                trace=SolutionGenerationTrace(
                    provider=self.primary.name,
                    model=getattr(self.primary, "model", None),
                    prompt_version=self.prompt_version,
                    status=GenerationStatus.ERROR,
                    correction_attempted=True,
                    recovery_notes=["model_correction_failed"],
                    error=f"{exc.__class__.__name__}: {exc}"[:2_000],
                    duration_ms=self._duration_ms(started),
                ),
            )
        return result.model_copy(
            update={
                "trace": result.trace.model_copy(
                    update={
                        "correction_attempted": True,
                        "duration_ms": self._duration_ms(started),
                    }
                )
            }
        )

    def fallback_after_verification(
        self,
        problem: str,
        methods: list[MethodMatch],
        math_target: SolveMathTarget,
        max_output_tokens: int,
        verification: VerificationReport,
        *,
        correction_attempted: bool,
        correction_error: str | None,
    ) -> SolutionGenerationResult | None:
        if not self.verification_fallback_enabled:
            return None

        started = perf_counter()
        fallback_result = self.fallback.generate(
            problem,
            methods,
            math_target,
            max_output_tokens,
        )
        errors = [f"verification: {verification.status.value}: {verification.summary}"]
        if correction_error:
            errors.append(f"correction: {correction_error}")
        if fallback_result.trace.error:
            errors.append(f"fallback: {fallback_result.trace.error}")
        return fallback_result.model_copy(
            update={
                "trace": fallback_result.trace.model_copy(
                    update={
                        "provider": self.name,
                        "model": getattr(self.primary, "model", None),
                        "prompt_version": self.prompt_version,
                        "status": (
                            GenerationStatus.FALLBACK
                            if fallback_result.candidate is not None
                            else GenerationStatus.ERROR
                        ),
                        "fallback_used": True,
                        "verification_fallback_used": True,
                        "correction_attempted": correction_attempted,
                        "correction_succeeded": False,
                        "recovery_notes": ["sympy_verification_fallback"],
                        "error": "; ".join(errors)[:2_000],
                        "duration_ms": self._duration_ms(started),
                    }
                )
            }
        )

    @staticmethod
    def _duration_ms(started: float) -> int:
        return max(0, round((perf_counter() - started) * 1_000))


def build_solution_generator_from_env() -> SolutionGeneratorProtocol:
    provider = os.getenv("MATH_HARNESS_SOLVER", "sympy").strip().lower()
    if provider == "sympy":
        return OfflineSympySolutionGenerator()
    if provider == "openai":
        from math_harness.providers.openai_solver import OpenAISolutionGenerator

        primary = OpenAISolutionGenerator(
            model=os.getenv(
                "MATH_HARNESS_OPENAI_SOLVER_MODEL",
                "gpt-5.6-sol",
            ),
            reasoning_effort=os.getenv(
                "MATH_HARNESS_OPENAI_SOLVER_REASONING_EFFORT",
                "medium",
            ),
            timeout_seconds=_positive_float_env(
                "MATH_HARNESS_OPENAI_SOLVER_TIMEOUT_SECONDS",
                45.0,
            ),
        )
        return FallbackSolutionGenerator(
            primary,
            verification_repair_enabled=_boolean_env(
                "MATH_HARNESS_VERIFICATION_REPAIR",
                True,
            ),
            verification_fallback_enabled=_boolean_env(
                "MATH_HARNESS_VERIFICATION_FALLBACK",
                True,
            ),
        )
    if provider == "mimo":
        from math_harness.providers.mimo import (
            DEFAULT_MIMO_BASE_URL,
            DEFAULT_MIMO_MODEL,
            MiMoSolutionGenerator,
        )

        primary = MiMoSolutionGenerator(
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
                "MATH_HARNESS_MIMO_SOLVER_REASONING_EFFORT",
                "none",
            ),
            timeout_seconds=_positive_float_env(
                "MATH_HARNESS_MIMO_TIMEOUT_SECONDS",
                60.0,
            ),
        )
        return FallbackSolutionGenerator(
            primary,
            verification_repair_enabled=_boolean_env(
                "MATH_HARNESS_VERIFICATION_REPAIR",
                True,
            ),
            verification_fallback_enabled=_boolean_env(
                "MATH_HARNESS_VERIFICATION_FALLBACK",
                True,
            ),
        )
    raise ValueError("MATH_HARNESS_SOLVER must be 'sympy', 'openai', or 'mimo'")


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


def _boolean_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean")
