from __future__ import annotations

import sqlite3

import pytest

from math_harness.errors import RecordNotFound
from math_harness.models import (
    CandidateSolution,
    CandidateStep,
    GenerationStatus,
    SolutionAttemptStatus,
    SolutionCorrection,
    SolutionGenerationResult,
    SolutionGenerationTrace,
    SolveEvaluationCase,
    SolveEvaluationRequest,
    SolveMathTarget,
    SolveRequest,
    VerificationStatus,
    WorkspaceCreate,
)
from math_harness.service import MathHarnessService


class WrongSolutionGenerator:
    name = "wrong-test-generator"
    prompt_version = "wrong-test-v1"

    def generate(self, problem, methods, math_target, max_output_tokens):
        del problem, methods, math_target, max_output_tokens
        candidate = CandidateSolution(
            answer_text="错误候选答案：1/3",
            answer_expression="1/3",
            steps=[
                CandidateStep(
                    explanation="故意生成错误答案以测试验收门禁。",
                    expression="1/3",
                )
            ],
            used_method_keys=["rationalization", "invented_method"],
            assumptions=[],
            confidence=0.2,
        )
        return SolutionGenerationResult(
            candidate=candidate,
            trace=SolutionGenerationTrace(
                provider=self.name,
                prompt_version=self.prompt_version,
                status=GenerationStatus.SUCCESS,
            ),
        )


class TextOnlySolutionGenerator:
    name = "text-only-test-generator"
    prompt_version = "text-only-test-v1"

    def generate(self, problem, methods, math_target, max_output_tokens):
        del problem, methods, math_target, max_output_tokens
        return SolutionGenerationResult(
            candidate=CandidateSolution(
                answer_text="只有自然语言答案。",
                answer_expression=None,
                steps=[
                    CandidateStep(
                        explanation="未给出机器可检查表达式。",
                        expression=None,
                    )
                ],
                used_method_keys=["rationalization"],
                assumptions=[],
                confidence=0.4,
            ),
            trace=SolutionGenerationTrace(
                provider=self.name,
                prompt_version=self.prompt_version,
                status=GenerationStatus.SUCCESS,
            ),
        )


def _solve_request() -> SolveRequest:
    return SolveRequest(
        problem="求根式相减在无穷远处的渐进展开",
        tags=["radical", "cancellation"],
        math_target=SolveMathTarget(
            expression="sqrt(x**2 + x) - x",
            variable="x",
            point="oo",
            remainder_power=2,
        ),
    )


def test_verified_solution_is_persisted_and_credits_used_methods(
    tmp_path, verified_asymptotic_example
):
    service = MathHarnessService(tmp_path)
    workspace = service.create_workspace(WorkspaceCreate(name="渐进估计"))
    service.ingest_example(workspace.id, verified_asymptotic_example)
    before = {
        method.key: method.success_count
        for method in service.list_methods(workspace.id)
    }

    attempt = service.solve_problem(workspace.id, _solve_request())

    assert attempt.status is SolutionAttemptStatus.VERIFIED
    assert attempt.verification.status is VerificationStatus.VERIFIED
    assert attempt.generation.provider == "sympy"
    assert attempt.candidate is not None
    assert attempt.candidate.answer_expression
    assert service.get_solution_attempt(workspace.id, attempt.id) == attempt
    assert service.list_solution_attempts(workspace.id) == [attempt]

    after = {
        method.key: method.success_count
        for method in service.list_methods(workspace.id)
    }
    assert attempt.candidate.used_method_keys
    for method_key in attempt.candidate.used_method_keys:
        assert after[method_key] == before[method_key] + 1

    events = service.list_learning_events(workspace.id)
    assert any(
        event.event_type == "solution_attempt_recorded"
        and event.target_id == attempt.id
        for event in events
    )
    assert sum(
        event.event_type == "method_outcome_recorded"
        and event.payload["attempt_id"] == attempt.id
        for event in events
    ) == len(attempt.candidate.used_method_keys)


def test_rejected_candidate_is_stored_and_debits_only_known_used_method(
    tmp_path, verified_asymptotic_example
):
    service = MathHarnessService(tmp_path, generator=WrongSolutionGenerator())
    workspace = service.create_workspace(WorkspaceCreate(name="渐进估计"))
    service.ingest_example(workspace.id, verified_asymptotic_example)
    before = {
        method.key: method.failure_count
        for method in service.list_methods(workspace.id)
    }

    attempt = service.solve_problem(workspace.id, _solve_request())

    assert attempt.status is SolutionAttemptStatus.REJECTED
    assert attempt.candidate is not None
    assert attempt.candidate.used_method_keys == ["rationalization"]
    after = {
        method.key: method.failure_count
        for method in service.list_methods(workspace.id)
    }
    assert after["rationalization"] == before["rationalization"] + 1
    assert after["taylor_expansion"] == before["taylor_expansion"]
    assert "invented_method" not in after


def test_generation_failure_is_audited_without_method_feedback(
    tmp_path, verified_asymptotic_example
):
    service = MathHarnessService(tmp_path)
    workspace = service.create_workspace(WorkspaceCreate(name="渐进估计"))
    service.ingest_example(workspace.id, verified_asymptotic_example)
    before = {
        method.key: (method.success_count, method.failure_count)
        for method in service.list_methods(workspace.id)
    }

    attempt = service.solve_problem(
        workspace.id,
        SolveRequest(problem="只有自然语言、没有机器可检查的目标"),
    )

    assert attempt.status is SolutionAttemptStatus.GENERATION_FAILED
    assert attempt.candidate is None
    assert attempt.verification.status is VerificationStatus.NEEDS_REVIEW
    after = {
        method.key: (method.success_count, method.failure_count)
        for method in service.list_methods(workspace.id)
    }
    assert after == before


def test_needs_review_candidate_does_not_change_method_counts(
    tmp_path, verified_asymptotic_example
):
    service = MathHarnessService(
        tmp_path,
        generator=TextOnlySolutionGenerator(),
    )
    workspace = service.create_workspace(WorkspaceCreate(name="渐进估计"))
    service.ingest_example(workspace.id, verified_asymptotic_example)
    before = {
        method.key: (method.success_count, method.failure_count)
        for method in service.list_methods(workspace.id)
    }

    attempt = service.solve_problem(workspace.id, _solve_request())

    assert attempt.status is SolutionAttemptStatus.NEEDS_REVIEW
    assert attempt.verification.status is VerificationStatus.NEEDS_REVIEW
    after = {
        method.key: (method.success_count, method.failure_count)
        for method in service.list_methods(workspace.id)
    }
    assert after == before


def test_human_correction_creates_a_new_verified_attempt(
    tmp_path, verified_asymptotic_example
):
    service = MathHarnessService(tmp_path, generator=WrongSolutionGenerator())
    workspace = service.create_workspace(WorkspaceCreate(name="渐进估计"))
    service.ingest_example(workspace.id, verified_asymptotic_example)
    wrong = service.solve_problem(workspace.id, _solve_request())

    corrected = service.correct_solution_attempt(
        workspace.id,
        wrong.id,
        SolutionCorrection(
            answer_text="正确展开为 1/2 - 1/(8*x) + O(x^-2)。",
            answer_expression="1/2 - 1/(8*x)",
            reviewer_note="修正了常数项。",
        ),
    )

    assert corrected.id != wrong.id
    assert corrected.correction_of == wrong.id
    assert corrected.generation.provider == "human"
    assert corrected.status is SolutionAttemptStatus.VERIFIED
    assert service.get_solution_attempt(workspace.id, wrong.id).status is (
        SolutionAttemptStatus.REJECTED
    )
    assert service.list_solution_attempts(workspace.id) == [wrong, corrected]


def test_solve_evaluation_does_not_persist_attempts_or_change_method_counts(
    tmp_path, verified_asymptotic_example
):
    service = MathHarnessService(tmp_path)
    workspace = service.create_workspace(WorkspaceCreate(name="渐进估计"))
    service.ingest_example(workspace.id, verified_asymptotic_example)
    before = {
        method.key: (method.success_count, method.failure_count)
        for method in service.list_methods(workspace.id)
    }

    run = service.evaluate_solver(
        workspace.id,
        SolveEvaluationRequest(
            name="offline-gate",
            top_k=3,
            cases=[
                SolveEvaluationCase(
                    id="radical-1",
                    problem="求根式相减的渐进展开",
                    tags=["radical"],
                    math_target=_solve_request().math_target,
                )
            ],
        ),
    )

    assert run.metrics.verified_rate == 1
    assert run.metrics.generation_failure_rate == 0
    assert service.list_solve_evaluations(workspace.id) == [run]
    assert service.list_solution_attempts(workspace.id) == []
    after = {
        method.key: (method.success_count, method.failure_count)
        for method in service.list_methods(workspace.id)
    }
    assert after == before


def test_attempts_remain_workspace_isolated(tmp_path, verified_asymptotic_example):
    service = MathHarnessService(tmp_path)
    workspace_a = service.create_workspace(WorkspaceCreate(name="A"))
    workspace_b = service.create_workspace(WorkspaceCreate(name="B"))
    service.ingest_example(workspace_a.id, verified_asymptotic_example)
    attempt = service.solve_problem(workspace_a.id, _solve_request())

    assert service.list_solution_attempts(workspace_b.id) == []
    with pytest.raises(RecordNotFound):
        service.get_solution_attempt(workspace_b.id, attempt.id)


def test_existing_workspace_gets_v030_tables_on_first_open(
    tmp_path, verified_asymptotic_example
):
    service = MathHarnessService(tmp_path)
    workspace = service.create_workspace(WorkspaceCreate(name="旧工作区"))
    service.ingest_example(workspace.id, verified_asymptotic_example)
    database_path = service.workspaces.database_path(workspace.id)
    with sqlite3.connect(database_path) as connection:
        connection.execute("DROP TABLE attempt_methods")
        connection.execute("DROP TABLE solution_attempts")
        connection.execute("DROP TABLE solve_evaluation_runs")

    reopened = MathHarnessService(tmp_path)
    attempt = reopened.solve_problem(workspace.id, _solve_request())

    assert attempt.status is SolutionAttemptStatus.VERIFIED
    assert reopened.list_solution_attempts(workspace.id) == [attempt]
