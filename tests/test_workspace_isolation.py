from __future__ import annotations

import pytest

from math_harness.errors import RecordNotFound
from math_harness.models import WorkspaceCreate
from math_harness.service import MathHarnessService


def test_workspaces_are_physically_and_logically_isolated(
    tmp_path, verified_asymptotic_example
):
    service = MathHarnessService(tmp_path)
    workspace_a = service.create_workspace(WorkspaceCreate(name="渐进估计"))
    workspace_b = service.create_workspace(WorkspaceCreate(name="线性代数"))

    result = service.ingest_example(workspace_a.id, verified_asymptotic_example)

    assert len(service.list_examples(workspace_a.id)) == 1
    assert len(service.list_methods(workspace_a.id)) >= 1
    assert service.list_examples(workspace_b.id) == []
    assert service.list_methods(workspace_b.id) == []
    assert service.workspaces.database_path(
        workspace_a.id
    ) != service.workspaces.database_path(workspace_b.id)

    with pytest.raises(RecordNotFound):
        service.get_example(workspace_b.id, result.example.id)


def test_workspace_database_rejects_cross_workspace_write(
    tmp_path, verified_asymptotic_example
):
    service = MathHarnessService(tmp_path)
    workspace_a = service.create_workspace(WorkspaceCreate(name="A"))
    workspace_b = service.create_workspace(WorkspaceCreate(name="B"))
    result = service.ingest_example(workspace_a.id, verified_asymptotic_example)

    store_b = service.workspaces.store(workspace_b.id)
    with pytest.raises(ValueError, match="cross-workspace"):
        store_b.add_example(result.example)
