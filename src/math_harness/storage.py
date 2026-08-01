from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from math_harness.dedup import MergeCandidate, card_to_draft
from math_harness.errors import InvalidKnowledgeState, RecordNotFound, WorkspaceNotFound
from math_harness.merging import merge_method_content, sanitize_method_draft
from math_harness.models import (
    EvaluationRun,
    ExampleVersion,
    KnowledgeStatus,
    LearningEvent,
    MergeProposalStatus,
    MethodCard,
    MethodDraft,
    MethodExtractionTrace,
    MethodMergeProposal,
    MethodVersion,
    ProblemExample,
    SolutionAttempt,
    SolutionAttemptStatus,
    SolveEvaluationRun,
    Workspace,
    WorkspaceCreate,
    utc_now,
)
from math_harness.structure import MethodSignature, StructuralFeatures


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
                    reviewed INTEGER NOT NULL DEFAULT 0,
                    problem_kind TEXT NOT NULL,
                    math_payload_json TEXT,
                    verification_json TEXT NOT NULL,
                    extraction_json TEXT,
                    method_drafts_json TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL,
                    origin TEXT NOT NULL DEFAULT 'manual',
                    source_attempt_id TEXT,
                    reviewed_at TEXT,
                    reviewer_note TEXT NOT NULL DEFAULT '',
                    revision INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    CHECK (workspace_id <> '')
                );

                CREATE INDEX IF NOT EXISTS idx_examples_workspace
                    ON examples(workspace_id, created_at);

                CREATE TABLE IF NOT EXISTS example_versions (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    example_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    content_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(workspace_id, example_id, revision),
                    FOREIGN KEY (example_id) REFERENCES examples(id) ON DELETE CASCADE,
                    CHECK (workspace_id <> '')
                );

                CREATE INDEX IF NOT EXISTS idx_example_versions_example
                    ON example_versions(workspace_id, example_id, revision);

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
                    signature_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(workspace_id, method_key),
                    CHECK (workspace_id <> '')
                );

                CREATE TABLE IF NOT EXISTS method_versions (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    method_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    content_json TEXT NOT NULL,
                    source_example_id TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(workspace_id, method_id, version),
                    FOREIGN KEY (method_id) REFERENCES methods(id) ON DELETE CASCADE,
                    CHECK (workspace_id <> '')
                );

                CREATE INDEX IF NOT EXISTS idx_method_versions_method
                    ON method_versions(workspace_id, method_id, version);

                CREATE TABLE IF NOT EXISTS method_merge_proposals (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    primary_method_id TEXT NOT NULL,
                    duplicate_method_id TEXT NOT NULL,
                    score REAL NOT NULL,
                    detail_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    resolved_at TEXT,
                    UNIQUE(workspace_id, primary_method_id, duplicate_method_id),
                    FOREIGN KEY (primary_method_id)
                        REFERENCES methods(id) ON DELETE CASCADE,
                    FOREIGN KEY (duplicate_method_id)
                        REFERENCES methods(id) ON DELETE CASCADE,
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

                CREATE TABLE IF NOT EXISTS solution_attempts (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    correction_of TEXT,
                    report_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (correction_of)
                        REFERENCES solution_attempts(id) ON DELETE RESTRICT
                );

                CREATE INDEX IF NOT EXISTS idx_solution_attempts_workspace
                    ON solution_attempts(workspace_id, created_at);

                CREATE TABLE IF NOT EXISTS attempt_methods (
                    workspace_id TEXT NOT NULL,
                    attempt_id TEXT NOT NULL,
                    method_id TEXT NOT NULL,
                    method_key TEXT NOT NULL,
                    rank INTEGER NOT NULL,
                    score REAL NOT NULL,
                    used INTEGER NOT NULL,
                    PRIMARY KEY (workspace_id, attempt_id, method_id),
                    FOREIGN KEY (attempt_id)
                        REFERENCES solution_attempts(id) ON DELETE CASCADE,
                    FOREIGN KEY (method_id) REFERENCES methods(id) ON DELETE RESTRICT
                );

                CREATE TABLE IF NOT EXISTS solve_evaluation_runs (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    report_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_solve_evaluation_runs_workspace
                    ON solve_evaluation_runs(workspace_id, created_at);
                """
            )
            self._ensure_column(connection, "examples", "extraction_json", "TEXT")
            self._ensure_column(
                connection,
                "examples",
                "method_drafts_json",
                "TEXT NOT NULL DEFAULT '[]'",
            )
            self._ensure_column(
                connection,
                "examples",
                "reviewed",
                "INTEGER NOT NULL DEFAULT 0",
            )
            self._ensure_column(
                connection,
                "examples",
                "origin",
                "TEXT NOT NULL DEFAULT 'manual'",
            )
            self._ensure_column(connection, "examples", "source_attempt_id", "TEXT")
            self._ensure_column(connection, "examples", "reviewed_at", "TEXT")
            self._ensure_column(
                connection,
                "examples",
                "reviewer_note",
                "TEXT NOT NULL DEFAULT ''",
            )
            self._ensure_column(
                connection,
                "examples",
                "revision",
                "INTEGER NOT NULL DEFAULT 1",
            )
            self._ensure_column(connection, "examples", "updated_at", "TEXT")
            connection.execute(
                """
                UPDATE examples SET updated_at = created_at
                WHERE updated_at IS NULL OR updated_at = ''
                """
            )
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_examples_source_attempt
                ON examples(workspace_id, source_attempt_id)
                WHERE source_attempt_id IS NOT NULL
                """
            )
            self._ensure_column(
                connection,
                "methods",
                "signature_json",
                "TEXT NOT NULL DEFAULT '{}'",
            )

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

    def add_example(
        self,
        example: ProblemExample,
        method_drafts: list[MethodDraft] | None = None,
    ) -> ProblemExample:
        self._assert_workspace(example.workspace_id)
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO examples (
                    id, workspace_id, problem, solution, tags_json, method_hint,
                    reviewed, problem_kind, math_payload_json, verification_json,
                    extraction_json, method_drafts_json, status, origin,
                    source_attempt_id, reviewed_at, reviewer_note, revision,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    example.id,
                    example.workspace_id,
                    example.problem,
                    example.solution,
                    _dump(example.tags),
                    example.method_hint,
                    1 if example.reviewed else 0,
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
                    _dump(
                        [draft.model_dump(mode="json") for draft in method_drafts or []]
                    ),
                    example.status.value,
                    example.origin.value,
                    example.source_attempt_id,
                    example.reviewed_at.isoformat() if example.reviewed_at else None,
                    example.reviewer_note,
                    example.revision,
                    example.created_at.isoformat(),
                    example.updated_at.isoformat(),
                ),
            )
            self._record_event(
                connection,
                "example_captured",
                example.id,
                {
                    "status": example.status.value,
                    "reviewed": example.reviewed,
                    "origin": example.origin.value,
                    "source_attempt_id": example.source_attempt_id,
                },
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

    def list_example_versions(self, example_id: str) -> list[ExampleVersion]:
        # Preserve the same not-found semantics as the live example endpoint.
        self.get_example(example_id)
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT content_json FROM example_versions
                WHERE workspace_id = ? AND example_id = ?
                ORDER BY revision, id
                """,
                (self.workspace_id, example_id),
            ).fetchall()
        return [
            ExampleVersion.model_validate(json.loads(row["content_json"]))
            for row in rows
        ]

    def replace_example_draft(
        self,
        example: ProblemExample,
        method_drafts: list[MethodDraft],
        *,
        changed_fields: list[str],
    ) -> ProblemExample:
        """Replace an untrusted draft and snapshot the previous revision."""

        self._assert_workspace(example.workspace_id)
        now = utc_now()
        with self.connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM examples
                WHERE id = ? AND workspace_id = ?
                """,
                (example.id, self.workspace_id),
            ).fetchone()
            if row is None:
                raise RecordNotFound(f"example not found in workspace: {example.id}")
            previous = self._row_to_example(row)
            if example.revision != previous.revision + 1:
                raise InvalidKnowledgeState(
                    "知识草稿已被其他操作更新；请刷新后再保存。"
                )
            if previous.status is KnowledgeStatus.PROMOTED or previous.reviewed:
                raise InvalidKnowledgeState("已晋级或已复核的例题不能作为草稿编辑。")
            if previous.status is KnowledgeStatus.DEPRECATED or (
                previous.status is KnowledgeStatus.REJECTED
                and previous.reviewed_at is not None
            ):
                raise InvalidKnowledgeState("已人工驳回或废弃的例题不能继续编辑。")

            snapshot = ExampleVersion(
                example_id=previous.id,
                workspace_id=previous.workspace_id,
                revision=previous.revision,
                problem=previous.problem,
                solution=previous.solution,
                tags=previous.tags,
                method_hint=previous.method_hint,
                problem_kind=previous.problem_kind,
                math_payload=previous.math_payload,
                verification=previous.verification,
                extraction=previous.extraction,
                method_drafts=previous.method_drafts,
                status=previous.status,
                created_at=now,
            )
            connection.execute(
                """
                INSERT INTO example_versions (
                    id, workspace_id, example_id, revision, content_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    self.workspace_id,
                    example.id,
                    previous.revision,
                    _dump(snapshot.model_dump(mode="json")),
                    now.isoformat(),
                ),
            )

            linked_rows = connection.execute(
                """
                SELECT method_id FROM method_examples
                WHERE workspace_id = ? AND example_id = ?
                """,
                (self.workspace_id, example.id),
            ).fetchall()
            connection.execute(
                """
                DELETE FROM method_examples
                WHERE workspace_id = ? AND example_id = ?
                """,
                (self.workspace_id, example.id),
            )
            retired_method_ids: list[str] = []
            for linked in linked_rows:
                method_id = linked["method_id"]
                remaining = connection.execute(
                    """
                    SELECT 1 FROM method_examples
                    WHERE workspace_id = ? AND method_id = ? LIMIT 1
                    """,
                    (self.workspace_id, method_id),
                ).fetchone()
                if remaining is not None:
                    continue
                updated = connection.execute(
                    """
                    UPDATE methods
                    SET status = ?, version = version + 1, updated_at = ?
                    WHERE id = ? AND workspace_id = ?
                      AND status IN (?, ?)
                    """,
                    (
                        KnowledgeStatus.CAPTURED.value,
                        now.isoformat(),
                        method_id,
                        self.workspace_id,
                        KnowledgeStatus.PENDING_REVIEW.value,
                        KnowledgeStatus.CAPTURED.value,
                    ),
                )
                if updated.rowcount:
                    retired_method_ids.append(method_id)
                    self._record_event(
                        connection,
                        "orphan_pending_method_detached",
                        method_id,
                        {"edited_example_id": example.id},
                    )

            connection.execute(
                """
                UPDATE examples
                SET problem = ?, solution = ?, tags_json = ?, method_hint = ?,
                    reviewed = 0, problem_kind = ?, math_payload_json = ?,
                    verification_json = ?, extraction_json = ?,
                    method_drafts_json = ?, status = ?, reviewed_at = NULL,
                    reviewer_note = '', revision = ?, updated_at = ?
                WHERE id = ? AND workspace_id = ?
                """,
                (
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
                    _dump([draft.model_dump(mode="json") for draft in method_drafts]),
                    example.status.value,
                    example.revision,
                    example.updated_at.isoformat(),
                    example.id,
                    self.workspace_id,
                ),
            )
            self._record_event(
                connection,
                "example_draft_updated",
                example.id,
                {
                    "from_revision": previous.revision,
                    "to_revision": example.revision,
                    "changed_fields": changed_fields,
                    "verification_status": example.verification.status.value,
                    "retired_method_ids": retired_method_ids,
                },
            )
        return self.get_example(example.id)

    def get_example_by_source_attempt(self, attempt_id: str) -> ProblemExample | None:
        with self.connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM examples
                WHERE workspace_id = ? AND source_attempt_id = ?
                """,
                (self.workspace_id, attempt_id),
            ).fetchone()
        return self._row_to_example(row) if row is not None else None

    def get_example_method_drafts(self, example_id: str) -> list[MethodDraft]:
        with self.connection() as connection:
            row = connection.execute(
                """
                SELECT method_drafts_json FROM examples
                WHERE id = ? AND workspace_id = ?
                """,
                (example_id, self.workspace_id),
            ).fetchone()
        if row is None:
            raise RecordNotFound(f"example not found in workspace: {example_id}")
        return [
            MethodDraft.model_validate(item)
            for item in json.loads(row["method_drafts_json"] or "[]")
        ]

    def list_methods_for_example(self, example_id: str) -> list[MethodCard]:
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT methods.* FROM methods
                JOIN method_examples
                  ON method_examples.method_id = methods.id
                 AND method_examples.workspace_id = methods.workspace_id
                WHERE method_examples.workspace_id = ?
                  AND method_examples.example_id = ?
                ORDER BY methods.created_at, methods.id
                """,
                (self.workspace_id, example_id),
            ).fetchall()
        return [self.get_method(row["id"]) for row in rows]

    def complete_example_review(
        self,
        example_id: str,
        reviewer_note: str,
        *,
        expected_revision: int,
        extraction: MethodExtractionTrace | None = None,
        method_drafts: list[MethodDraft] | None = None,
    ) -> ProblemExample:
        now = utc_now()
        with self.connection() as connection:
            row = connection.execute(
                """
                SELECT status, revision FROM examples
                WHERE id = ? AND workspace_id = ?
                """,
                (example_id, self.workspace_id),
            ).fetchone()
            if row is None:
                raise RecordNotFound(f"example not found in workspace: {example_id}")
            if row["revision"] != expected_revision:
                raise InvalidKnowledgeState(
                    "知识草稿已被其他操作更新；请刷新并重新复核。"
                )
            fields = [
                "reviewed = 1",
                "status = ?",
                "reviewed_at = ?",
                "reviewer_note = ?",
                "updated_at = ?",
            ]
            values: list[object] = [
                KnowledgeStatus.PROMOTED.value,
                now.isoformat(),
                reviewer_note,
                now.isoformat(),
            ]
            if extraction is not None:
                fields.append("extraction_json = ?")
                values.append(_dump(extraction.model_dump(mode="json")))
            if method_drafts is not None:
                fields.append("method_drafts_json = ?")
                values.append(
                    _dump([draft.model_dump(mode="json") for draft in method_drafts])
                )
            values.extend([example_id, self.workspace_id])
            connection.execute(
                f"""
                UPDATE examples SET {", ".join(fields)}
                WHERE id = ? AND workspace_id = ?
                """,
                values,
            )
            self._record_event(
                connection,
                "example_review_approved",
                example_id,
                {
                    "from": row["status"],
                    "to": KnowledgeStatus.PROMOTED.value,
                    "reviewer_note": reviewer_note,
                },
            )
        return self.get_example(example_id)

    def reject_example(self, example_id: str, reviewer_note: str) -> ProblemExample:
        """Reject a draft and retire pending methods that have no evidence left."""

        now = utc_now()
        with self.connection() as connection:
            row = connection.execute(
                """
                SELECT status FROM examples
                WHERE id = ? AND workspace_id = ?
                """,
                (example_id, self.workspace_id),
            ).fetchone()
            if row is None:
                raise RecordNotFound(f"example not found in workspace: {example_id}")
            linked_rows = connection.execute(
                """
                SELECT method_id FROM method_examples
                WHERE workspace_id = ? AND example_id = ?
                """,
                (self.workspace_id, example_id),
            ).fetchall()
            connection.execute(
                """
                DELETE FROM method_examples
                WHERE workspace_id = ? AND example_id = ?
                """,
                (self.workspace_id, example_id),
            )
            retired_method_ids: list[str] = []
            for linked in linked_rows:
                method_id = linked["method_id"]
                remaining = connection.execute(
                    """
                    SELECT 1 FROM method_examples
                    WHERE workspace_id = ? AND method_id = ? LIMIT 1
                    """,
                    (self.workspace_id, method_id),
                ).fetchone()
                if remaining is not None:
                    continue
                updated = connection.execute(
                    """
                    UPDATE methods
                    SET status = ?, version = version + 1, updated_at = ?
                    WHERE id = ? AND workspace_id = ?
                      AND status IN (?, ?)
                    """,
                    (
                        KnowledgeStatus.REJECTED.value,
                        now.isoformat(),
                        method_id,
                        self.workspace_id,
                        KnowledgeStatus.PENDING_REVIEW.value,
                        KnowledgeStatus.CAPTURED.value,
                    ),
                )
                if updated.rowcount:
                    retired_method_ids.append(method_id)
                    self._record_event(
                        connection,
                        "orphan_pending_method_rejected",
                        method_id,
                        {"rejected_example_id": example_id},
                    )
            connection.execute(
                """
                UPDATE examples
                SET reviewed = 0, status = ?, reviewed_at = ?, reviewer_note = ?,
                    updated_at = ?
                WHERE id = ? AND workspace_id = ?
                """,
                (
                    KnowledgeStatus.REJECTED.value,
                    now.isoformat(),
                    reviewer_note,
                    now.isoformat(),
                    example_id,
                    self.workspace_id,
                ),
            )
            self._record_event(
                connection,
                "example_review_rejected",
                example_id,
                {
                    "from": row["status"],
                    "to": KnowledgeStatus.REJECTED.value,
                    "reviewer_note": reviewer_note,
                    "retired_method_ids": retired_method_ids,
                },
            )
        return self.get_example(example_id)

    def record_learning_event(
        self,
        event_type: str,
        target_id: str,
        payload: dict[str, object],
    ) -> None:
        with self.connection() as connection:
            self._record_event(connection, event_type, target_id, payload)

    def upsert_method(
        self,
        draft: MethodDraft,
        example_id: str,
        status: KnowledgeStatus,
        verified: bool,
        features: StructuralFeatures | None = None,
        *,
        idempotent_evidence: bool = False,
    ) -> MethodCard:
        """Create or evolve a method without letting unreviewed data cross trust levels.

        ``verified`` means the caller approved promotion (machine verification plus
        human review), not merely that SymPy accepted the example. Pending cards may
        collect pending evidence, but promoted or manually retired cards are immutable
        to untrusted ingestion.
        """

        now = utc_now()
        draft = sanitize_method_draft(draft)
        ignored = False
        with self.connection() as connection:
            existing = connection.execute(
                "SELECT * FROM methods WHERE workspace_id = ? AND method_key = ?",
                (self.workspace_id, draft.key),
            ).fetchone()
            if existing is None:
                method_id = str(uuid.uuid4())
                signature = (
                    MethodSignature().accumulate(features)
                    if verified and features is not None
                    else MethodSignature()
                )
                initial_status = (
                    KnowledgeStatus.PROMOTED
                    if verified
                    else KnowledgeStatus.PENDING_REVIEW
                )
                connection.execute(
                    """
                    INSERT INTO methods (
                        id, workspace_id, method_key, name, goal, applicable_json,
                        procedure_json, failure_modes_json, tags_json, status, version,
                        success_count, failure_count, signature_json,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        initial_status.value,
                        1,
                        1 if verified else 0,
                        0,
                        signature.model_dump_json(),
                        now.isoformat(),
                        now.isoformat(),
                    ),
                )
                event_type = "method_created"
                event_extra: dict[str, object] = {}
                event_status = initial_status
            else:
                method_id = existing["id"]
                current = self._row_to_method(existing, [])
                current_status = KnowledgeStatus(existing["status"])
                already_linked = (
                    connection.execute(
                        """
                        SELECT 1 FROM method_examples
                        WHERE workspace_id = ? AND method_id = ? AND example_id = ?
                        """,
                        (self.workspace_id, method_id, example_id),
                    ).fetchone()
                    is not None
                )
                protected = {
                    KnowledgeStatus.PROMOTED,
                    KnowledgeStatus.REJECTED,
                    KnowledgeStatus.DEPRECATED,
                }
                retired = {
                    KnowledgeStatus.REJECTED,
                    KnowledgeStatus.DEPRECATED,
                }

                if (
                    idempotent_evidence
                    and verified
                    and already_linked
                    and current_status is KnowledgeStatus.PROMOTED
                ):
                    ignored = True
                    self._record_event(
                        connection,
                        "duplicate_verified_evidence_ignored",
                        method_id,
                        {"example_id": example_id},
                    )
                elif (not verified and current_status in protected) or (
                    verified and current_status in retired
                ):
                    ignored = True
                    event_type = (
                        "untrusted_method_update_ignored"
                        if not verified
                        else "inactive_method_update_ignored"
                    )
                    self._record_event(
                        connection,
                        event_type,
                        method_id,
                        {
                            "example_id": example_id,
                            "existing_status": current_status.value,
                            "requested_status": status.value,
                        },
                    )
                elif verified and current_status in {
                    KnowledgeStatus.PENDING_REVIEW,
                    KnowledgeStatus.CAPTURED,
                }:
                    # The pending content may be entirely model-derived. The first
                    # trusted sample establishes the canonical card instead of
                    # inheriting that untrusted first draft.
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO method_versions (
                            id, workspace_id, method_id, version, content_json,
                            source_example_id, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            str(uuid.uuid4()),
                            self.workspace_id,
                            method_id,
                            existing["version"],
                            _dump(
                                {
                                    "name": current.name,
                                    "goal": current.goal,
                                    "applicable_when": current.applicable_when,
                                    "procedure": current.procedure,
                                    "failure_modes": current.failure_modes,
                                    "tags": current.tags,
                                }
                            ),
                            example_id,
                            now.isoformat(),
                        ),
                    )
                    signature = (
                        MethodSignature().accumulate(features)
                        if features is not None
                        else MethodSignature()
                    )
                    connection.execute(
                        """
                        UPDATE methods
                        SET status = ?, version = version + 1,
                            success_count = success_count + ?,
                            name = ?, goal = ?, applicable_json = ?,
                            procedure_json = ?, failure_modes_json = ?, tags_json = ?,
                            signature_json = ?, updated_at = ?
                        WHERE id = ? AND workspace_id = ?
                        """,
                        (
                            KnowledgeStatus.PROMOTED.value,
                            1,
                            draft.name,
                            draft.goal,
                            _dump(draft.applicable_when),
                            _dump(draft.procedure),
                            _dump(draft.failure_modes),
                            _dump(draft.tags),
                            signature.model_dump_json(),
                            now.isoformat(),
                            method_id,
                            self.workspace_id,
                        ),
                    )
                    connection.execute(
                        "DELETE FROM method_examples WHERE workspace_id = ? AND method_id = ?",
                        (self.workspace_id, method_id),
                    )
                    event_type = "pending_method_promoted"
                    event_extra = {"replaced_pending_content": True}
                    event_status = KnowledgeStatus.PROMOTED
                else:
                    previous_signature = MethodSignature.model_validate_json(
                        existing["signature_json"] or "{}"
                    )
                    signature = (
                        previous_signature.accumulate(features)
                        if verified and features is not None
                        else previous_signature
                    )
                    next_status = (
                        KnowledgeStatus.PROMOTED
                        if verified
                        else (
                            KnowledgeStatus.PENDING_REVIEW
                            if current_status is KnowledgeStatus.CAPTURED
                            else current_status
                        )
                    )
                    merged = merge_method_content(current, draft)
                    if merged.changed:
                        # Snapshot the content before a trusted or pending rewrite.
                        connection.execute(
                            """
                            INSERT OR IGNORE INTO method_versions (
                                id, workspace_id, method_id, version, content_json,
                                source_example_id, created_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                str(uuid.uuid4()),
                                self.workspace_id,
                                method_id,
                                existing["version"],
                                _dump(
                                    {
                                        "name": current.name,
                                        "goal": current.goal,
                                        "applicable_when": current.applicable_when,
                                        "procedure": current.procedure,
                                        "failure_modes": current.failure_modes,
                                        "tags": current.tags,
                                    }
                                ),
                                example_id,
                                now.isoformat(),
                            ),
                        )
                        connection.execute(
                            """
                            UPDATE methods
                            SET status = ?, version = version + 1,
                                success_count = success_count + ?,
                                name = ?, goal = ?, applicable_json = ?,
                                procedure_json = ?, failure_modes_json = ?, tags_json = ?,
                                signature_json = ?, updated_at = ?
                            WHERE id = ? AND workspace_id = ?
                            """,
                            (
                                next_status.value,
                                1 if verified else 0,
                                merged.name,
                                merged.goal,
                                _dump(merged.applicable_when),
                                _dump(merged.procedure),
                                _dump(merged.failure_modes),
                                _dump(merged.tags),
                                signature.model_dump_json(),
                                now.isoformat(),
                                method_id,
                                self.workspace_id,
                            ),
                        )
                        event_type = "method_content_evolved"
                        event_extra = {"added": merged.added}
                    else:
                        connection.execute(
                            """
                            UPDATE methods
                            SET status = ?, version = version + 1,
                                success_count = success_count + ?,
                                signature_json = ?, updated_at = ?
                            WHERE id = ? AND workspace_id = ?
                            """,
                            (
                                next_status.value,
                                1 if verified else 0,
                                signature.model_dump_json(),
                                now.isoformat(),
                                method_id,
                                self.workspace_id,
                            ),
                        )
                        event_type = "method_updated"
                        event_extra = {}
                    event_status = next_status

            if not ignored:
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
                    {
                        "example_id": example_id,
                        "status": event_status.value,
                        **event_extra,
                    },
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

    def list_method_versions(self, method_id: str) -> list[MethodVersion]:
        """按版本升序返回该方法卡被改写前的历史快照。"""

        with self.connection() as connection:
            if (
                connection.execute(
                    "SELECT 1 FROM methods WHERE id = ? AND workspace_id = ?",
                    (method_id, self.workspace_id),
                ).fetchone()
                is None
            ):
                raise RecordNotFound(f"method not found in workspace: {method_id}")
            rows = connection.execute(
                """
                SELECT * FROM method_versions
                WHERE workspace_id = ? AND method_id = ?
                ORDER BY version
                """,
                (self.workspace_id, method_id),
            ).fetchall()
        return [
            MethodVersion(
                method_id=row["method_id"],
                workspace_id=row["workspace_id"],
                version=row["version"],
                source_example_id=row["source_example_id"],
                created_at=datetime.fromisoformat(row["created_at"]),
                **json.loads(row["content_json"]),
            )
            for row in rows
        ]

    def record_merge_proposals(
        self, candidates: list[MergeCandidate]
    ) -> list[MethodMergeProposal]:
        """Write unordered duplicate pairs while preserving human resolutions.

        A successful merge makes neighbouring graph edges stale. A later scan may
        reactivate a still-valid stale edge with its current orientation and score;
        applied and rejected decisions remain immutable.
        """

        now = utc_now()
        with self.connection() as connection:
            for candidate in candidates:
                detail = _dump(
                    {
                        "primary_key": candidate.primary_key,
                        "duplicate_key": candidate.duplicate_key,
                        "signature_similarity": candidate.signature_similarity,
                        "text_similarity": candidate.text_similarity,
                        "reasons": candidate.reasons,
                    }
                )
                existing_rows = connection.execute(
                    """
                    SELECT id, status, primary_method_id, duplicate_method_id
                    FROM method_merge_proposals
                    WHERE workspace_id = ? AND (
                        (primary_method_id = ? AND duplicate_method_id = ?)
                        OR (primary_method_id = ? AND duplicate_method_id = ?)
                    )
                    ORDER BY created_at, id
                    """,
                    (
                        self.workspace_id,
                        candidate.primary_id,
                        candidate.duplicate_id,
                        candidate.duplicate_id,
                        candidate.primary_id,
                    ),
                ).fetchall()
                if any(
                    row["status"]
                    in {
                        MergeProposalStatus.APPLIED.value,
                        MergeProposalStatus.REJECTED.value,
                    }
                    for row in existing_rows
                ):
                    # A human decision applies to the unordered pair even if an
                    # older release once stored the reverse orientation separately.
                    continue
                if not existing_rows:
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO method_merge_proposals (
                            id, workspace_id, primary_method_id, duplicate_method_id,
                            score, detail_json, status, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            str(uuid.uuid4()),
                            self.workspace_id,
                            candidate.primary_id,
                            candidate.duplicate_id,
                            candidate.score,
                            detail,
                            MergeProposalStatus.PENDING.value,
                            now.isoformat(),
                        ),
                    )
                else:
                    existing = next(
                        (
                            row
                            for row in existing_rows
                            if row["primary_method_id"] == candidate.primary_id
                            and row["duplicate_method_id"] == candidate.duplicate_id
                        ),
                        existing_rows[0],
                    )
                    connection.execute(
                        """
                        UPDATE method_merge_proposals
                        SET status = ?, resolved_at = ?
                        WHERE workspace_id = ? AND id <> ? AND status IN (?, ?)
                          AND (
                            (primary_method_id = ? AND duplicate_method_id = ?)
                            OR (primary_method_id = ? AND duplicate_method_id = ?)
                          )
                        """,
                        (
                            MergeProposalStatus.STALE.value,
                            now.isoformat(),
                            self.workspace_id,
                            existing["id"],
                            MergeProposalStatus.PENDING.value,
                            MergeProposalStatus.STALE.value,
                            candidate.primary_id,
                            candidate.duplicate_id,
                            candidate.duplicate_id,
                            candidate.primary_id,
                        ),
                    )
                    connection.execute(
                        """
                        UPDATE method_merge_proposals
                        SET primary_method_id = ?, duplicate_method_id = ?,
                            score = ?, detail_json = ?, status = ?, resolved_at = NULL
                        WHERE id = ? AND workspace_id = ?
                        """,
                        (
                            candidate.primary_id,
                            candidate.duplicate_id,
                            candidate.score,
                            detail,
                            MergeProposalStatus.PENDING.value,
                            existing["id"],
                            self.workspace_id,
                        ),
                    )
        return self.list_merge_proposals()

    def list_merge_proposals(
        self, status: MergeProposalStatus | None = None
    ) -> list[MethodMergeProposal]:
        query = "SELECT * FROM method_merge_proposals WHERE workspace_id = ?"
        parameters: list[object] = [self.workspace_id]
        if status is not None:
            query += " AND status = ?"
            parameters.append(status.value)
        query += " ORDER BY score DESC, created_at, id"
        with self.connection() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [self._row_to_proposal(row) for row in rows]

    def get_merge_proposal(self, proposal_id: str) -> MethodMergeProposal:
        with self.connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM method_merge_proposals
                WHERE id = ? AND workspace_id = ?
                """,
                (proposal_id, self.workspace_id),
            ).fetchone()
        if row is None:
            raise RecordNotFound(f"merge proposal not found: {proposal_id}")
        return self._row_to_proposal(row)

    def resolve_merge_proposal(
        self, proposal_id: str, status: MergeProposalStatus
    ) -> MethodMergeProposal:
        proposal = self.get_merge_proposal(proposal_id)
        if proposal.status is not MergeProposalStatus.PENDING:
            raise ValueError(
                f"merge proposal already resolved: {proposal.status.value}"
            )
        with self.connection() as connection:
            connection.execute(
                """
                UPDATE method_merge_proposals
                SET status = ?, resolved_at = ?
                WHERE id = ? AND workspace_id = ?
                """,
                (status.value, utc_now().isoformat(), proposal_id, self.workspace_id),
            )
        return self.get_merge_proposal(proposal_id)

    def apply_merge_proposal(self, proposal_id: str) -> MethodCard:
        """把副卡并入主卡。副卡置为 deprecated 而非删除。

        不删除既是审计要求，也是技术必需：`attempt_methods` 的外键是
        `ON DELETE RESTRICT`，被尝试记录引用过的方法卡本来就删不掉。
        """

        now = utc_now()
        stale_error: str | None = None
        primary_id: str | None = None

        with self.connection() as connection:
            # Serialize proposal resolution and re-read every decision input inside
            # the lock. This prevents two overlapping graph edges from both applying.
            connection.execute("BEGIN IMMEDIATE")
            proposal_row = connection.execute(
                """
                SELECT * FROM method_merge_proposals
                WHERE id = ? AND workspace_id = ?
                """,
                (proposal_id, self.workspace_id),
            ).fetchone()
            if proposal_row is None:
                raise RecordNotFound(f"merge proposal not found: {proposal_id}")
            proposal = self._row_to_proposal(proposal_row)
            if proposal.status is not MergeProposalStatus.PENDING:
                raise ValueError(
                    f"merge proposal already resolved: {proposal.status.value}"
                )

            method_rows = connection.execute(
                """
                SELECT * FROM methods
                WHERE workspace_id = ? AND id IN (?, ?)
                """,
                (
                    self.workspace_id,
                    proposal.primary_method_id,
                    proposal.duplicate_method_id,
                ),
            ).fetchall()
            methods_by_id = {
                row["id"]: self._row_to_method(row, []) for row in method_rows
            }
            if len(methods_by_id) != 2:
                raise RecordNotFound("one or more merge methods no longer exist")
            primary = methods_by_id[proposal.primary_method_id]
            duplicate = methods_by_id[proposal.duplicate_method_id]

            inactive = {KnowledgeStatus.REJECTED, KnowledgeStatus.DEPRECATED}
            if primary.status in inactive or duplicate.status in inactive:
                connection.execute(
                    """
                    UPDATE method_merge_proposals
                    SET status = ?, resolved_at = ?
                    WHERE id = ? AND workspace_id = ? AND status = ?
                    """,
                    (
                        MergeProposalStatus.STALE.value,
                        now.isoformat(),
                        proposal_id,
                        self.workspace_id,
                        MergeProposalStatus.PENDING.value,
                    ),
                )
                connection.execute(
                    """
                    UPDATE method_merge_proposals
                    SET status = ?, resolved_at = ?
                    WHERE workspace_id = ? AND status = ?
                      AND (
                        primary_method_id IN (?, ?)
                        OR duplicate_method_id IN (?, ?)
                      )
                    """,
                    (
                        MergeProposalStatus.STALE.value,
                        now.isoformat(),
                        self.workspace_id,
                        MergeProposalStatus.PENDING.value,
                        primary.id,
                        duplicate.id,
                        primary.id,
                        duplicate.id,
                    ),
                )
                stale_error = "merge proposal became stale because a method is inactive"
            else:
                if (
                    primary.status is not KnowledgeStatus.PROMOTED
                    and duplicate.status is KnowledgeStatus.PROMOTED
                ):
                    # Protect existing v0.5.0 proposals whose stored orientation
                    # predates the promoted-first ordering rule.
                    primary, duplicate = duplicate, primary
                merged = merge_method_content(primary, card_to_draft(duplicate))
                signature = MethodSignature.model_validate(
                    primary.signature or {}
                ).combined_with(
                    MethodSignature.model_validate(duplicate.signature or {})
                )
                primary_id = primary.id

                connection.execute(
                    """
                    INSERT OR IGNORE INTO method_versions (
                        id, workspace_id, method_id, version, content_json,
                        source_example_id, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        self.workspace_id,
                        primary.id,
                        primary.version,
                        _dump(
                            {
                                "name": primary.name,
                                "goal": primary.goal,
                                "applicable_when": primary.applicable_when,
                                "procedure": primary.procedure,
                                "failure_modes": primary.failure_modes,
                                "tags": primary.tags,
                            }
                        ),
                        None,
                        now.isoformat(),
                    ),
                )
                connection.execute(
                    """
                    UPDATE methods
                    SET name = ?, goal = ?, applicable_json = ?, procedure_json = ?,
                        failure_modes_json = ?, tags_json = ?, signature_json = ?,
                        success_count = success_count + ?,
                        failure_count = failure_count + ?,
                        version = version + 1, updated_at = ?
                    WHERE id = ? AND workspace_id = ?
                    """,
                    (
                        merged.name,
                        merged.goal,
                        _dump(merged.applicable_when),
                        _dump(merged.procedure),
                        _dump(merged.failure_modes),
                        _dump(merged.tags),
                        signature.model_dump_json(),
                        duplicate.success_count,
                        duplicate.failure_count,
                        now.isoformat(),
                        primary.id,
                        self.workspace_id,
                    ),
                )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO method_examples
                        (workspace_id, method_id, example_id)
                    SELECT workspace_id, ?, example_id FROM method_examples
                    WHERE workspace_id = ? AND method_id = ?
                    """,
                    (primary.id, self.workspace_id, duplicate.id),
                )
                connection.execute(
                    "DELETE FROM method_examples WHERE workspace_id = ? AND method_id = ?",
                    (self.workspace_id, duplicate.id),
                )
                connection.execute(
                    """
                    UPDATE methods
                    SET status = ?, version = version + 1, updated_at = ?
                    WHERE id = ? AND workspace_id = ?
                    """,
                    (
                        KnowledgeStatus.DEPRECATED.value,
                        now.isoformat(),
                        duplicate.id,
                        self.workspace_id,
                    ),
                )
                connection.execute(
                    """
                    UPDATE method_merge_proposals
                    SET status = ?, resolved_at = ?
                    WHERE id = ? AND workspace_id = ?
                    """,
                    (
                        MergeProposalStatus.APPLIED.value,
                        now.isoformat(),
                        proposal_id,
                        self.workspace_id,
                    ),
                )
                connection.execute(
                    """
                    UPDATE method_merge_proposals
                    SET status = ?, resolved_at = ?
                    WHERE workspace_id = ? AND id <> ? AND status = ?
                      AND (
                        primary_method_id IN (?, ?)
                        OR duplicate_method_id IN (?, ?)
                      )
                    """,
                    (
                        MergeProposalStatus.STALE.value,
                        now.isoformat(),
                        self.workspace_id,
                        proposal_id,
                        MergeProposalStatus.PENDING.value,
                        primary.id,
                        duplicate.id,
                        primary.id,
                        duplicate.id,
                    ),
                )
                self._record_event(
                    connection,
                    "methods_merged",
                    primary.id,
                    {
                        "primary_key": primary.key,
                        "duplicate_key": duplicate.key,
                        "duplicate_method_id": duplicate.id,
                        "score": proposal.score,
                    },
                )
        if stale_error is not None:
            raise ValueError(stale_error)
        if primary_id is None:  # pragma: no cover - guarded by the branches above
            raise RuntimeError("merge completed without a primary method")
        return self.get_method(primary_id)

    @staticmethod
    def _row_to_proposal(row: sqlite3.Row) -> MethodMergeProposal:
        detail = json.loads(row["detail_json"])
        return MethodMergeProposal(
            id=row["id"],
            workspace_id=row["workspace_id"],
            primary_method_id=row["primary_method_id"],
            primary_key=detail["primary_key"],
            duplicate_method_id=row["duplicate_method_id"],
            duplicate_key=detail["duplicate_key"],
            score=row["score"],
            signature_similarity=detail["signature_similarity"],
            text_similarity=detail["text_similarity"],
            reasons=detail["reasons"],
            status=MergeProposalStatus(row["status"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            resolved_at=(
                datetime.fromisoformat(row["resolved_at"])
                if row["resolved_at"]
                else None
            ),
        )

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

    def add_solution_attempt(self, attempt: SolutionAttempt) -> SolutionAttempt:
        """Persist an immutable attempt and atomically apply method feedback."""

        self._assert_workspace(attempt.workspace_id)
        used_keys = (
            set(attempt.candidate.used_method_keys) if attempt.candidate else set()
        )
        feedback_keys = set(attempt.feedback_method_keys)
        feedback_delta = 0
        if attempt.status is SolutionAttemptStatus.VERIFIED:
            feedback_delta = 1
        elif attempt.status is SolutionAttemptStatus.REJECTED:
            feedback_delta = -1

        with self.connection() as connection:
            if attempt.correction_of is not None:
                parent = connection.execute(
                    """
                    SELECT id FROM solution_attempts
                    WHERE id = ? AND workspace_id = ?
                    """,
                    (attempt.correction_of, self.workspace_id),
                ).fetchone()
                if parent is None:
                    raise RecordNotFound(
                        "correction parent not found in workspace: "
                        f"{attempt.correction_of}"
                    )

            connection.execute(
                """
                INSERT INTO solution_attempts (
                    id, workspace_id, status, correction_of, report_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    attempt.id,
                    attempt.workspace_id,
                    attempt.status.value,
                    attempt.correction_of,
                    _dump(attempt.model_dump(mode="json")),
                    attempt.created_at.isoformat(),
                ),
            )

            credited_method_ids: set[str] = set()
            for rank, match in enumerate(attempt.recommended_methods, start=1):
                if match.method.workspace_id != self.workspace_id:
                    raise ValueError("cross-workspace method feedback rejected")
                exists = connection.execute(
                    """
                    SELECT id FROM methods
                    WHERE id = ? AND workspace_id = ?
                    """,
                    (match.method.id, self.workspace_id),
                ).fetchone()
                if exists is None:
                    raise RecordNotFound(
                        f"method not found in workspace: {match.method.id}"
                    )
                used = match.method.key in used_keys
                connection.execute(
                    """
                    INSERT INTO attempt_methods (
                        workspace_id, attempt_id, method_id, method_key,
                        rank, score, used
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        self.workspace_id,
                        attempt.id,
                        match.method.id,
                        match.method.key,
                        rank,
                        match.score,
                        1 if used else 0,
                    ),
                )
                if (
                    match.method.key in feedback_keys
                    and feedback_delta
                    and match.method.id not in credited_method_ids
                ):
                    credited_method_ids.add(match.method.id)
                    success_delta = 1 if feedback_delta > 0 else 0
                    failure_delta = 1 if feedback_delta < 0 else 0
                    connection.execute(
                        """
                        UPDATE methods
                        SET success_count = success_count + ?,
                            failure_count = failure_count + ?,
                            updated_at = ?
                        WHERE id = ? AND workspace_id = ?
                        """,
                        (
                            success_delta,
                            failure_delta,
                            attempt.created_at.isoformat(),
                            match.method.id,
                            self.workspace_id,
                        ),
                    )
                    self._record_event(
                        connection,
                        "method_outcome_recorded",
                        match.method.id,
                        {
                            "attempt_id": attempt.id,
                            "method_key": match.method.key,
                            "outcome": attempt.status.value,
                        },
                    )

            self._record_event(
                connection,
                "solution_attempt_recorded",
                attempt.id,
                {
                    "status": attempt.status.value,
                    "verification_status": attempt.verification.status.value,
                    "generation_provider": attempt.generation.provider,
                    "used_method_keys": sorted(used_keys),
                    "feedback_method_keys": sorted(feedback_keys),
                    "credited_method_count": len(credited_method_ids),
                    "correction_of": attempt.correction_of,
                },
            )
        return attempt

    def get_solution_attempt(self, attempt_id: str) -> SolutionAttempt:
        with self.connection() as connection:
            row = connection.execute(
                """
                SELECT report_json FROM solution_attempts
                WHERE id = ? AND workspace_id = ?
                """,
                (attempt_id, self.workspace_id),
            ).fetchone()
        if row is None:
            raise RecordNotFound(
                f"solution attempt not found in workspace: {attempt_id}"
            )
        return SolutionAttempt.model_validate(json.loads(row["report_json"]))

    def list_solution_attempts(self) -> list[SolutionAttempt]:
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT report_json FROM solution_attempts
                WHERE workspace_id = ?
                ORDER BY created_at, id
                """,
                (self.workspace_id,),
            ).fetchall()
        return [
            SolutionAttempt.model_validate(json.loads(row["report_json"]))
            for row in rows
        ]

    def add_solve_evaluation(self, run: SolveEvaluationRun) -> SolveEvaluationRun:
        self._assert_workspace(run.workspace_id)
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO solve_evaluation_runs (
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
                "solve_evaluation_completed",
                run.id,
                {
                    "name": run.name,
                    "top_k": run.top_k,
                    "metrics": run.metrics.model_dump(mode="json"),
                },
            )
        return run

    def list_solve_evaluations(self) -> list[SolveEvaluationRun]:
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT report_json FROM solve_evaluation_runs
                WHERE workspace_id = ?
                ORDER BY created_at, id
                """,
                (self.workspace_id,),
            ).fetchall()
        return [
            SolveEvaluationRun.model_validate(json.loads(row["report_json"]))
            for row in rows
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
                "reviewed": bool(row["reviewed"]),
                "problem_kind": row["problem_kind"],
                "math_payload": payload,
                "verification": json.loads(row["verification_json"]),
                "extraction": (
                    json.loads(row["extraction_json"])
                    if row["extraction_json"]
                    else None
                ),
                "method_drafts": json.loads(row["method_drafts_json"] or "[]"),
                "status": row["status"],
                "origin": row["origin"],
                "source_attempt_id": row["source_attempt_id"],
                "reviewed_at": row["reviewed_at"],
                "reviewer_note": row["reviewer_note"],
                "revision": row["revision"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"] or row["created_at"],
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
            signature=json.loads(row["signature_json"] or "{}"),
            success_count=row["success_count"],
            failure_count=row["failure_count"],
            example_ids=example_ids,
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )
