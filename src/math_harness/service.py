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
    CandidateSolution,
    CandidateStep,
    EvaluationCaseResult,
    EvaluationMetrics,
    EvaluationRequest,
    EvaluationRun,
    ExampleCreate,
    ExtractionStatus,
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
    ProblemExample,
    SolutionAttempt,
    SolutionAttemptStatus,
    SolutionCorrection,
    SolutionGenerationResult,
    SolutionGenerationTrace,
    SolveEvaluationCaseResult,
    SolveEvaluationMetrics,
    SolveEvaluationRequest,
    SolveEvaluationRun,
    SolvePlan,
    SolveRequest,
    VerificationReport,
    VerificationStatus,
    Workspace,
    WorkspaceCreate,
    utc_now,
)
from math_harness.retrieval import MethodRetriever
from math_harness.solving import (
    SolutionGeneratorProtocol,
    build_solution_generator_from_env,
)
from math_harness.storage import WorkspaceManager
from math_harness.verifier import SolutionVerifier


class MathHarnessService:
    def __init__(
        self,
        data_root: Path | str,
        verifier: SolutionVerifier | None = None,
        extractor: MethodExtractorProtocol | None = None,
        retriever: MethodRetriever | None = None,
        generator: SolutionGeneratorProtocol | None = None,
    ) -> None:
        load_local_environment()
        self.workspaces = WorkspaceManager(data_root)
        self.verifier = verifier or SolutionVerifier()
        self.extractor = extractor or build_method_extractor_from_env()
        self.retriever = retriever or MethodRetriever()
        self.generator = generator or build_solution_generator_from_env()

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
        if verification.status is VerificationStatus.VERIFIED:
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
                if verification.status is VerificationStatus.VERIFIED
                else KnowledgeStatus.PENDING_REVIEW
            )
            for draft in extraction_result.methods:
                learned_methods.append(
                    store.upsert_method(
                        draft=draft,
                        example_id=example.id,
                        status=method_status,
                        verified=verification.status is VerificationStatus.VERIFIED,
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
    ) -> list[MethodMatch]:
        methods = self.workspaces.store(workspace_id).list_methods(
            include_pending=False
        )
        return self.retriever.search(methods, query, tags=tags, top_k=top_k)

    def build_solve_plan(
        self,
        workspace_id: str,
        problem: str,
        tags: list[str] | None = None,
        top_k: int = 5,
    ) -> SolvePlan:
        matches = self.search_methods(workspace_id, problem, tags=tags, top_k=top_k)
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
        matches = self.search_methods(
            workspace_id,
            request.problem,
            tags=request.tags,
            top_k=request.top_k,
        )
        if generation_result is None:
            generation_result = self._generate_candidate(request, matches)
        generation_result = self._filter_unretrieved_method_keys(
            generation_result, matches
        )
        verification, status = self._verify_candidate(request, generation_result)
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
        if request.math_target is None:
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

        status_map = {
            VerificationStatus.VERIFIED: SolutionAttemptStatus.VERIFIED,
            VerificationStatus.NEEDS_REVIEW: SolutionAttemptStatus.NEEDS_REVIEW,
            VerificationStatus.REJECTED: SolutionAttemptStatus.REJECTED,
        }
        return verification, status_map[verification.status]

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
        inherited_keys = (
            original.candidate.used_method_keys if original.candidate else []
        )
        candidate = CandidateSolution(
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
                request.used_method_keys
                if request.used_method_keys is not None
                else inherited_keys
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
                    generation_provider=attempt.generation.provider,
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
