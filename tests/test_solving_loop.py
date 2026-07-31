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
from math_harness.solving import FallbackSolutionGenerator


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


class FormattingSolutionGenerator:
    name = "formatting-test-generator"
    prompt_version = "formatting-test-v1"

    def generate(self, problem, methods, math_target, max_output_tokens):
        del problem, methods, math_target, max_output_tokens
        candidate = CandidateSolution(
            answer_text="正确答案，但机器表达式带有 Big-O。",
            answer_expression="1/2 - 1/(8*x) + O(x**(-2))",
            steps=[
                CandidateStep(
                    explanation="展开到指定余项阶。",
                    expression="1/2 - 1/(8*x) + O(x**(-2))",
                )
            ],
            used_method_keys=[],
            assumptions=[],
            confidence=0.9,
        )
        return SolutionGenerationResult(
            candidate=candidate,
            trace=SolutionGenerationTrace(
                provider=self.name,
                prompt_version=self.prompt_version,
                status=GenerationStatus.SUCCESS,
            ),
        )


class RepairingSolutionGenerator(WrongSolutionGenerator):
    name = "repairing-test-generator"
    prompt_version = "repairing-test-v1"

    def __init__(self, repair_expression: str) -> None:
        self.repair_expression = repair_expression
        self.repair_calls = 0

    def repair(
        self,
        problem,
        methods,
        math_target,
        previous_candidate,
        verification,
        max_output_tokens,
    ):
        del (
            problem,
            methods,
            math_target,
            previous_candidate,
            verification,
            max_output_tokens,
        )
        self.repair_calls += 1
        candidate = CandidateSolution(
            answer_text=f"纠正答案：{self.repair_expression}",
            answer_expression=self.repair_expression,
            steps=[
                CandidateStep(
                    explanation="根据验证反馈重新计算。",
                    expression=self.repair_expression,
                )
            ],
            used_method_keys=[],
            assumptions=[],
            confidence=0.9,
        )
        return SolutionGenerationResult(
            candidate=candidate,
            trace=SolutionGenerationTrace(
                provider=self.name,
                prompt_version=self.prompt_version,
                status=GenerationStatus.SUCCESS,
                correction_attempted=True,
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


def test_parser_notation_is_normalized_before_verification(tmp_path):
    service = MathHarnessService(
        tmp_path,
        generator=FormattingSolutionGenerator(),
    )
    workspace = service.create_workspace(WorkspaceCreate(name="格式规范化"))

    attempt = service.solve_problem(workspace.id, _solve_request())

    assert attempt.status is SolutionAttemptStatus.VERIFIED
    assert attempt.candidate is not None
    assert attempt.candidate.answer_expression == "1/2 - 1/(8*x)"
    assert attempt.generation.normalization_actions == ["strip_trailing_order_term"]
    assert attempt.generation.correction_attempted is False


def test_verified_model_correction_is_kept_without_sympy_fallback(tmp_path):
    primary = RepairingSolutionGenerator("1/2 - 1/(8*x)")
    service = MathHarnessService(
        tmp_path,
        generator=FallbackSolutionGenerator(primary),
    )
    workspace = service.create_workspace(WorkspaceCreate(name="模型纠错"))

    attempt = service.solve_problem(workspace.id, _solve_request())

    assert primary.repair_calls == 1
    assert attempt.status is SolutionAttemptStatus.VERIFIED
    assert attempt.generation.provider == primary.name
    assert attempt.generation.correction_attempted is True
    assert attempt.generation.correction_succeeded is True
    assert attempt.generation.fallback_used is False
    assert any(
        note.startswith("initial_verification=rejected")
        for note in attempt.generation.recovery_notes
    )


def test_failed_model_correction_uses_verified_sympy_fallback(tmp_path):
    primary = RepairingSolutionGenerator("1/3")
    service = MathHarnessService(
        tmp_path,
        generator=FallbackSolutionGenerator(primary),
    )
    workspace = service.create_workspace(WorkspaceCreate(name="验证回退"))

    attempt = service.solve_problem(workspace.id, _solve_request())

    assert primary.repair_calls == 1
    assert attempt.status is SolutionAttemptStatus.VERIFIED
    assert attempt.generation.provider == f"{primary.name}->sympy"
    assert attempt.generation.status is GenerationStatus.FALLBACK
    assert attempt.generation.correction_attempted is True
    assert attempt.generation.correction_succeeded is False
    assert attempt.generation.fallback_used is True
    assert attempt.generation.verification_fallback_used is True
    assert attempt.candidate is not None
    assert attempt.candidate.answer_expression != "1/3"
    assert any(
        note.startswith("fallback_verification=verified")
        for note in attempt.generation.recovery_notes
    )


def test_wrong_primary_without_repair_uses_verification_fallback(tmp_path):
    service = MathHarnessService(
        tmp_path,
        generator=FallbackSolutionGenerator(WrongSolutionGenerator()),
    )
    workspace = service.create_workspace(WorkspaceCreate(name="直接验证回退"))

    attempt = service.solve_problem(workspace.id, _solve_request())

    assert attempt.status is SolutionAttemptStatus.VERIFIED
    assert attempt.generation.fallback_used is True
    assert attempt.generation.verification_fallback_used is True
    assert attempt.generation.correction_attempted is False


def test_verification_recovery_can_be_disabled(tmp_path):
    primary = RepairingSolutionGenerator("1/2 - 1/(8*x)")
    service = MathHarnessService(
        tmp_path,
        generator=FallbackSolutionGenerator(
            primary,
            verification_repair_enabled=False,
            verification_fallback_enabled=False,
        ),
    )
    workspace = service.create_workspace(WorkspaceCreate(name="关闭自动恢复"))

    attempt = service.solve_problem(workspace.id, _solve_request())

    assert attempt.status is SolutionAttemptStatus.REJECTED
    assert primary.repair_calls == 0
    assert attempt.generation.correction_attempted is False
    assert attempt.generation.fallback_used is False


def test_solve_evaluation_reports_correction_and_fallback_rates(tmp_path):
    primary = RepairingSolutionGenerator("1/2 - 1/(8*x)")
    service = MathHarnessService(
        tmp_path,
        generator=FallbackSolutionGenerator(primary),
    )
    workspace = service.create_workspace(WorkspaceCreate(name="恢复指标"))

    run = service.evaluate_solver(
        workspace.id,
        SolveEvaluationRequest(
            name="recovery-metrics",
            cases=[
                SolveEvaluationCase(
                    id="repair-success",
                    problem=_solve_request().problem,
                    math_target=_solve_request().math_target,
                )
            ],
        ),
    )

    assert run.metrics.verified_rate == 1
    assert run.metrics.correction_attempt_rate == 1
    assert run.metrics.correction_success_rate == 1
    assert run.metrics.fallback_rate == 0
    assert run.cases[0].correction_attempted is True
    assert run.cases[0].correction_succeeded is True


def test_legacy_generation_trace_gets_safe_recovery_defaults():
    trace = SolutionGenerationTrace.model_validate(
        {
            "provider": "legacy",
            "prompt_version": "legacy-v1",
            "status": "success",
        }
    )

    assert trace.normalization_actions == []
    assert trace.recovery_notes == []
    assert trace.correction_attempted is False
    assert trace.correction_succeeded is False
    assert trace.verification_fallback_used is False
    assert trace.method_feedback_eligible is True
    assert trace.stages == []


def test_verified_offline_solution_is_persisted_without_method_credit(
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
    assert attempt.candidate.used_method_keys == []
    assert attempt.feedback_method_keys == []
    assert after == before

    events = service.list_learning_events(workspace.id)
    assert any(
        event.event_type == "solution_attempt_recorded"
        and event.target_id == attempt.id
        for event in events
    )
    assert (
        sum(
            event.event_type == "method_outcome_recorded"
            and event.payload["attempt_id"] == attempt.id
            for event in events
        )
        == 0
    )


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


def test_human_correction_is_not_automatically_normalized(
    tmp_path,
    verified_asymptotic_example,
):
    service = MathHarnessService(tmp_path, generator=WrongSolutionGenerator())
    workspace = service.create_workspace(WorkspaceCreate(name="人工输入保持原样"))
    service.ingest_example(workspace.id, verified_asymptotic_example)
    wrong = service.solve_problem(workspace.id, _solve_request())

    corrected = service.correct_solution_attempt(
        workspace.id,
        wrong.id,
        SolutionCorrection(
            answer_text="人工输入包含 Big-O。",
            answer_expression="1/2 - 1/(8*x) + O(x**(-2))",
        ),
    )

    assert corrected.status is SolutionAttemptStatus.REJECTED
    assert corrected.candidate is not None
    assert corrected.candidate.answer_expression.endswith("O(x**(-2))")
    assert corrected.generation.normalization_actions == []
    assert corrected.generation.correction_attempted is False


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
    assert run.metrics.fallback_rate == 0
    assert run.metrics.correction_attempt_rate == 0
    assert run.metrics.correction_success_rate == 0
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
    ingestion = service.ingest_example(workspace.id, verified_asymptotic_example)
    database_path = service.workspaces.database_path(workspace.id)
    with sqlite3.connect(database_path) as connection:
        connection.execute("ALTER TABLE examples DROP COLUMN reviewed")
        connection.execute("DROP TABLE attempt_methods")
        connection.execute("DROP TABLE solution_attempts")
        connection.execute("DROP TABLE solve_evaluation_runs")

    reopened = MathHarnessService(tmp_path)
    attempt = reopened.solve_problem(workspace.id, _solve_request())

    assert attempt.status is SolutionAttemptStatus.VERIFIED
    assert reopened.get_example(workspace.id, ingestion.example.id).reviewed is False
    assert reopened.list_solution_attempts(workspace.id) == [attempt]
