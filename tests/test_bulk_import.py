from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from math_harness.api import create_app
from math_harness.errors import InvalidPortableData
from math_harness.models import (
    BulkExampleImportRequest,
    ExtractionStatus,
    ImportExtractorPolicy,
    MethodExtractionResult,
    MethodExtractionTrace,
    WorkspaceCreate,
)
from math_harness.service import MathHarnessService
from math_harness.storage import WorkspaceStore


def _item(coefficient: int, *, reviewed: bool = True) -> dict[str, object]:
    return {
        "problem": f"求 x→∞ 时 sqrt(x^2+{coefficient}x)-x 的渐进展开",
        "solution": "乘共轭式后令 t=1/x，再使用泰勒展开。",
        "tags": ["渐进估计", "根式"],
        "reviewed": reviewed,
        "math_payload": {
            "expression": f"sqrt(x**2 + {coefficient}*x) - x",
            "expected": f"{coefficient}/2 - {coefficient**2}/(8*x)",
            "variable": "x",
            "point": "oo",
            "remainder_power": 2,
        },
    }


def _request(
    items: list[dict[str, object]], *, commit: bool
) -> BulkExampleImportRequest:
    return BulkExampleImportRequest(
        content="\n".join(json.dumps(item, ensure_ascii=False) for item in items),
        file_format="jsonl",
        source_name="corpus.jsonl",
        commit=commit,
    )


def test_bulk_import_previews_then_commits_atomically(tmp_path):
    service = MathHarnessService(tmp_path)
    workspace = service.create_workspace(WorkspaceCreate(name="批量导入"))
    request = _request([_item(1), _item(3)], commit=False)

    preview = service.bulk_import_examples(workspace.id, request)

    assert preview.can_commit is True
    assert preview.committed is False
    assert preview.ready_count == 2
    assert not service.list_examples(workspace.id)
    assert all(item.verification.status == "verified" for item in preview.items)

    imported = service.bulk_import_examples(
        workspace.id,
        request.model_copy(update={"commit": True}),
    )

    assert imported.committed is True
    assert imported.imported_count == 2
    assert [item.status for item in imported.items] == ["imported", "imported"]
    examples = service.list_examples(workspace.id)
    assert len(examples) == 2
    assert all(example.reviewed is False for example in examples)
    assert all(example.status == "pending_review" for example in examples)


def test_bulk_import_skips_existing_and_in_file_duplicates(tmp_path):
    service = MathHarnessService(tmp_path)
    workspace = service.create_workspace(WorkspaceCreate(name="去重"))
    first = _request([_item(1)], commit=True)
    assert service.bulk_import_examples(workspace.id, first).imported_count == 1

    repeated = service.bulk_import_examples(
        workspace.id,
        _request([_item(1), _item(3), _item(3)], commit=True),
    )

    assert repeated.imported_count == 1
    assert repeated.duplicate_count == 2
    assert [item.status for item in repeated.items] == [
        "duplicate",
        "imported",
        "duplicate",
    ]
    assert len(service.list_examples(workspace.id)) == 2


def test_duplicate_detection_is_scoped_to_one_workspace(tmp_path):
    service = MathHarnessService(tmp_path)
    first = service.create_workspace(WorkspaceCreate(name="空间一"))
    second = service.create_workspace(WorkspaceCreate(name="空间二"))

    assert (
        service.bulk_import_examples(
            first.id,
            _request([_item(1)], commit=True),
        ).imported_count
        == 1
    )
    result = service.bulk_import_examples(
        second.id,
        _request([_item(1)], commit=True),
    )

    assert result.imported_count == 1
    assert result.duplicate_count == 0
    assert service.list_examples(first.id)[0].workspace_id == first.id
    assert service.list_examples(second.id)[0].workspace_id == second.id


def test_invalid_item_blocks_the_entire_commit(tmp_path):
    service = MathHarnessService(tmp_path)
    workspace = service.create_workspace(WorkspaceCreate(name="全有或全无"))
    invalid = _item(3)
    invalid["solution"] = ""

    result = service.bulk_import_examples(
        workspace.id,
        _request([_item(1), invalid], commit=True),
    )

    assert result.commit_requested is True
    assert result.can_commit is False
    assert result.committed is False
    assert result.invalid_count == 1
    assert result.imported_count == 0
    assert not service.list_examples(workspace.id)


def test_database_failure_rolls_back_every_item(tmp_path, monkeypatch):
    service = MathHarnessService(tmp_path)
    workspace = service.create_workspace(WorkspaceCreate(name="事务回滚"))
    original = WorkspaceStore.add_example
    call_count = 0

    def fail_second(self, example, method_drafts=None):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise RuntimeError("simulated storage failure")
        return original(self, example, method_drafts)

    monkeypatch.setattr(WorkspaceStore, "add_example", fail_second)

    with pytest.raises(RuntimeError, match="simulated storage failure"):
        service.bulk_import_examples(
            workspace.id,
            _request([_item(1), _item(3)], commit=True),
        )

    assert not service.list_examples(workspace.id)
    assert not service.list_methods(workspace.id)


class _UnexpectedExtractor:
    name = "unexpected"
    prompt_version = "test"

    def extract(self, problem: str, solution: str, method_hint: str | None):
        raise AssertionError("preflight must not call the configured extractor")


def test_preflight_never_calls_configured_extractor(tmp_path):
    service = MathHarnessService(tmp_path, extractor=_UnexpectedExtractor())
    workspace = service.create_workspace(WorkspaceCreate(name="无模型预检"))

    result = service.bulk_import_examples(
        workspace.id,
        _request([_item(1)], commit=False).model_copy(
            update={"extractor_policy": ImportExtractorPolicy.CONFIGURED}
        ),
    )

    assert result.ready_count == 1


class _CountingExtractor:
    name = "counting"
    prompt_version = "test"

    def __init__(self) -> None:
        self.calls = 0

    def extract(self, problem: str, solution: str, method_hint: str | None):
        self.calls += 1
        return MethodExtractionResult(
            trace=MethodExtractionTrace(
                provider=self.name,
                prompt_version=self.prompt_version,
                status=ExtractionStatus.SUCCESS,
            )
        )


def test_configured_extractor_runs_once_per_committed_item(tmp_path):
    extractor = _CountingExtractor()
    service = MathHarnessService(tmp_path, extractor=extractor)
    workspace = service.create_workspace(WorkspaceCreate(name="模型提炼"))

    result = service.bulk_import_examples(
        workspace.id,
        _request([_item(1), _item(3)], commit=True).model_copy(
            update={"extractor_policy": ImportExtractorPolicy.CONFIGURED}
        ),
    )

    assert result.imported_count == 2
    assert extractor.calls == 2


def test_bulk_import_api_reports_jsonl_line_errors_without_writes(tmp_path):
    client = TestClient(create_app(tmp_path))
    workspace = client.post("/workspaces", json={"name": "API import"}).json()
    content = json.dumps(_item(1), ensure_ascii=False) + "\n{broken"

    response = client.post(
        f"/workspaces/{workspace['id']}/example-imports",
        json={
            "content": content,
            "file_format": "jsonl",
            "source_name": "broken.jsonl",
            "commit": True,
        },
    )

    assert response.status_code == 200
    report = response.json()
    assert report["can_commit"] is False
    assert report["invalid_count"] == 1
    assert "source line 2" in report["items"][1]["errors"][0]
    assert client.get(f"/workspaces/{workspace['id']}/examples").json() == []


def test_import_limit_is_measured_in_utf8_bytes(tmp_path):
    service = MathHarnessService(tmp_path)
    workspace = service.create_workspace(WorkspaceCreate(name="字节限制"))
    request = BulkExampleImportRequest(
        content="数" * 2_000_000,
        source_name="oversized.jsonl",
    )

    with pytest.raises(InvalidPortableData, match="UTF-8 bytes"):
        service.bulk_import_examples(workspace.id, request)
