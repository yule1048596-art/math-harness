from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import zipfile

import pytest
from fastapi.testclient import TestClient

from math_harness.api import create_app
from math_harness.errors import InvalidPortableData
from math_harness.models import (
    ExampleCreate,
    MathPayload,
    SolveRequest,
    WorkspaceCreate,
)
from math_harness.portability import (
    ARCHIVE_MEDIA_TYPE,
    DATABASE_NAME,
    MANIFEST_NAME,
)
from math_harness.service import MathHarnessService


def _verified_example() -> ExampleCreate:
    return ExampleCreate(
        problem="求 x→∞ 时 sqrt(x^2+x)-x 的渐进展开",
        solution="乘共轭式，再令 t=1/x 并做泰勒展开。",
        tags=["渐进估计", "根式"],
        reviewed=True,
        math_payload=MathPayload(
            expression="sqrt(x**2 + x) - x",
            expected="1/2 - 1/(8*x)",
            variable="x",
            point="oo",
            remainder_power=2,
        ),
    )


def _populated_service(tmp_path):
    service = MathHarnessService(tmp_path)
    workspace = service.create_workspace(
        WorkspaceCreate(name="渐进估计", description="需要长期保存的知识")
    )
    service.ingest_example(workspace.id, _verified_example())
    attempt = service.solve_problem(
        workspace.id,
        SolveRequest(
            problem="求 sqrt(x^2+3x)-x 的渐进展开",
            tags=["根式"],
            math_target={
                "expression": "sqrt(x**2 + 3*x) - x",
                "variable": "x",
                "point": "oo",
                "remainder_power": 2,
            },
        ),
    )
    return service, workspace, attempt


def test_backup_restore_round_trip_rebinds_every_workspace_reference(tmp_path):
    service, source, source_attempt = _populated_service(tmp_path)

    archive = service.export_workspace_backup(source.id)
    restored = service.restore_workspace_backup(archive)

    target = restored.workspace
    assert target.id != source.id
    assert target.name == "渐进估计（恢复）"
    assert target.description == source.description
    assert restored.source_workspace_id == source.id
    assert restored.archive_format_version == 1
    assert len(service.list_workspaces()) == 2

    examples = service.list_examples(target.id)
    methods = service.list_methods(target.id)
    attempts = service.list_solution_attempts(target.id)
    events = service.list_learning_events(target.id)
    assert examples and methods and attempts and events
    assert all(example.workspace_id == target.id for example in examples)
    assert all(method.workspace_id == target.id for method in methods)
    assert all(attempt.workspace_id == target.id for attempt in attempts)
    assert all(event.workspace_id == target.id for event in events)
    assert all(
        match.method.workspace_id == target.id
        for attempt in attempts
        for match in attempt.recommended_methods
    )
    assert any(attempt.id == source_attempt.id for attempt in attempts)

    # Restoring creates a copy and cannot mutate the source workspace.
    assert service.get_workspace(source.id).name == "渐进估计"
    assert all(
        example.workspace_id == source.id
        for example in service.list_examples(source.id)
    )


def test_archive_manifest_matches_database_digest_and_counts(tmp_path):
    service, workspace, _ = _populated_service(tmp_path)
    archive_bytes = service.export_workspace_backup(workspace.id)

    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        assert set(archive.namelist()) == {MANIFEST_NAME, DATABASE_NAME}
        manifest = json.loads(archive.read(MANIFEST_NAME))
        database = archive.read(DATABASE_NAME)

    assert manifest["kind"] == "math-harness-workspace"
    assert manifest["format_version"] == 1
    assert manifest["workspace"]["id"] == workspace.id
    assert manifest["database"]["sha256"] == hashlib.sha256(database).hexdigest()
    assert manifest["database"]["size"] == len(database)
    assert manifest["database"]["record_counts"]["examples"] >= 2


def test_restore_rejects_tampered_database_without_creating_workspace(tmp_path):
    service, workspace, _ = _populated_service(tmp_path)
    original = service.export_workspace_backup(workspace.id)
    with zipfile.ZipFile(io.BytesIO(original)) as archive:
        manifest = archive.read(MANIFEST_NAME)
        database = bytearray(archive.read(DATABASE_NAME))
    database[-1] ^= 0x01
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(MANIFEST_NAME, manifest)
        archive.writestr(DATABASE_NAME, database)

    with pytest.raises(InvalidPortableData, match="checksum"):
        service.restore_workspace_backup(output.getvalue())

    assert [item.id for item in service.list_workspaces()] == [workspace.id]


def test_restore_rejects_triggers_even_with_updated_checksum(tmp_path):
    service, workspace, _ = _populated_service(tmp_path)
    original = service.export_workspace_backup(workspace.id)
    with zipfile.ZipFile(io.BytesIO(original)) as archive:
        manifest = json.loads(archive.read(MANIFEST_NAME))
        database = archive.read(DATABASE_NAME)
    database_path = tmp_path / "tampered.sqlite3"
    database_path.write_bytes(database)
    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            "CREATE TRIGGER unsafe_trigger AFTER INSERT ON examples BEGIN SELECT 1; END"
        )
        connection.commit()
    finally:
        connection.close()
    tampered = database_path.read_bytes()
    manifest["database"]["sha256"] = hashlib.sha256(tampered).hexdigest()
    manifest["database"]["size"] = len(tampered)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(MANIFEST_NAME, json.dumps(manifest))
        archive.writestr(DATABASE_NAME, tampered)

    with pytest.raises(InvalidPortableData, match="triggers or views"):
        service.restore_workspace_backup(output.getvalue())


def test_backup_and_restore_api_use_binary_archive(tmp_path):
    client = TestClient(create_app(tmp_path))
    source = client.post(
        "/workspaces",
        json={"name": "API backup", "description": "round trip"},
    ).json()
    client.post(
        f"/workspaces/{source['id']}/examples",
        json=_verified_example().model_dump(mode="json"),
    )

    backup = client.get(f"/workspaces/{source['id']}/backup")

    assert backup.status_code == 200
    assert backup.headers["content-type"] == ARCHIVE_MEDIA_TYPE
    assert backup.headers["content-disposition"].endswith('.mathharness"')

    restore = client.post(
        "/workspace-restores",
        content=backup.content,
        headers={"Content-Type": ARCHIVE_MEDIA_TYPE},
    )

    assert restore.status_code == 200
    result = restore.json()
    assert result["source_workspace_id"] == source["id"]
    assert result["workspace"]["id"] != source["id"]
    assert result["restored_record_counts"]["examples"] == 1


def test_restore_api_rejects_non_archive(tmp_path):
    client = TestClient(create_app(tmp_path))

    response = client.post(
        "/workspace-restores",
        content=b"not a workspace archive",
        headers={"Content-Type": ARCHIVE_MEDIA_TYPE},
    )

    assert response.status_code == 400
    assert "invalid workspace archive" in response.json()["detail"]
