from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from threading import RLock

from math_harness.bulk_import import (
    ParsedImportItem,
    example_fingerprint,
    parse_example_corpus,
    problem_preview,
    stored_example_fingerprint,
)
from math_harness.classifier import classify_problem
from math_harness.config import load_local_environment
from math_harness.dedup import find_merge_candidates
from math_harness.errors import InvalidKnowledgeState
from math_harness.extraction import (
    MethodExtractorProtocol,
    build_method_extractor_from_env,
)
from math_harness.methods import MethodExtractor
from math_harness.models import (
    AnswerKind,
    BulkExampleImportRequest,
    BulkExampleImportResult,
    BulkImportItemResult,
    BulkImportItemStatus,
    CandidateSolution,
    CandidateStep,
    ConversationCaptureResult,
    EvaluationCaseResult,
    EvaluationMetrics,
    EvaluationRequest,
    EvaluationRun,
    ExampleCreate,
    ExampleDraftUpdate,
    ExampleDraftUpdateResult,
    ExampleOrigin,
    ExampleReviewDecision,
    ExampleReviewRequest,
    ExampleReviewResult,
    ExampleVersion,
    ExtractionStatus,
    GenerationStageKind,
    GenerationStatus,
    ImportExtractorPolicy,
    IngestionResult,
    KnowledgeStatus,
    LearningEvent,
    MathPayload,
    MathTargetDraftRequest,
    MathTargetDraftResult,
    MergeProposalStatus,
    MethodCard,
    MethodExtractionResult,
    MethodExtractionTrace,
    MethodMatch,
    MethodMergeProposal,
    MethodStatusUpdate,
    MethodVersion,
    ProblemExample,
    SolutionAttempt,
    SolutionAttemptStatus,
    SolutionCorrection,
    SolutionGenerationResult,
    SolutionGenerationStage,
    SolutionGenerationTrace,
    SolveEvaluationCaseResult,
    SolveEvaluationMetrics,
    SolveEvaluationRequest,
    SolveEvaluationRun,
    SolveMathTarget,
    SolvePlan,
    SolveRequest,
    VerificationMode,
    VerificationReport,
    VerificationStatus,
    Workspace,
    WorkspaceCreate,
    WorkspaceRestoreResult,
    utc_now,
)
from math_harness.normalization import CandidateSolutionNormalizer
from math_harness.portability import (
    create_workspace_archive,
    restore_workspace_archive,
)
from math_harness.retrieval import MethodRetriever
from math_harness.solving import (
    SolutionGeneratorProtocol,
    build_solution_generator_from_env,
)
from math_harness.storage import WorkspaceManager, WorkspaceStore
from math_harness.structure import extract_features
from math_harness.target_drafting import (
    TargetDrafterProtocol,
    build_target_drafter_from_env,
)
from math_harness.verifier import SolutionVerifier


@dataclass(frozen=True)
class _PreparedIngestion:
    request: ExampleCreate
    example: ProblemExample
    extraction_result: MethodExtractionResult
    promotion_approved: bool


class MathHarnessService:
    def __init__(
        self,
        data_root: Path | str,
        verifier: SolutionVerifier | None = None,
        extractor: MethodExtractorProtocol | None = None,
        retriever: MethodRetriever | None = None,
        generator: SolutionGeneratorProtocol | None = None,
        normalizer: CandidateSolutionNormalizer | None = None,
        target_drafter: TargetDrafterProtocol | None = None,
    ) -> None:
        load_local_environment()
        self.workspaces = WorkspaceManager(data_root)
        self.verifier = verifier or SolutionVerifier()
        self.extractor = extractor or build_method_extractor_from_env()
        self.retriever = retriever or MethodRetriever()
        self.generator = generator or build_solution_generator_from_env()
        self.normalizer = normalizer or CandidateSolutionNormalizer()
        self.target_drafter = target_drafter or build_target_drafter_from_env()
        self._knowledge_lock = RLock()

    def create_workspace(self, request: WorkspaceCreate) -> Workspace:
        return self.workspaces.create(request)

    def get_workspace(self, workspace_id: str) -> Workspace:
        return self.workspaces.get(workspace_id)

    def list_workspaces(self) -> list[Workspace]:
        return self.workspaces.list()

    def export_workspace_backup(self, workspace_id: str) -> bytes:
        return create_workspace_archive(self.workspaces, workspace_id)

    def restore_workspace_backup(self, payload: bytes) -> WorkspaceRestoreResult:
        with self._knowledge_lock:
            return restore_workspace_archive(self.workspaces, payload)

    def draft_math_target(
        self,
        workspace_id: str,
        request: MathTargetDraftRequest,
    ) -> MathTargetDraftResult:
        # Resolve the workspace first so drafting cannot be used as a cross-scope
        # side channel from an invalid workspace identifier.
        self.workspaces.get(workspace_id)
        return self.target_drafter.draft(request.problem)

    def ingest_example(
        self, workspace_id: str, request: ExampleCreate
    ) -> IngestionResult:
        with self._knowledge_lock:
            return self._ingest_example(
                workspace_id,
                request,
                origin=ExampleOrigin.MANUAL,
            )

    def bulk_import_examples(
        self,
        workspace_id: str,
        request: BulkExampleImportRequest,
    ) -> BulkExampleImportResult:
        with self._knowledge_lock:
            return self._bulk_import_examples(workspace_id, request)

    def _bulk_import_examples(
        self,
        workspace_id: str,
        request: BulkExampleImportRequest,
    ) -> BulkExampleImportResult:
        store = self.workspaces.store(workspace_id)
        detected_format, parsed = parse_example_corpus(
            request.content,
            request.file_format,
            request.review_policy,
            source_name=request.source_name,
        )
        known_fingerprints = {
            stored_example_fingerprint(example) for example in store.list_examples()
        }
        ready: list[tuple[ParsedImportItem, VerificationReport]] = []
        item_results: list[BulkImportItemResult] = []
        for item in parsed:
            if item.request is None:
                item_results.append(
                    BulkImportItemResult(
                        index=item.index,
                        status=BulkImportItemStatus.INVALID,
                        errors=list(item.errors),
                    )
                )
                continue

            fingerprint = example_fingerprint(item.request)
            if fingerprint in known_fingerprints:
                item_results.append(
                    BulkImportItemResult(
                        index=item.index,
                        status=BulkImportItemStatus.DUPLICATE,
                        problem_preview=problem_preview(item.request),
                        fingerprint=fingerprint,
                    )
                )
                continue

            known_fingerprints.add(fingerprint)
            verification = self.verifier.verify(item.request.math_payload)
            ready.append((item, verification))
            item_results.append(
                BulkImportItemResult(
                    index=item.index,
                    status=BulkImportItemStatus.READY,
                    problem_preview=problem_preview(item.request),
                    fingerprint=fingerprint,
                    verification=verification,
                )
            )

        invalid_count = sum(
            item.status is BulkImportItemStatus.INVALID for item in item_results
        )
        duplicate_count = sum(
            item.status is BulkImportItemStatus.DUPLICATE for item in item_results
        )
        can_commit = invalid_count == 0
        if not request.commit or not can_commit:
            return BulkExampleImportResult(
                source_name=request.source_name,
                detected_format=detected_format,
                commit_requested=request.commit,
                committed=False,
                can_commit=can_commit,
                total_count=len(item_results),
                ready_count=len(ready),
                duplicate_count=duplicate_count,
                invalid_count=invalid_count,
                imported_count=0,
                items=item_results,
            )

        extractor = (
            MethodExtractor()
            if request.extractor_policy is ImportExtractorPolicy.RULES
            else self.extractor
        )
        prepared = [
            self._prepare_ingestion(
                workspace_id,
                item.request,
                origin=ExampleOrigin.MANUAL,
                verification_override=verification,
                extractor=extractor,
            )
            for item, verification in ready
            if item.request is not None
        ]
        persisted: dict[int, IngestionResult] = {}
        with store.atomic():
            for (item, _), prepared_item in zip(ready, prepared, strict=True):
                persisted[item.index] = self._persist_prepared_ingestion(
                    store,
                    prepared_item,
                )

        committed_items: list[BulkImportItemResult] = []
        for result in item_results:
            ingestion = persisted.get(result.index)
            if ingestion is None:
                committed_items.append(result)
                continue
            committed_items.append(
                result.model_copy(
                    update={
                        "status": BulkImportItemStatus.IMPORTED,
                        "example_id": ingestion.example.id,
                        "method_keys": [
                            method.key for method in ingestion.learned_methods
                        ],
                    }
                )
            )
        return BulkExampleImportResult(
            source_name=request.source_name,
            detected_format=detected_format,
            commit_requested=True,
            committed=True,
            can_commit=True,
            total_count=len(committed_items),
            ready_count=len(ready),
            duplicate_count=duplicate_count,
            invalid_count=0,
            imported_count=len(persisted),
            items=committed_items,
        )

    def _ingest_example(
        self,
        workspace_id: str,
        request: ExampleCreate,
        *,
        origin: ExampleOrigin,
        source_attempt_id: str | None = None,
        verification_override: VerificationReport | None = None,
        extractor: MethodExtractorProtocol | None = None,
        store: WorkspaceStore | None = None,
    ) -> IngestionResult:
        workspace_store = store or self.workspaces.store(workspace_id)
        prepared = self._prepare_ingestion(
            workspace_id,
            request,
            origin=origin,
            source_attempt_id=source_attempt_id,
            verification_override=verification_override,
            extractor=extractor,
        )
        return self._persist_prepared_ingestion(workspace_store, prepared)

    def _prepare_ingestion(
        self,
        workspace_id: str,
        request: ExampleCreate,
        *,
        origin: ExampleOrigin,
        source_attempt_id: str | None = None,
        verification_override: VerificationReport | None = None,
        extractor: MethodExtractorProtocol | None = None,
    ) -> _PreparedIngestion:
        verification = verification_override or self.verifier.verify(
            request.math_payload
        )
        promotion_approved = (
            verification.status is VerificationStatus.VERIFIED and request.reviewed
        )
        if promotion_approved:
            status = KnowledgeStatus.PROMOTED
        elif verification.status is VerificationStatus.REJECTED:
            status = KnowledgeStatus.REJECTED
        else:
            status = KnowledgeStatus.PENDING_REVIEW

        extraction_result = self._extract_methods(
            request,
            verification.status,
            extractor=extractor,
        )
        if origin is ExampleOrigin.CONVERSATION:
            extraction_result = self._filter_conversation_extraction(extraction_result)
        now = utc_now()
        example = ProblemExample(
            id=str(uuid.uuid4()),
            workspace_id=workspace_id,
            problem=request.problem,
            solution=request.solution,
            tags=request.tags,
            method_hint=request.method_hint,
            reviewed=request.reviewed,
            problem_kind=classify_problem(request.problem),
            math_payload=request.math_payload,
            verification=verification,
            extraction=extraction_result.trace,
            method_drafts=[
                draft.model_dump(mode="json") for draft in extraction_result.methods
            ],
            status=status,
            origin=origin,
            source_attempt_id=source_attempt_id,
            reviewed_at=now if request.reviewed else None,
            created_at=now,
            updated_at=now,
        )
        return _PreparedIngestion(
            request=request,
            example=example,
            extraction_result=extraction_result,
            promotion_approved=promotion_approved,
        )

    def _persist_prepared_ingestion(
        self,
        store: WorkspaceStore,
        prepared: _PreparedIngestion,
    ) -> IngestionResult:
        request = prepared.request
        example = prepared.example
        extraction_result = prepared.extraction_result
        promotion_approved = prepared.promotion_approved
        store.add_example(example, extraction_result.methods)

        learned_methods: list[MethodCard] = []
        if example.verification.status is not VerificationStatus.REJECTED:
            method_status = (
                KnowledgeStatus.PROMOTED
                if promotion_approved
                else KnowledgeStatus.PENDING_REVIEW
            )
            # 签名会直接影响后续检索，因此它和方法晋级共用同一条信任边界：
            # 数学上可验证但尚未经人工复核的例子，也不能改写已晋级知识。
            features = (
                extract_features(request.math_payload) if promotion_approved else None
            )
            for draft in extraction_result.methods:
                learned_methods.append(
                    store.upsert_method(
                        draft=draft,
                        example_id=example.id,
                        status=method_status,
                        verified=promotion_approved,
                        features=features,
                    )
                )

        return IngestionResult(example=example, learned_methods=learned_methods)

    def _extract_methods(
        self,
        request: ExampleCreate,
        verification_status: VerificationStatus,
        *,
        extractor: MethodExtractorProtocol | None = None,
    ) -> MethodExtractionResult:
        selected_extractor = extractor or self.extractor
        provider = getattr(
            selected_extractor,
            "name",
            selected_extractor.__class__.__name__,
        )
        prompt_version = getattr(selected_extractor, "prompt_version", "unknown")
        if verification_status is VerificationStatus.REJECTED:
            return MethodExtractionResult(
                trace=MethodExtractionTrace(
                    provider=provider,
                    prompt_version=prompt_version,
                    status=ExtractionStatus.SKIPPED,
                    extracted_method_keys=[],
                )
            )
        try:
            return selected_extractor.extract(
                request.problem, request.solution, request.method_hint
            )
        except Exception as exc:  # noqa: BLE001
            return MethodExtractionResult(
                trace=MethodExtractionTrace(
                    provider=provider,
                    model=getattr(selected_extractor, "model", None),
                    prompt_version=prompt_version,
                    status=ExtractionStatus.ERROR,
                    error=f"{exc.__class__.__name__}: {exc}"[:2_000],
                    extracted_method_keys=[],
                )
            )

    @staticmethod
    def _filter_conversation_extraction(
        extraction_result: MethodExtractionResult,
    ) -> MethodExtractionResult:
        # A generic fallback is useful for explicit corpus ingestion, but on every
        # chat turn it would create low-signal method cards.
        kept_methods = [
            draft
            for draft in extraction_result.methods
            if draft.key != "generic_example"
        ]
        if len(kept_methods) == len(extraction_result.methods):
            return extraction_result
        kept_keys = {draft.key for draft in kept_methods}
        return extraction_result.model_copy(
            update={
                "methods": kept_methods,
                "trace": extraction_result.trace.model_copy(
                    update={
                        "extracted_method_keys": [
                            key
                            for key in extraction_result.trace.extracted_method_keys
                            if key in kept_keys
                        ],
                        "evidence_by_method": {
                            key: evidence
                            for key, evidence in extraction_result.trace.evidence_by_method.items()
                            if key in kept_keys
                        },
                        "confidence_by_method": {
                            key: confidence
                            for key, confidence in extraction_result.trace.confidence_by_method.items()
                            if key in kept_keys
                        },
                    }
                ),
            }
        )

    def get_example(self, workspace_id: str, example_id: str) -> ProblemExample:
        return self.workspaces.store(workspace_id).get_example(example_id)

    def list_examples(self, workspace_id: str) -> list[ProblemExample]:
        return self.workspaces.store(workspace_id).list_examples()

    def list_example_versions(
        self,
        workspace_id: str,
        example_id: str,
    ) -> list[ExampleVersion]:
        return self.workspaces.store(workspace_id).list_example_versions(example_id)

    def update_example_draft(
        self,
        workspace_id: str,
        example_id: str,
        request: ExampleDraftUpdate,
    ) -> ExampleDraftUpdateResult:
        with self._knowledge_lock:
            return self._update_example_draft(workspace_id, example_id, request)

    def _update_example_draft(
        self,
        workspace_id: str,
        example_id: str,
        request: ExampleDraftUpdate,
    ) -> ExampleDraftUpdateResult:
        store = self.workspaces.store(workspace_id)
        example = store.get_example(example_id)
        if example.revision != request.expected_revision:
            raise InvalidKnowledgeState("知识草稿已被其他操作更新；请刷新后再保存。")
        if example.status is KnowledgeStatus.PROMOTED or example.reviewed:
            raise InvalidKnowledgeState("已晋级或已复核的例题不能作为草稿编辑。")
        if example.status is KnowledgeStatus.DEPRECATED or (
            example.status is KnowledgeStatus.REJECTED
            and example.reviewed_at is not None
        ):
            raise InvalidKnowledgeState("已人工驳回或废弃的例题不能继续编辑。")

        changed_fields = [
            field
            for field, old, new in (
                ("problem", example.problem, request.problem),
                ("solution", example.solution, request.solution),
                ("tags", example.tags, request.tags),
                ("method_hint", example.method_hint, request.method_hint),
                ("math_payload", example.math_payload, request.math_payload),
            )
            if old != new
        ]
        if not changed_fields:
            return ExampleDraftUpdateResult(
                example=example,
                learned_methods=store.list_methods_for_example(example.id),
            )

        verification = self.verifier.verify(request.math_payload)
        create_request = ExampleCreate(
            problem=request.problem,
            solution=request.solution,
            tags=request.tags,
            method_hint=request.method_hint,
            math_payload=request.math_payload,
            reviewed=False,
        )
        extraction_result = self._extract_methods(
            create_request,
            verification.status,
        )
        if example.origin is ExampleOrigin.CONVERSATION:
            extraction_result = self._filter_conversation_extraction(extraction_result)

        now = utc_now()
        updated = example.model_copy(
            update={
                "problem": request.problem,
                "solution": request.solution,
                "tags": request.tags,
                "method_hint": request.method_hint,
                "problem_kind": classify_problem(request.problem),
                "math_payload": request.math_payload,
                "verification": verification,
                "extraction": extraction_result.trace,
                "method_drafts": [
                    draft.model_dump(mode="json") for draft in extraction_result.methods
                ],
                "status": (
                    KnowledgeStatus.REJECTED
                    if verification.status is VerificationStatus.REJECTED
                    else KnowledgeStatus.PENDING_REVIEW
                ),
                "reviewed": False,
                "reviewed_at": None,
                "reviewer_note": "",
                "revision": example.revision + 1,
                "updated_at": now,
            }
        )
        saved = store.replace_example_draft(
            updated,
            extraction_result.methods,
            changed_fields=changed_fields,
        )
        learned_methods: list[MethodCard] = []
        if verification.status is not VerificationStatus.REJECTED:
            for draft in extraction_result.methods:
                learned_methods.append(
                    store.upsert_method(
                        draft=draft,
                        example_id=example.id,
                        status=KnowledgeStatus.PENDING_REVIEW,
                        verified=False,
                    )
                )
        return ExampleDraftUpdateResult(
            example=saved,
            learned_methods=learned_methods,
        )

    def capture_solution_attempt(
        self,
        workspace_id: str,
        attempt_id: str,
    ) -> ConversationCaptureResult:
        with self._knowledge_lock:
            return self._capture_solution_attempt(workspace_id, attempt_id)

    def _capture_solution_attempt(
        self,
        workspace_id: str,
        attempt_id: str,
    ) -> ConversationCaptureResult:
        """Turn one persisted conversation into one idempotent knowledge draft."""

        store = self.workspaces.store(workspace_id)
        existing = store.get_example_by_source_attempt(attempt_id)
        if existing is not None:
            return ConversationCaptureResult(
                example=existing,
                learned_methods=store.list_methods_for_example(existing.id),
                created=False,
            )

        attempt = store.get_solution_attempt(attempt_id)
        candidate = attempt.candidate
        if candidate is None:
            raise InvalidKnowledgeState(
                "生成失败的求解记录没有候选解，不能转为知识草稿。"
            )

        math_payload: MathPayload | None = None
        if (
            attempt.math_target is not None
            and candidate.answer_kind is AnswerKind.EXPRESSION
            and candidate.answer_expression
        ):
            math_payload = MathPayload(
                **attempt.math_target.model_dump(),
                expected=candidate.answer_expression,
            )
        method_hint = ", ".join(candidate.used_method_keys) or None
        if method_hint is not None:
            method_hint = method_hint[:200]
        result = self._ingest_example(
            workspace_id,
            ExampleCreate(
                problem=attempt.problem,
                solution=self._candidate_solution_text(candidate),
                tags=attempt.tags,
                method_hint=method_hint,
                math_payload=math_payload,
                reviewed=False,
            ),
            origin=ExampleOrigin.CONVERSATION,
            source_attempt_id=attempt.id,
            verification_override=attempt.verification,
        )
        return ConversationCaptureResult(
            example=result.example,
            learned_methods=result.learned_methods,
            created=True,
        )

    def review_example(
        self,
        workspace_id: str,
        example_id: str,
        request: ExampleReviewRequest,
    ) -> ExampleReviewResult:
        with self._knowledge_lock:
            return self._review_example(workspace_id, example_id, request)

    def _review_example(
        self,
        workspace_id: str,
        example_id: str,
        request: ExampleReviewRequest,
    ) -> ExampleReviewResult:
        store = self.workspaces.store(workspace_id)
        example = store.get_example(example_id)
        if example.revision != request.expected_revision:
            raise InvalidKnowledgeState("知识草稿已被其他操作更新；请刷新并重新复核。")

        if request.decision is ExampleReviewDecision.REJECT:
            if example.status is KnowledgeStatus.PROMOTED:
                raise InvalidKnowledgeState(
                    "已晋级例题不能直接驳回；请单独废弃受影响的方法卡。"
                )
            if example.status is KnowledgeStatus.REJECTED:
                return ExampleReviewResult(example=example, learned_methods=[])
            rejected = store.reject_example(example.id, request.reviewer_note)
            return ExampleReviewResult(example=rejected, learned_methods=[])

        if example.status is KnowledgeStatus.PROMOTED and example.reviewed:
            return ExampleReviewResult(
                example=example,
                learned_methods=store.list_methods_for_example(example.id),
            )
        if example.status in {
            KnowledgeStatus.REJECTED,
            KnowledgeStatus.DEPRECATED,
        }:
            raise InvalidKnowledgeState("已拒绝或废弃的例题不能晋级。")
        if example.source_attempt_id is not None and example.revision == 1:
            source_attempt = store.get_solution_attempt(example.source_attempt_id)
            if source_attempt.status is not SolutionAttemptStatus.VERIFIED:
                raise InvalidKnowledgeState(
                    "来源求解记录未完整通过独立数学验证，不能晋级这条例题。"
                )

        fresh_verification = self.verifier.verify(example.math_payload)
        if fresh_verification.status is not VerificationStatus.VERIFIED:
            raise InvalidKnowledgeState(
                "只有独立数学验证通过的例题才能晋级；请先补充可验证数学目标。"
            )

        drafts = store.get_example_method_drafts(example.id)
        replacement_extraction: MethodExtractionTrace | None = None
        if (
            not drafts
            and example.extraction is not None
            and example.extraction.extracted_method_keys
        ):
            extraction_result = self._extract_methods(
                ExampleCreate(
                    problem=example.problem,
                    solution=example.solution,
                    tags=example.tags,
                    method_hint=example.method_hint,
                    math_payload=example.math_payload,
                    reviewed=True,
                ),
                fresh_verification.status,
            )
            drafts = extraction_result.methods
            replacement_extraction = extraction_result.trace

        features = extract_features(example.math_payload)
        learned_methods = [
            store.upsert_method(
                draft=draft,
                example_id=example.id,
                status=KnowledgeStatus.PROMOTED,
                verified=True,
                features=features,
                idempotent_evidence=True,
            )
            for draft in drafts
        ]
        reviewed = store.complete_example_review(
            example.id,
            request.reviewer_note,
            expected_revision=request.expected_revision,
            extraction=replacement_extraction,
            method_drafts=drafts if replacement_extraction is not None else None,
        )
        return ExampleReviewResult(
            example=reviewed,
            learned_methods=learned_methods,
        )

    @staticmethod
    def _candidate_solution_text(candidate: CandidateSolution) -> str:
        parts = [candidate.answer_text.strip()]
        if candidate.steps:
            rendered_steps = []
            for index, step in enumerate(candidate.steps, start=1):
                line = f"{index}. {step.explanation.strip()}"
                if step.expression:
                    line += f"\n   {step.expression.strip()}"
                rendered_steps.append(line)
            parts.append("解题步骤：\n" + "\n".join(rendered_steps))
        if (
            candidate.answer_expression
            and candidate.answer_expression not in candidate.answer_text
        ):
            parts.append(f"最终表达式：{candidate.answer_expression}")
        return "\n\n".join(part for part in parts if part).strip()[:40_000]

    def list_methods(
        self,
        workspace_id: str,
        include_pending: bool = True,
        include_deprecated: bool = False,
    ) -> list[MethodCard]:
        return self.workspaces.store(workspace_id).list_methods(
            include_pending=include_pending,
            include_deprecated=include_deprecated,
        )

    def list_method_versions(
        self, workspace_id: str, method_id: str
    ) -> list[MethodVersion]:
        return self.workspaces.store(workspace_id).list_method_versions(method_id)

    def scan_merge_proposals(
        self, workspace_id: str, threshold: float | None = None
    ) -> list[MethodMergeProposal]:
        """扫描疑似重复的方法卡。只写提案，不动数据——合并需人工确认。

        显式触发而不是挂在摄取路径上：全量两两比较既慢又吵。
        """

        store = self.workspaces.store(workspace_id)
        methods = store.list_methods(include_pending=True, include_deprecated=False)
        return store.record_merge_proposals(
            find_merge_candidates(methods, threshold=threshold)
        )

    def list_merge_proposals(
        self, workspace_id: str, status: MergeProposalStatus | None = None
    ) -> list[MethodMergeProposal]:
        return self.workspaces.store(workspace_id).list_merge_proposals(status)

    def apply_merge_proposal(self, workspace_id: str, proposal_id: str) -> MethodCard:
        return self.workspaces.store(workspace_id).apply_merge_proposal(proposal_id)

    def reject_merge_proposal(
        self, workspace_id: str, proposal_id: str
    ) -> MethodMergeProposal:
        return self.workspaces.store(workspace_id).resolve_merge_proposal(
            proposal_id, MergeProposalStatus.REJECTED
        )

    def update_method_status(
        self,
        workspace_id: str,
        method_id: str,
        request: MethodStatusUpdate,
    ) -> MethodCard:
        if request.status is KnowledgeStatus.PROMOTED:
            raise InvalidKnowledgeState("方法卡只能通过已验证例题的人工复核晋级。")
        return self.workspaces.store(workspace_id).update_method_status(
            method_id, request.status
        )

    def list_learning_events(self, workspace_id: str) -> list[LearningEvent]:
        return self.workspaces.store(workspace_id).list_learning_events()

    def search_methods(
        self,
        workspace_id: str,
        query: str,
        tags: list[str] | None = None,
        top_k: int = 5,
        math_target: SolveMathTarget | None = None,
    ) -> list[MethodMatch]:
        methods = self.workspaces.store(workspace_id).list_methods(
            include_pending=False
        )
        return self.retriever.search(
            methods,
            query,
            tags=tags,
            top_k=top_k,
            features=extract_features(math_target) if math_target else None,
        )

    def build_solve_plan(
        self,
        workspace_id: str,
        problem: str,
        tags: list[str] | None = None,
        top_k: int = 5,
        math_target: SolveMathTarget | None = None,
    ) -> SolvePlan:
        matches = self.search_methods(
            workspace_id,
            problem,
            tags=tags,
            top_k=top_k,
            math_target=math_target,
        )
        return SolvePlan(
            workspace_id=workspace_id,
            problem=problem,
            problem_kind=classify_problem(problem),
            recommended_methods=matches,
            note=(
                "这是不执行生成与反馈的检索预览；使用 /solve 可生成候选解、"
                "执行数学验证并保存尝试记录。"
            ),
        )

    def solve_problem(
        self,
        workspace_id: str,
        request: SolveRequest,
    ) -> SolutionAttempt:
        attempt = self._build_solution_attempt(workspace_id, request)
        return self._persist_attempt_and_capture(workspace_id, attempt)

    def _persist_attempt_and_capture(
        self,
        workspace_id: str,
        attempt: SolutionAttempt,
    ) -> SolutionAttempt:
        store = self.workspaces.store(workspace_id)
        saved = store.add_solution_attempt(attempt)
        if saved.candidate is None:
            store.record_learning_event(
                "conversation_capture_skipped",
                saved.id,
                {"reason": "generation_failed"},
            )
            return saved
        try:
            capture = self.capture_solution_attempt(workspace_id, saved.id)
            store.record_learning_event(
                "conversation_capture_completed",
                saved.id,
                {
                    "example_id": capture.example.id,
                    "created": capture.created,
                    "status": capture.example.status.value,
                },
            )
        except Exception as exc:  # noqa: BLE001
            # A knowledge-extraction failure must not make a completed solve look
            # lost. The explicit /capture endpoint can safely retry this attempt.
            store.record_learning_event(
                "conversation_capture_failed",
                saved.id,
                {"error": f"{exc.__class__.__name__}: {exc}"[:2_000]},
            )
        return saved

    def _build_solution_attempt(
        self,
        workspace_id: str,
        request: SolveRequest,
        *,
        correction_of: str | None = None,
        generation_result: SolutionGenerationResult | None = None,
    ) -> SolutionAttempt:
        allow_automatic_recovery = generation_result is None
        matches = self.search_methods(
            workspace_id,
            request.problem,
            tags=request.tags,
            top_k=request.top_k,
            math_target=request.math_target,
        )
        if generation_result is None:
            generation_result = self._generate_candidate(request, matches)
        if allow_automatic_recovery:
            generation_result = self._prepare_generation_result(
                generation_result,
                matches,
                request.math_target,
            )
        else:
            generation_result = self._filter_unretrieved_method_keys(
                generation_result,
                matches,
            )
        verification, status = self._verify_candidate(request, generation_result)
        initial_stage = (
            GenerationStageKind.INITIAL
            if allow_automatic_recovery
            else GenerationStageKind.HUMAN
        )
        generation_result = self._record_initial_verification(
            generation_result,
            initial_stage,
            verification,
        )
        if allow_automatic_recovery:
            generation_result, verification, status = self._recover_after_verification(
                request,
                matches,
                generation_result,
                verification,
                status,
            )
        generation_result = self._canonicalize_verified_answer(
            generation_result,
            status,
        )
        feedback_method_keys = (
            generation_result.candidate.used_method_keys
            if generation_result.candidate is not None
            and generation_result.trace.method_feedback_eligible
            else []
        )
        return SolutionAttempt(
            id=str(uuid.uuid4()),
            workspace_id=workspace_id,
            problem=request.problem,
            tags=request.tags,
            problem_kind=classify_problem(request.problem),
            math_target=request.math_target,
            recommended_methods=matches,
            candidate=generation_result.candidate,
            generation=generation_result.trace,
            verification=verification,
            status=status,
            feedback_method_keys=feedback_method_keys,
            correction_of=correction_of,
            created_at=utc_now(),
        )

    def _generate_candidate(
        self,
        request: SolveRequest,
        matches: list[MethodMatch],
    ) -> SolutionGenerationResult:
        try:
            return self.generator.generate(
                request.problem,
                matches,
                request.math_target,
                request.max_output_tokens,
            )
        except Exception as exc:  # noqa: BLE001
            return SolutionGenerationResult(
                candidate=None,
                trace=SolutionGenerationTrace(
                    provider=getattr(
                        self.generator,
                        "name",
                        self.generator.__class__.__name__,
                    ),
                    model=getattr(self.generator, "model", None),
                    prompt_version=getattr(
                        self.generator,
                        "prompt_version",
                        "unknown",
                    ),
                    status=GenerationStatus.ERROR,
                    error=f"{exc.__class__.__name__}: {exc}"[:2_000],
                ),
            )

    def _prepare_generation_result(
        self,
        result: SolutionGenerationResult,
        matches: list[MethodMatch],
        math_target: SolveMathTarget | None,
    ) -> SolutionGenerationResult:
        result = self._filter_unretrieved_method_keys(result, matches)
        if result.candidate is None:
            return result

        normalized = self.normalizer.normalize(result.candidate, math_target)
        if not normalized.actions and normalized.candidate == result.candidate:
            return result
        actions = self._unique_strings(
            [*result.trace.normalization_actions, *normalized.actions]
        )
        return result.model_copy(
            update={
                "candidate": normalized.candidate,
                "trace": result.trace.model_copy(
                    update={"normalization_actions": actions}
                ),
            }
        )

    @staticmethod
    def _stage_from_result(
        stage: GenerationStageKind,
        result: SolutionGenerationResult,
        verification: VerificationReport,
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
            verification=verification,
            raw_output=trace.raw_output,
            error=trace.error,
            duration_ms=trace.duration_ms,
        )

    def _record_initial_verification(
        self,
        result: SolutionGenerationResult,
        stage: GenerationStageKind,
        verification: VerificationReport,
    ) -> SolutionGenerationResult:
        stages = list(result.trace.stages)
        if stages:
            stages[-1] = stages[-1].model_copy(update={"verification": verification})
        else:
            stages.append(self._stage_from_result(stage, result, verification))
        return result.model_copy(
            update={
                "trace": result.trace.model_copy(
                    update={"stages": stages},
                )
            }
        )

    @staticmethod
    def _canonicalize_verified_answer(
        result: SolutionGenerationResult,
        status: SolutionAttemptStatus,
    ) -> SolutionGenerationResult:
        candidate = result.candidate
        if (
            status is not SolutionAttemptStatus.VERIFIED
            or candidate is None
            or candidate.answer_expression is None
        ):
            return result
        canonical_text = f"已验证答案：{candidate.answer_expression}"
        if candidate.answer_text == canonical_text:
            return result
        notes = MathHarnessService._unique_strings(
            [*result.trace.recovery_notes, "canonicalized_verified_answer_text"]
        )
        return result.model_copy(
            update={
                "candidate": candidate.model_copy(
                    update={"answer_text": canonical_text},
                ),
                "trace": result.trace.model_copy(
                    update={"recovery_notes": notes},
                ),
            }
        )

    def _recover_after_verification(
        self,
        request: SolveRequest,
        matches: list[MethodMatch],
        initial_result: SolutionGenerationResult,
        initial_verification: VerificationReport,
        initial_status: SolutionAttemptStatus,
    ) -> tuple[
        SolutionGenerationResult,
        VerificationReport,
        SolutionAttemptStatus,
    ]:
        if (
            request.math_target is None
            or initial_status is SolutionAttemptStatus.VERIFIED
            or initial_result.candidate is None
            or initial_result.candidate.answer_kind is not AnswerKind.EXPRESSION
            or initial_result.trace.fallback_used
        ):
            return initial_result, initial_verification, initial_status

        total_duration = initial_result.trace.duration_ms
        stages = list(initial_result.trace.stages)
        recovery_notes = [
            self._verification_note("initial_verification", initial_verification)
        ]
        normalization_actions = list(initial_result.trace.normalization_actions)
        final_result = initial_result
        final_verification = initial_verification
        final_status = initial_status
        correction_attempted = False
        correction_error: str | None = None

        repair = getattr(self.generator, "repair_after_verification", None)
        if callable(repair):
            corrected = repair(
                request.problem,
                matches,
                request.math_target,
                initial_result.candidate,
                initial_verification,
                request.max_output_tokens,
            )
            if corrected is not None:
                correction_attempted = True
                total_duration += corrected.trace.duration_ms
                corrected = self._prepare_generation_result(
                    corrected,
                    matches,
                    request.math_target,
                )
                normalization_actions.extend(corrected.trace.normalization_actions)
                recovery_notes.extend(corrected.trace.recovery_notes)
                correction_error = corrected.trace.error
                if corrected.candidate is not None:
                    corrected_verification, corrected_status = self._verify_candidate(
                        request,
                        corrected,
                    )
                    stages.append(
                        self._stage_from_result(
                            GenerationStageKind.CORRECTION,
                            corrected,
                            corrected_verification,
                        )
                    )
                    recovery_notes.append(
                        self._verification_note(
                            "correction_verification",
                            corrected_verification,
                        )
                    )
                    final_result = corrected
                    final_verification = corrected_verification
                    final_status = corrected_status
                    if corrected.candidate.answer_kind is not AnswerKind.EXPRESSION:
                        recovery_notes.append(
                            "model_correction_returned_non_expression_claim"
                        )
                        final_result = corrected.model_copy(
                            update={
                                "trace": corrected.trace.model_copy(
                                    update={
                                        "correction_attempted": True,
                                        "correction_succeeded": False,
                                        "normalization_actions": self._unique_strings(
                                            normalization_actions
                                        ),
                                        "recovery_notes": self._unique_strings(
                                            recovery_notes
                                        ),
                                        "stages": stages,
                                        "duration_ms": total_duration,
                                    }
                                )
                            }
                        )
                        return (
                            final_result,
                            final_verification,
                            final_status,
                        )
                    if corrected_status is SolutionAttemptStatus.VERIFIED:
                        final_result = corrected.model_copy(
                            update={
                                "trace": corrected.trace.model_copy(
                                    update={
                                        "correction_attempted": True,
                                        "correction_succeeded": True,
                                        "normalization_actions": self._unique_strings(
                                            normalization_actions
                                        ),
                                        "recovery_notes": self._unique_strings(
                                            recovery_notes
                                        ),
                                        "stages": stages,
                                        "duration_ms": total_duration,
                                    }
                                )
                            }
                        )
                        return (
                            final_result,
                            final_verification,
                            final_status,
                        )
                else:
                    corrected_verification, _ = self._verify_candidate(
                        request,
                        corrected,
                    )
                    stages.append(
                        self._stage_from_result(
                            GenerationStageKind.CORRECTION,
                            corrected,
                            corrected_verification,
                        )
                    )
                    recovery_notes.append("model_correction_returned_no_candidate")

        fallback = getattr(self.generator, "fallback_after_verification", None)
        if callable(fallback):
            fallback_result = fallback(
                request.problem,
                matches,
                request.math_target,
                request.max_output_tokens,
                final_verification,
                correction_attempted=correction_attempted,
                correction_error=correction_error,
            )
            if fallback_result is not None:
                total_duration += fallback_result.trace.duration_ms
                fallback_result = self._prepare_generation_result(
                    fallback_result,
                    matches,
                    request.math_target,
                )
                normalization_actions.extend(
                    fallback_result.trace.normalization_actions
                )
                recovery_notes.extend(fallback_result.trace.recovery_notes)
                if fallback_result.candidate is not None:
                    fallback_verification, fallback_status = self._verify_candidate(
                        request,
                        fallback_result,
                    )
                    stages.append(
                        self._stage_from_result(
                            GenerationStageKind.FALLBACK,
                            fallback_result,
                            fallback_verification,
                        )
                    )
                    recovery_notes.append(
                        self._verification_note(
                            "fallback_verification",
                            fallback_verification,
                        )
                    )
                    final_result = fallback_result
                    final_verification = fallback_verification
                    final_status = fallback_status
                elif fallback_result.trace.error:
                    fallback_verification, _ = self._verify_candidate(
                        request,
                        fallback_result,
                    )
                    stages.append(
                        self._stage_from_result(
                            GenerationStageKind.FALLBACK,
                            fallback_result,
                            fallback_verification,
                        )
                    )
                    recovery_notes.append(
                        "verification_fallback_failed: "
                        + fallback_result.trace.error[:500]
                    )

        final_result = final_result.model_copy(
            update={
                "trace": final_result.trace.model_copy(
                    update={
                        "correction_attempted": correction_attempted,
                        "correction_succeeded": False,
                        "normalization_actions": self._unique_strings(
                            normalization_actions
                        ),
                        "recovery_notes": self._unique_strings(recovery_notes),
                        "stages": stages,
                        "duration_ms": total_duration,
                    }
                )
            }
        )
        return final_result, final_verification, final_status

    @staticmethod
    def _verification_note(
        stage: str,
        verification: VerificationReport,
    ) -> str:
        return (f"{stage}={verification.status.value}: {verification.summary}")[:1_000]

    @staticmethod
    def _unique_strings(values: list[str]) -> list[str]:
        return list(dict.fromkeys(value for value in values if value))

    @staticmethod
    def _filter_unretrieved_method_keys(
        result: SolutionGenerationResult,
        matches: list[MethodMatch],
    ) -> SolutionGenerationResult:
        if result.candidate is None:
            return result
        allowed_keys = {match.method.key for match in matches}
        filtered = [
            key for key in result.candidate.used_method_keys if key in allowed_keys
        ]
        if filtered == result.candidate.used_method_keys:
            return result
        return result.model_copy(
            update={
                "candidate": result.candidate.model_copy(
                    update={"used_method_keys": filtered}
                )
            }
        )

    def _verify_candidate(
        self,
        request: SolveRequest,
        generation_result: SolutionGenerationResult,
    ) -> tuple[VerificationReport, SolutionAttemptStatus]:
        candidate = generation_result.candidate
        if candidate is None:
            return (
                VerificationReport(
                    status=VerificationStatus.NEEDS_REVIEW,
                    summary="候选解生成失败，未进入自动验收。",
                    checks=["candidate_generation_failed"],
                    error=generation_result.trace.error,
                ),
                SolutionAttemptStatus.GENERATION_FAILED,
            )
        if candidate.answer_kind is not AnswerKind.EXPRESSION:
            verification = VerificationReport(
                status=VerificationStatus.NEEDS_REVIEW,
                summary=(
                    "候选解声明的是不存在、无极限或条件性结论；"
                    "当前版本保留该结论并交由复核，不自动改写。"
                ),
                checks=[
                    "candidate_generated",
                    f"answer_kind:{candidate.answer_kind.value}",
                    "non_expression_claim_requires_review",
                ],
            )
        elif request.math_target is None:
            verification = VerificationReport(
                status=VerificationStatus.NEEDS_REVIEW,
                summary="候选解已保存；缺少 math_target，无法自动验收。",
                checks=["candidate_generated", "missing_math_target"],
            )
        elif candidate.answer_expression is None:
            verification = VerificationReport(
                status=VerificationStatus.NEEDS_REVIEW,
                summary="候选解已保存；未提供机器可检查的答案表达式。",
                checks=["candidate_generated", "missing_answer_expression"],
            )
        else:
            verification = self.verifier.verify(
                MathPayload(
                    **request.math_target.model_dump(),
                    expected=candidate.answer_expression,
                )
            )
            if verification.status is VerificationStatus.VERIFIED:
                verification = self._verify_final_step_consistency(
                    request.math_target,
                    candidate,
                    verification,
                )

        status_map = {
            VerificationStatus.VERIFIED: SolutionAttemptStatus.VERIFIED,
            VerificationStatus.NEEDS_REVIEW: SolutionAttemptStatus.NEEDS_REVIEW,
            VerificationStatus.REJECTED: SolutionAttemptStatus.REJECTED,
        }
        return verification, status_map[verification.status]

    def _verify_final_step_consistency(
        self,
        math_target: SolveMathTarget,
        candidate: CandidateSolution,
        mathematical_verification: VerificationReport,
    ) -> VerificationReport:
        final_step_expression = next(
            (
                step.expression
                for step in reversed(candidate.steps)
                if step.expression is not None
            ),
            None,
        )
        if final_step_expression is None or candidate.answer_expression is None:
            return VerificationReport(
                status=VerificationStatus.NEEDS_REVIEW,
                summary=(
                    "答案表达式通过数学验证，但最终步骤没有可检查表达式，需要复核。"
                ),
                checks=[
                    *mathematical_verification.checks,
                    "candidate_final_step_missing",
                ],
                computed=mathematical_verification.computed,
            )

        step_candidate = candidate.model_copy(
            update={
                "answer_kind": AnswerKind.EXPRESSION,
                "answer_expression": final_step_expression,
            }
        )
        normalized_step = self.normalizer.normalize(
            step_candidate,
            math_target,
        ).candidate.answer_expression
        if normalized_step is None:
            return VerificationReport(
                status=VerificationStatus.NEEDS_REVIEW,
                summary="最终步骤表达式无法规范化，需要复核。",
                checks=[
                    *mathematical_verification.checks,
                    "candidate_final_step_missing",
                ],
                computed=mathematical_verification.computed,
            )

        payload = math_target.model_dump()
        payload.update(
            {
                "expression": candidate.answer_expression,
                "expected": normalized_step,
                "mode": VerificationMode.EXACT_EQUIVALENCE,
                "remainder_power": None,
            }
        )
        consistency = self.verifier.verify(MathPayload(**payload))
        if consistency.status is VerificationStatus.VERIFIED:
            return mathematical_verification.model_copy(
                update={
                    "summary": (
                        mathematical_verification.summary + " 候选答案与最终步骤一致。"
                    ),
                    "checks": self._unique_strings(
                        [
                            *mathematical_verification.checks,
                            "candidate_final_step_consistency",
                        ]
                    ),
                }
            )

        return VerificationReport(
            status=consistency.status,
            summary=(
                "数学答案表达式本身可通过验证，但与候选解的最终步骤不一致。"
                if consistency.status is VerificationStatus.REJECTED
                else "数学答案表达式本身可通过验证，但最终步骤一致性无法判定。"
            ),
            checks=self._unique_strings(
                [
                    *mathematical_verification.checks,
                    "candidate_final_step_consistency",
                    *consistency.checks,
                ]
            ),
            computed={
                **mathematical_verification.computed,
                **{
                    f"final_step_{key}": value
                    for key, value in consistency.computed.items()
                },
            },
            error=consistency.error,
        )

    def get_solution_attempt(
        self,
        workspace_id: str,
        attempt_id: str,
    ) -> SolutionAttempt:
        return self.workspaces.store(workspace_id).get_solution_attempt(attempt_id)

    def list_solution_attempts(
        self,
        workspace_id: str,
    ) -> list[SolutionAttempt]:
        return self.workspaces.store(workspace_id).list_solution_attempts()

    def correct_solution_attempt(
        self,
        workspace_id: str,
        attempt_id: str,
        request: SolutionCorrection,
    ) -> SolutionAttempt:
        original = self.get_solution_attempt(workspace_id, attempt_id)
        candidate = CandidateSolution(
            answer_kind=request.answer_kind,
            answer_text=request.answer_text,
            answer_expression=request.answer_expression,
            steps=request.steps
            or [
                CandidateStep(
                    explanation="人工复核后提交纠正答案。",
                    expression=request.answer_expression,
                )
            ],
            used_method_keys=(
                request.used_method_keys if request.used_method_keys is not None else []
            ),
            assumptions=request.assumptions,
            confidence=1,
        )
        generation_result = SolutionGenerationResult(
            candidate=candidate,
            trace=SolutionGenerationTrace(
                provider="human",
                prompt_version="human-correction-v1",
                status=GenerationStatus.SUCCESS,
                method_feedback_eligible=bool(request.used_method_keys),
                raw_output=json.dumps(
                    {
                        "candidate": candidate.model_dump(mode="json"),
                        "reviewer_note": request.reviewer_note,
                    },
                    ensure_ascii=False,
                )[:8_000],
            ),
        )
        solve_request = SolveRequest(
            problem=original.problem,
            tags=original.tags,
            top_k=max(1, len(original.recommended_methods)),
            math_target=original.math_target,
        )
        corrected = self._build_solution_attempt(
            workspace_id,
            solve_request,
            correction_of=original.id,
            generation_result=generation_result,
        )
        return self._persist_attempt_and_capture(workspace_id, corrected)

    def evaluate_workspace(
        self, workspace_id: str, request: EvaluationRequest
    ) -> EvaluationRun:
        case_results: list[EvaluationCaseResult] = []
        zero_results = 0
        for case in request.cases:
            matches = self.search_methods(
                workspace_id,
                case.problem,
                tags=case.tags,
                top_k=request.top_k,
                math_target=case.math_target,
            )
            returned = [match.method.key for match in matches]
            expected = set(case.expected_method_keys)
            if not returned:
                zero_results += 1
            first_relevant_rank = next(
                (
                    index
                    for index, method_key in enumerate(returned, start=1)
                    if method_key in expected
                ),
                None,
            )
            case_results.append(
                EvaluationCaseResult(
                    case_id=case.id,
                    expected_method_keys=case.expected_method_keys,
                    returned_method_keys=returned,
                    hit_at_1=bool(returned and returned[0] in expected),
                    recall_at_k=len(expected & set(returned)) / len(expected),
                    reciprocal_rank=(
                        1 / first_relevant_rank if first_relevant_rank else 0
                    ),
                )
            )

        count = len(case_results)
        run = EvaluationRun(
            id=str(uuid.uuid4()),
            workspace_id=workspace_id,
            name=request.name,
            top_k=request.top_k,
            metrics=EvaluationMetrics(
                case_count=count,
                hit_at_1=round(
                    sum(result.hit_at_1 for result in case_results) / count, 6
                ),
                recall_at_k=round(
                    sum(result.recall_at_k for result in case_results) / count, 6
                ),
                mean_reciprocal_rank=round(
                    sum(result.reciprocal_rank for result in case_results) / count,
                    6,
                ),
                zero_result_rate=round(zero_results / count, 6),
            ),
            cases=case_results,
            created_at=utc_now(),
        )
        return self.workspaces.store(workspace_id).add_evaluation(run)

    def list_evaluations(self, workspace_id: str) -> list[EvaluationRun]:
        return self.workspaces.store(workspace_id).list_evaluations()

    def evaluate_solver(
        self,
        workspace_id: str,
        request: SolveEvaluationRequest,
    ) -> SolveEvaluationRun:
        case_results: list[SolveEvaluationCaseResult] = []
        for case in request.cases:
            attempt = self._build_solution_attempt(
                workspace_id,
                SolveRequest(
                    problem=case.problem,
                    tags=case.tags,
                    top_k=request.top_k,
                    math_target=case.math_target,
                    max_output_tokens=request.max_output_tokens,
                ),
            )
            case_results.append(
                SolveEvaluationCaseResult(
                    case_id=case.id,
                    status=attempt.status,
                    verification_status=attempt.verification.status,
                    retrieved_method_keys=[
                        match.method.key for match in attempt.recommended_methods
                    ],
                    used_method_keys=(
                        attempt.candidate.used_method_keys if attempt.candidate else []
                    ),
                    feedback_method_keys=attempt.feedback_method_keys,
                    generation_provider=attempt.generation.provider,
                    fallback_used=attempt.generation.fallback_used,
                    correction_attempted=attempt.generation.correction_attempted,
                    correction_succeeded=attempt.generation.correction_succeeded,
                )
            )

        count = len(case_results)
        run = SolveEvaluationRun(
            id=str(uuid.uuid4()),
            workspace_id=workspace_id,
            name=request.name,
            top_k=request.top_k,
            metrics=SolveEvaluationMetrics(
                case_count=count,
                verified_rate=self._status_rate(
                    case_results,
                    SolutionAttemptStatus.VERIFIED,
                ),
                needs_review_rate=self._status_rate(
                    case_results,
                    SolutionAttemptStatus.NEEDS_REVIEW,
                ),
                rejected_rate=self._status_rate(
                    case_results,
                    SolutionAttemptStatus.REJECTED,
                ),
                generation_failure_rate=self._status_rate(
                    case_results,
                    SolutionAttemptStatus.GENERATION_FAILED,
                ),
                fallback_rate=round(
                    sum(case.fallback_used for case in case_results) / count,
                    6,
                ),
                correction_attempt_rate=round(
                    sum(case.correction_attempted for case in case_results) / count,
                    6,
                ),
                correction_success_rate=round(
                    sum(case.correction_succeeded for case in case_results) / count,
                    6,
                ),
            ),
            cases=case_results,
            created_at=utc_now(),
        )
        return self.workspaces.store(workspace_id).add_solve_evaluation(run)

    @staticmethod
    def _status_rate(
        cases: list[SolveEvaluationCaseResult],
        status: SolutionAttemptStatus,
    ) -> float:
        return round(sum(case.status is status for case in cases) / len(cases), 6)

    def list_solve_evaluations(
        self,
        workspace_id: str,
    ) -> list[SolveEvaluationRun]:
        return self.workspaces.store(workspace_id).list_solve_evaluations()
