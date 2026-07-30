from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


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


class ExtractionStatus(StrEnum):
    SUCCESS = "success"
    FALLBACK = "fallback"
    SKIPPED = "skipped"
    ERROR = "error"


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


class MathPayload(BaseModel):
    """Deterministic verification input.

    The natural-language problem and solution remain the source of truth. This
    payload is a safe, machine-checkable sidecar for the first vertical slice.
    """

    expression: str = Field(min_length=1, max_length=1000)
    expected: str = Field(min_length=1, max_length=1000)
    variable: str = "x"
    parameters: list[str] = Field(default_factory=list, max_length=12)
    point: str = "oo"
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


class ExampleCreate(BaseModel):
    problem: str = Field(min_length=1, max_length=20_000)
    solution: str = Field(min_length=1, max_length=40_000)
    tags: list[str] = Field(default_factory=list, max_length=30)
    method_hint: str | None = Field(default=None, max_length=200)
    math_payload: MathPayload | None = None

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
    example_ids: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    @field_validator("key", mode="before")
    @classmethod
    def validate_key(cls, value: str | MethodKind) -> str:
        return _normalize_method_key(value)


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


class HealthResponse(BaseModel):
    status: str
    version: str


class ErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    detail: str
    context: dict[str, Any] = Field(default_factory=dict)
