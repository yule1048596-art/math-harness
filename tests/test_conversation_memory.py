from __future__ import annotations

import sqlite3

import pytest

from math_harness.errors import InvalidKnowledgeState, RecordNotFound
from math_harness.models import (
    CandidateSolution,
    CandidateStep,
    ExampleOrigin,
    ExampleReviewDecision,
    ExampleReviewRequest,
    GenerationStatus,
    KnowledgeStatus,
    SolutionGenerationResult,
    SolutionGenerationTrace,
    SolveMathTarget,
    SolveRequest,
    WorkspaceCreate,
)
from math_harness.service import MathHarnessService


class StaticGenerator:
    name = "static"
    model = "test-model"
    prompt_version = "static-v1"

    def generate(self, problem, matches, math_target, max_output_tokens):
        del problem, matches, math_target, max_output_tokens
        candidate = CandidateSolution(
            answer_text="先令 t=1/x，再做泰勒展开，得到 1/2 - 1/(8*x)。",
            answer_expression="1/2 - 1/(8*x)",
            steps=[
                CandidateStep(
                    explanation="令 t=1/x 并在零点做泰勒展开。",
                    expression="1/2 - 1/(8*x)",
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
                model=self.model,
                prompt_version=self.prompt_version,
                status=GenerationStatus.SUCCESS,
            ),
        )


def _verified_request() -> SolveRequest:
    return SolveRequest(
        problem="用变量倒换与泰勒展开求根式之差的渐进展开",
        tags=["asymptotic", "radical"],
        math_target=SolveMathTarget(
            expression="sqrt(x**2 + x) - x",
            point="oo",
            remainder_power=2,
        ),
    )


def test_solve_automatically_captures_one_idempotent_knowledge_draft(tmp_path):
    service = MathHarnessService(tmp_path, generator=StaticGenerator())
    workspace = service.create_workspace(WorkspaceCreate(name="自动记忆"))

    attempt = service.solve_problem(workspace.id, _verified_request())
    examples = service.list_examples(workspace.id)

    assert len(examples) == 1
    example = examples[0]
    assert example.origin is ExampleOrigin.CONVERSATION
    assert example.source_attempt_id == attempt.id
    assert example.status is KnowledgeStatus.PENDING_REVIEW
    assert example.reviewed is False
    assert example.verification.status.value == "verified"
    assert "泰勒展开" in example.solution

    repeated = service.capture_solution_attempt(workspace.id, attempt.id)
    assert repeated.created is False
    assert repeated.example.id == example.id
    assert len(service.list_examples(workspace.id)) == 1


def test_verified_conversation_draft_promotes_only_after_human_review(tmp_path):
    service = MathHarnessService(tmp_path, generator=StaticGenerator())
    workspace = service.create_workspace(WorkspaceCreate(name="安全晋级"))
    attempt = service.solve_problem(workspace.id, _verified_request())
    example = service.list_examples(workspace.id)[0]
    pending = service.list_methods(workspace.id)

    assert pending
    assert all(method.status is KnowledgeStatus.PENDING_REVIEW for method in pending)
    assert service.search_methods(workspace.id, attempt.problem) == []

    approved = service.review_example(
        workspace.id,
        example.id,
        ExampleReviewRequest(
            decision=ExampleReviewDecision.APPROVE,
            reviewer_note="已核对推导与余项。",
        ),
    )

    assert approved.example.reviewed is True
    assert approved.example.status is KnowledgeStatus.PROMOTED
    assert approved.example.reviewed_at is not None
    assert approved.example.reviewer_note == "已核对推导与余项。"
    assert approved.learned_methods
    assert all(
        method.status is KnowledgeStatus.PROMOTED for method in approved.learned_methods
    )
    versions = {method.id: method.version for method in approved.learned_methods}

    repeated = service.review_example(
        workspace.id,
        example.id,
        ExampleReviewRequest(decision=ExampleReviewDecision.APPROVE),
    )
    assert {
        method.id: method.version for method in repeated.learned_methods
    } == versions


def test_unverifiable_conversation_cannot_be_approved(tmp_path):
    service = MathHarnessService(tmp_path, generator=StaticGenerator())
    workspace = service.create_workspace(WorkspaceCreate(name="缺少目标"))
    service.solve_problem(
        workspace.id,
        SolveRequest(problem="解释如何做泰勒展开，但不提供机器可读目标"),
    )
    example = service.list_examples(workspace.id)[0]

    with pytest.raises(InvalidKnowledgeState, match="独立数学验证"):
        service.review_example(
            workspace.id,
            example.id,
            ExampleReviewRequest(decision=ExampleReviewDecision.APPROVE),
        )

    assert service.get_example(workspace.id, example.id).reviewed is False


def test_rejecting_a_draft_retires_its_orphan_pending_methods(tmp_path):
    service = MathHarnessService(tmp_path, generator=StaticGenerator())
    workspace = service.create_workspace(WorkspaceCreate(name="拒绝草稿"))
    service.solve_problem(workspace.id, _verified_request())
    example = service.list_examples(workspace.id)[0]
    pending = service.list_methods(workspace.id)

    rejected = service.review_example(
        workspace.id,
        example.id,
        ExampleReviewRequest(
            decision=ExampleReviewDecision.REJECT,
            reviewer_note="步骤不适合作为训练样本。",
        ),
    )

    assert rejected.example.status is KnowledgeStatus.REJECTED
    assert rejected.example.reviewer_note == "步骤不适合作为训练样本。"
    assert service.list_methods(workspace.id) == []
    assert all(
        service.workspaces.store(workspace.id).get_method(method.id).status
        is KnowledgeStatus.REJECTED
        for method in pending
    )


def test_conversation_capture_stays_inside_its_workspace(tmp_path):
    service = MathHarnessService(tmp_path, generator=StaticGenerator())
    workspace_a = service.create_workspace(WorkspaceCreate(name="空间 A"))
    workspace_b = service.create_workspace(WorkspaceCreate(name="空间 B"))

    attempt = service.solve_problem(workspace_a.id, _verified_request())

    assert len(service.list_examples(workspace_a.id)) == 1
    assert service.list_examples(workspace_b.id) == []
    with pytest.raises(RecordNotFound, match="not found"):
        service.capture_solution_attempt(workspace_b.id, attempt.id)


def test_v060_workspace_is_migrated_without_losing_examples(tmp_path):
    service = MathHarnessService(tmp_path, generator=StaticGenerator())
    workspace = service.create_workspace(WorkspaceCreate(name="旧数据迁移"))
    service.solve_problem(workspace.id, _verified_request())
    database_path = service.workspaces.database_path(workspace.id)
    with sqlite3.connect(database_path) as connection:
        connection.execute("DROP INDEX idx_examples_source_attempt")
        for column in (
            "method_drafts_json",
            "origin",
            "source_attempt_id",
            "reviewed_at",
            "reviewer_note",
        ):
            connection.execute(f"ALTER TABLE examples DROP COLUMN {column}")

    reopened = MathHarnessService(tmp_path, generator=StaticGenerator())
    migrated = reopened.list_examples(workspace.id)

    assert len(migrated) == 1
    assert migrated[0].origin is ExampleOrigin.MANUAL
    assert migrated[0].source_attempt_id is None
    assert migrated[0].reviewer_note == ""
    new_attempt = reopened.solve_problem(workspace.id, _verified_request())
    new_example = next(
        example
        for example in reopened.list_examples(workspace.id)
        if example.source_attempt_id == new_attempt.id
    )
    assert new_example.origin is ExampleOrigin.CONVERSATION
