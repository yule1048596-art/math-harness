from __future__ import annotations

from math_harness.models import (
    ExampleCreate,
    KnowledgeStatus,
    MathPayload,
    MethodKind,
    VerificationStatus,
    WorkspaceCreate,
)
from math_harness.service import MathHarnessService


def test_verified_example_promotes_extracted_methods(
    tmp_path, verified_asymptotic_example
):
    service = MathHarnessService(tmp_path)
    workspace = service.create_workspace(WorkspaceCreate(name="渐进估计"))

    result = service.ingest_example(workspace.id, verified_asymptotic_example)

    assert result.example.verification.status is VerificationStatus.VERIFIED
    assert result.example.status is KnowledgeStatus.PROMOTED
    keys = {method.key for method in result.learned_methods}
    assert MethodKind.RATIONALIZATION in keys
    assert MethodKind.VARIABLE_INVERSION in keys
    assert MethodKind.TAYLOR_EXPANSION in keys
    assert all(
        method.status is KnowledgeStatus.PROMOTED for method in result.learned_methods
    )
    assert all(
        result.example.id in method.example_ids for method in result.learned_methods
    )


def test_wrong_expansion_is_rejected_and_does_not_create_methods(tmp_path):
    service = MathHarnessService(tmp_path)
    workspace = service.create_workspace(WorkspaceCreate(name="渐进估计"))
    request = ExampleCreate(
        problem="求根式之差的渐进展开",
        solution="使用泰勒展开，答案是 1/3。",
        math_payload=MathPayload(
            expression="sqrt(x**2 + x) - x",
            expected="1/3",
            variable="x",
            point="oo",
            remainder_power=2,
        ),
    )

    result = service.ingest_example(workspace.id, request)

    assert result.example.verification.status is VerificationStatus.REJECTED
    assert result.example.status is KnowledgeStatus.REJECTED
    assert result.example.extraction is not None
    assert result.example.extraction.status == "skipped"
    assert result.learned_methods == []
    assert service.list_methods(workspace.id) == []


def test_unstructured_example_is_captured_as_pending(tmp_path):
    service = MathHarnessService(tmp_path)
    workspace = service.create_workspace(WorkspaceCreate(name="渐进估计"))
    request = ExampleCreate(
        problem="求某函数在零点附近的展开",
        solution="使用泰勒展开，并根据抵消情况多保留一阶。",
    )

    result = service.ingest_example(workspace.id, request)

    assert result.example.verification.status is VerificationStatus.NEEDS_REVIEW
    assert result.example.status is KnowledgeStatus.PENDING_REVIEW
    assert result.learned_methods
    assert all(
        method.status is KnowledgeStatus.PENDING_REVIEW
        for method in result.learned_methods
    )
    assert service.list_methods(workspace.id, include_pending=False) == []


def test_promoted_method_is_retrieved_only_from_current_workspace(
    tmp_path, verified_asymptotic_example
):
    service = MathHarnessService(tmp_path)
    workspace_a = service.create_workspace(WorkspaceCreate(name="渐进估计"))
    workspace_b = service.create_workspace(WorkspaceCreate(name="别的领域"))
    service.ingest_example(workspace_a.id, verified_asymptotic_example)

    matches = service.search_methods(
        workspace_a.id,
        "根式相减出现抵消时如何在无穷远处做渐进展开？",
        tags=["asymptotic", "radical"],
    )

    assert matches
    assert matches[0].method.workspace_id == workspace_a.id
    assert all(match.method.workspace_id == workspace_a.id for match in matches)
    assert service.search_methods(workspace_b.id, "根式渐进展开") == []
