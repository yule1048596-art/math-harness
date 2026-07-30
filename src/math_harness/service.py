from __future__ import annotations

import uuid
from pathlib import Path

from math_harness.classifier import classify_problem
from math_harness.extraction import (
    MethodExtractorProtocol,
    build_method_extractor_from_env,
)
from math_harness.models import (
    EvaluationCaseResult,
    EvaluationMetrics,
    EvaluationRequest,
    EvaluationRun,
    ExampleCreate,
    ExtractionStatus,
    IngestionResult,
    KnowledgeStatus,
    LearningEvent,
    MethodCard,
    MethodExtractionResult,
    MethodExtractionTrace,
    MethodMatch,
    MethodStatusUpdate,
    ProblemExample,
    SolvePlan,
    VerificationStatus,
    Workspace,
    WorkspaceCreate,
    utc_now,
)
from math_harness.retrieval import MethodRetriever
from math_harness.storage import WorkspaceManager
from math_harness.verifier import SolutionVerifier


class MathHarnessService:
    def __init__(
        self,
        data_root: Path | str,
        verifier: SolutionVerifier | None = None,
        extractor: MethodExtractorProtocol | None = None,
        retriever: MethodRetriever | None = None,
    ) -> None:
        self.workspaces = WorkspaceManager(data_root)
        self.verifier = verifier or SolutionVerifier()
        self.extractor = extractor or build_method_extractor_from_env()
        self.retriever = retriever or MethodRetriever()

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
                "当前版本先返回可追溯的方法计划；下一阶段接入模型生成候选解，"
                "再由数学验证器决定是否接受。"
            ),
        )

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
