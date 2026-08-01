from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from math_harness.api import create_app
from math_harness.dedup import (
    DEFAULT_THRESHOLD,
    card_to_draft,
    find_merge_candidates,
    load_threshold,
)
from math_harness.errors import RecordNotFound
from math_harness.models import (
    KnowledgeStatus,
    MathPayload,
    MergeProposalStatus,
    MethodCard,
    MethodDraft,
    WorkspaceCreate,
    utc_now,
)
from math_harness.service import MathHarnessService
from math_harness.structure import (
    MethodSignature,
    extract_features,
    signature_similarity,
)


def _payload(expression: str = "sqrt(x**2 + x) - x", **overrides) -> MathPayload:
    base = {
        "expression": expression,
        "expected": "1/2 - 1/(8*x)",
        "variable": "x",
        "point": "oo",
        "remainder_power": 2,
    }
    return MathPayload(**{**base, **overrides})


def _signature(*expressions: str) -> MethodSignature:
    signature = MethodSignature()
    for expression in expressions:
        signature = signature.accumulate(extract_features(_payload(expression)))
    return signature


def _card(key: str, signature: MethodSignature, **overrides) -> MethodCard:
    base = {
        "id": f"id-{key}",
        "workspace_id": "w",
        "key": key,
        "name": "共轭有理化",
        "goal": "处理根式相减的主项抵消。",
        "applicable_when": ["表达式含根式之差"],
        "procedure": ["乘共轭式"],
        "failure_modes": ["遗漏定义域"],
        "tags": ["asymptotic", "radical"],
        "status": KnowledgeStatus.PROMOTED,
        "signature": signature.model_dump(),
        "created_at": utc_now(),
        "updated_at": utc_now(),
    }
    return MethodCard(**{**base, **overrides})


# --- 相似度 -------------------------------------------------------------


def test_identical_signatures_are_maximally_similar():
    signature = _signature("sqrt(x**2 + x) - x")

    assert signature_similarity(signature, signature) == pytest.approx(1.0)


def test_unrelated_signatures_score_low():
    radical = _signature("sqrt(x**2 + x) - x")
    factorial = MethodSignature().accumulate(
        extract_features(
            _payload(
                "gamma(n + 1)",
                expected="sqrt(2*pi*n)*(n/E)**n",
                variable="n",
                mode="asymptotic_equivalence",
                remainder_power=None,
            )
        )
    )

    assert signature_similarity(radical, factorial) < 0.5


def test_empty_signature_is_never_similar():
    assert signature_similarity(MethodSignature(), _signature("x + 1")) == 0.0


# --- 候选检出 -----------------------------------------------------------


def test_duplicate_cards_are_detected():
    signature = _signature("sqrt(x**2 + x) - x", "sqrt(x**2 + 3*x) - x")
    methods = [
        _card("rationalization", signature),
        _card("conjugate_multiplication", signature),
    ]

    candidates = find_merge_candidates(methods)

    assert len(candidates) == 1
    assert {candidates[0].primary_key, candidates[0].duplicate_key} == {
        "rationalization",
        "conjugate_multiplication",
    }
    assert candidates[0].score >= DEFAULT_THRESHOLD


def test_distinct_cards_are_not_flagged():
    methods = [
        _card("rationalization", _signature("sqrt(x**2 + x) - x")),
        _card(
            "stirling",
            MethodSignature().accumulate(
                extract_features(
                    _payload(
                        "gamma(n + 1)",
                        expected="sqrt(2*pi*n)*(n/E)**n",
                        variable="n",
                        mode="asymptotic_equivalence",
                        remainder_power=None,
                    )
                )
            ),
            name="Stirling 公式",
            goal="估计阶乘增长。",
            applicable_when=["含阶乘或 Gamma"],
            procedure=["套用 Stirling"],
            failure_modes=["只保留主项"],
            tags=["factorial"],
        ),
    ]

    assert find_merge_candidates(methods) == []


def test_same_key_is_never_a_candidate():
    signature = _signature("sqrt(x**2 + x) - x")
    methods = [
        _card("rationalization", signature),
        _card("rationalization", signature, id="id-duplicate"),
    ]

    assert find_merge_candidates(methods) == []


def test_deprecated_cards_are_excluded():
    signature = _signature("sqrt(x**2 + x) - x")
    methods = [
        _card("rationalization", signature),
        _card(
            "conjugate_multiplication",
            signature,
            status=KnowledgeStatus.DEPRECATED,
        ),
    ]

    assert find_merge_candidates(methods) == []


def test_primary_selection_prefers_more_samples_then_earlier_creation():
    rich = _signature(
        "sqrt(x**2 + x) - x", "sqrt(x**2 + 3*x) - x", "sqrt(x**2 + 5*x) - x"
    )
    poor = _signature("sqrt(x**2 + x) - x")
    methods = [
        _card("thin_card", poor),
        _card("rich_card", rich),
    ]

    candidates = find_merge_candidates(methods)

    assert candidates[0].primary_key == "rich_card"
    assert candidates[0].duplicate_key == "thin_card"


def test_threshold_is_configurable(monkeypatch):
    monkeypatch.setenv("MATH_HARNESS_DEDUP_THRESHOLD", "0.99")
    assert load_threshold() == 0.99

    monkeypatch.setenv("MATH_HARNESS_DEDUP_THRESHOLD", "not-a-number")
    assert load_threshold() == DEFAULT_THRESHOLD


def test_card_to_draft_round_trips_content():
    card = _card("rationalization", _signature("sqrt(x**2 + x) - x"))
    draft = card_to_draft(card)

    assert isinstance(draft, MethodDraft)
    assert draft.key == card.key
    assert draft.applicable_when == card.applicable_when
    assert draft.failure_modes == card.failure_modes


# --- 提案与合并 ---------------------------------------------------------


@pytest.fixture
def workspace_with_duplicates(tmp_path, verified_asymptotic_example):
    service = MathHarnessService(tmp_path)
    workspace = service.create_workspace(WorkspaceCreate(name="去重测试"))
    example_id = service.ingest_example(
        workspace.id, verified_asymptotic_example
    ).example.id
    store = service.workspaces.store(workspace.id)
    features = extract_features(_payload())

    original = store.upsert_method(
        draft=card_to_draft(_card("alpha_method", MethodSignature())),
        example_id=example_id,
        status=KnowledgeStatus.PROMOTED,
        verified=True,
        features=features,
    )
    twin = store.upsert_method(
        draft=card_to_draft(
            _card(
                "beta_method",
                MethodSignature(),
                applicable_when=["表达式含根式之差", "出现 ∞-∞ 抵消"],
            )
        ),
        example_id=example_id,
        status=KnowledgeStatus.PROMOTED,
        verified=True,
        features=features,
    )
    return service, workspace, store, original, twin


def test_scan_records_pending_proposals(workspace_with_duplicates):
    service, workspace, _store, _a, _b = workspace_with_duplicates

    proposals = service.scan_merge_proposals(workspace.id)
    duplicates = [
        item
        for item in proposals
        if {item.primary_key, item.duplicate_key} == {"alpha_method", "beta_method"}
    ]

    assert len(duplicates) == 1
    assert duplicates[0].status is MergeProposalStatus.PENDING


def test_rescanning_does_not_duplicate_proposals(workspace_with_duplicates):
    service, workspace, _store, _a, _b = workspace_with_duplicates

    first = service.scan_merge_proposals(workspace.id)
    second = service.scan_merge_proposals(workspace.id)

    assert len(first) == len(second)
    assert {item.id for item in first} == {item.id for item in second}


def test_apply_merges_content_counts_and_examples(workspace_with_duplicates):
    service, workspace, store, _a, _b = workspace_with_duplicates
    proposal = next(
        item
        for item in service.scan_merge_proposals(workspace.id)
        if {item.primary_key, item.duplicate_key} == {"alpha_method", "beta_method"}
    )
    primary_before = store.get_method(proposal.primary_method_id)
    duplicate_before = store.get_method(proposal.duplicate_method_id)

    merged = service.apply_merge_proposal(workspace.id, proposal.id)
    duplicate_after = store.get_method(proposal.duplicate_method_id)

    # 内容并集累积，计数相加，来源例子归并到主卡。
    assert set(primary_before.applicable_when) <= set(merged.applicable_when)
    assert set(duplicate_before.applicable_when) <= set(merged.applicable_when)
    assert merged.success_count == (
        primary_before.success_count + duplicate_before.success_count
    )
    assert set(duplicate_before.example_ids) <= set(merged.example_ids)
    assert duplicate_after.example_ids == []

    # 副卡置为 deprecated 而非删除：审计要求，也是 attempt_methods 外键的技术必需。
    assert duplicate_after.status is KnowledgeStatus.DEPRECATED
    assert store.get_method(duplicate_before.id) is not None


def test_apply_snapshots_the_primary_card(workspace_with_duplicates):
    service, workspace, store, _a, _b = workspace_with_duplicates
    proposal = next(
        item
        for item in service.scan_merge_proposals(workspace.id)
        if {item.primary_key, item.duplicate_key} == {"alpha_method", "beta_method"}
    )
    before = store.get_method(proposal.primary_method_id)

    service.apply_merge_proposal(workspace.id, proposal.id)
    versions = store.list_method_versions(proposal.primary_method_id)

    assert any(
        version.applicable_when == before.applicable_when for version in versions
    )


def test_apply_records_a_learning_event(workspace_with_duplicates):
    service, workspace, _store, _a, _b = workspace_with_duplicates
    proposal = next(
        item
        for item in service.scan_merge_proposals(workspace.id)
        if {item.primary_key, item.duplicate_key} == {"alpha_method", "beta_method"}
    )

    service.apply_merge_proposal(workspace.id, proposal.id)
    events = service.list_learning_events(workspace.id)
    merged = [event for event in events if event.event_type == "methods_merged"]

    assert len(merged) == 1
    assert merged[0].payload["duplicate_key"] == proposal.duplicate_key


def test_signature_is_combined_on_merge(workspace_with_duplicates):
    service, workspace, store, _a, _b = workspace_with_duplicates
    proposal = next(
        item
        for item in service.scan_merge_proposals(workspace.id)
        if {item.primary_key, item.duplicate_key} == {"alpha_method", "beta_method"}
    )
    primary = MethodSignature.model_validate(
        store.get_method(proposal.primary_method_id).signature
    )
    duplicate = MethodSignature.model_validate(
        store.get_method(proposal.duplicate_method_id).signature
    )

    merged = service.apply_merge_proposal(workspace.id, proposal.id)
    combined = MethodSignature.model_validate(merged.signature)

    assert combined.sample_count == primary.sample_count + duplicate.sample_count


def test_resolved_proposal_cannot_be_applied_twice(workspace_with_duplicates):
    service, workspace, _store, _a, _b = workspace_with_duplicates
    proposal = next(
        item
        for item in service.scan_merge_proposals(workspace.id)
        if {item.primary_key, item.duplicate_key} == {"alpha_method", "beta_method"}
    )

    service.apply_merge_proposal(workspace.id, proposal.id)
    with pytest.raises(ValueError, match="already resolved"):
        service.apply_merge_proposal(workspace.id, proposal.id)


def test_reject_leaves_both_cards_untouched(workspace_with_duplicates):
    service, workspace, store, _a, _b = workspace_with_duplicates
    proposal = next(
        item
        for item in service.scan_merge_proposals(workspace.id)
        if {item.primary_key, item.duplicate_key} == {"alpha_method", "beta_method"}
    )
    before = store.get_method(proposal.duplicate_method_id)

    rejected = service.reject_merge_proposal(workspace.id, proposal.id)
    after = store.get_method(proposal.duplicate_method_id)

    assert rejected.status is MergeProposalStatus.REJECTED
    assert after.status is before.status
    assert after.version == before.version


def test_proposals_are_workspace_scoped(tmp_path, verified_asymptotic_example):
    service = MathHarnessService(tmp_path)
    workspace_a = service.create_workspace(WorkspaceCreate(name="A"))
    workspace_b = service.create_workspace(WorkspaceCreate(name="B"))
    service.ingest_example(workspace_a.id, verified_asymptotic_example)
    service.scan_merge_proposals(workspace_a.id)

    assert service.list_merge_proposals(workspace_b.id) == []
    with pytest.raises(RecordNotFound):
        service.workspaces.store(workspace_b.id).get_merge_proposal("missing")


# --- API ----------------------------------------------------------------


def test_merge_proposal_endpoints(tmp_path, verified_asymptotic_example):
    service = MathHarnessService(tmp_path)
    workspace = service.create_workspace(WorkspaceCreate(name="去重"))
    example_id = service.ingest_example(
        workspace.id, verified_asymptotic_example
    ).example.id
    store = service.workspaces.store(workspace.id)
    features = extract_features(_payload())
    for key in ("alpha_method", "beta_method"):
        store.upsert_method(
            draft=card_to_draft(_card(key, MethodSignature())),
            example_id=example_id,
            status=KnowledgeStatus.PROMOTED,
            verified=True,
            features=features,
        )

    client = TestClient(create_app(tmp_path))
    scanned = client.post(
        f"/workspaces/{workspace.id}/methods/merge-proposals", json={}
    )
    assert scanned.status_code == 201

    listed = client.get(f"/workspaces/{workspace.id}/methods/merge-proposals")
    assert listed.status_code == 200
    proposal = next(
        item
        for item in listed.json()
        if {item["primary_key"], item["duplicate_key"]}
        == {"alpha_method", "beta_method"}
    )

    applied = client.post(
        f"/workspaces/{workspace.id}/methods/merge-proposals/{proposal['id']}/apply"
    )
    assert applied.status_code == 200

    pending = client.get(
        f"/workspaces/{workspace.id}/methods/merge-proposals",
        params={"status": "pending"},
    )
    assert all(item["id"] != proposal["id"] for item in pending.json())
