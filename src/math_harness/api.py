from __future__ import annotations

import os
import secrets
from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse

from math_harness import __version__
from math_harness.errors import (
    InvalidKnowledgeState,
    RecordNotFound,
    WorkspaceNotFound,
)
from math_harness.models import (
    ConversationCaptureResult,
    EvaluationRequest,
    EvaluationRun,
    ExampleCreate,
    ExampleReviewRequest,
    ExampleReviewResult,
    HealthResponse,
    IngestionResult,
    LearningEvent,
    MergeProposalStatus,
    MergeScanRequest,
    MethodCard,
    MethodMatch,
    MethodMergeProposal,
    MethodSearchRequest,
    MethodStatusUpdate,
    MethodVersion,
    ProblemExample,
    SolutionAttempt,
    SolutionCorrection,
    SolveEvaluationRequest,
    SolveEvaluationRun,
    SolvePlan,
    SolveRequest,
    Workspace,
    WorkspaceCreate,
)
from math_harness.service import MathHarnessService


def create_app(
    data_root: Path | str | None = None,
    *,
    local_token: str | None = None,
) -> FastAPI:
    root = data_root or os.getenv("MATH_HARNESS_DATA_DIR", ".math_harness")
    token = local_token or os.getenv("MATH_HARNESS_LOCAL_TOKEN")
    service = MathHarnessService(root)
    app = FastAPI(
        title="Math Harness",
        version=__version__,
        description="工作区隔离、可验证、可成长的数学 AI harness 原型。",
    )
    app.state.service = service

    if token:

        @app.middleware("http")
        async def require_local_token(request: Request, call_next):
            supplied = request.headers.get("authorization", "")
            expected = f"Bearer {token}"
            if not secrets.compare_digest(supplied, expected):
                return JSONResponse(
                    status_code=401,
                    content={"detail": "missing or invalid local app token"},
                )
            return await call_next(request)

    @app.exception_handler(WorkspaceNotFound)
    async def workspace_not_found_handler(_, exc: WorkspaceNotFound):
        return _not_found(exc)

    @app.exception_handler(RecordNotFound)
    async def record_not_found_handler(_, exc: RecordNotFound):
        return _not_found(exc)

    @app.exception_handler(InvalidKnowledgeState)
    async def invalid_knowledge_state_handler(_, exc: InvalidKnowledgeState):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(status="ok", version=__version__)

    @app.post("/workspaces", response_model=Workspace, status_code=201)
    def create_workspace(request: WorkspaceCreate) -> Workspace:
        return service.create_workspace(request)

    @app.get("/workspaces", response_model=list[Workspace])
    def list_workspaces() -> list[Workspace]:
        return service.list_workspaces()

    @app.get("/workspaces/{workspace_id}", response_model=Workspace)
    def get_workspace(workspace_id: str) -> Workspace:
        return service.get_workspace(workspace_id)

    @app.post(
        "/workspaces/{workspace_id}/examples",
        response_model=IngestionResult,
        status_code=201,
    )
    def ingest_example(workspace_id: str, request: ExampleCreate) -> IngestionResult:
        return service.ingest_example(workspace_id, request)

    @app.get(
        "/workspaces/{workspace_id}/examples",
        response_model=list[ProblemExample],
    )
    def list_examples(workspace_id: str) -> list[ProblemExample]:
        return service.list_examples(workspace_id)

    @app.get(
        "/workspaces/{workspace_id}/examples/{example_id}",
        response_model=ProblemExample,
    )
    def get_example(workspace_id: str, example_id: str) -> ProblemExample:
        return service.get_example(workspace_id, example_id)

    @app.post(
        "/workspaces/{workspace_id}/examples/{example_id}/review",
        response_model=ExampleReviewResult,
    )
    def review_example(
        workspace_id: str,
        example_id: str,
        request: ExampleReviewRequest,
    ) -> ExampleReviewResult:
        return service.review_example(workspace_id, example_id, request)

    @app.get(
        "/workspaces/{workspace_id}/methods",
        response_model=list[MethodCard],
    )
    def list_methods(
        workspace_id: str,
        include_pending: bool = Query(default=True),
        include_deprecated: bool = Query(default=False),
    ) -> list[MethodCard]:
        return service.list_methods(
            workspace_id,
            include_pending=include_pending,
            include_deprecated=include_deprecated,
        )

    @app.get(
        "/workspaces/{workspace_id}/methods/{method_id}/versions",
        response_model=list[MethodVersion],
    )
    def list_method_versions(workspace_id: str, method_id: str) -> list[MethodVersion]:
        return service.list_method_versions(workspace_id, method_id)

    @app.post(
        "/workspaces/{workspace_id}/methods/merge-proposals",
        response_model=list[MethodMergeProposal],
        status_code=201,
    )
    def scan_merge_proposals(
        workspace_id: str, request: MergeScanRequest | None = None
    ) -> list[MethodMergeProposal]:
        return service.scan_merge_proposals(
            workspace_id, threshold=request.threshold if request else None
        )

    @app.get(
        "/workspaces/{workspace_id}/methods/merge-proposals",
        response_model=list[MethodMergeProposal],
    )
    def list_merge_proposals(
        workspace_id: str,
        status: MergeProposalStatus | None = None,
    ) -> list[MethodMergeProposal]:
        return service.list_merge_proposals(workspace_id, status)

    @app.post(
        "/workspaces/{workspace_id}/methods/merge-proposals/{proposal_id}/apply",
        response_model=MethodCard,
    )
    def apply_merge_proposal(workspace_id: str, proposal_id: str) -> MethodCard:
        return service.apply_merge_proposal(workspace_id, proposal_id)

    @app.post(
        "/workspaces/{workspace_id}/methods/merge-proposals/{proposal_id}/reject",
        response_model=MethodMergeProposal,
    )
    def reject_merge_proposal(
        workspace_id: str, proposal_id: str
    ) -> MethodMergeProposal:
        return service.reject_merge_proposal(workspace_id, proposal_id)

    @app.patch(
        "/workspaces/{workspace_id}/methods/{method_id}",
        response_model=MethodCard,
    )
    def update_method_status(
        workspace_id: str,
        method_id: str,
        request: MethodStatusUpdate,
    ) -> MethodCard:
        return service.update_method_status(workspace_id, method_id, request)

    @app.post(
        "/workspaces/{workspace_id}/methods/search",
        response_model=list[MethodMatch],
    )
    def search_methods(
        workspace_id: str, request: MethodSearchRequest
    ) -> list[MethodMatch]:
        return service.search_methods(
            workspace_id,
            request.query,
            tags=request.tags,
            top_k=request.top_k,
            math_target=request.math_target,
        )

    @app.post(
        "/workspaces/{workspace_id}/solve-plan",
        response_model=SolvePlan,
    )
    def solve_plan(workspace_id: str, request: MethodSearchRequest) -> SolvePlan:
        return service.build_solve_plan(
            workspace_id,
            request.query,
            tags=request.tags,
            top_k=request.top_k,
            math_target=request.math_target,
        )

    @app.post(
        "/workspaces/{workspace_id}/solve",
        response_model=SolutionAttempt,
        status_code=201,
    )
    def solve_problem(
        workspace_id: str,
        request: SolveRequest,
    ) -> SolutionAttempt:
        return service.solve_problem(workspace_id, request)

    @app.get(
        "/workspaces/{workspace_id}/attempts",
        response_model=list[SolutionAttempt],
    )
    def list_solution_attempts(workspace_id: str) -> list[SolutionAttempt]:
        return service.list_solution_attempts(workspace_id)

    @app.get(
        "/workspaces/{workspace_id}/attempts/{attempt_id}",
        response_model=SolutionAttempt,
    )
    def get_solution_attempt(
        workspace_id: str,
        attempt_id: str,
    ) -> SolutionAttempt:
        return service.get_solution_attempt(workspace_id, attempt_id)

    @app.post(
        "/workspaces/{workspace_id}/attempts/{attempt_id}/capture",
        response_model=ConversationCaptureResult,
    )
    def capture_solution_attempt(
        workspace_id: str,
        attempt_id: str,
    ) -> ConversationCaptureResult:
        return service.capture_solution_attempt(workspace_id, attempt_id)

    @app.post(
        "/workspaces/{workspace_id}/attempts/{attempt_id}/corrections",
        response_model=SolutionAttempt,
        status_code=201,
    )
    def correct_solution_attempt(
        workspace_id: str,
        attempt_id: str,
        request: SolutionCorrection,
    ) -> SolutionAttempt:
        return service.correct_solution_attempt(
            workspace_id,
            attempt_id,
            request,
        )

    @app.get(
        "/workspaces/{workspace_id}/learning-events",
        response_model=list[LearningEvent],
    )
    def list_learning_events(workspace_id: str) -> list[LearningEvent]:
        return service.list_learning_events(workspace_id)

    @app.post(
        "/workspaces/{workspace_id}/evaluations",
        response_model=EvaluationRun,
        status_code=201,
    )
    def evaluate_workspace(
        workspace_id: str, request: EvaluationRequest
    ) -> EvaluationRun:
        return service.evaluate_workspace(workspace_id, request)

    @app.get(
        "/workspaces/{workspace_id}/evaluations",
        response_model=list[EvaluationRun],
    )
    def list_evaluations(workspace_id: str) -> list[EvaluationRun]:
        return service.list_evaluations(workspace_id)

    @app.post(
        "/workspaces/{workspace_id}/solve-evaluations",
        response_model=SolveEvaluationRun,
        status_code=201,
    )
    def evaluate_solver(
        workspace_id: str,
        request: SolveEvaluationRequest,
    ) -> SolveEvaluationRun:
        return service.evaluate_solver(workspace_id, request)

    @app.get(
        "/workspaces/{workspace_id}/solve-evaluations",
        response_model=list[SolveEvaluationRun],
    )
    def list_solve_evaluations(
        workspace_id: str,
    ) -> list[SolveEvaluationRun]:
        return service.list_solve_evaluations(workspace_id)

    return app


def _not_found(exc: Exception):
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=404, content={"detail": str(exc)})


app = create_app()
