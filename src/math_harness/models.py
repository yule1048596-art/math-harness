from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def utc_now() -> datetime:
    return datetime.now(UTC)


class KnowledgeStatus(StrEnum):
    CAPTURED = "captured"
    PENDING_REVIEW = "pending_review"
    PROMOTED = "promoted"
    REJECTED = "rejected"
    DEPRECATED = "deprecated"


class VerificationStatus(StrEnum):
    VERIFIED = "verified"
    NEEDS_REVIEW = "needs_review"
    REJECTED = "rejected"


class ProblemKind(StrEnum):
    ASYMPTOTIC = "asymptotic"
    LIMIT = "limit"
    ALGEBRA = "algebra"
    UNKNOWN = "unknown"


class MethodKind(StrEnum):
    RATIONALIZATION = "rationalization"
    VARIABLE_INVERSION = "variable_inversion"
    TAYLOR_EXPANSION = "taylor_expansion"
    DOMINANT_BALANCE = "dominant_balance"
    LOG_TRANSFORM = "log_transform"
    STIRLING = "stirling"
    LAPLACE_METHOD = "laplace_method"
    GENERIC_EXAMPLE = "generic_example"


class VerificationMode(StrEnum):
    ASYMPTOTIC_EXPANSION = "asymptotic_expansion"
    ASYMPTOTIC_EQUIVALENCE = "asymptotic_equivalence"
    EXACT_EQUIVALENCE = "exact_equivalence"
    LIMIT = "limit"


class ApproachDirection(StrEnum):
    TWO_SIDED = "two_sided"
    LEFT = "left"
    RIGHT = "right"


class SymbolProperty(StrEnum):
    REAL = "real"
    POSITIVE = "positive"
    NEGATIVE = "negative"
    NONZERO = "nonzero"
    INTEGER = "integer"
    NONNEGATIVE = "nonnegative"
    NONPOSITIVE = "nonpositive"


class AnswerKind(StrEnum):
    EXPRESSION = "expression"
    NO_EQUIVALENT = "no_equivalent"
    NO_LIMIT = "no_limit"
    CONDITIONAL = "conditional"


class ExtractionStatus(StrEnum):
    SUCCESS = "success"
    FALLBACK = "fallback"
    SKIPPED = "skipped"
    ERROR = "error"


class GenerationStatus(StrEnum):
    SUCCESS = "success"
    FALLBACK = "fallback"
    ERROR = "error"


class SolutionAttemptStatus(StrEnum):
    VERIFIED = "verified"
    NEEDS_REVIEW = "needs_review"
    REJECTED = "rejected"
    GENERATION_FAILED = "generation_failed"


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("workspace name cannot be blank")
        return normalized


class Workspace(BaseModel):
    id: str
    name: str
    description: str
    created_at: datetime


_SYMBOL_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


class SolveMathTarget(BaseModel):
    """Machine-checkable mathematical target supplied with a solve request."""

    expression: str = Field(min_length=1, max_length=1000)
    variable: str = "x"
    parameters: list[str] = Field(default_factory=list, max_length=12)
    assumptions: dict[str, list[SymbolProperty]] = Field(
        default_factory=dict,
        max_length=13,
    )
    point: str = "oo"
    direction: ApproachDirection = ApproachDirection.TWO_SIDED
    mode: VerificationMode = VerificationMode.ASYMPTOTIC_EXPANSION
    remainder_power: int | None = Field(default=None, ge=1, le=50)

    @field_validator("variable")
    @classmethod
    def validate_variable(cls, value: str) -> str:
        if not _SYMBOL_PATTERN.fullmatch(value):
            raise ValueError("variable must be a simple ASCII symbol name")
        return value

    @field_validator("parameters")
    @classmethod
    def validate_parameters(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("parameters must be unique")
        if any(not _SYMBOL_PATTERN.fullmatch(value) for value in values):
            raise ValueError("parameters must be simple ASCII symbol names")
        return values

    @model_validator(mode="after")
    def validate_assumptions(self) -> SolveMathTarget:
        if self.variable in self.parameters:
            raise ValueError("variable cannot also be listed as a parameter")
        allowed_symbols = {self.variable, *self.parameters}
        unknown_symbols = set(self.assumptions) - allowed_symbols
        if unknown_symbols:
            raise ValueError(
                "assumptions contain undeclared symbols: "
                + ", ".join(sorted(unknown_symbols))
            )

        contradictory_pairs = (
            {SymbolProperty.POSITIVE, SymbolProperty.NEGATIVE},
            {SymbolProperty.POSITIVE, SymbolProperty.NONPOSITIVE},
            {SymbolProperty.NEGATIVE, SymbolProperty.NONNEGATIVE},
            {SymbolProperty.NONNEGATIVE, SymbolProperty.NONPOSITIVE},
        )
        for symbol, values in self.assumptions.items():
            if len(values) != len(set(values)):
                raise ValueError(f"assumptions for {symbol} must be unique")
            value_set = set(values)
            if any(pair <= value_set for pair in contradictory_pairs):
                raise ValueError(f"assumptions for {symbol} are contradictory")
        return self


class MathPayload(SolveMathTarget):
    """Deterministic verification input.

    The natural-language problem and solution remain the source of truth. This
    payload is a safe, machine-checkable sidecar for the first vertical slice.
    """

    expected: str = Field(min_length=1, max_length=1000)


class ExampleCreate(BaseModel):
    problem: str = Field(min_length=1, max_length=20_000)
    solution: str = Field(min_length=1, max_length=40_000)
    tags: list[str] = Field(default_factory=list, max_length=30)
    method_hint: str | None = Field(default=None, max_length=200)
    math_payload: MathPayload | None = None
    reviewed: bool = False

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, values: list[str]) -> list[str]:
        normalized = []
        seen = set()
        for value in values:
            tag = " ".join(value.strip().lower().split())
            if tag and tag not in seen:
                normalized.append(tag)
                seen.add(tag)
        return normalized


class VerificationReport(BaseModel):
    status: VerificationStatus
    summary: str
    checks: list[str] = Field(default_factory=list)
    computed: dict[str, str] = Field(default_factory=dict)
    error: str | None = None


class MethodExtractionTrace(BaseModel):
    provider: str = Field(min_length=1, max_length=80)
    model: str | None = Field(default=None, max_length=120)
    response_id: str | None = Field(default=None, max_length=200)
    prompt_version: str = Field(min_length=1, max_length=80)
    status: ExtractionStatus
    fallback_used: bool = False
    raw_output: str | None = Field(default=None, max_length=8_000)
    error: str | None = Field(default=None, max_length=2_000)
    extracted_method_keys: list[str] = Field(default_factory=list, max_length=50)
    evidence_by_method: dict[str, list[str]] = Field(default_factory=dict)
    confidence_by_method: dict[str, float] = Field(default_factory=dict)


class ProblemExample(BaseModel):
    id: str
    workspace_id: str
    problem: str
    solution: str
    tags: list[str]
    method_hint: str | None = None
    reviewed: bool = False
    problem_kind: ProblemKind
    math_payload: MathPayload | None = None
    verification: VerificationReport
    extraction: MethodExtractionTrace | None = None
    status: KnowledgeStatus
    created_at: datetime


_METHOD_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,79}$")


def _normalize_method_key(value: str | MethodKind) -> str:
    normalized = str(value).strip().lower()
    if not _METHOD_KEY_PATTERN.fullmatch(normalized):
        raise ValueError(
            "method key must be a lowercase ASCII slug starting with a letter"
        )
    return normalized


class MethodCard(BaseModel):
    id: str
    workspace_id: str
    key: str
    name: str
    goal: str
    applicable_when: list[str]
    procedure: list[str]
    failure_modes: list[str]
    tags: list[str]
    status: KnowledgeStatus
    version: int = 1
    success_count: int = 0
    failure_count: int = 0
    # 这张方法卡通常用在什么数学结构上，由它关联例子确定性累积而来。
    signature: dict[str, object] = Field(default_factory=dict)
    example_ids: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    @field_validator("key", mode="before")
    @classmethod
    def validate_key(cls, value: str | MethodKind) -> str:
        return _normalize_method_key(value)


class MethodVersion(BaseModel):
    """某个方法卡在被改写之前的内容快照，用于回溯它是如何长出来的。"""

    method_id: str
    workspace_id: str
    version: int
    name: str
    goal: str
    applicable_when: list[str]
    procedure: list[str]
    failure_modes: list[str]
    tags: list[str]
    source_example_id: str | None = None
    created_at: datetime


class MethodDraft(BaseModel):
    key: str
    name: str
    goal: str
    applicable_when: list[str]
    procedure: list[str]
    failure_modes: list[str]
    tags: list[str]

    @field_validator("key", mode="before")
    @classmethod
    def validate_key(cls, value: str | MethodKind) -> str:
        return _normalize_method_key(value)


class MethodExtractionResult(BaseModel):
    methods: list[MethodDraft] = Field(default_factory=list, max_length=50)
    trace: MethodExtractionTrace


class IngestionResult(BaseModel):
    example: ProblemExample
    learned_methods: list[MethodCard]


class MethodSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=10_000)
    tags: list[str] = Field(default_factory=list, max_length=30)
    top_k: int = Field(default=5, ge=1, le=20)
    # 提供后走结构化检索；不提供则完全保持原有的词面打分。
    math_target: SolveMathTarget | None = None


class MethodMatch(BaseModel):
    method: MethodCard
    score: float = Field(ge=0, le=1)
    reasons: list[str]


class SolvePlan(BaseModel):
    workspace_id: str
    problem: str
    problem_kind: ProblemKind
    recommended_methods: list[MethodMatch]
    note: str


class SolveRequest(BaseModel):
    problem: str = Field(min_length=1, max_length=20_000)
    tags: list[str] = Field(default_factory=list, max_length=30)
    top_k: int = Field(default=5, ge=1, le=20)
    math_target: SolveMathTarget | None = None
    max_output_tokens: int = Field(default=3_000, ge=256, le=8_000)

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, values: list[str]) -> list[str]:
        normalized = []
        seen = set()
        for value in values:
            tag = " ".join(value.strip().lower().split())
            if tag and tag not in seen:
                normalized.append(tag)
                seen.add(tag)
        return normalized


class CandidateStep(BaseModel):
    explanation: str = Field(min_length=1, max_length=2_000)
    expression: str | None = Field(max_length=1_000)


class CandidateSolution(BaseModel):
    """Public, concise derivation returned by a solution generator."""

    answer_kind: AnswerKind = AnswerKind.EXPRESSION
    answer_text: str = Field(min_length=1, max_length=40_000)
    answer_expression: str | None = Field(max_length=1_000)
    steps: list[CandidateStep] = Field(max_length=30)
    used_method_keys: list[str] = Field(max_length=20)
    assumptions: list[str] = Field(max_length=20)
    confidence: float = Field(ge=0, le=1)

    @field_validator("used_method_keys", mode="before")
    @classmethod
    def validate_method_keys(cls, values: list[str | MethodKind]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            key = _normalize_method_key(value)
            if key not in seen:
                normalized.append(key)
                seen.add(key)
        return normalized

    @model_validator(mode="after")
    def validate_answer_kind(self) -> CandidateSolution:
        if (
            self.answer_kind is not AnswerKind.EXPRESSION
            and self.answer_expression is not None
        ):
            raise ValueError(
                "non-expression answer kinds cannot contain answer_expression"
            )
        return self


class GenerationStageKind(StrEnum):
    INITIAL = "initial"
    CORRECTION = "correction"
    FALLBACK = "fallback"
    HUMAN = "human"


class SolutionGenerationStage(BaseModel):
    stage: GenerationStageKind
    provider: str = Field(min_length=1, max_length=120)
    model: str | None = Field(default=None, max_length=120)
    response_id: str | None = Field(default=None, max_length=200)
    prompt_version: str = Field(min_length=1, max_length=80)
    status: GenerationStatus
    candidate: CandidateSolution | None = None
    verification: VerificationReport | None = None
    raw_output: str | None = Field(default=None, max_length=8_000)
    error: str | None = Field(default=None, max_length=2_000)
    duration_ms: int = Field(default=0, ge=0)


class SolutionGenerationTrace(BaseModel):
    provider: str = Field(min_length=1, max_length=120)
    model: str | None = Field(default=None, max_length=120)
    response_id: str | None = Field(default=None, max_length=200)
    prompt_version: str = Field(min_length=1, max_length=80)
    status: GenerationStatus
    fallback_used: bool = False
    verification_fallback_used: bool = False
    correction_attempted: bool = False
    correction_succeeded: bool = False
    method_feedback_eligible: bool = True
    normalization_actions: list[str] = Field(default_factory=list, max_length=20)
    recovery_notes: list[str] = Field(default_factory=list, max_length=20)
    stages: list[SolutionGenerationStage] = Field(default_factory=list, max_length=10)
    raw_output: str | None = Field(default=None, max_length=8_000)
    error: str | None = Field(default=None, max_length=2_000)
    duration_ms: int = Field(default=0, ge=0)


class SolutionGenerationResult(BaseModel):
    candidate: CandidateSolution | None
    trace: SolutionGenerationTrace


class SolutionAttempt(BaseModel):
    id: str
    workspace_id: str
    problem: str
    tags: list[str]
    problem_kind: ProblemKind
    math_target: SolveMathTarget | None = None
    recommended_methods: list[MethodMatch]
    candidate: CandidateSolution | None
    generation: SolutionGenerationTrace
    verification: VerificationReport
    status: SolutionAttemptStatus
    feedback_method_keys: list[str] = Field(default_factory=list, max_length=20)
    correction_of: str | None = None
    created_at: datetime

    @model_validator(mode="after")
    def validate_attempt_invariants(self) -> SolutionAttempt:
        if self.status is SolutionAttemptStatus.GENERATION_FAILED:
            if self.candidate is not None:
                raise ValueError("generation_failed attempt cannot contain a candidate")
            if self.verification.status is not VerificationStatus.NEEDS_REVIEW:
                raise ValueError(
                    "generation_failed attempt must use needs_review verification"
                )
        else:
            if self.candidate is None:
                raise ValueError("non-failed attempt must contain a candidate")
            expected_status = SolutionAttemptStatus(self.verification.status.value)
            if self.status is not expected_status:
                raise ValueError("attempt status must match verification status")

        if any(
            match.method.workspace_id != self.workspace_id
            for match in self.recommended_methods
        ):
            raise ValueError("recommended methods must belong to the attempt workspace")
        allowed_keys = {match.method.key for match in self.recommended_methods}
        if self.candidate and not set(self.candidate.used_method_keys) <= allowed_keys:
            raise ValueError("used methods must come from this attempt's retrieval")
        if self.candidate is None and self.feedback_method_keys:
            raise ValueError("attempt without a candidate cannot credit methods")
        if self.candidate and not set(self.feedback_method_keys) <= set(
            self.candidate.used_method_keys
        ):
            raise ValueError("feedback methods must be used by the final candidate")
        if not self.generation.method_feedback_eligible and self.feedback_method_keys:
            raise ValueError("ineligible generation cannot credit methods")
        return self


class SolutionCorrection(BaseModel):
    answer_kind: AnswerKind = AnswerKind.EXPRESSION
    answer_text: str = Field(min_length=1, max_length=40_000)
    answer_expression: str | None = Field(default=None, max_length=1_000)
    steps: list[CandidateStep] = Field(default_factory=list, max_length=30)
    used_method_keys: list[str] | None = Field(default=None, max_length=20)
    assumptions: list[str] = Field(default_factory=list, max_length=20)
    reviewer_note: str = Field(default="", max_length=4_000)

    @field_validator("used_method_keys", mode="before")
    @classmethod
    def validate_optional_method_keys(
        cls, values: list[str | MethodKind] | None
    ) -> list[str] | None:
        if values is None:
            return None
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            key = _normalize_method_key(value)
            if key not in seen:
                normalized.append(key)
                seen.add(key)
        return normalized

    @model_validator(mode="after")
    def validate_answer_kind(self) -> SolutionCorrection:
        if (
            self.answer_kind is not AnswerKind.EXPRESSION
            and self.answer_expression is not None
        ):
            raise ValueError(
                "non-expression answer kinds cannot contain answer_expression"
            )
        return self


class MethodStatusUpdate(BaseModel):
    status: KnowledgeStatus

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: KnowledgeStatus) -> KnowledgeStatus:
        allowed = {
            KnowledgeStatus.PENDING_REVIEW,
            KnowledgeStatus.PROMOTED,
            KnowledgeStatus.DEPRECATED,
        }
        if value not in allowed:
            raise ValueError(
                "method status must be pending_review, promoted, or deprecated"
            )
        return value


class LearningEvent(BaseModel):
    id: str
    workspace_id: str
    event_type: str
    target_id: str
    payload: dict[str, Any]
    created_at: datetime


class EvaluationCase(BaseModel):
    id: str = Field(min_length=1, max_length=120)
    problem: str = Field(min_length=1, max_length=20_000)
    tags: list[str] = Field(default_factory=list, max_length=30)
    expected_method_keys: list[str] = Field(min_length=1, max_length=20)
    # 带上结构化目标后，检索才能用数学结构而不只是词面来匹配方法。
    math_target: SolveMathTarget | None = None

    @field_validator("expected_method_keys", mode="before")
    @classmethod
    def validate_method_keys(cls, values: list[str | MethodKind]) -> list[str]:
        return [_normalize_method_key(value) for value in values]


class EvaluationRequest(BaseModel):
    name: str = Field(default="evaluation", min_length=1, max_length=120)
    cases: list[EvaluationCase] = Field(min_length=1, max_length=500)
    top_k: int = Field(default=3, ge=1, le=20)


class EvaluationCaseResult(BaseModel):
    case_id: str
    expected_method_keys: list[str]
    returned_method_keys: list[str]
    hit_at_1: bool
    recall_at_k: float = Field(ge=0, le=1)
    reciprocal_rank: float = Field(ge=0, le=1)


class EvaluationMetrics(BaseModel):
    case_count: int = Field(ge=1)
    hit_at_1: float = Field(ge=0, le=1)
    recall_at_k: float = Field(ge=0, le=1)
    mean_reciprocal_rank: float = Field(ge=0, le=1)
    zero_result_rate: float = Field(ge=0, le=1)


class EvaluationRun(BaseModel):
    id: str
    workspace_id: str
    name: str
    top_k: int
    metrics: EvaluationMetrics
    cases: list[EvaluationCaseResult]
    created_at: datetime


class SolveEvaluationCase(BaseModel):
    id: str = Field(min_length=1, max_length=120)
    problem: str = Field(min_length=1, max_length=20_000)
    tags: list[str] = Field(default_factory=list, max_length=30)
    math_target: SolveMathTarget


class SolveEvaluationRequest(BaseModel):
    name: str = Field(default="solve-evaluation", min_length=1, max_length=120)
    cases: list[SolveEvaluationCase] = Field(min_length=1, max_length=200)
    top_k: int = Field(default=3, ge=1, le=20)
    max_output_tokens: int = Field(default=3_000, ge=256, le=8_000)


class SolveEvaluationCaseResult(BaseModel):
    case_id: str
    status: SolutionAttemptStatus
    verification_status: VerificationStatus
    retrieved_method_keys: list[str]
    used_method_keys: list[str]
    feedback_method_keys: list[str] = Field(default_factory=list)
    generation_provider: str
    fallback_used: bool = False
    correction_attempted: bool = False
    correction_succeeded: bool = False


class SolveEvaluationMetrics(BaseModel):
    case_count: int = Field(ge=1)
    verified_rate: float = Field(ge=0, le=1)
    needs_review_rate: float = Field(ge=0, le=1)
    rejected_rate: float = Field(ge=0, le=1)
    generation_failure_rate: float = Field(ge=0, le=1)
    fallback_rate: float = Field(default=0, ge=0, le=1)
    correction_attempt_rate: float = Field(default=0, ge=0, le=1)
    correction_success_rate: float = Field(default=0, ge=0, le=1)


class SolveEvaluationRun(BaseModel):
    id: str
    workspace_id: str
    name: str
    top_k: int
    metrics: SolveEvaluationMetrics
    cases: list[SolveEvaluationCaseResult]
    created_at: datetime


class HealthResponse(BaseModel):
    status: str
    version: str


class ErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    detail: str
    context: dict[str, Any] = Field(default_factory=dict)
