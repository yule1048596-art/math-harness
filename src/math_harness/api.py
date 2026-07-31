from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Query

from math_harness import __version__
from math_harness.errors import RecordNotFound, WorkspaceNotFound
from math_harness.models import (
    EvaluationRequest,
    EvaluationRun,
    ExampleCreate,
    HealthResponse,
    IngestionResult,
    LearningEvent,
    MethodCard,
    MethodMatch,
    MethodSearchRequest,
    MethodStatusUpdate,
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


def create_app(data_root: Path | str | None = None) -> FastAPI:
    root = data_root or os.getenv("MATH_HARNESS_DATA_DIR", ".math_harness")
    service = MathHarnessService(root)
    app = FastAPI(
        title="Math Harness",
        version=__version__,
        description="工作区隔离、可验证、可成长的数学 AI harness 原型。",
    )
    app.state.service = service

    @app.exception_handler(WorkspaceNotFound)
    async def workspace_not_found_handler(_, exc: WorkspaceNotFound):
        return _not_found(exc)

    @app.exception_handler(RecordNotFound)
    async def record_not_found_handler(_, exc: RecordNotFound):
        return _not_found(exc)

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
