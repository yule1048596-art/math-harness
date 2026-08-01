from __future__ import annotations

from math_harness.methods import MethodExtractor
from math_harness.models import (
    AnswerKind,
    ApproachDirection,
    CandidateSolution,
    CandidateStep,
    ExampleCreate,
    ExtractionStatus,
    GenerationStatus,
    KnowledgeStatus,
    MathPayload,
    MethodDraft,
    MethodExtractionResult,
    MethodExtractionTrace,
    MethodStatusUpdate,
    SolutionAttemptStatus,
    SolutionCorrection,
    SolutionGenerationResult,
    SolutionGenerationTrace,
    SolveMathTarget,
    SolveRequest,
    SymbolProperty,
    VerificationMode,
    VerificationStatus,
    WorkspaceCreate,
)
from math_harness.service import MathHarnessService
from math_harness.solving import (
    FallbackSolutionGenerator,
    OfflineSympySolutionGenerator,
)
from math_harness.structure import MethodSignature
from math_harness.verifier import SolutionVerifier


def _root_target() -> SolveMathTarget:
    return SolveMathTarget(
        expression="sqrt(x**2 + x) - x",
        variable="x",
        point="oo",
        remainder_power=2,
    )


class InconsistentCandidateGenerator:
    name = "inconsistent-test-generator"
    prompt_version = "inconsistent-v1"

    def generate(self, problem, methods, math_target, max_output_tokens):
        del problem, methods, math_target, max_output_tokens
        return SolutionGenerationResult(
            candidate=CandidateSolution(
                answer_text="最终答案是 999。",
                answer_expression="1/2 - 1/(8*x)",
                steps=[
                    CandidateStep(
                        explanation="错误地把最终步骤写成 999。",
                        expression="999",
                    )
                ],
                used_method_keys=[],
                assumptions=[],
                confidence=0.99,
            ),
            trace=SolutionGenerationTrace(
                provider=self.name,
                response_id="resp_inconsistent",
                prompt_version=self.prompt_version,
                status=GenerationStatus.SUCCESS,
                raw_output="INCONSISTENT_OUTPUT",
            ),
        )


class WrongAnswerWithRepair:
    name = "wrong-repair-test-generator"
    model = "fake"
    prompt_version = "wrong-repair-v1"

    def generate(self, problem, methods, math_target, max_output_tokens):
        del problem, math_target, max_output_tokens
        used = [methods[0].method.key] if methods else []
        return SolutionGenerationResult(
            candidate=CandidateSolution(
                answer_text="错误首答。",
                answer_expression="0",
                steps=[CandidateStep(explanation="错误首答。", expression="0")],
                used_method_keys=used,
                assumptions=[],
                confidence=0.1,
            ),
            trace=SolutionGenerationTrace(
                provider=self.name,
                model=self.model,
                response_id="resp_initial",
                prompt_version=self.prompt_version,
                status=GenerationStatus.SUCCESS,
                raw_output="INITIAL_MODEL_OUTPUT",
            ),
        )

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
            math_target,
            previous_candidate,
            verification,
            max_output_tokens,
        )
        used = [methods[0].method.key] if methods else []
        return SolutionGenerationResult(
            candidate=CandidateSolution(
                answer_text="纠错后仍然错误。",
                answer_expression="1",
                steps=[CandidateStep(explanation="错误纠正。", expression="1")],
                used_method_keys=used,
                assumptions=[],
                confidence=0.2,
            ),
            trace=SolutionGenerationTrace(
                provider=self.name,
                model=self.model,
                response_id="resp_corrected",
                prompt_version=self.prompt_version,
                status=GenerationStatus.SUCCESS,
                raw_output="CORRECTED_MODEL_OUTPUT",
            ),
        )


class NoEquivalentGenerator:
    name = "no-equivalent-test-generator"
    prompt_version = "no-equivalent-v1"

    def __init__(self) -> None:
        self.repair_calls = 0

    def generate(self, problem, methods, math_target, max_output_tokens):
        del problem, methods, math_target, max_output_tokens
        return SolutionGenerationResult(
            candidate=CandidateSolution(
                answer_kind=AnswerKind.NO_EQUIVALENT,
                answer_text="该振荡函数不存在通常意义下的非零渐进等价式。",
                answer_expression=None,
                steps=[
                    CandidateStep(
                        explanation="沿不同子序列取值不一致。",
                        expression=None,
                    )
                ],
                used_method_keys=[],
                assumptions=[],
                confidence=0.8,
            ),
            trace=SolutionGenerationTrace(
                provider=self.name,
                prompt_version=self.prompt_version,
                status=GenerationStatus.SUCCESS,
            ),
        )

    def repair(self, *args, **kwargs):
        del args, kwargs
        self.repair_calls += 1
        raise AssertionError("non-expression claims must not trigger model repair")


class WrongThenNoEquivalentGenerator(NoEquivalentGenerator):
    name = "wrong-then-no-equivalent-test-generator"

    def generate(self, problem, methods, math_target, max_output_tokens):
        del problem, methods, math_target, max_output_tokens
        return SolutionGenerationResult(
            candidate=CandidateSolution(
                answer_text="错误地给出零。",
                answer_expression="0",
                steps=[CandidateStep(explanation="错误答案。", expression="0")],
                used_method_keys=[],
                assumptions=[],
                confidence=0.1,
            ),
            trace=SolutionGenerationTrace(
                provider=self.name,
                prompt_version=self.prompt_version,
                status=GenerationStatus.SUCCESS,
            ),
        )

    def repair(self, problem, methods, math_target, previous, verification, tokens):
        del problem, methods, math_target, previous, verification, tokens
        self.repair_calls += 1
        return super().generate("", [], None, 256)


class SequencedExtractor:
    name = "sequenced-test-extractor"
    prompt_version = "sequenced-v1"

    def __init__(self, drafts: list[MethodDraft]) -> None:
        self._drafts = iter(drafts)

    def extract(self, problem: str, solution: str, hint: str | None = None):
        del problem, solution, hint
        draft = next(self._drafts)
        return MethodExtractionResult(
            methods=[draft],
            trace=MethodExtractionTrace(
                provider=self.name,
                prompt_version=self.prompt_version,
                status=ExtractionStatus.SUCCESS,
                extracted_method_keys=[draft.key],
            ),
        )


def _method_draft(name: str, marker: str) -> MethodDraft:
    return MethodDraft(
        key="rationalization",
        name=name,
        goal=f"{marker}目标",
        applicable_when=[f"{marker}适用条件"],
        procedure=[f"{marker}步骤"],
        failure_modes=[f"{marker}失败模式"],
        tags=[marker],
    )


def _exact_example(expression: str, *, reviewed: bool) -> ExampleCreate:
    return ExampleCreate(
        problem=f"精确化简 {expression}",
        solution="测试解答",
        reviewed=reviewed,
        math_payload=MathPayload(
            expression=expression,
            expected=expression,
            mode=VerificationMode.EXACT_EQUIVALENCE,
        ),
    )


def test_unreviewed_solution_cannot_promote_methods(tmp_path):
    service = MathHarnessService(tmp_path, extractor=MethodExtractor())
    workspace = service.create_workspace(WorkspaceCreate(name="知识隔离"))

    result = service.ingest_example(
        workspace.id,
        ExampleCreate(
            problem="求根式渐进展开",
            solution="错误解答：使用 Stirling 公式并断言答案是 999。",
            math_payload=MathPayload(
                expression="sqrt(x**2 + x) - x",
                expected="1/2 - 1/(8*x)",
                point="oo",
                remainder_power=2,
            ),
        ),
    )

    assert result.example.verification.status is VerificationStatus.VERIFIED
    assert result.example.status.value == "pending_review"
    assert [method.key for method in result.learned_methods] == ["stirling"]
    assert result.learned_methods[0].status.value == "pending_review"
    assert result.learned_methods[0].success_count == 0


def test_reviewed_verified_solution_can_promote_methods(
    tmp_path,
    verified_asymptotic_example,
):
    service = MathHarnessService(tmp_path)
    workspace = service.create_workspace(WorkspaceCreate(name="人工复核知识"))

    result = service.ingest_example(
        workspace.id,
        verified_asymptotic_example.model_copy(update={"reviewed": True}),
    )

    assert result.example.status.value == "promoted"
    assert service.get_example(workspace.id, result.example.id).reviewed is True
    assert all(method.status.value == "promoted" for method in result.learned_methods)


def test_unreviewed_example_cannot_mutate_an_existing_promoted_method(tmp_path):
    trusted = _method_draft("可信方法", "trusted")
    poisoned = _method_draft("污染方法", "poisoned")
    service = MathHarnessService(
        tmp_path,
        extractor=SequencedExtractor([trusted, poisoned]),
    )
    workspace = service.create_workspace(WorkspaceCreate(name="晋级边界"))

    first = service.ingest_example(
        workspace.id,
        _exact_example("sqrt(x**2 + x) - x", reviewed=True),
    )
    before = first.learned_methods[0]
    second = service.ingest_example(
        workspace.id,
        _exact_example("sin(x)**2 + cos(x)**2", reviewed=False),
    )
    after = second.learned_methods[0]

    assert after == before
    assert "poisoned" not in after.tags
    assert second.example.id not in after.example_ids
    assert any(
        event.event_type == "untrusted_method_update_ignored"
        for event in service.list_learning_events(workspace.id)
    )


def test_first_trusted_example_replaces_pending_content_and_signature(tmp_path):
    poisoned = _method_draft("不可信旧方法", "poisoned")
    trusted = _method_draft("可信新方法", "trusted")
    service = MathHarnessService(
        tmp_path,
        extractor=SequencedExtractor([poisoned, trusted]),
    )
    workspace = service.create_workspace(WorkspaceCreate(name="可信首样本"))

    pending = service.ingest_example(
        workspace.id,
        _exact_example("sin(x)**2 + cos(x)**2", reviewed=False),
    )
    promoted = service.ingest_example(
        workspace.id,
        _exact_example("sqrt(x**2 + x) - x", reviewed=True),
    )
    method = promoted.learned_methods[0]

    assert pending.learned_methods[0].status.value == "pending_review"
    assert method.status.value == "promoted"
    assert method.name == trusted.name
    assert method.goal == trusted.goal
    assert method.procedure == trusted.procedure
    assert method.tags == trusted.tags
    assert method.example_ids == [promoted.example.id]
    assert method.success_count == 1
    signature = MethodSignature.model_validate(method.signature)
    assert signature.sample_count == 1
    assert signature.modes == {"exact_equivalence": 1}


def test_ingestion_does_not_revive_a_deprecated_method(tmp_path):
    original = _method_draft("已废弃方法", "original")
    incoming = _method_draft("试图复活", "revive")
    service = MathHarnessService(
        tmp_path,
        extractor=SequencedExtractor([original, incoming]),
    )
    workspace = service.create_workspace(WorkspaceCreate(name="废弃边界"))
    created = service.ingest_example(
        workspace.id,
        _exact_example("sqrt(x**2 + x) - x", reviewed=True),
    ).learned_methods[0]
    before = service.update_method_status(
        workspace.id,
        created.id,
        MethodStatusUpdate(status=KnowledgeStatus.DEPRECATED),
    )

    after = service.ingest_example(
        workspace.id,
        _exact_example("sqrt(x**2 + 3*x) - x", reviewed=True),
    ).learned_methods[0]

    assert after == before
    assert "revive" not in after.tags
    assert any(
        event.event_type == "inactive_method_update_ignored"
        for event in service.list_learning_events(workspace.id)
    )


def test_exact_equivalence_rejects_domain_holes():
    report = SolutionVerifier().verify(
        MathPayload(
            expression="(x**2 - 1)/(x - 1)",
            expected="x + 1",
            variable="x",
            point="0",
            mode=VerificationMode.EXACT_EQUIVALENCE,
        )
    )

    assert report.status is VerificationStatus.REJECTED
    assert report.computed["expression_domain"] != report.computed["expected_domain"]


def test_exact_equivalence_checks_parameter_domain_holes():
    report = SolutionVerifier().verify(
        MathPayload(
            expression="a/a",
            expected="1",
            variable="x",
            parameters=["a"],
            mode=VerificationMode.EXACT_EQUIVALENCE,
        )
    )

    assert report.status is VerificationStatus.REJECTED
    assert (
        report.computed["expression_domain:a"] != report.computed["expected_domain:a"]
    )


def test_exact_equivalence_honors_nonzero_parameter_assumption():
    report = SolutionVerifier().verify(
        MathPayload(
            expression="a/a",
            expected="1",
            variable="x",
            parameters=["a"],
            assumptions={"a": [SymbolProperty.NONZERO]},
            mode=VerificationMode.EXACT_EQUIVALENCE,
        )
    )

    assert report.status is VerificationStatus.VERIFIED


def test_offline_exact_solver_preserves_parameter_domain_conditions():
    target = SolveMathTarget(
        expression="a/a",
        variable="x",
        parameters=["a"],
        mode=VerificationMode.EXACT_EQUIVALENCE,
    )

    result = OfflineSympySolutionGenerator().generate("化简 a/a", [], target, 256)

    assert result.candidate is not None
    assert result.candidate.answer_kind is AnswerKind.CONDITIONAL
    assert result.candidate.answer_expression is None


def test_zero_is_not_a_valid_asymptotic_equivalent():
    report = SolutionVerifier().verify(
        MathPayload(
            expression="1/x",
            expected="0",
            variable="x",
            point="oo",
            mode=VerificationMode.ASYMPTOTIC_EQUIVALENCE,
        )
    )

    assert report.status is VerificationStatus.REJECTED
    assert "invalid_zero_equivalent" in report.checks

    limit_report = SolutionVerifier().verify(
        MathPayload(
            expression="1/x",
            expected="0",
            variable="x",
            point="oo",
            mode=VerificationMode.LIMIT,
        )
    )
    assert limit_report.status is VerificationStatus.VERIFIED


def test_direction_and_parameter_assumptions_are_machine_checked():
    verifier = SolutionVerifier()
    right = verifier.verify(
        MathPayload(
            expression="Abs(x)/x",
            expected="1",
            variable="x",
            point="0",
            direction=ApproachDirection.RIGHT,
            mode=VerificationMode.ASYMPTOTIC_EQUIVALENCE,
        )
    )
    left = verifier.verify(
        MathPayload(
            expression="Abs(x)/x",
            expected="1",
            variable="x",
            point="0",
            direction=ApproachDirection.LEFT,
            mode=VerificationMode.ASYMPTOTIC_EQUIVALENCE,
        )
    )
    parameterized = verifier.verify(
        MathPayload(
            expression="sqrt(a**2*x**2)/x",
            expected="a",
            variable="x",
            parameters=["a"],
            assumptions={"a": [SymbolProperty.POSITIVE]},
            point="oo",
            mode=VerificationMode.ASYMPTOTIC_EQUIVALENCE,
        )
    )

    assert right.status is VerificationStatus.VERIFIED
    assert left.status is VerificationStatus.REJECTED
    assert parameterized.status is VerificationStatus.VERIFIED


def test_inconsistent_final_step_cannot_receive_verified_status(tmp_path):
    service = MathHarnessService(
        tmp_path,
        generator=InconsistentCandidateGenerator(),
    )
    workspace = service.create_workspace(WorkspaceCreate(name="答案一致性"))

    attempt = service.solve_problem(
        workspace.id,
        SolveRequest(problem="求根式渐进展开", math_target=_root_target()),
    )

    assert attempt.status is SolutionAttemptStatus.REJECTED
    assert "candidate_final_step_consistency" in attempt.verification.checks
    captured = service.list_examples(workspace.id)
    assert len(captured) == 1
    assert captured[0].source_attempt_id == attempt.id
    assert captured[0].verification.status is VerificationStatus.REJECTED
    assert captured[0].status is KnowledgeStatus.REJECTED


def test_fallback_preserves_history_without_crediting_retrieved_methods(
    tmp_path,
    verified_asymptotic_example,
):
    service = MathHarnessService(
        tmp_path,
        generator=FallbackSolutionGenerator(WrongAnswerWithRepair()),
    )
    workspace = service.create_workspace(WorkspaceCreate(name="恢复审计"))
    service.ingest_example(
        workspace.id,
        verified_asymptotic_example.model_copy(update={"reviewed": True}),
    )
    before = {
        method.key: (method.success_count, method.failure_count)
        for method in service.list_methods(workspace.id)
    }

    attempt = service.solve_problem(
        workspace.id,
        SolveRequest(
            problem="求根式渐进展开",
            tags=["渐进估计", "根式"],
            math_target=_root_target(),
        ),
    )
    after = {
        method.key: (method.success_count, method.failure_count)
        for method in service.list_methods(workspace.id)
    }

    assert attempt.status is SolutionAttemptStatus.VERIFIED
    assert attempt.generation.verification_fallback_used is True
    assert attempt.candidate is not None
    assert attempt.candidate.used_method_keys == []
    assert attempt.feedback_method_keys == []
    assert after == before
    assert [stage.stage.value for stage in attempt.generation.stages] == [
        "initial",
        "correction",
        "fallback",
    ]
    assert attempt.generation.stages[0].response_id == "resp_initial"
    assert attempt.generation.stages[0].raw_output == "INITIAL_MODEL_OUTPUT"
    assert attempt.generation.stages[1].response_id == "resp_corrected"
    assert attempt.generation.stages[1].raw_output == "CORRECTED_MODEL_OUTPUT"
    assert all(stage.verification is not None for stage in attempt.generation.stages)


def test_human_correction_does_not_inherit_method_credit(
    tmp_path,
    verified_asymptotic_example,
):
    primary = WrongAnswerWithRepair()
    service = MathHarnessService(tmp_path, generator=primary)
    workspace = service.create_workspace(WorkspaceCreate(name="人工反馈"))
    service.ingest_example(
        workspace.id,
        verified_asymptotic_example.model_copy(update={"reviewed": True}),
    )
    wrong = service.solve_problem(
        workspace.id,
        SolveRequest(
            problem="求根式渐进展开",
            tags=["渐进估计", "根式"],
            math_target=_root_target(),
        ),
    )
    before = {
        method.key: method.success_count
        for method in service.list_methods(workspace.id)
    }

    corrected = service.correct_solution_attempt(
        workspace.id,
        wrong.id,
        SolutionCorrection(
            answer_text="人工纠正答案。",
            answer_expression="1/2 - 1/(8*x)",
        ),
    )
    after = {
        method.key: method.success_count
        for method in service.list_methods(workspace.id)
    }

    assert corrected.status is SolutionAttemptStatus.VERIFIED
    assert corrected.candidate is not None
    assert corrected.candidate.used_method_keys == []
    assert corrected.feedback_method_keys == []
    assert after == before


def test_non_expression_answer_is_preserved_for_review_without_recovery(tmp_path):
    primary = NoEquivalentGenerator()
    service = MathHarnessService(
        tmp_path,
        generator=FallbackSolutionGenerator(primary),
    )
    workspace = service.create_workspace(WorkspaceCreate(name="不存在答案"))

    attempt = service.solve_problem(
        workspace.id,
        SolveRequest(
            problem="判断 sin(x) 在无穷远是否存在非零渐进等价式",
            math_target=SolveMathTarget(
                expression="sin(x)",
                point="oo",
                mode=VerificationMode.ASYMPTOTIC_EQUIVALENCE,
            ),
        ),
    )

    assert attempt.status is SolutionAttemptStatus.NEEDS_REVIEW
    assert attempt.candidate is not None
    assert attempt.candidate.answer_kind is AnswerKind.NO_EQUIVALENT
    assert attempt.generation.fallback_used is False
    assert primary.repair_calls == 0
    assert attempt.feedback_method_keys == []


def test_non_expression_model_correction_stops_before_sympy_fallback(tmp_path):
    primary = WrongThenNoEquivalentGenerator()
    service = MathHarnessService(
        tmp_path,
        generator=FallbackSolutionGenerator(primary),
    )
    workspace = service.create_workspace(WorkspaceCreate(name="纠错后不存在"))

    attempt = service.solve_problem(
        workspace.id,
        SolveRequest(
            problem="判断 sin(x) 在无穷远是否存在非零渐进等价式",
            math_target=SolveMathTarget(
                expression="sin(x)",
                point="oo",
                mode=VerificationMode.ASYMPTOTIC_EQUIVALENCE,
            ),
        ),
    )

    assert primary.repair_calls == 1
    assert attempt.status is SolutionAttemptStatus.NEEDS_REVIEW
    assert attempt.candidate is not None
    assert attempt.candidate.answer_kind is AnswerKind.NO_EQUIVALENT
    assert attempt.generation.correction_attempted is True
    assert attempt.generation.verification_fallback_used is False
    assert [stage.stage.value for stage in attempt.generation.stages] == [
        "initial",
        "correction",
    ]
