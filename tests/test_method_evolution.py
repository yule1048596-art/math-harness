from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from math_harness.api import create_app
from math_harness.errors import RecordNotFound
from math_harness.merging import (
    MAX_APPLICABLE_WHEN,
    MAX_PROCEDURE,
    MAX_TAGS,
    merge_method_content,
)
from math_harness.models import (
    KnowledgeStatus,
    MethodCard,
    MethodDraft,
    WorkspaceCreate,
    utc_now,
)
from math_harness.service import MathHarnessService


def _card(**overrides) -> MethodCard:
    base = {
        "id": "method-1",
        "workspace_id": "workspace-1",
        "key": "rationalization",
        "name": "共轭有理化",
        "goal": "处理根式相减的主项抵消。",
        "applicable_when": ["表达式含根式之差"],
        "procedure": ["乘共轭式", "分析主导阶"],
        "failure_modes": ["遗漏定义域"],
        "tags": ["asymptotic", "radical"],
        "status": KnowledgeStatus.PROMOTED,
        "created_at": utc_now(),
        "updated_at": utc_now(),
    }
    return MethodCard(**{**base, **overrides})


def _draft(**overrides) -> MethodDraft:
    base = {
        "key": "rationalization",
        "name": "共轭有理化",
        "goal": "处理根式相减的主项抵消。",
        "applicable_when": ["表达式含根式之差"],
        "procedure": ["乘共轭式", "分析主导阶"],
        "failure_modes": ["遗漏定义域"],
        "tags": ["asymptotic", "radical"],
    }
    return MethodDraft(**{**base, **overrides})


# --- merge_method_content 单元行为 ---------------------------------------


def test_union_appends_new_entries_after_existing_ones():
    merged = merge_method_content(
        _card(),
        _draft(
            applicable_when=["直接代入出现 ∞-∞ 型抵消"],
            failure_modes=["有理化后过早截断"],
            tags=["cancellation"],
        ),
    )

    assert merged.applicable_when == ["表达式含根式之差", "直接代入出现 ∞-∞ 型抵消"]
    assert merged.failure_modes == ["遗漏定义域", "有理化后过早截断"]
    assert merged.tags == ["asymptotic", "radical", "cancellation"]
    assert merged.changed is True
    assert merged.added["applicable_when"] == ["直接代入出现 ∞-∞ 型抵消"]
    assert merged.added["tags"] == ["cancellation"]


def test_merge_deduplicates_ignoring_case_and_whitespace():
    merged = merge_method_content(
        _card(),
        _draft(
            applicable_when=["表达式含根式之差  "],
            tags=["ASYMPTOTIC", "Radical"],
        ),
    )

    assert merged.applicable_when == ["表达式含根式之差"]
    assert merged.tags == ["asymptotic", "radical"]
    assert merged.changed is False
    assert merged.added == {}


def test_identical_draft_reports_no_change():
    merged = merge_method_content(_card(), _draft())

    assert merged.changed is False
    assert merged.added == {}


def test_merge_enforces_field_limits_and_keeps_existing_entries():
    existing = _card(
        applicable_when=[f"旧条件 {index}" for index in range(MAX_APPLICABLE_WHEN)],
        tags=[f"tag-{index}" for index in range(MAX_TAGS)],
    )
    merged = merge_method_content(
        existing,
        _draft(
            applicable_when=["新条件"],
            tags=["new-tag"],
        ),
    )

    assert len(merged.applicable_when) == MAX_APPLICABLE_WHEN
    assert merged.applicable_when == existing.applicable_when
    assert len(merged.tags) == MAX_TAGS
    assert "新条件" not in merged.applicable_when
    assert merged.changed is False


def test_procedure_keeps_first_version():
    merged = merge_method_content(
        _card(),
        _draft(procedure=["完全不同的步骤"], name="另一个名字", goal="另一个目标"),
    )

    assert merged.procedure == ["乘共轭式", "分析主导阶"]
    assert merged.name == "共轭有理化"
    assert merged.goal == "处理根式相减的主项抵消。"


def test_empty_existing_content_adopts_draft():
    merged = merge_method_content(
        _card(name="", goal="   ", procedure=[]),
        _draft(name="共轭有理化", goal="新目标", procedure=["步骤一"]),
    )

    assert merged.name == "共轭有理化"
    assert merged.goal == "新目标"
    assert merged.procedure == ["步骤一"]
    assert merged.changed is True


# --- 存储层集成 ---------------------------------------------------------


@pytest.fixture
def seeded(tmp_path, verified_asymptotic_example):
    service = MathHarnessService(tmp_path)
    workspace = service.create_workspace(WorkspaceCreate(name="渐进估计"))
    result = service.ingest_example(workspace.id, verified_asymptotic_example)
    store = service.workspaces.store(workspace.id)
    return service, workspace, store, result.example.id


def test_method_content_accumulates_across_examples(seeded):
    _service, _workspace, store, example_id = seeded

    first = store.upsert_method(
        draft=_draft(key="conjugate_demo"),
        example_id=example_id,
        status=KnowledgeStatus.PROMOTED,
        verified=True,
    )
    second = store.upsert_method(
        draft=_draft(
            key="conjugate_demo",
            applicable_when=["直接代入出现 ∞-∞ 型抵消"],
            failure_modes=["有理化后过早截断"],
            tags=["cancellation"],
        ),
        example_id=example_id,
        status=KnowledgeStatus.PROMOTED,
        verified=True,
    )

    assert first.applicable_when == ["表达式含根式之差"]
    assert second.applicable_when == [
        "表达式含根式之差",
        "直接代入出现 ∞-∞ 型抵消",
    ]
    assert second.failure_modes == ["遗漏定义域", "有理化后过早截断"]
    assert second.tags == ["asymptotic", "radical", "cancellation"]
    assert second.version == first.version + 1


def test_first_method_version_enforces_field_limits(seeded):
    _service, _workspace, store, example_id = seeded

    created = store.upsert_method(
        draft=_draft(
            key="bounded_first_version",
            applicable_when=[
                f"条件 {index}" for index in range(MAX_APPLICABLE_WHEN + 5)
            ],
            procedure=[f"步骤 {index}" for index in range(MAX_PROCEDURE + 5)],
            tags=[f"tag-{index}" for index in range(MAX_TAGS + 5)],
        ),
        example_id=example_id,
        status=KnowledgeStatus.PROMOTED,
        verified=True,
    )

    assert len(created.applicable_when) == MAX_APPLICABLE_WHEN
    assert len(created.procedure) == MAX_PROCEDURE
    assert len(created.tags) == MAX_TAGS


def test_version_snapshot_preserves_content_before_the_rewrite(seeded):
    _service, _workspace, store, example_id = seeded

    first = store.upsert_method(
        draft=_draft(key="conjugate_demo"),
        example_id=example_id,
        status=KnowledgeStatus.PROMOTED,
        verified=True,
    )
    store.upsert_method(
        draft=_draft(key="conjugate_demo", applicable_when=["新增条件"]),
        example_id=example_id,
        status=KnowledgeStatus.PROMOTED,
        verified=True,
    )

    versions = store.list_method_versions(first.id)

    assert len(versions) == 1
    assert versions[0].version == first.version
    # 快照必须是改写「之前」的内容，不能是合并后的结果。
    assert versions[0].applicable_when == ["表达式含根式之差"]
    assert versions[0].source_example_id == example_id


def test_unchanged_draft_writes_no_version(seeded):
    _service, _workspace, store, example_id = seeded

    first = store.upsert_method(
        draft=_draft(key="conjugate_demo"),
        example_id=example_id,
        status=KnowledgeStatus.PROMOTED,
        verified=True,
    )
    again = store.upsert_method(
        draft=_draft(key="conjugate_demo"),
        example_id=example_id,
        status=KnowledgeStatus.PROMOTED,
        verified=True,
    )

    assert store.list_method_versions(first.id) == []
    # 计数与版本号仍然推进，只是内容没有演进。
    assert again.version == first.version + 1
    assert again.success_count == first.success_count + 1


def test_content_evolution_is_recorded_as_a_learning_event(seeded):
    service, workspace, store, example_id = seeded

    store.upsert_method(
        draft=_draft(key="conjugate_demo"),
        example_id=example_id,
        status=KnowledgeStatus.PROMOTED,
        verified=True,
    )
    store.upsert_method(
        draft=_draft(key="conjugate_demo", tags=["cancellation"]),
        example_id=example_id,
        status=KnowledgeStatus.PROMOTED,
        verified=True,
    )

    events = service.list_learning_events(workspace.id)
    evolved = [
        event for event in events if event.event_type == "method_content_evolved"
    ]

    assert len(evolved) == 1
    assert evolved[0].payload["added"]["tags"] == ["cancellation"]


def test_method_versions_are_workspace_scoped(tmp_path, verified_asymptotic_example):
    service = MathHarnessService(tmp_path)
    workspace_a = service.create_workspace(WorkspaceCreate(name="A"))
    workspace_b = service.create_workspace(WorkspaceCreate(name="B"))
    result = service.ingest_example(workspace_a.id, verified_asymptotic_example)
    method_id = result.learned_methods[0].id

    with pytest.raises(RecordNotFound):
        service.list_method_versions(workspace_b.id, method_id)


# --- API ----------------------------------------------------------------


def test_method_versions_endpoint_returns_evolution_history(
    tmp_path, verified_asymptotic_example
):
    # 离线规则提取器的 7 个模板是静态的，同一方法每次都给出相同草稿，内容不会演进。
    # 演进发生在 LLM 提取路径上，这里直接经存储层制造一次，以验证端点本身。
    service = MathHarnessService(tmp_path)
    workspace = service.create_workspace(WorkspaceCreate(name="渐进估计"))
    example_id = service.ingest_example(
        workspace.id, verified_asymptotic_example
    ).example.id
    store = service.workspaces.store(workspace.id)

    created = store.upsert_method(
        draft=_draft(key="conjugate_demo"),
        example_id=example_id,
        status=KnowledgeStatus.PROMOTED,
        verified=True,
    )
    store.upsert_method(
        draft=_draft(key="conjugate_demo", applicable_when=["直接代入出现 ∞-∞ 型抵消"]),
        example_id=example_id,
        status=KnowledgeStatus.PROMOTED,
        verified=True,
    )

    client = TestClient(create_app(tmp_path))
    response = client.get(f"/workspaces/{workspace.id}/methods/{created.id}/versions")
    assert response.status_code == 200
    history = response.json()
    assert len(history) == 1
    assert history[0]["version"] == created.version
    assert history[0]["applicable_when"] == ["表达式含根式之差"]

    current = client.get(f"/workspaces/{workspace.id}/methods").json()
    evolved = next(item for item in current if item["id"] == created.id)
    assert evolved["applicable_when"] == [
        "表达式含根式之差",
        "直接代入出现 ∞-∞ 型抵消",
    ]

    response = client.get(f"/workspaces/{workspace.id}/methods/unknown-id/versions")
    assert response.status_code == 404
