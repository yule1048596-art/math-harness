from __future__ import annotations

import os
from time import perf_counter
from typing import Protocol

import sympy as sp

from math_harness.math_parser import SafeMathParser, build_symbol_table
from math_harness.models import (
    AnswerKind,
    CandidateSolution,
    CandidateStep,
    GenerationStageKind,
    GenerationStatus,
    MethodMatch,
    SolutionGenerationResult,
    SolutionGenerationStage,
    SolutionGenerationTrace,
    SolveMathTarget,
    VerificationMode,
    VerificationReport,
)
from math_harness.verifier import SolutionVerifier


class _NonExpressionOutcome(Exception):
    def __init__(self, answer_kind: AnswerKind, message: str) -> None:
        super().__init__(message)
        self.answer_kind = answer_kind
        self.message = message


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
            symbols = build_symbol_table(
                math_target.variable,
                math_target.parameters,
                math_target.assumptions,
            )
            expression = self.parser.parse(math_target.expression, symbols)
            variable = symbols[math_target.variable]
            point = self._parse_point(math_target.point, symbols)
            answer = self._solve(expression, variable, point, math_target, symbols)
            answer_expression = sp.sstr(answer)
            self.parser.parse(answer_expression, symbols)
        except _NonExpressionOutcome as outcome:
            candidate = CandidateSolution(
                answer_kind=outcome.answer_kind,
                answer_text=outcome.message,
                answer_expression=None,
                steps=[
                    CandidateStep(
                        explanation=(
                            "符号后端没有产生可由当前安全表达式语言验收的单一结果。"
                        ),
                        expression=None,
                    )
                ],
                used_method_keys=[],
                assumptions=[
                    f"{math_target.variable} → {math_target.point}",
                    *(
                        ["参数：" + ", ".join(math_target.parameters)]
                        if math_target.parameters
                        else []
                    ),
                ],
                confidence=0.4,
            )
            return SolutionGenerationResult(
                candidate=candidate,
                trace=SolutionGenerationTrace(
                    provider=self.name,
                    prompt_version=self.prompt_version,
                    status=GenerationStatus.SUCCESS,
                    method_feedback_eligible=False,
                    raw_output=candidate.model_dump_json()[:8_000],
                    duration_ms=self._duration_ms(started),
                ),
            )
        except Exception as exc:  # noqa: BLE001
            return self._failure(
                f"{exc.__class__.__name__}: {exc}",
                started,
            )

        del methods
        used_method_keys: list[str] = []
        method_step = "使用安全符号计算生成兜底候选式；该步骤不参与方法卡成败反馈。"
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
                method_feedback_eligible=False,
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
        symbols: dict[str, sp.Symbol],
    ) -> sp.Expr:
        if target.mode is VerificationMode.EXACT_EQUIVALENCE:
            simplified = sp.simplify(expression)
            for symbol in symbols.values():
                try:
                    base_domain = SolutionVerifier._base_real_domain(symbol)
                    original_domain = sp.calculus.util.continuous_domain(
                        expression,
                        symbol,
                        sp.S.Reals,
                    ).intersect(base_domain)
                    simplified_domain = sp.calculus.util.continuous_domain(
                        simplified,
                        symbol,
                        sp.S.Reals,
                    ).intersect(base_domain)
                except (NotImplementedError, ValueError) as exc:
                    raise _NonExpressionOutcome(
                        AnswerKind.CONDITIONAL,
                        "精确化简的定义域无法自动判定，需要人工复核。",
                    ) from exc
                if original_domain != simplified_domain:
                    raise _NonExpressionOutcome(
                        AnswerKind.CONDITIONAL,
                        (
                            "化简会改变原表达式的定义域，不能把化简式作为全域上的"
                            "完全相同结果。"
                        ),
                    )
            return simplified
        if target.mode is VerificationMode.LIMIT:
            value = self._limit(expression, variable, point, target)
            if value.has(sp.AccumBounds) or isinstance(value, sp.Limit):
                raise _NonExpressionOutcome(
                    AnswerKind.NO_LIMIT,
                    "符号后端没有得到单一极限值，需要人工复核。",
                )
            return value
        if target.mode is VerificationMode.ASYMPTOTIC_EQUIVALENCE:
            leading = sp.simplify(self._leading_term(expression, variable, point))
            if leading.has(sp.AccumBounds):
                raise _NonExpressionOutcome(
                    AnswerKind.NO_EQUIVALENT,
                    "符号后端检测到持续振荡，未得到单一的非零渐进等价式。",
                )
            return leading
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

    @staticmethod
    def _limit(
        expression: sp.Expr,
        variable: sp.Symbol,
        point: sp.Expr,
        target: SolveMathTarget,
    ) -> sp.Expr:
        if point == sp.oo:
            direction = "-"
        elif point == -sp.oo:
            direction = "+"
        else:
            direction = {
                "two_sided": "+-",
                "left": "-",
                "right": "+",
            }[target.direction.value]
        return sp.limit(expression, variable, point, dir=direction)

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

    def _parse_point(
        self,
        text: str,
        symbols: dict[str, sp.Symbol],
    ) -> sp.Expr:
        normalized = text.strip()
        if normalized in {"oo", "+oo", "infinity", "+infinity"}:
            return sp.oo
        if normalized in {"-oo", "-infinity"}:
            return -sp.oo
        return self.parser.parse(normalized, symbols)

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
        return self._generate(
            problem,
            methods,
            math_target,
            max_output_tokens,
            conversation_context=None,
        )

    def generate_with_context(
        self,
        problem: str,
        methods: list[MethodMatch],
        math_target: SolveMathTarget | None,
        max_output_tokens: int,
        conversation_context: dict[str, object],
    ) -> SolutionGenerationResult:
        return self._generate(
            problem,
            methods,
            math_target,
            max_output_tokens,
            conversation_context=conversation_context,
        )

    def _generate(
        self,
        problem: str,
        methods: list[MethodMatch],
        math_target: SolveMathTarget | None,
        max_output_tokens: int,
        *,
        conversation_context: dict[str, object] | None,
    ) -> SolutionGenerationResult:
        started = perf_counter()
        try:
            contextual_generate = getattr(self.primary, "generate_with_context", None)
            if conversation_context is not None and callable(contextual_generate):
                primary_result = contextual_generate(
                    problem,
                    methods,
                    math_target,
                    max_output_tokens,
                    conversation_context,
                )
            else:
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
            primary_result = SolutionGenerationResult(
                candidate=None,
                trace=SolutionGenerationTrace(
                    provider=self.primary.name,
                    model=getattr(self.primary, "model", None),
                    prompt_version=self.prompt_version,
                    status=GenerationStatus.ERROR,
                    error=primary_error[:2_000],
                ),
            )

        fallback_result = self.fallback.generate(
            problem,
            methods,
            math_target,
            max_output_tokens,
        )
        stages = [
            *(
                primary_result.trace.stages
                or [self._stage(GenerationStageKind.INITIAL, primary_result)]
            ),
            *(
                fallback_result.trace.stages
                or [self._stage(GenerationStageKind.FALLBACK, fallback_result)]
            ),
        ]
        if fallback_result.candidate is None:
            return fallback_result.model_copy(
                update={
                    "trace": fallback_result.trace.model_copy(
                        update={
                            "provider": self.name,
                            "model": getattr(self.primary, "model", None),
                            "fallback_used": True,
                            "method_feedback_eligible": False,
                            "stages": stages,
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
                        "method_feedback_eligible": False,
                        "stages": stages,
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
                        "method_feedback_eligible": False,
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

    @staticmethod
    def _stage(
        stage: GenerationStageKind,
        result: SolutionGenerationResult,
    ) -> SolutionGenerationStage:
        trace = result.trace
        return SolutionGenerationStage(
            stage=stage,
            provider=trace.provider,
            model=trace.model,
            response_id=trace.response_id,
            prompt_version=trace.prompt_version,
            status=trace.status,
            candidate=result.candidate,
            raw_output=trace.raw_output,
            error=trace.error,
            duration_ms=trace.duration_ms,
        )


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
