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


class ExampleOrigin(StrEnum):
    MANUAL = "manual"
    CONVERSATION = "conversation"


class ExampleReviewDecision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


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


class ImportFileFormat(StrEnum):
    AUTO = "auto"
    JSON = "json"
    JSONL = "jsonl"


class ImportReviewPolicy(StrEnum):
    PENDING = "pending"
    PRESERVE = "preserve"


class ImportExtractorPolicy(StrEnum):
    RULES = "rules"
    CONFIGURED = "configured"


class BulkImportItemStatus(StrEnum):
    READY = "ready"
    DUPLICATE = "duplicate"
    INVALID = "invalid"
    IMPORTED = "imported"


class GenerationStatus(StrEnum):
    SUCCESS = "success"
    FALLBACK = "fallback"
    ERROR = "error"


class SolutionAttemptStatus(StrEnum):
    VERIFIED = "verified"
    NEEDS_REVIEW = "needs_review"
    REJECTED = "rejected"
    GENERATION_FAILED = "generation_failed"


class ConversationRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class ConversationMessageKind(StrEnum):
    CHAT = "chat"
    SOLVE = "solve"


class MemoryKind(StrEnum):
    PROFILE = "profile"
    LEARNING_GOAL = "learning_goal"
    EXPLANATION_PREFERENCE = "explanation_preference"
    TOPIC_CONTEXT = "topic_context"
    MANUAL_NOTE = "manual_note"


class MemoryStatus(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    ARCHIVED = "archived"


class MemorySource(StrEnum):
    AUTOMATIC = "automatic"
    MANUAL = "manual"
    USER_EDIT = "user_edit"


class MemoryJobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    STALE = "stale"


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


class ConversationCreate(BaseModel):
    title: str = Field(default="", max_length=120)

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        return " ".join(value.split())


class Conversation(BaseModel):
    id: str
    workspace_id: str
    title: str
    summary: str = ""
    summary_through_ordinal: int = Field(default=0, ge=0)
    message_count: int = Field(default=0, ge=0)
    created_at: datetime
    updated_at: datetime


class ConversationMessage(BaseModel):
    id: str
    workspace_id: str
    conversation_id: str
    turn_id: str
    ordinal: int = Field(ge=1)
    role: ConversationRole
    kind: ConversationMessageKind
    content: str = Field(min_length=1, max_length=80_000)
    provider: str | None = Field(default=None, max_length=120)
    model: str | None = Field(default=None, max_length=120)
    attempt_id: str | None = None
    knowledge_draft_id: str | None = None
    verification_status: VerificationStatus | None = None
    method_keys: list[str] = Field(default_factory=list, max_length=20)
    created_at: datetime


class ConversationTurnRequest(BaseModel):
    message: str = Field(min_length=1, max_length=20_000)
    turn_id: str | None = Field(default=None, min_length=1, max_length=120)
    tags: list[str] = Field(default_factory=list, max_length=30)
    top_k: int = Field(default=5, ge=1, le=20)
    math_target: SolveMathTarget | None = None
    max_output_tokens: int = Field(default=3_000, ge=256, le=8_000)

    @field_validator("message")
    @classmethod
    def normalize_message(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("conversation message cannot be blank")
        return normalized

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            tag = " ".join(value.strip().lower().split())
            if tag and tag not in seen:
                normalized.append(tag)
                seen.add(tag)
        return normalized


class MemoryCreate(BaseModel):
    content: str = Field(min_length=1, max_length=2_000)
    kind: MemoryKind = MemoryKind.MANUAL_NOTE
    tags: list[str] = Field(default_factory=list, max_length=12)
    pinned: bool = False

    @field_validator("content")
    @classmethod
    def normalize_content(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("memory content cannot be blank")
        return normalized

    @field_validator("tags")
    @classmethod
    def normalize_memory_tags(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            tag = " ".join(value.strip().lower().split())[:80]
            if tag and tag not in seen:
                normalized.append(tag)
                seen.add(tag)
        return normalized


class MemoryUpdate(BaseModel):
    content: str | None = Field(default=None, min_length=1, max_length=2_000)
    kind: MemoryKind | None = None
    tags: list[str] | None = Field(default=None, max_length=12)
    pinned: bool | None = None
    status: MemoryStatus | None = None

    @field_validator("content")
    @classmethod
    def normalize_optional_content(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("memory content cannot be blank")
        return normalized

    @field_validator("tags")
    @classmethod
    def normalize_optional_tags(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return None
        return MemoryCreate.normalize_memory_tags(values)

    @model_validator(mode="after")
    def require_change(self) -> MemoryUpdate:
        if not self.model_fields_set:
            raise ValueError("memory update must change at least one field")
        return self


class MemoryItem(BaseModel):
    id: str
    workspace_id: str
    kind: MemoryKind
    content: str
    tags: list[str] = Field(default_factory=list)
    status: MemoryStatus = MemoryStatus.ACTIVE
    pinned: bool = False
    source: MemorySource
    conversation_id: str | None = None
    source_message_id: str | None = None
    evidence: str | None = None
    supersedes_id: str | None = None
    created_at: datetime
    updated_at: datetime


class MemorySettings(BaseModel):
    workspace_id: str
    automatic_extraction_enabled: bool = True
    updated_at: datetime


class MemorySettingsUpdate(BaseModel):
    automatic_extraction_enabled: bool


class MemoryExtractionJob(BaseModel):
    id: str
    workspace_id: str
    conversation_id: str
    from_ordinal: int = Field(ge=0)
    through_ordinal: int = Field(ge=0)
    source_revision: str
    status: MemoryJobStatus
    attempts: int = Field(default=0, ge=0, le=3)
    provider: str | None = None
    model: str | None = None
    extracted_count: int = Field(default=0, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    duration_ms: int = Field(default=0, ge=0)
    error: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


class MemoryHealth(BaseModel):
    workspace_id: str
    automatic_extraction_enabled: bool
    extractor_available: bool
    queued_count: int = Field(ge=0)
    running_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    last_success_at: datetime | None = None
    last_error_at: datetime | None = None
    last_error: str | None = None


class MemoryBackfillResult(BaseModel):
    workspace_id: str
    queued_jobs: list[MemoryExtractionJob]
    skipped_conversation_count: int = Field(ge=0)


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


class MathTargetDraftRequest(BaseModel):
    problem: str = Field(min_length=1, max_length=20_000)


class MathTargetDraftResult(BaseModel):
    """Untrusted target suggestion that must be confirmed before solving."""

    target: SolveMathTarget | None = None
    status: ExtractionStatus
    provider: str = Field(min_length=1, max_length=120)
    model: str | None = Field(default=None, max_length=120)
    response_id: str | None = Field(default=None, max_length=200)
    prompt_version: str = Field(min_length=1, max_length=80)
    confidence: float = Field(default=0, ge=0, le=1)
    summary: str = Field(default="", max_length=2_000)
    warnings: list[str] = Field(default_factory=list, max_length=20)
    fallback_used: bool = False
    raw_output: str | None = Field(default=None, max_length=8_000)
    error: str | None = Field(default=None, max_length=2_000)
    requires_confirmation: bool = True


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


class MethodDraftPreview(BaseModel):
    key: str
    name: str
    goal: str
    applicable_when: list[str]
    procedure: list[str]
    failure_modes: list[str]
    tags: list[str]


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
    method_drafts: list[MethodDraftPreview] = Field(default_factory=list, max_length=50)
    status: KnowledgeStatus
    origin: ExampleOrigin = ExampleOrigin.MANUAL
    source_attempt_id: str | None = None
    reviewed_at: datetime | None = None
    reviewer_note: str = ""
    revision: int = Field(default=1, ge=1)
    created_at: datetime
    updated_at: datetime


class ExampleReviewRequest(BaseModel):
    decision: ExampleReviewDecision
    expected_revision: int = Field(ge=1)
    reviewer_note: str = Field(default="", max_length=4_000)


class ExampleDraftUpdate(BaseModel):
    """Complete replacement content for one unreviewed knowledge draft."""

    expected_revision: int = Field(ge=1)
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


class ExampleVersion(BaseModel):
    example_id: str
    workspace_id: str
    revision: int = Field(ge=1)
    problem: str
    solution: str
    tags: list[str]
    method_hint: str | None = None
    problem_kind: ProblemKind
    math_payload: MathPayload | None = None
    verification: VerificationReport
    extraction: MethodExtractionTrace | None = None
    method_drafts: list[MethodDraftPreview] = Field(default_factory=list, max_length=50)
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


class ExampleReviewResult(BaseModel):
    example: ProblemExample
    learned_methods: list[MethodCard]


class ExampleDraftUpdateResult(BaseModel):
    example: ProblemExample
    learned_methods: list[MethodCard]


class ConversationCaptureResult(BaseModel):
    example: ProblemExample
    learned_methods: list[MethodCard]
    created: bool


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


class MergeProposalStatus(StrEnum):
    PENDING = "pending"
    APPLIED = "applied"
    REJECTED = "rejected"
    STALE = "stale"


class MethodMergeProposal(BaseModel):
    """疑似重复的一对方法卡。默认只提议，合并需人工确认。"""

    id: str
    workspace_id: str
    primary_method_id: str
    primary_key: str
    duplicate_method_id: str
    duplicate_key: str
    score: float = Field(ge=0, le=1)
    signature_similarity: float = Field(ge=0, le=1)
    text_similarity: float = Field(ge=0, le=1)
    reasons: list[str] = Field(default_factory=list)
    status: MergeProposalStatus
    created_at: datetime
    resolved_at: datetime | None = None


class MergeScanRequest(BaseModel):
    threshold: float | None = Field(default=None, ge=0, le=1)


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


class BulkExampleImportRequest(BaseModel):
    """Text corpus submitted for validation or one atomic import."""

    content: str = Field(min_length=1, max_length=5_000_000)
    file_format: ImportFileFormat = ImportFileFormat.AUTO
    review_policy: ImportReviewPolicy = ImportReviewPolicy.PENDING
    extractor_policy: ImportExtractorPolicy = ImportExtractorPolicy.RULES
    commit: bool = False
    source_name: str = Field(default="import", min_length=1, max_length=255)


class BulkImportItemResult(BaseModel):
    index: int = Field(ge=1)
    status: BulkImportItemStatus
    problem_preview: str = Field(default="", max_length=240)
    fingerprint: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    verification: VerificationReport | None = None
    method_keys: list[str] = Field(default_factory=list, max_length=50)
    example_id: str | None = None
    errors: list[str] = Field(default_factory=list, max_length=20)


class BulkExampleImportResult(BaseModel):
    source_name: str
    detected_format: ImportFileFormat
    commit_requested: bool
    committed: bool
    can_commit: bool
    total_count: int = Field(ge=0, le=500)
    ready_count: int = Field(ge=0, le=500)
    duplicate_count: int = Field(ge=0, le=500)
    invalid_count: int = Field(ge=0, le=500)
    imported_count: int = Field(ge=0, le=500)
    items: list[BulkImportItemResult] = Field(default_factory=list, max_length=500)


class WorkspaceRestoreResult(BaseModel):
    workspace: Workspace
    source_workspace_id: str
    source_app_version: str
    archive_format_version: int = Field(ge=1)
    restored_record_counts: dict[str, int] = Field(default_factory=dict)


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


class ConversationTurnResult(BaseModel):
    conversation: Conversation
    user_message: ConversationMessage
    assistant_message: ConversationMessage
    attempt: SolutionAttempt | None = None
    knowledge_draft: ProblemExample | None = None
    summary_updated: bool = False
    memory_job: MemoryExtractionJob | None = None


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


class EvaluationSlice(StrEnum):
    """留出题的分组。

    聚合分数会掩盖问题——v0.12 的检索聚合分是 1.0，而其中 78% 的题在训练集里有结构
    完全相同的样本。按切片分开报数，「记住见过的形状」和「泛化到新形状」才区分得开。
    """

    # 训练里有结构同构样本。对照组：这一格掉分才说明检索真的坏了。
    CONTROL = "control"
    # 路径组合在训练集中不存在，方法仍在已知方法之内。真正的泛化测量。
    NOVEL_SHAPE = "novel_shape"
    # 表面算子像某个家族，正确方法却是另一个。测会不会被表面形状骗走。
    CROSS_FAMILY = "cross_family"
    # 需要两个以上方法。单标签下 Recall@K 恒 ≥ Hit@1，只有多标签才让它携带信息。
    MULTI_METHOD = "multi_method"


class EvaluationCase(BaseModel):
    id: str = Field(min_length=1, max_length=120)
    problem: str = Field(min_length=1, max_length=20_000)
    tags: list[str] = Field(default_factory=list, max_length=30)
    expected_method_keys: list[str] = Field(min_length=1, max_length=20)
    # 带上结构化目标后，检索才能用数学结构而不只是词面来匹配方法。
    math_target: SolveMathTarget | None = None
    # 可选：旧的方法卡改写句留出集没有切片，仍然要能跑。
    slice: EvaluationSlice | None = None
    # 方法标签的判定理由，供人工复核时核对。
    rationale: str = Field(default="", max_length=500)

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
    # provider 配置解析失败时把原因带出来，设置界面才能告诉用户哪里写坏了；
    # 配置正常时为 None。
    provider_config_error: str | None = None


class ProviderTestRequest(BaseModel):
    """连接测试入参。密钥只用于本次请求，不落盘、不进日志、不回显。"""

    base_url: str = Field(min_length=1, max_length=500)
    model: str = Field(min_length=1, max_length=200)
    api_key: str | None = Field(default=None, max_length=500)
    timeout_seconds: float = Field(default=20.0, gt=0, le=120)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped.startswith(("http://", "https://")):
            raise ValueError("base_url must start with http:// or https://")
        return stripped.rstrip("/")


class ProviderTestResult(BaseModel):
    ok: bool
    duration_ms: int = Field(ge=0)
    model: str | None = None
    sample: str | None = Field(default=None, max_length=200)
    error: str | None = Field(default=None, max_length=400)


class ErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    detail: str
    context: dict[str, Any] = Field(default_factory=dict)
