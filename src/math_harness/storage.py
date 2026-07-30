from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from math_harness.errors import RecordNotFound, WorkspaceNotFound
from math_harness.models import (
    EvaluationRun,
    KnowledgeStatus,
    LearningEvent,
    MethodCard,
    MethodDraft,
    ProblemExample,
    Workspace,
    WorkspaceCreate,
    utc_now,
)


def _dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class WorkspaceManager:
    """Registry plus one physical SQLite database per workspace."""

    def __init__(self, data_root: Path | str) -> None:
        self.data_root = Path(data_root).expanduser().resolve()
        self.workspaces_root = self.data_root / "workspaces"
        self.registry_path = self.data_root / "registry.sqlite3"
        self.workspaces_root.mkdir(parents=True, exist_ok=True)
        self._initialize_registry()

    @contextmanager
    def _registry_connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.registry_path)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize_registry(self) -> None:
        with self._registry_connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS workspaces (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )

    def create(self, request: WorkspaceCreate) -> Workspace:
        workspace = Workspace(
            id=str(uuid.uuid4()),
            name=request.name,
            description=request.description,
            created_at=utc_now(),
        )
        with self._registry_connection() as connection:
            connection.execute(
                "INSERT INTO workspaces (id, name, description, created_at) VALUES (?, ?, ?, ?)",
                (
                    workspace.id,
                    workspace.name,
                    workspace.description,
                    workspace.created_at.isoformat(),
                ),
            )
        WorkspaceStore(self.database_path(workspace.id), workspace.id)
        return workspace

    def get(self, workspace_id: str) -> Workspace:
        with self._registry_connection() as connection:
            row = connection.execute(
                "SELECT * FROM workspaces WHERE id = ?", (workspace_id,)
            ).fetchone()
        if row is None:
            raise WorkspaceNotFound(f"workspace not found: {workspace_id}")
        return Workspace(
            id=row["id"],
            name=row["name"],
            description=row["description"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def list(self) -> list[Workspace]:
        with self._registry_connection() as connection:
            rows = connection.execute(
                "SELECT * FROM workspaces ORDER BY created_at, id"
            ).fetchall()
        return [
            Workspace(
                id=row["id"],
                name=row["name"],
                description=row["description"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]

    def database_path(self, workspace_id: str) -> Path:
        try:
            uuid.UUID(workspace_id)
        except ValueError as exc:
            raise WorkspaceNotFound(f"invalid workspace id: {workspace_id}") from exc
        return self.workspaces_root / workspace_id / "workspace.sqlite3"

    def store(self, workspace_id: str) -> WorkspaceStore:
        self.get(workspace_id)
        return WorkspaceStore(self.database_path(workspace_id), workspace_id)


class WorkspaceStore:
    def __init__(self, database_path: Path, workspace_id: str) -> None:
        self.database_path = database_path
        self.workspace_id = workspace_id
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA journal_mode = WAL")
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self.connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS examples (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    problem TEXT NOT NULL,
                    solution TEXT NOT NULL,
                    tags_json TEXT NOT NULL,
                    method_hint TEXT,
                    problem_kind TEXT NOT NULL,
                    math_payload_json TEXT,
                    verification_json TEXT NOT NULL,
                    extraction_json TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    CHECK (workspace_id <> '')
                );

                CREATE INDEX IF NOT EXISTS idx_examples_workspace
                    ON examples(workspace_id, created_at);

                CREATE TABLE IF NOT EXISTS methods (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    method_key TEXT NOT NULL,
                    name TEXT NOT NULL,
                    goal TEXT NOT NULL,
                    applicable_json TEXT NOT NULL,
                    procedure_json TEXT NOT NULL,
                    failure_modes_json TEXT NOT NULL,
                    tags_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    success_count INTEGER NOT NULL,
                    failure_count INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(workspace_id, method_key),
                    CHECK (workspace_id <> '')
                );

                CREATE TABLE IF NOT EXISTS method_examples (
                    workspace_id TEXT NOT NULL,
                    method_id TEXT NOT NULL,
                    example_id TEXT NOT NULL,
                    PRIMARY KEY (workspace_id, method_id, example_id),
                    FOREIGN KEY (method_id) REFERENCES methods(id) ON DELETE CASCADE,
                    FOREIGN KEY (example_id) REFERENCES examples(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS learning_events (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS evaluation_runs (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    report_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_evaluation_runs_workspace
                    ON evaluation_runs(workspace_id, created_at);
                """
            )
            self._ensure_column(connection, "examples", "extraction_json", "TEXT")

    @staticmethod
    def _ensure_column(
        connection: sqlite3.Connection,
        table: str,
        column: str,
        definition: str,
    ) -> None:
        columns = {
            row["name"] for row in connection.execute(f"PRAGMA table_info({table})")
        }
        if column not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def add_example(self, example: ProblemExample) -> ProblemExample:
        self._assert_workspace(example.workspace_id)
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO examples (
                    id, workspace_id, problem, solution, tags_json, method_hint,
                    problem_kind, math_payload_json, verification_json, extraction_json,
                    status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    example.id,
                    example.workspace_id,
                    example.problem,
                    example.solution,
                    _dump(example.tags),
                    example.method_hint,
                    example.problem_kind.value,
                    (
                        _dump(example.math_payload.model_dump(mode="json"))
                        if example.math_payload
                        else None
                    ),
                    _dump(example.verification.model_dump(mode="json")),
                    (
                        _dump(example.extraction.model_dump(mode="json"))
                        if example.extraction
                        else None
                    ),
                    example.status.value,
                    example.created_at.isoformat(),
                ),
            )
            self._record_event(
                connection,
                "example_captured",
                example.id,
                {"status": example.status.value},
            )
        return example

    def get_example(self, example_id: str) -> ProblemExample:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM examples WHERE id = ? AND workspace_id = ?",
                (example_id, self.workspace_id),
            ).fetchone()
        if row is None:
            raise RecordNotFound(f"example not found in workspace: {example_id}")
        return self._row_to_example(row)

    def list_examples(self) -> list[ProblemExample]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM examples WHERE workspace_id = ? ORDER BY created_at, id",
                (self.workspace_id,),
            ).fetchall()
        return [self._row_to_example(row) for row in rows]

    def upsert_method(
        self,
        draft: MethodDraft,
        example_id: str,
        status: KnowledgeStatus,
        verified: bool,
    ) -> MethodCard:
        now = utc_now()
        with self.connection() as connection:
            existing = connection.execute(
                "SELECT * FROM methods WHERE workspace_id = ? AND method_key = ?",
                (self.workspace_id, draft.key),
            ).fetchone()
            if existing is None:
                method_id = str(uuid.uuid4())
                connection.execute(
                    """
                    INSERT INTO methods (
                        id, workspace_id, method_key, name, goal, applicable_json,
                        procedure_json, failure_modes_json, tags_json, status, version,
                        success_count, failure_count, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        method_id,
                        self.workspace_id,
                        draft.key,
                        draft.name,
                        draft.goal,
                        _dump(draft.applicable_when),
                        _dump(draft.procedure),
                        _dump(draft.failure_modes),
                        _dump(draft.tags),
                        status.value,
                        1,
                        1 if verified else 0,
                        0,
                        now.isoformat(),
                        now.isoformat(),
                    ),
                )
                event_type = "method_created"
            else:
                method_id = existing["id"]
                next_status = (
                    KnowledgeStatus.PROMOTED.value
                    if verified or existing["status"] == KnowledgeStatus.PROMOTED.value
                    else existing["status"]
                )
                connection.execute(
                    """
                    UPDATE methods
                    SET status = ?, version = version + 1,
                        success_count = success_count + ?,
                        updated_at = ?
                    WHERE id = ? AND workspace_id = ?
                    """,
                    (
                        next_status,
                        1 if verified else 0,
                        now.isoformat(),
                        method_id,
                        self.workspace_id,
                    ),
                )
                event_type = "method_updated"

            connection.execute(
                """
                INSERT OR IGNORE INTO method_examples
                    (workspace_id, method_id, example_id)
                VALUES (?, ?, ?)
                """,
                (self.workspace_id, method_id, example_id),
            )
            self._record_event(
                connection,
                event_type,
                method_id,
                {"example_id": example_id, "status": status.value},
            )

        return self.get_method(method_id)

    def update_method_status(
        self, method_id: str, status: KnowledgeStatus
    ) -> MethodCard:
        now = utc_now()
        with self.connection() as connection:
            existing = connection.execute(
                "SELECT status FROM methods WHERE id = ? AND workspace_id = ?",
                (method_id, self.workspace_id),
            ).fetchone()
            if existing is None:
                raise RecordNotFound(f"method not found in workspace: {method_id}")
            connection.execute(
                """
                UPDATE methods
                SET status = ?, version = version + 1, updated_at = ?
                WHERE id = ? AND workspace_id = ?
                """,
                (status.value, now.isoformat(), method_id, self.workspace_id),
            )
            self._record_event(
                connection,
                "method_status_changed",
                method_id,
                {"from": existing["status"], "to": status.value},
            )
        return self.get_method(method_id)

    def get_method(self, method_id: str) -> MethodCard:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM methods WHERE id = ? AND workspace_id = ?",
                (method_id, self.workspace_id),
            ).fetchone()
            if row is None:
                raise RecordNotFound(f"method not found in workspace: {method_id}")
            example_rows = connection.execute(
                """
                SELECT example_id FROM method_examples
                WHERE workspace_id = ? AND method_id = ?
                ORDER BY example_id
                """,
                (self.workspace_id, method_id),
            ).fetchall()
        return self._row_to_method(row, [item["example_id"] for item in example_rows])

    def list_methods(
        self, include_pending: bool = True, include_deprecated: bool = False
    ) -> list[MethodCard]:
        statuses = [KnowledgeStatus.PROMOTED.value]
        if include_pending:
            statuses.append(KnowledgeStatus.PENDING_REVIEW.value)
        if include_deprecated:
            statuses.append(KnowledgeStatus.DEPRECATED.value)
        placeholders = ",".join("?" for _ in statuses)
        with self.connection() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM methods
                WHERE workspace_id = ? AND status IN ({placeholders})
                ORDER BY status DESC, success_count DESC, updated_at DESC
                """,
                (self.workspace_id, *statuses),
            ).fetchall()
            result = []
            for row in rows:
                example_rows = connection.execute(
                    """
                    SELECT example_id FROM method_examples
                    WHERE workspace_id = ? AND method_id = ?
                    ORDER BY example_id
                    """,
                    (self.workspace_id, row["id"]),
                ).fetchall()
                result.append(
                    self._row_to_method(
                        row, [item["example_id"] for item in example_rows]
                    )
                )
        return result

    def list_learning_events(self) -> list[LearningEvent]:
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM learning_events
                WHERE workspace_id = ?
                ORDER BY created_at, id
                """,
                (self.workspace_id,),
            ).fetchall()
        return [
            LearningEvent(
                id=row["id"],
                workspace_id=row["workspace_id"],
                event_type=row["event_type"],
                target_id=row["target_id"],
                payload=json.loads(row["payload_json"]),
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]

    def add_evaluation(self, run: EvaluationRun) -> EvaluationRun:
        self._assert_workspace(run.workspace_id)
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO evaluation_runs (
                    id, workspace_id, report_json, created_at
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    run.id,
                    run.workspace_id,
                    _dump(run.model_dump(mode="json")),
                    run.created_at.isoformat(),
                ),
            )
            self._record_event(
                connection,
                "evaluation_completed",
                run.id,
                {
                    "name": run.name,
                    "top_k": run.top_k,
                    "metrics": run.metrics.model_dump(mode="json"),
                },
            )
        return run

    def list_evaluations(self) -> list[EvaluationRun]:
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT report_json FROM evaluation_runs
                WHERE workspace_id = ?
                ORDER BY created_at, id
                """,
                (self.workspace_id,),
            ).fetchall()
        return [
            EvaluationRun.model_validate(json.loads(row["report_json"])) for row in rows
        ]

    def _record_event(
        self,
        connection: sqlite3.Connection,
        event_type: str,
        target_id: str,
        payload: dict[str, object],
    ) -> None:
        connection.execute(
            """
            INSERT INTO learning_events (
                id, workspace_id, event_type, target_id, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                self.workspace_id,
                event_type,
                target_id,
                _dump(payload),
                utc_now().isoformat(),
            ),
        )

    def _assert_workspace(self, workspace_id: str) -> None:
        if workspace_id != self.workspace_id:
            raise ValueError("cross-workspace write rejected")

    @staticmethod
    def _row_to_example(row: sqlite3.Row) -> ProblemExample:
        payload = (
            json.loads(row["math_payload_json"]) if row["math_payload_json"] else None
        )
        return ProblemExample.model_validate(
            {
                "id": row["id"],
                "workspace_id": row["workspace_id"],
                "problem": row["problem"],
                "solution": row["solution"],
                "tags": json.loads(row["tags_json"]),
                "method_hint": row["method_hint"],
                "problem_kind": row["problem_kind"],
                "math_payload": payload,
                "verification": json.loads(row["verification_json"]),
                "extraction": (
                    json.loads(row["extraction_json"])
                    if row["extraction_json"]
                    else None
                ),
                "status": row["status"],
                "created_at": row["created_at"],
            }
        )

    @staticmethod
    def _row_to_method(row: sqlite3.Row, example_ids: list[str]) -> MethodCard:
        return MethodCard(
            id=row["id"],
            workspace_id=row["workspace_id"],
            key=row["method_key"],
            name=row["name"],
            goal=row["goal"],
            applicable_when=json.loads(row["applicable_json"]),
            procedure=json.loads(row["procedure_json"]),
            failure_modes=json.loads(row["failure_modes_json"]),
            tags=json.loads(row["tags_json"]),
            status=KnowledgeStatus(row["status"]),
            version=row["version"],
            success_count=row["success_count"],
            failure_count=row["failure_count"],
            example_ids=example_ids,
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )
