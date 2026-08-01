from __future__ import annotations

import json
import uuid
from pathlib import Path

from math_harness.classifier import classify_problem
from math_harness.config import load_local_environment
from math_harness.extraction import (
    MethodExtractorProtocol,
    build_method_extractor_from_env,
)
from math_harness.models import (
    AnswerKind,
    CandidateSolution,
    CandidateStep,
    EvaluationCaseResult,
    EvaluationMetrics,
    EvaluationRequest,
    EvaluationRun,
    ExampleCreate,
    ExtractionStatus,
    GenerationStageKind,
    GenerationStatus,
    IngestionResult,
    KnowledgeStatus,
    LearningEvent,
    MathPayload,
    MethodCard,
    MethodExtractionResult,
    MethodExtractionTrace,
    MethodMatch,
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
    utc_now,
)
from math_harness.normalization import CandidateSolutionNormalizer
from math_harness.retrieval import MethodRetriever
from math_harness.solving import (
    SolutionGeneratorProtocol,
    build_solution_generator_from_env,
)
from math_harness.storage import WorkspaceManager
from math_harness.structure import extract_features
from math_harness.verifier import SolutionVerifier


class MathHarnessService:
    def __init__(
        self,
        data_root: Path | str,
        verifier: SolutionVerifier | None = None,
        extractor: MethodExtractorProtocol | None = None,
        retriever: MethodRetriever | None = None,
        generator: SolutionGeneratorProtocol | None = None,
        normalizer: CandidateSolutionNormalizer | None = None,
    ) -> None:
        load_local_environment()
        self.workspaces = WorkspaceManager(data_root)
        self.verifier = verifier or SolutionVerifier()
        self.extractor = extractor or build_method_extractor_from_env()
        self.retriever = retriever or MethodRetriever()
        self.generator = generator or build_solution_generator_from_env()
        self.normalizer = normalizer or CandidateSolutionNormalizer()

    def create_workspace(self, request: WorkspaceCreate) -> Workspace:
        return self.workspaces.create(request)

    def get_workspace(self, workspace_id: str) -> Workspace:
        return self.workspaces.get(workspace_id)

    def list_workspaces(self) -> list[Workspace]:
        return self.workspaces.list()

    def ingest_example(
        self, workspace_id: str, request: ExampleCreate
    ) -> IngestionResult:
        store = self.workspaces.store(workspace_id)
        verification = self.verifier.verify(request.math_payload)
        promotion_approved = (
            verification.status is VerificationStatus.VERIFIED and request.reviewed
        )
        if promotion_approved:
            status = KnowledgeStatus.PROMOTED
        elif verification.status is VerificationStatus.REJECTED:
            status = KnowledgeStatus.REJECTED
        else:
            status = KnowledgeStatus.PENDING_REVIEW

        extraction_result = self._extract_methods(request, verification.status)
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
            status=status,
            created_at=utc_now(),
        )
        store.add_example(example)

        learned_methods: list[MethodCard] = []
        if verification.status is not VerificationStatus.REJECTED:
            method_status = (
                KnowledgeStatus.PROMOTED
                if promotion_approved
                else KnowledgeStatus.PENDING_REVIEW
            )
            # 只有验证通过的例子才把结构计入签名：未经验证的结构不该影响以后的检索。
            features = (
                extract_features(request.math_payload)
                if verification.status is VerificationStatus.VERIFIED
                else None
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
    ) -> MethodExtractionResult:
        provider = getattr(self.extractor, "name", self.extractor.__class__.__name__)
        prompt_version = getattr(self.extractor, "prompt_version", "unknown")
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
            return self.extractor.extract(
                request.problem, request.solution, request.method_hint
            )
        except Exception as exc:  # noqa: BLE001
            return MethodExtractionResult(
                trace=MethodExtractionTrace(
                    provider=provider,
                    model=getattr(self.extractor, "model", None),
                    prompt_version=prompt_version,
                    status=ExtractionStatus.ERROR,
                    error=f"{exc.__class__.__name__}: {exc}"[:2_000],
                    extracted_method_keys=[],
                )
            )

    def get_example(self, workspace_id: str, example_id: str) -> ProblemExample:
        return self.workspaces.store(workspace_id).get_example(example_id)

    def list_examples(self, workspace_id: str) -> list[ProblemExample]:
        return self.workspaces.store(workspace_id).list_examples()

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

    def update_method_status(
        self,
        workspace_id: str,
        method_id: str,
        request: MethodStatusUpdate,
    ) -> MethodCard:
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
        return self.workspaces.store(workspace_id).add_solution_attempt(attempt)

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
        return self.workspaces.store(workspace_id).add_solution_attempt(corrected)

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
